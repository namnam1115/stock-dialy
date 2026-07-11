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
# 会計年度末（fiscalYearEnd）。予想日の自前算出（四半期末＋一定日数）に使う。
_FYE_KEYS = (
    'fiscal_year_end', 'fiscalYearEnd', 'fiscalYearEndDate', 'fy_end', 'fyEnd',
)
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
        start = start or date.today()
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

    def fetch_history(self, months: int = 13, end=None) -> list:
        """基準日から過去 months ヶ月分の（確定済み）決算発表実績を取得する。

        提供元APIは予想日を返さなくなったため、予想日は各社の会計年度末
        （fiscalYearEnd）から自前算出する（earnings_calendar_estimate）。その
        算出に必要な「各社の会計年度末・社名・市場区分」の名簿をこの履歴から作る。
        決算集中期（2・5・8・11月）は1レンジで limit を超えるため、fetch_window と
        同様に日付レンジを二分割して取り切る。

        Args:
            months: 遡る月数（既定13＝直近1年＋余裕。年次のみ開示の企業も拾える）
            end: 取得終端日（既定=今日）

        Returns:
            list[dict]: fetch_window と同形（fiscal_year_end を含む）
        """
        from dateutil.relativedelta import relativedelta

        end = end or date.today()
        start = end - relativedelta(months=months)

        self._request_count = 0
        raw = self._fetch_range(start, end)

        normalized = []
        for item in raw:
            n = self._normalize_item(item)
            if n:
                normalized.append(n)
        logger.info('決算履歴API取得: 生%s件 → 正規化%s件', len(raw), len(normalized))
        return normalized

    def resolve_edinet_code(self, sec_code: str):
        """証券コードから edinet_code を検索して解決する（1リクエスト）。

        /v1/search は企業名だけでなく証券コードでの完全一致検索にも使える
        （q=証券コード）。/v1/calendar には code によるフィルタが無いため、
        個社エンドポイント（/companies/{edinet_code}/...）を呼ぶ前段として使う。
        """
        code4 = (sec_code or '').strip()
        if len(code4) == 5 and code4.endswith('0'):
            code4 = code4[:4]
        if not code4:
            return None
        try:
            resp = self.session.get(
                f'{self.base_url}/v1/search', params={'q': code4}, timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            logger.warning('edinet_code解決失敗（通信エラー・code=%s）: %s', code4, e)
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        rows = data.get('data') if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return None
        for row in rows:
            row_sec = str(row.get('sec_code') or '').strip()
            if row_sec in (code4, code4 + '0'):
                return row.get('edinet_code')
        return None

    def fetch_latest_disclosure(self, edinet_code: str):
        """個社の直近の決算短信（実績）を1件取得する（1リクエスト）。

        /v1/calendar（横断フィード）は提供元側のインデックス漏れがあり、実際に
        開示済みの銘柄が丸ごと出てこないことがある（実例: 8267 イオンの
        2026-07-10開示）。この個社エンドポイントは決算短信そのものを見るため
        正確だが、1銘柄=1リクエストで無料枠(100/日)を消費するため、全銘柄には
        使わず「予想と食い違っていそうな銘柄だけ」の答え合わせに限定して使う。

        Returns:
            dict | None: {earnings_date(date), quarter, fiscal_year_end} また
            は取得失敗・データ無しで None。
        """
        if not edinet_code:
            return None
        try:
            resp = self.session.get(
                f'{self.base_url}/v1/companies/{edinet_code}/earnings',
                params={'limit': 1}, timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            logger.warning('個社決算実績取得失敗（通信エラー・edinet_code=%s）: %s',
                           edinet_code, e)
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        inner = data.get('data') if isinstance(data, dict) else None
        rows = inner.get('earnings') if isinstance(inner, dict) else None
        if not rows:
            return None
        item = rows[0]
        d = self._parse_disclosure_date(item.get('disclosure_date'))
        if d is None:
            return None
        return {
            'earnings_date': d,
            'quarter': item.get('quarter'),
            'fiscal_year_end': item.get('fiscal_year_end'),
        }

    @staticmethod
    def _parse_disclosure_date(value):
        """'Fri, 10 Jul 2026 00:00:00 GMT' 等のRFC1123形式を date に変換。"""
        if not value:
            return None
        from email.utils import parsedate_to_datetime
        try:
            return parsedate_to_datetime(str(value)).date()
        except (TypeError, ValueError):
            return EarningsCalendarAPIService._parse_date(str(value))

    # 1回の取得（fetch_window / fetch_history）での最大リクエスト数。
    # 無料枠100/日に対し、日次同期は window(〜3) + history(〜15) = 〜18 程度。
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
            # 会計年度末（予想日の自前算出に使う。無ければ空文字）
            'fiscal_year_end': _first(raw, _FYE_KEYS),
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
