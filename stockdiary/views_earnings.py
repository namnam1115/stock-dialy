# stockdiary/views_earnings.py
"""決算カレンダー（決算予定の表示）

保有銘柄・ウォッチリスト（メモ）の次回決算日と残り日数、月グリッド＋選択日リストの
ハイブリッド型カレンダーを表示する。表示は外部APIを叩かず、ローカルの
EarningsSchedule（証券コードがキーの決算予定マスタ）のみを参照する。

決算日は日記側に持たせない。日記は stock_symbol（銘柄コード）を持つので、
表示時に get_next_earnings_map / attach_next_earnings で都度 join して引く
（マスタと日記の二重管理・日次コピーを避ける）。

決算予定データの取り込みは earnings_analysis の sync_earnings_calendar コマンド
（日次バッチ）が担う。
"""
from calendar import Calendar, monthrange
from datetime import date, datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import render

from earnings_analysis.models import EarningsSchedule
from .models import StockDiary
# 決算予定のコード参照ヘルパー（サービス層）。RecallService からも再利用する。
# 後方互換・テスト参照のため、旧名（_to_ticker 等）も別名で公開する。
from .services.earnings_lookup import (  # noqa: F401
    NextEarnings,
    PROXIMITY_IMMINENT_DAYS,
    PROXIMITY_SOON_DAYS,
    get_next_earnings_map,
    attach_next_earnings,
    to_ticker as _to_ticker,
    candidate_codes as _candidate_codes,
    classify_proximity as _classify_proximity,
)

# カレンダーの表示期間（当日からの日数）
CALENDAR_WINDOW_DAYS = 90

# カレンダーで遡れる過去の日数（直近の決算実績を確認できるように）
CALENDAR_PAST_WINDOW_DAYS = 30

# 月グリッドの曜日見出し（月曜始まり）
WEEKDAY_HEADERS = ['月', '火', '水', '木', '金', '土', '日']


def build_month_grid(year, month, today, window_start, window_end,
                     counts, mine_dates, selected_date):
    """月グリッド（週×7セル）を組む。

    各セルは date / in_month / count / has_mine / in_window / is_today /
    is_selected を持つ。`counts` は {date: 件数}、`mine_dates` は記録銘柄を
    含む日付の集合。
    """
    weeks = []
    for week in Calendar(firstweekday=0).monthdatescalendar(year, month):
        cells = []
        for day in week:
            cells.append({
                'date': day,
                'in_month': day.month == month,
                'count': counts.get(day, 0),
                'has_mine': day in mine_dates,
                'in_window': window_start <= day <= window_end,
                'is_today': day == today,
                'is_selected': day == selected_date,
            })
        weeks.append(cells)
    return weeks


def _scope_filter(qs, scope, user_symbols):
    """scope=mine のとき記録銘柄に絞る。記録銘柄が無ければ空。"""
    if scope == 'mine':
        if user_symbols:
            return qs.filter(securities_code__in=_candidate_codes(user_symbols))
        return qs.none()
    return qs


def _parse_month(value, default_first):
    """'YYYY-MM' を当月1日の date に。失敗時は default_first。"""
    if value:
        try:
            return datetime.strptime(value, '%Y-%m').date().replace(day=1)
        except ValueError:
            pass
    return default_first


def _parse_date(value):
    """'YYYY-MM-DD' を date に。失敗時は None。"""
    if value:
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError:
            pass
    return None


@login_required
def earnings_calendar(request):
    """決算カレンダー（ハイブリッド型: 月グリッド＋選択日リスト）。

    GET パラメータ:
        scope: 'mine'（記録銘柄のみ・既定）/ 'all'（全銘柄）
        month: 'YYYY-MM'（表示月。既定=当月。当日〜90日の範囲にクランプ）
        date:  'YYYY-MM-DD'（選択日。既定=今日 or 当月で決算のある最初の日）
        panel=day: 指定時、HTMXで選択日パネルのみ返す
    """
    scope = request.GET.get('scope', 'mine')
    if scope not in ('mine', 'all'):
        scope = 'mine'

    today = date.today()
    window_start = today - timedelta(days=CALENDAR_PAST_WINDOW_DAYS)
    window_end = today + timedelta(days=CALENDAR_WINDOW_DAYS)
    ws_first = window_start.replace(day=1)
    we_first = window_end.replace(day=1)

    # 表示月を決定し、ウィンドウ（月単位）にクランプ
    month_first = _parse_month(request.GET.get('month'), today.replace(day=1))
    if month_first < ws_first:
        month_first = ws_first
    elif month_first > we_first:
        month_first = we_first
    year, month = month_first.year, month_first.month
    month_last = month_first.replace(day=monthrange(year, month)[1])

    # 記録銘柄（4桁）集合
    user_diaries = StockDiary.objects.filter(user=request.user, is_excluded=False)
    user_symbols = set(
        user_diaries
        .filter(stock_symbol__regex=r'^\d{4}$')
        .values_list('stock_symbol', flat=True)
    )

    # --- サマリー: 保有・売却済・メモ（日記の状態3分類、月・日に依らず常時表示） ---
    # StockDiary.is_holding / is_sold_out / is_memo と同じ判定基準に揃える
    holdings = attach_next_earnings(
        user_diaries.filter(current_quantity__gt=0), today=today, with_previous=True)
    holdings = sorted(
        [d for d in holdings if d.next_earnings],
        key=lambda d: d.next_earnings.date)

    sold = attach_next_earnings(
        user_diaries.filter(transaction_count__gt=0, current_quantity=0),
        today=today, with_previous=True)
    sold = sorted(
        [d for d in sold if d.next_earnings],
        key=lambda d: d.next_earnings.date)

    watchlist = attach_next_earnings(
        user_diaries.filter(transaction_count=0), today=today, with_previous=True)
    watchlist = sorted(
        [d for d in watchlist if d.next_earnings],
        key=lambda d: d.next_earnings.date)

    # サマリーの既定タブ（データがある最初の区分を開く。全て空なら保有を既定に）
    if holdings:
        default_tab = 'hold'
    elif sold:
        default_tab = 'sold'
    elif watchlist:
        default_tab = 'watch'
    else:
        default_tab = 'hold'

    # --- 月グリッド用の集計（対象月のみ・軽量） ---
    month_qs = _scope_filter(
        EarningsSchedule.objects.filter(
            earnings_date__range=(month_first, month_last)),
        scope, user_symbols)
    counts = {
        row['earnings_date']: row['c']
        for row in month_qs.values('earnings_date').annotate(c=Count('id'))
    }
    # 記録銘柄を含む日（scope に依らずハイライト用に算出）
    if user_symbols:
        mine_dates = set(
            EarningsSchedule.objects
            .filter(earnings_date__range=(month_first, month_last),
                    securities_code__in=_candidate_codes(user_symbols))
            .values_list('earnings_date', flat=True).distinct()
        )
    else:
        mine_dates = set()

    # 選択日: パラメータ優先。なければ今日（当月内）→決算のある最初の日→月初
    selected_date = _parse_date(request.GET.get('date'))
    if not (selected_date and month_first <= selected_date <= month_last):
        if month_first <= today <= month_last:
            selected_date = today
        elif counts:
            selected_date = min(counts)
        else:
            selected_date = month_first

    grid = build_month_grid(
        year, month, today, window_start, window_end,
        counts, mine_dates, selected_date)

    # --- 選択日の決算一覧 ---
    day_qs = _scope_filter(
        EarningsSchedule.objects.filter(earnings_date=selected_date),
        scope, user_symbols).order_by('securities_code')
    day_items = []
    for schedule in day_qs:
        ticker = _to_ticker(schedule.securities_code)
        schedule.ticker_normalized = ticker
        schedule.is_mine = ticker in user_symbols
        day_items.append(schedule)

    # 月送りナビ（ウィンドウ外は None）
    prev_first = (month_first - timedelta(days=1)).replace(day=1)
    next_first = month_last + timedelta(days=1)
    prev_month = prev_first.strftime('%Y-%m') if prev_first >= ws_first else None
    next_month = next_first.strftime('%Y-%m') if next_first <= we_first else None

    is_htmx = (
        request.headers.get('HX-Request') == 'true'
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    )

    context = {
        'scope': scope,
        'today': today,
        'window_days': CALENDAR_WINDOW_DAYS,
        'past_window_days': CALENDAR_PAST_WINDOW_DAYS,
        'holdings': holdings,
        'sold': sold,
        'watchlist': watchlist,
        'default_tab': default_tab,
        'weekday_headers': WEEKDAY_HEADERS,
        'grid': grid,
        'month_label': f'{year}年{month}月',
        'month_value': month_first.strftime('%Y-%m'),
        'prev_month': prev_month,
        'next_month': next_month,
        'selected_date': selected_date,
        'day_items': day_items,
        # HTMX応答でのみ #ec-summary をOOB更新する（フルページでは二重描画を避ける）
        'is_htmx': is_htmx,
    }

    if is_htmx and request.GET.get('panel') == 'day':
        return render(
            request, 'stockdiary/partials/earnings_calendar_day.html', context)
    if is_htmx:
        return render(
            request, 'stockdiary/partials/earnings_calendar_month.html', context)
    return render(request, 'stockdiary/earnings_calendar.html', context)
