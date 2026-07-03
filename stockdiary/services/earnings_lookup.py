# stockdiary/services/earnings_lookup.py
"""決算予定をコードで引くための共有ヘルパー（サービス層）

決算日は日記に持たせず、EarningsSchedule（証券コードがキーの決算予定マスタ）を
唯一の正とし、表示・想起の各所から銘柄コードで都度 join して引く。

views_earnings（決算カレンダー）と RecallService（ホームの想起）から再利用する。
"""
from dataclasses import dataclass
from datetime import date

from earnings_analysis.models import EarningsSchedule

# 決算の近さ区分のしきい値（日数）
PROXIMITY_IMMINENT_DAYS = 3   # 間近
PROXIMITY_SOON_DAYS = 14      # 近日


@dataclass
class NextEarnings:
    """ある銘柄の「次回決算」表示用の値オブジェクト（DBカラムではない）。"""
    date: date
    type: str
    days_until: int
    proximity: str
    is_estimated: bool = True  # 予想日か確定日か（既定は予想）


@dataclass
class PreviousEarnings:
    """ある銘柄の「前回（直前）決算」表示用の値オブジェクト。

    実発表の専用ソース（TDNET決算短信）は未投入のため、確定分
    （is_estimated=False）を実績、予想分を予想として扱う（A+B）。
    """
    date: date
    type: str
    is_estimated: bool  # False=確定(実績) / True=予想のまま過ぎた日
    days_ago: int


def to_ticker(securities_code: str) -> str:
    """証券コードを4桁の銘柄コードへ正規化する。"""
    code = (securities_code or '').strip()
    if len(code) == 5 and code.endswith('0'):
        return code[:4]
    return code


def candidate_codes(symbols):
    """4桁銘柄コード集合から、EarningsSchedule 照合用の候補コード一覧を作る。"""
    candidates = []
    for s in symbols:
        candidates.append(s)
        candidates.append(s + '0')
    return candidates


def classify_proximity(days_until: int) -> str:
    """残り日数を近さ区分へ分類する。"""
    if days_until <= PROXIMITY_IMMINENT_DAYS:
        return 'imminent'
    if days_until <= PROXIMITY_SOON_DAYS:
        return 'soon'
    return 'scheduled'


def get_next_earnings_map(symbols, today=None) -> dict:
    """銘柄コード集合 → {ticker(4桁): NextEarnings} を1クエリで引く。

    各銘柄について「当日以降で最も近い」決算予定を採用する。決算日は日記に
    持たせず、ここで EarningsSchedule（マスタ）から都度参照する。
    """
    if today is None:
        today = date.today()

    # 4桁の日本株コードのみ対象（外国株などは決算予定マスタに無い）
    tickers = {s for s in symbols if s and s.isdigit() and len(s) == 4}
    if not tickers:
        return {}

    rows = (
        EarningsSchedule.objects
        .filter(securities_code__in=candidate_codes(tickers),
                earnings_date__gte=today)
        .order_by('securities_code', 'earnings_date')
        .values('securities_code', 'earnings_date', 'earnings_type', 'is_estimated')
    )

    result = {}
    for row in rows:
        ticker = to_ticker(row['securities_code'])
        if ticker in result:
            continue  # ソート済みなので先頭＝最も近い未来日
        days = (row['earnings_date'] - today).days
        result[ticker] = NextEarnings(
            date=row['earnings_date'],
            type=row['earnings_type'],
            days_until=days,
            proximity=classify_proximity(days),
            is_estimated=row.get('is_estimated', True),
        )
    return result


def get_previous_earnings_map(symbols, today=None) -> dict:
    """銘柄コード集合 → {ticker(4桁): PreviousEarnings} を1クエリで引く。

    各銘柄について「当日より前で最も新しい」決算予定（履歴）を採用する。
    同期は当日以降のみ洗い替えるため、過去分は履歴として残っている。
    """
    if today is None:
        today = date.today()

    tickers = {s for s in symbols if s and s.isdigit() and len(s) == 4}
    if not tickers:
        return {}

    rows = (
        EarningsSchedule.objects
        .filter(securities_code__in=candidate_codes(tickers),
                earnings_date__lt=today)
        .order_by('securities_code', '-earnings_date')
        .values('securities_code', 'earnings_date', 'earnings_type', 'is_estimated')
    )

    result = {}
    for row in rows:
        ticker = to_ticker(row['securities_code'])
        if ticker in result:
            continue  # ソート済みなので先頭＝最も新しい過去日
        result[ticker] = PreviousEarnings(
            date=row['earnings_date'],
            type=row['earnings_type'],
            is_estimated=row.get('is_estimated', True),
            days_ago=(today - row['earnings_date']).days,
        )
    return result


def attach_next_earnings(diaries, today=None, with_previous=False):
    """日記群へ `diary.next_earnings`（NextEarnings or None）を付与する。

    一覧表示で使う。渡された全件分の決算予定を1クエリでまとめて引く。
    with_previous=True のとき `diary.prev_earnings`（PreviousEarnings or None）も付与する。
    """
    diaries = list(diaries)
    symbols = {d.stock_symbol for d in diaries if d.stock_symbol}
    mapping = get_next_earnings_map(symbols, today=today)
    prev_map = get_previous_earnings_map(symbols, today=today) if with_previous else {}
    for diary in diaries:
        diary.next_earnings = mapping.get(diary.stock_symbol)
        if with_previous:
            diary.prev_earnings = prev_map.get(diary.stock_symbol)
    return diaries
