# earnings_analysis/services/earnings_calendar_api.py
"""決算予定API（EDINET DB /v1/calendar）クライアント

商用利用可能な無料プラン（100リクエスト/日）の決算予定APIから、当日〜90日後の
決算発表予定を取得する。バッチ処理（日次1回）からのみ呼び出し、画面表示時は
このクライアントを使わない（ローカルDB参照）。

エンドポイント仕様（EDINET DB, https://edinetdb.jp/v1/calendar）:
- クエリ: from / to（YYYY-MM-DD）・code・market・sort・order・limit（既定500・最大2000）
- **offset は無い**。件数が limit を超える場合は日付レンジを分割して取得する。
- 認証: X-API-Key ヘッダー（Authorization: Bearer も可）。
- レスポンスは {"data": {"calendar": [...], "count": N, ...}} と1段ネストする。
- 各項目（camelCase）: secCode(4桁) / companyName / periodType(決算種別) /
  marketSegment / announcementDate（確定日, 予測時 null）/
  estimatedAnnouncementDate（予測日）/ dateStatus(confirmed|estimated) 等。
  → 確定日を優先し、無ければ予測日を採用する。

設計方針:
- エンドポイントURL・認証ヘッダーは settings.EARNINGS_CALENDAR_API_SETTINGS で
  差し替え可能にする（提供元の仕様変更や別プロバイダへの切り替えに耐える）。
- レスポンスのフィールド名は提供元により揺れがあるため、複数の候補キー
  （snake / camel）から defensive に取り出して正規化する（_normalize_item）。
- 決算は特定時期に集中するため 90 日窓で limit(2000) を超え得る。返却件数が limit と
  等しい（＝切り捨ての可能性）ときは日付レンジを二分割して再取得する（_fetch_range）。
"""
import logging
from datetime import date, timedelta

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# レスポンスの揺れに備えた候補キー（先勝ち。snake/camel 両対応）
_CODE_KEYS = (
    'securities_code', 'securitiesCode', 'secCode', 'sec_code', 'securityCode',
    'code', 'ticker', 'stock_code',
)
_NAME_KEYS = (
    'company_name', 'companyName', 'name', 'filer_name', 'filerName',
)
# 確定日(announcementDate)を優先し、無ければ予測日(estimatedAnnouncementDate)に
# フォールバックする。将来分は dateStatus='estimated' で確定日が null のことが多い。
_DATE_KEYS = (
    'earnings_date', 'announcementDate', 'announcement_date',
    'estimatedAnnouncementDate', 'estimated_announcement_date',
    'scheduled_date', 'disclosureDate', 'disclosed_date', 'forecast_date', 'date',
)
_TYPE_KEYS = (
    'earnings_type', 'periodType', 'period_type', 'type', 'fiscal_period', 'quarter',
)
_MARKET_KEYS = (
    'market_segment', 'marketSegment', 'market', 'segment', 'market_division',
    'market_code',
)
_UPDATED_KEYS = ('updated_at', 'updatedAt', 'modified', 'last_updated')
# 確定日（announcementDate 系）と予想日（estimatedAnnouncementDate 系）を区別する
_CONFIRMED_DATE_KEYS = ('earnings_date', 'announcementDate', 'announcement_date')
_STATUS_KEYS = ('dateStatus', 'date_status', 'status')


def _first(d: dict, keys) -> str:
    """候補キーのうち最初に見つかった非空の値を文字列で返す。"""
    for key in keys:
        if key in d and d[key] not in (None, ''):
            return str(d[key]).strip()
    return ''


class EarningsCalendarAPIError(Exception):
    """決算予定APIの呼び出し失敗。"""


class EarningsCalendarAPIService:
    """決算予定APIクライアント。"""

    def __init__(self):
        conf = getattr(settings, 'EARNINGS_CALENDAR_API_SETTINGS', {}) or {}
        self.api_key = conf.get('API_KEY', '')
        self.base_url = conf.get('BASE_URL', 'https://edinetdb.jp').rstrip('/')
        self.calendar_path = conf.get('CALENDAR_PATH', '/v1/calendar')
        self.auth_header = conf.get('AUTH_HEADER', 'X-API-Key')
        self.auth_scheme = conf.get('AUTH_SCHEME', '')  # 例: 'Bearer'。空ならキーをそのまま
        self.page_limit = int(conf.get('PAGE_LIMIT', 2000))
        self.timeout = int(conf.get('TIMEOUT', 60))
        self.user_agent = conf.get(
            'USER_AGENT', 'KabulogEarningsCalendarBot/1.0 (https://kabu-log.net)'
        )

        self.session = requests.Session()
        headers = {'User-Agent': self.user_agent, 'Accept': 'application/json'}
        if self.api_key:
            value = f'{self.auth_scheme} {self.api_key}'.strip() if self.auth_scheme else self.api_key
            headers[self.auth_header] = value
        self.session.headers.update(headers)

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}{self.calendar_path}"

    def fetch_window(self, days: int = 90, start=None) -> list:
        """基準日〜days日後の決算予定を全件取得して正規化済みのリストで返す。

        Args:
            days: 取得期間（基準日からの日数）
            start: 取得基準日（既定=今日）。失敗日のリカバリ実行で過去日を指定可能。

        Returns:
            list[dict]: {securities_code, company_name, earnings_date(date),
                         earnings_type, market_segment, source_updated_at(str)}
        """
        start = start or timezone.localdate()
        end = start + timedelta(days=days)

        self._request_count = 0
        raw = self._fetch_range(start, end)

        normalized = []
        for item in raw:
            n = self._normalize_item(item)
            if n:
                normalized.append(n)
        logger.info('決算予定API取得: 生%s件 → 正規化%s件', len(raw), len(normalized))
        return normalized

    # 1回の取得（fetch_window）での最大リクエスト数。
    # 無料枠100/日に対し、日次同期は確定窓の取得のみで数リクエスト程度。
    MAX_REQUESTS = 40

    def _fetch_range(self, date_from: date, date_to: date) -> list:
        """[date_from, date_to] を取得。limit に達したら日付を二分割して取り切る。

        /v1/calendar に offset は無いため、件数が limit と等しい（＝切り捨ての
        可能性）ときはレンジを半分にして再帰取得する。重複は同期側で
        (コード×日付) により排除されるため、分割の境界重複は無害。
        """
        if self._request_count >= self.MAX_REQUESTS:
            logger.warning('決算予定API: リクエスト上限(%s)に達したため打ち切り',
                           self.MAX_REQUESTS)
            return []

        rows = self._fetch_page(date_from, date_to)
        self._request_count += 1

        if len(rows) < self.page_limit or date_from >= date_to:
            return rows

        # 切り捨ての可能性 → レンジを二分割
        mid = date_from + timedelta(days=(date_to - date_from).days // 2)
        left = self._fetch_range(date_from, mid)
        right = self._fetch_range(mid + timedelta(days=1), date_to)
        return left + right

    def _fetch_page(self, date_from: date, date_to: date) -> list:
        params = {
            'from': date_from.isoformat(),
            'to': date_to.isoformat(),
            'limit': self.page_limit,
            'sort': 'date',
            'order': 'asc',
        }
        try:
            resp = self.session.get(self.endpoint, params=params, timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            raise EarningsCalendarAPIError(f'決算予定API通信エラー: {e}') from e

        if resp.status_code != 200:
            raise EarningsCalendarAPIError(
                f'決算予定API HTTPエラー: status={resp.status_code} body={resp.text[:300]}'
            )
        try:
            data = resp.json()
        except ValueError as e:
            raise EarningsCalendarAPIError(f'決算予定API JSONパース失敗: {e}') from e

        return self._extract_results(data)

    # 結果配列が入り得るキー（EDINET DB は data.calendar にネストする）
    _ARRAY_KEYS = ('calendar', 'data', 'results', 'items', 'earnings',
                   'entries', 'records')

    @classmethod
    def _extract_results(cls, data) -> list:
        """レスポンスから結果リストを取り出す。

        EDINET DB /v1/calendar は {"data": {"calendar": [...]}} のように1段
        ネストするため、トップレベルと data 直下の両方を探索する。
        """
        if isinstance(data, list):
            return data
        if not isinstance(data, dict):
            return []

        # トップレベルの配列キー（data が配列のケースも含む）
        for key in cls._ARRAY_KEYS:
            value = data.get(key)
            if isinstance(value, list):
                return value

        # 1段ネスト（data / result 等のコンテナ内の配列キー）
        for container_key in ('data', 'result', 'response', 'payload'):
            inner = data.get(container_key)
            if isinstance(inner, dict):
                for key in cls._ARRAY_KEYS:
                    value = inner.get(key)
                    if isinstance(value, list):
                        return value
        return []

    @staticmethod
    def _normalize_item(raw: dict):
        """1件の生データを正規化する。必須項目（コード・日付）が欠けたら None。"""
        if not isinstance(raw, dict):
            return None

        code = _first(raw, _CODE_KEYS)
        date_str = _first(raw, _DATE_KEYS)
        if not code or not date_str:
            return None

        parsed = EarningsCalendarAPIService._parse_date(date_str)
        if parsed is None:
            return None
        # 予測日（将来分の estimatedAnnouncementDate）は前年実績＋約1年で
        # 曜日がずれ、土日に落ちることがある。日本企業は土日に決算発表しない
        # ため、週末に落ちた予定日は最寄りの平日へ寄せる。
        parsed = EarningsCalendarAPIService._to_business_day(parsed)

        return {
            'securities_code': code,
            'company_name': _first(raw, _NAME_KEYS),
            'earnings_date': parsed,
            'is_estimated': EarningsCalendarAPIService._is_estimated(raw),
            'earnings_type': _first(raw, _TYPE_KEYS),
            'market_segment': _first(raw, _MARKET_KEYS),
            'source_updated_at': _first(raw, _UPDATED_KEYS),
        }

    @staticmethod
    def _is_estimated(raw: dict) -> bool:
        """この決算日が予想(estimated)か確定(confirmed)かを判定する。

        優先順位:
          1. dateStatus が明示されていればそれに従う（confirmed→False / estimated→True）
          2. 無ければ、確定日(announcementDate 系)に値があれば確定=False
          3. どちらも無ければ予想=True（＝将来分の予測日のみ）
        """
        status = (_first(raw, _STATUS_KEYS) or '').strip().lower()
        if status == 'confirmed':
            return False
        if status == 'estimated':
            return True
        return not bool(_first(raw, _CONFIRMED_DATE_KEYS))

    @staticmethod
    def _is_business_day(d) -> bool:
        """営業日（土日でも祝日でもない）か判定する。"""
        if d.weekday() >= 5:  # 土=5, 日=6
            return False
        try:
            import jpholiday
            return not jpholiday.is_holiday(d)
        except Exception:
            # jpholiday が無い環境では土日のみ考慮（フォールバック）
            return True

    @staticmethod
    def _to_business_day(d):
        """土日・祝日に落ちた決算予定日を最寄りの営業日へ寄せる。

        同じ距離なら前営業日を優先する（土曜→金曜・日曜→月曜と整合）。
        連休（GW・年末年始等）に落ちた場合も、外側へ探索して最寄りの営業日へ。
        """
        if EarningsCalendarAPIService._is_business_day(d):
            return d
        for delta in range(1, 10):
            prev = d - timedelta(days=delta)
            if EarningsCalendarAPIService._is_business_day(prev):
                return prev
            nxt = d + timedelta(days=delta)
            if EarningsCalendarAPIService._is_business_day(nxt):
                return nxt
        return d

    @staticmethod
    def _parse_date(value: str):
        """ISO（YYYY-MM-DD）/ スラッシュ区切りの日付を date に変換。"""
        from datetime import datetime

        value = value.strip()[:10]
        for fmt in ('%Y-%m-%d', '%Y/%m/%d'):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        return None
