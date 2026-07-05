# earnings_analysis/services/earnings_calendar_estimate.py
"""決算発表予想日の自前算出

提供元API（edinetdb.jp /v1/calendar）は現在、確定した直近の発表日しか返さず、
数ヶ月先までの「予想日」を返さなくなった（以前は estimatedAnnouncementDate を
提供していた）。そのため確定分だけで洗い替えすると、記録銘柄の多くが決算予定
マスタから外れ、決算カレンダーで日記に関連付かなくなる（実害: 直近2週間に確定
発表がある銘柄しか「自身の記録」に出ない）。

ここでは各社の fiscalYearEnd（会計年度末）と決算種別から、四半期末＋一定日数で
次回以降の発表予想日を自前で算出し、確定分と合わせて EarningsSchedule を埋める。

オフセット（期末→発表までの日数）は履歴実測の中央値 43 日（第1〜3四半期・本決算の
全種別で 40〜45 日に収束。TSE の「四半期末後45日以内開示」ルールに整合）。毎回の
同期でゼロから再生成するため、洗い替え方式のまま予想日が陳腐化しない。
"""
from datetime import date, datetime, timedelta

# 決算種別 → 会計年度末から見た四半期末までの遡り月数
#   例: 会計年度末 3/31 のとき、第1四半期末 = 6/30（9ヶ月前）
PERIOD_QUARTER_OFFSET_MONTHS = {
    '第１四半期': 9,
    '第２四半期': 6,
    '第３四半期': 3,
    '本決算': 0,
}

# 四半期末から発表日までの日数（履歴実測の中央値）
ANNOUNCE_OFFSET_DAYS = 43

# 予想を出す期間（基準日からの日数）。既定はカレンダー表示窓（90日）に合わせる。
ESTIMATE_HORIZON_DAYS = 90

# 確定日の近傍（±日数）は予想を出さない（同一四半期の重複・矛盾を防ぐ）
CONFIRMED_SUPPRESS_DAYS = 30

# fiscalYearEnd を将来へ転がして次回以降の四半期末を探す年数
_ROLL_YEARS = 3


def _parse_date(value):
    """'YYYY-MM-DD'（先頭10文字）を date に。失敗時 None。"""
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _minus_months(d: date, months: int) -> date:
    """d から months ヶ月前の「月末」を返す（四半期末の算出用）。

    会計年度末は月末日のことが多いため、月をまたぐ日付ズレを避けて対象月の
    末日に丸める（例: 3/31 の9ヶ月前 → 6/30）。dateutil に依存しない軽量版。
    """
    m = d.month - 1 - months  # 0-indexed month
    year = d.year + m // 12
    month = m % 12 + 1
    # 翌月1日の前日 = 当月末日
    if month == 12:
        first_next = date(year + 1, 1, 1)
    else:
        first_next = date(year, month + 1, 1)
    return first_next - timedelta(days=1)


def _plus_years(d: date, years: int) -> date:
    """d の years 年後（2/29 は 2/28 に丸める）。"""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def build_roster(history_items) -> dict:
    """履歴（正規化済み item 群）から予想算出用の名簿を作る。

    Returns:
        dict: securities_code -> {'fye': date, 'company_name': str,
                                  'market_segment': str}
        各コードは最も新しい会計年度末を持つ履歴を採用する（社名・市場区分も最新）。
    """
    roster = {}
    for item in history_items:
        code = item.get('securities_code')
        fye = _parse_date(item.get('fiscal_year_end'))
        if not code or fye is None:
            continue
        current = roster.get(code)
        if current is None or fye > current['fye']:
            roster[code] = {
                'fye': fye,
                'company_name': item.get('company_name', ''),
                'market_segment': item.get('market_segment', ''),
            }
    return roster


def build_estimates(roster, confirmed_by_code, base_date,
                    horizon_days=ESTIMATE_HORIZON_DAYS, to_business_day=None):
    """名簿と確定日から、基準日〜horizon 内の発表予想日を算出する。

    Args:
        roster: build_roster の戻り（code -> {fye, company_name, market_segment}）
        confirmed_by_code: code -> [確定発表日(date), ...]（近傍の予想を抑止する）
        base_date: 基準日（この日以降・horizon 以内の予想のみ出す）
        horizon_days: 予想を出す期間（基準日からの日数）
        to_business_day: 予想日を最寄り営業日へ寄せる関数（土日祝の補正。任意）

    Returns:
        list[dict]: fetch_window と同形の item（is_estimated=True）
    """
    window_end = base_date + timedelta(days=horizon_days)
    estimates = []
    seen = set()  # (code, date) の重複除去

    for code, info in roster.items():
        fye = info['fye']
        confirmed_dates = confirmed_by_code.get(code, ())
        for k in range(_ROLL_YEARS + 1):
            base_fye = _plus_years(fye, k)
            for period, qmonths in PERIOD_QUARTER_OFFSET_MONTHS.items():
                quarter_end = _minus_months(base_fye, qmonths)
                predicted = quarter_end + timedelta(days=ANNOUNCE_OFFSET_DAYS)
                if to_business_day is not None:
                    predicted = to_business_day(predicted)
                if not (base_date <= predicted <= window_end):
                    continue
                # 確定日の近傍は予想を出さない（確定が正）
                if any(abs((predicted - cd).days) <= CONFIRMED_SUPPRESS_DAYS
                       for cd in confirmed_dates):
                    continue
                key = (code, predicted)
                if key in seen:
                    continue
                seen.add(key)
                estimates.append({
                    'securities_code': code,
                    'company_name': info['company_name'],
                    'earnings_date': predicted,
                    'is_estimated': True,
                    'earnings_type': period,
                    'market_segment': info['market_segment'],
                    'source_updated_at': '',
                })
    return estimates
