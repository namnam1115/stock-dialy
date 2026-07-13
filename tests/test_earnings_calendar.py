"""決算予定（決算カレンダー）機能のテスト。

なぜこのテストがあるか:
  決算予定API（EDINET DB /v1/calendar）から日次取得した決算予定を、保有銘柄・
  ウォッチリスト・決算カレンダーに表示し、決算前日に通知する一連の流れを固定する。
  外部APIはモックし、画面表示時にAPIを叩かない（ローカルDB参照のみ）ことも担保する。

  検証する主な不変条件:
  - APIクライアントはフィールド名の揺れを吸収して正規化する
  - limit到達時は offset でページングして全件取得する
  - 同期は未来分を洗い替えし、過去分は履歴として残す
  - 決算日は日記に持たせず、銘柄コードで EarningsSchedule を join して引く
    （4桁/5桁コードの両方に対応・最近接の未来日を採用）
  - 決算前日（翌日決算）の通知が記録ユーザーへ1通だけ届く（重複しない）
  - カレンダー画面（月グリッド＋選択日）は GET のみで描画でき、scope で
    記録銘柄/全銘柄を切り替えられる
"""
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from stockdiary.models import StockDiary, NotificationLog
from earnings_analysis.models import EarningsSchedule
from earnings_analysis.services.earnings_calendar_api import (
    EarningsCalendarAPIService,
)
from earnings_analysis.services import earnings_calendar_sync as sync
from stockdiary import views_earnings
from stockdiary.services.earnings_lookup import get_previous_earnings_map

User = get_user_model()
pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# APIクライアント（正規化・ページング）
# ---------------------------------------------------------------------------

def test_normalize_item_handles_field_name_variants():
    """提供元によるキー名の揺れ（secCode / announcement_date 等）を吸収する。"""
    raw = {
        'secCode': '7203',
        'companyName': 'トヨタ自動車',
        'announcement_date': '2026-08-05',
        'period_type': '第1四半期',
        'market': 'プライム',
    }
    item = EarningsCalendarAPIService._normalize_item(raw)
    assert item['securities_code'] == '7203'
    assert item['company_name'] == 'トヨタ自動車'
    assert item['earnings_date'] == date(2026, 8, 5)
    assert item['earnings_type'] == '第1四半期'
    assert item['market_segment'] == 'プライム'


def test_extract_results_handles_nested_calendar_envelope():
    """EDINET DB の {"data": {"calendar": [...]}} ネストから配列を取り出す。"""
    payload = {'data': {'calendar': [{'secCode': '9046'}], 'count': 1}}
    rows = EarningsCalendarAPIService._extract_results(payload)
    assert rows == [{'secCode': '9046'}]


def test_normalize_uses_estimated_date_when_announcement_null():
    """予測日（dateStatus=estimated）は announcementDate=null なので
    estimatedAnnouncementDate を採用する（将来分を取りこぼさない）。"""
    raw = {
        'secCode': '9046', 'companyName': '神戸電鉄株式会社',
        'announcementDate': None, 'estimatedAnnouncementDate': '2026-07-01',
        'periodType': '第1四半期', 'marketSegment': None, 'dateStatus': 'estimated',
    }
    item = EarningsCalendarAPIService._normalize_item(raw)
    assert item['securities_code'] == '9046'
    assert item['company_name'] == '神戸電鉄株式会社'
    assert item['earnings_date'] == date(2026, 7, 1)
    assert item['earnings_type'] == '第1四半期'
    assert item['market_segment'] == ''  # null は空文字に


def test_normalize_prefers_confirmed_announcement_date():
    """確定日（announcementDate）があればそちらを優先する。"""
    raw = {
        'secCode': '8316', 'companyName': 'SMFG',
        'announcementDate': '2026-08-03',
        'estimatedAnnouncementDate': '2026-07-02',
        'periodType': '第1四半期',
    }
    item = EarningsCalendarAPIService._normalize_item(raw)
    assert item['earnings_date'] == date(2026, 8, 3)


def test_normalize_item_rejects_missing_code_or_date():
    """必須項目（コード・日付）が欠けたら捨てる（不完全データで落とさない）。"""
    assert EarningsCalendarAPIService._normalize_item({'name': 'x'}) is None
    assert EarningsCalendarAPIService._normalize_item(
        {'secCode': '7203'}  # 日付なし
    ) is None


def test_fetch_window_parses_real_edinetdb_payload(settings):
    """実物の /v1/calendar 応答（data.calendar ネスト・estimated 日付）を取り込める。"""
    settings.EARNINGS_CALENDAR_API_SETTINGS = {
        'API_KEY': 'k', 'BASE_URL': 'https://edinetdb.jp',
        'CALENDAR_PATH': '/v1/calendar', 'PAGE_LIMIT': 2000, 'TIMEOUT': 5,
    }
    service = EarningsCalendarAPIService()
    payload = {'data': {'calendar': [
        {'secCode': '9046', 'companyName': '神戸電鉄株式会社',
         'announcementDate': None, 'estimatedAnnouncementDate': '2026-07-01',
         'periodType': '第1四半期', 'marketSegment': None,
         'dateStatus': 'estimated'},
        {'secCode': '8316', 'companyName': 'SMFG',
         'announcementDate': None, 'estimatedAnnouncementDate': '2026-07-02',
         'periodType': '第1四半期', 'marketSegment': None},
    ], 'count': 2}}

    def fake_get(url, params=None, timeout=None):
        class Resp:
            status_code = 200

            def json(self_inner):
                return payload
        return Resp()

    with patch.object(service.session, 'get', side_effect=fake_get):
        items = service.fetch_window(days=30, start=date(2026, 7, 1))

    assert {i['securities_code'] for i in items} == {'9046', '8316'}
    assert items[0]['earnings_date'] == date(2026, 7, 1)
    assert items[0]['earnings_type'] == '第1四半期'


def test_fetch_window_splits_date_range_when_truncated(settings):
    """件数が limit と同数（切り捨ての可能性）なら日付レンジを分割して取り切る。

    /v1/calendar に offset は無いため、offset ページングではなく from/to の
    二分割で全件を取得することを固定する。
    """
    settings.EARNINGS_CALENDAR_API_SETTINGS = {
        'API_KEY': 'k', 'BASE_URL': 'https://example.test',
        'CALENDAR_PATH': '/v1/calendar', 'AUTH_HEADER': 'X-API-Key',
        'AUTH_SCHEME': '', 'PAGE_LIMIT': 2, 'TIMEOUT': 5,
    }
    service = EarningsCalendarAPIService()
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((params['from'], params['to']))
        span = (params['from'], params['to'])

        class Resp:
            status_code = 200

            def json(self_inner):
                # フルレンジ（4日幅）は limit と同数(2件)で切り捨て → 分割を誘発
                if span[0] != span[1] and (
                        _daydiff(span) >= 3):
                    return {'data': [{'code': 'AAAA', 'date': span[0]},
                                     {'code': 'BBBB', 'date': span[1]}]}
                # 分割後の各サブレンジは1件（< limit）→ 終了
                return {'data': [{'code': span[0].replace('-', ''),
                                  'date': span[0]}]}
        return Resp()

    with patch.object(service.session, 'get', side_effect=fake_get):
        items = service.fetch_window(days=4, start=date(2026, 7, 1))

    assert len(calls) >= 3  # フル + 分割2回以上
    # 分割サブレンジ由来のレコードが取れている（全件取り切り）
    codes = {i['securities_code'] for i in items}
    assert codes  # 空でない
    assert all(len(c) for c in codes)


def _daydiff(span):
    from datetime import datetime
    a = datetime.strptime(span[0], '%Y-%m-%d').date()
    b = datetime.strptime(span[1], '%Y-%m-%d').date()
    return (b - a).days


# ---------------------------------------------------------------------------
# 同期（洗い替え・事前計算）
# ---------------------------------------------------------------------------

def test_sync_skips_when_unconfigured(settings):
    """APIキー未設定なら外部を叩かず0件で終わる（誤って公開APIを叩かない）。"""
    settings.EARNINGS_CALENDAR_API_SETTINGS = {'API_KEY': ''}
    assert sync.sync_earnings_calendar() == 0
    assert EarningsSchedule.objects.count() == 0


def test_sync_replaces_future_keeps_past(settings):
    """同期は未来分を洗い替えし、過去分（履歴）は残す。"""
    settings.EARNINGS_CALENDAR_API_SETTINGS = {'API_KEY': 'k'}
    today = timezone.localdate()

    # 既存: 過去1件 + 未来1件（未来分は洗い替え対象）
    EarningsSchedule.objects.create(
        securities_code='0001', earnings_date=today - timedelta(days=10),
    )
    EarningsSchedule.objects.create(
        securities_code='0002', earnings_date=today + timedelta(days=5),
    )

    new_items = [{
        'securities_code': '7203', 'company_name': 'トヨタ',
        'earnings_date': today + timedelta(days=20),
        'earnings_type': '本決算', 'market_segment': 'プライム',
        'source_updated_at': '',
    }]
    with patch.object(EarningsCalendarAPIService, 'fetch_window', return_value=new_items):
        saved = sync.sync_earnings_calendar(days=90)

    assert saved == 1
    codes = set(EarningsSchedule.objects.values_list('securities_code', flat=True))
    assert '0001' in codes          # 過去分は残る
    assert '0002' not in codes      # 未来分は洗い替えで消える
    assert '7203' in codes          # 新規が入る


def test_sync_base_date_controls_window_and_cutoff(settings):
    """base_date を指定すると取得起点と洗い替え境界がその日になる（失敗日リカバリ）。"""
    settings.EARNINGS_CALENDAR_API_SETTINGS = {'API_KEY': 'k'}
    today = timezone.localdate()
    base = today + timedelta(days=10)

    # base より前（=today+5）は base 基準では「過去」扱い → 残る
    EarningsSchedule.objects.create(
        securities_code='0002', earnings_date=today + timedelta(days=5))
    # base 以降は洗い替え対象 → 消える
    EarningsSchedule.objects.create(
        securities_code='0003', earnings_date=base + timedelta(days=1))

    captured = {}

    def fake_fetch(self, days=90, start=None):
        captured['days'] = days
        captured['start'] = start
        return []

    with patch.object(EarningsCalendarAPIService, 'fetch_window', fake_fetch):
        sync.sync_earnings_calendar(days=30, base_date=base)

    assert captured == {'days': 30, 'start': base}  # 起点が base に渡る
    codes = set(EarningsSchedule.objects.values_list('securities_code', flat=True))
    assert '0002' in codes      # base より前は残る
    assert '0003' not in codes  # base 以降は洗い替えで消える


# ---------------------------------------------------------------------------
# 決算日のコード参照ヘルパー（日記にカラムを持たせず join で引く）
# ---------------------------------------------------------------------------

def test_get_next_earnings_map_matches_4_and_5_digit():
    """4桁の日記コードに対し、4桁/末尾0付き5桁どちらの予定も照合できる。"""
    today = timezone.localdate()
    EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=today + timedelta(days=10),
        earnings_type='第1四半期')
    EarningsSchedule.objects.create(
        securities_code='67580', earnings_date=today + timedelta(days=3),
        earnings_type='本決算')

    mapping = views_earnings.get_next_earnings_map({'7203', '6758'}, today=today)
    assert mapping['7203'].date == today + timedelta(days=10)
    assert mapping['7203'].type == '第1四半期'
    assert mapping['7203'].days_until == 10
    assert mapping['6758'].date == today + timedelta(days=3)


def test_get_next_earnings_map_picks_nearest_across_code_formats():
    """同一銘柄が4桁/5桁の両表記でマスタに登録されていても最も近い日を採用する。

    以前は ORDER BY securities_code, earnings_date でソートしてから銘柄ごとに
    先頭行を採用していたため、コード文字列順で先に来る表記（例: '7203' が
    '72030' より先）の行がそのまま採用されてしまい、実際にはもっと近い日が
    もう一方の表記側にあっても無視されていた。その結果、決算カレンダー
    （日付範囲を1クエリで引く箇所）と日記詳細（この関数を使う箇所）で
    表示される「次回決算日」がずれるバグがあった。
    """
    today = timezone.localdate()
    EarningsSchedule.objects.create(
        securities_code='72030', earnings_date=today + timedelta(days=5),
        earnings_type='本決算', is_estimated=False)
    EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=today + timedelta(days=40),
        earnings_type='第1四半期', is_estimated=True)

    mapping = views_earnings.get_next_earnings_map({'7203'}, today=today)
    assert mapping['7203'].date == today + timedelta(days=5)


def test_get_previous_earnings_map_picks_nearest_across_code_formats():
    """前回決算も同様に、4桁/5桁の表記に関わらず最も新しい過去日を採用する。"""
    today = timezone.localdate()
    EarningsSchedule.objects.create(
        securities_code='72030', earnings_date=today - timedelta(days=3),
        earnings_type='本決算', is_estimated=False)
    EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=today - timedelta(days=40),
        earnings_type='第3四半期', is_estimated=False)

    mapping = get_previous_earnings_map({'7203'}, today=today)
    assert mapping['7203'].date == today - timedelta(days=3)


def test_get_next_earnings_map_picks_nearest_future():
    """過去の予定は無視し、当日以降で最も近い予定を採用する。"""
    today = timezone.localdate()
    EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=today - timedelta(days=5))
    EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=today + timedelta(days=40))
    EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=today + timedelta(days=12))

    mapping = views_earnings.get_next_earnings_map({'7203'}, today=today)
    assert mapping['7203'].date == today + timedelta(days=12)


def test_get_next_earnings_map_ignores_non_jp_codes():
    """4桁数字以外（外国株など）はマスタ照合の対象外。"""
    today = timezone.localdate()
    assert views_earnings.get_next_earnings_map({'AAPL', ''}, today=today) == {}


def test_attach_next_earnings_sets_attribute(db):
    """attach_next_earnings は各日記へ next_earnings（無ければ None）を付与する。"""
    user = User.objects.create_user('u_at', 'uat@e.com', 'p')
    today = timezone.localdate()
    d1 = StockDiary.objects.create(user=user, stock_name='A', stock_symbol='7203')
    d2 = StockDiary.objects.create(user=user, stock_name='B', stock_symbol='6758')
    EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=today + timedelta(days=2),
        earnings_type='本決算')

    views_earnings.attach_next_earnings([d1, d2], today=today)
    assert d1.next_earnings.date == today + timedelta(days=2)
    assert d1.next_earnings.proximity == 'imminent'  # 3日以内
    assert d2.next_earnings is None


def test_classify_proximity_boundaries():
    """近さ区分が境界値で正しく分類される（imminent≤3, soon≤14, 以降 scheduled）。"""
    assert views_earnings._classify_proximity(0) == 'imminent'
    assert views_earnings._classify_proximity(3) == 'imminent'
    assert views_earnings._classify_proximity(4) == 'soon'
    assert views_earnings._classify_proximity(14) == 'soon'
    assert views_earnings._classify_proximity(15) == 'scheduled'


def test_build_month_grid_marks_counts_and_states():
    """月グリッドのセルが件数・記録銘柄・選択日・範囲外を正しく持つ。"""
    today = date(2026, 8, 5)
    window_start, window_end = today, today + timedelta(days=90)
    d1 = date(2026, 8, 10)
    d2 = date(2026, 8, 20)
    counts = {d1: 3, d2: 1}
    mine_dates = {d1}

    grid = views_earnings.build_month_grid(
        2026, 8, today, window_start, window_end, counts, mine_dates, selected_date=d1)

    cells = {c['date']: c for week in grid for c in week}
    assert cells[d1]['count'] == 3 and cells[d1]['has_mine'] and cells[d1]['is_selected']
    assert cells[d2]['count'] == 1 and not cells[d2]['has_mine']
    assert cells[today]['is_today']
    # 8/1 は当月だがウィンドウ開始(8/5)より前 → 範囲外
    assert cells[date(2026, 8, 1)]['in_window'] is False


# ---------------------------------------------------------------------------
# 決算前日通知（ファンアウト・重複防止）
# ---------------------------------------------------------------------------

def test_fan_out_earnings_reminders_one_per_user_no_duplicate():
    """翌日決算の銘柄を記録するユーザーへ1通だけ通知し、再実行で重複しない。"""
    user = User.objects.create_user('u3', 'u3@e.com', 'p')
    # 同一銘柄の日記が2件あってもユーザーへの通知は1通
    StockDiary.objects.create(user=user, stock_name='A', stock_symbol='7203')
    StockDiary.objects.create(user=user, stock_name='A(別記録)', stock_symbol='7203')
    tomorrow = timezone.localdate() + timedelta(days=1)
    EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=tomorrow, earnings_type='本決算')

    created = sync.fan_out_earnings_reminders()
    assert created == 1
    log = NotificationLog.objects.get(user=user)
    assert log.earnings_schedule is not None
    assert '明日が決算予定' in log.title

    # 再実行しても重複しない（(user, earnings_schedule) 一意制約 + ignore_conflicts）
    again = sync.fan_out_earnings_reminders()
    assert again == 0
    assert NotificationLog.objects.filter(user=user).count() == 1


def test_reminder_survives_schedule_wash_replace():
    """日次同期が未来の決算予定を洗い替えしても、送信済み通知は消えない。

    なぜこのテストがあるか:
      EarningsSchedule は毎日「当日以降」を delete→再投入で洗い替えする。通知の
      FK が CASCADE だと、決算前日に出した通知が翌日の同期で巻き添え削除され、
      ちょうど決算当日に履歴が消える。SET_NULL でそれを防ぐことを固定する。
    """
    user = User.objects.create_user('u_wash', 'uw@e.com', 'p')
    StockDiary.objects.create(user=user, stock_name='A', stock_symbol='7203')
    tomorrow = timezone.localdate() + timedelta(days=1)
    schedule = EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=tomorrow)

    sync.fan_out_earnings_reminders()
    assert NotificationLog.objects.filter(user=user).count() == 1

    # 洗い替え（未来分の削除）
    schedule.delete()

    log = NotificationLog.objects.get(user=user)  # 残っている
    assert log.earnings_schedule is None           # FK は NULL に


def test_fan_out_excludes_excluded_diary():
    """除外フラグの日記しか持たないユーザーには通知しない。"""
    user = User.objects.create_user('u4', 'u4@e.com', 'p')
    StockDiary.objects.create(
        user=user, stock_name='A', stock_symbol='7203', is_excluded=True)
    tomorrow = timezone.localdate() + timedelta(days=1)
    EarningsSchedule.objects.create(securities_code='7203', earnings_date=tomorrow)

    assert sync.fan_out_earnings_reminders() == 0


# ---------------------------------------------------------------------------
# カレンダー画面（GETのみ・APIを叩かない・月グリッド＋選択日・scope切替）
# ---------------------------------------------------------------------------

def test_calendar_view_renders_without_calling_api(client):
    """画面表示は外部APIを一切叩かず、ローカルDB（マスタ join）だけで描画する。

    決算日は日記に持たせないので、サマリーは EarningsSchedule を作って初めて出る。
    """
    user = User.objects.create_user('v1', 'v1@e.com', 'p')
    client.force_login(user)
    today = timezone.localdate()

    holding = StockDiary.objects.create(
        user=user, stock_name='保有株', stock_symbol='7203',
        current_quantity=100, transaction_count=2)
    watch = StockDiary.objects.create(
        user=user, stock_name='監視株', stock_symbol='6758')
    EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=today + timedelta(days=5),
        company_name='保有株', earnings_type='本決算')
    EarningsSchedule.objects.create(
        securities_code='6758', earnings_date=today + timedelta(days=9),
        company_name='監視株')

    url = reverse('stockdiary:earnings_calendar')
    with patch.object(EarningsCalendarAPIService, 'fetch_window') as mocked:
        resp = client.get(url)
        assert mocked.call_count == 0  # 表示でAPIは呼ばれない

    assert resp.status_code == 200
    # サマリー（保有・ウォッチ）は選択日に依らず常時表示
    assert holding.stock_name.encode() in resp.content
    assert watch.stock_name.encode() in resp.content


def test_calendar_summary_has_three_tabs_matching_diary_status(client):
    """サマリーは 保有/売却済/メモ の3タブ（StockDiaryのis_holding/is_sold_out/is_memoと同じ基準）。

    以前は「保有/ウォッチ」の2タブで、ウォッチが実質メモ日記のことなのに
    別の言葉を使っていて分かりにくかった（#396フォローアップ）。取引はあるが
    保有数ゼロ（売却済）の日記を独立タブとして出す。
    """
    user = User.objects.create_user('v_3tab', 'v3tab@e.com', 'p')
    client.force_login(user)
    today = timezone.localdate()

    holding = StockDiary.objects.create(
        user=user, stock_name='保有株', stock_symbol='7203',
        current_quantity=100, transaction_count=2)
    sold = StockDiary.objects.create(
        user=user, stock_name='売却株', stock_symbol='6758',
        current_quantity=0, transaction_count=2)
    memo = StockDiary.objects.create(
        user=user, stock_name='メモ株', stock_symbol='9984',
        current_quantity=0, transaction_count=0)
    EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=today + timedelta(days=5),
        company_name='保有株')
    EarningsSchedule.objects.create(
        securities_code='6758', earnings_date=today + timedelta(days=6),
        company_name='売却株')
    EarningsSchedule.objects.create(
        securities_code='9984', earnings_date=today + timedelta(days=7),
        company_name='メモ株')

    resp = client.get(reverse('stockdiary:earnings_calendar'))
    html = resp.content.decode()

    assert '売却済' in html
    assert 'メモ' in html
    assert holding.stock_name in html
    assert sold.stock_name in html
    assert memo.stock_name in html
    assert 'id="ec-tab-sold"' in html


def test_calendar_selected_day_lists_that_days_earnings(client):
    """?date= で指定した日の決算が選択日パネルに一覧される。"""
    user = User.objects.create_user('v_day', 'vd@e.com', 'p')
    client.force_login(user)
    target = timezone.localdate() + timedelta(days=5)
    EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=target, company_name='トヨタ自動車')

    url = reverse('stockdiary:earnings_calendar')
    resp = client.get(url, {
        'scope': 'all',
        'month': target.strftime('%Y-%m'),
        'date': target.isoformat(),
    })
    assert resp.status_code == 200
    assert 'トヨタ自動車'.encode() in resp.content


def test_calendar_shows_recent_past_earnings(client):
    """直近30日以内の過去の決算日も選択日パネルに表示できる。

    以前は window_start=today に固定されており、当日より前の日付は
    month/date パラメータで指定してもウィンドウ外としてクランプされ、
    EarningsSchedule に履歴が残っていても確認できなかった（#決算カレンダー
    過去参照要望）。過去分は sync 時に削除されず履歴として保持される設計
    なので、表示側の制限だけを緩めれば見えるようになる。
    """
    user = User.objects.create_user('v_past', 'vpast@e.com', 'p')
    client.force_login(user)
    target = timezone.localdate() - timedelta(days=10)
    EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=target, company_name='トヨタ自動車')

    url = reverse('stockdiary:earnings_calendar')
    resp = client.get(url, {
        'scope': 'all',
        'month': target.strftime('%Y-%m'),
        'date': target.isoformat(),
    })
    assert resp.status_code == 200
    assert 'トヨタ自動車'.encode() in resp.content


def test_calendar_scope_mine_filters_to_recorded_symbols(client):
    """scope=mine は記録銘柄のみ、scope=all は全銘柄を選択日パネルに出す。"""
    user = User.objects.create_user('v2', 'v2@e.com', 'p')
    client.force_login(user)
    target = timezone.localdate() + timedelta(days=5)
    StockDiary.objects.create(user=user, stock_name='保有株', stock_symbol='7203')

    EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=target, company_name='記録銘柄')
    EarningsSchedule.objects.create(
        securities_code='9999', earnings_date=target, company_name='無関係銘柄')

    url = reverse('stockdiary:earnings_calendar')
    params = {'month': target.strftime('%Y-%m'), 'date': target.isoformat()}

    mine = client.get(url, dict(params, scope='mine'), HTTP_HX_REQUEST='true')
    assert '記録銘柄'.encode() in mine.content
    assert '無関係銘柄'.encode() not in mine.content

    everything = client.get(url, dict(params, scope='all'), HTTP_HX_REQUEST='true')
    assert '無関係銘柄'.encode() in everything.content


def test_calendar_day_panel_only_swap_via_htmx(client):
    """panel=day のHTMXリクエストは選択日パネルのみ返す（グリッドを含まない）。"""
    user = User.objects.create_user('v_panel', 'vp@e.com', 'p')
    client.force_login(user)
    target = timezone.localdate() + timedelta(days=5)
    EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=target, company_name='トヨタ自動車')

    url = reverse('stockdiary:earnings_calendar')
    resp = client.get(url, {
        'scope': 'all', 'panel': 'day',
        'month': target.strftime('%Y-%m'), 'date': target.isoformat(),
    }, HTTP_HX_REQUEST='true')

    assert resp.status_code == 200
    assert b'ec-day-panel' in resp.content
    assert 'トヨタ自動車'.encode() in resp.content
    # 月ナビ・グリッドは含まれない（パネルだけ差し替え）
    assert b'ec-month-nav' not in resp.content
    assert b'ec-grid' not in resp.content


def test_calendar_view_requires_login(client):
    """未ログインはログインへリダイレクトする。"""
    resp = client.get(reverse('stockdiary:earnings_calendar'))
    assert resp.status_code in (301, 302)


def test_calendar_htmx_nav_refreshes_summary_oob(client):
    """月送り/日付タップ(HTMX)のたびに、次回決算サマリーもOOBで最新化される。

    バグ再現(#396「決算カレンダーのリストとカレンダーの予定が異なる」の追報):
    保有/ウォッチサマリーは初回のフルページ描画にしか含まれておらず、HTMXでの
    月送り・日付タップ（#ec-calendar のみ差し替え）では再送されなかった。その
    ため、初回表示後にサマリーの元データ（is_estimated 等）が変わっても、
    ユーザーがカレンダーを操作するとサマリー欄だけ古いまま（例:カレンダー本体は
    「確定」表示なのにサマリーはまだ「予想」表示）に見えていた。ビューは
    holdings/watchlist を毎リクエスト計算しているため、HTMX応答に
    hx-swap-oob="true" の #ec-summary を含めて必ず最新化するようにした。
    """
    user = User.objects.create_user('v_oob', 'voob@e.com', 'p')
    client.force_login(user)
    StockDiary.objects.create(
        user=user, stock_name='トヨタ自動車', stock_symbol='7203',
        current_quantity=100)
    target = timezone.localdate() + timedelta(days=10)
    EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=target,
        company_name='トヨタ自動車', is_estimated=False)

    url = reverse('stockdiary:earnings_calendar')

    full = client.get(url)
    full_html = full.content.decode()
    assert full_html.count('id="ec-summary"') == 1
    assert 'hx-swap-oob' not in full_html  # フルページでは二重描画させない

    htmx = client.get(url, {'scope': 'mine'}, HTTP_HX_REQUEST='true')
    htmx_html = htmx.content.decode()
    assert htmx_html.count('id="ec-summary"') == 1
    assert 'hx-swap-oob="true"' in htmx_html
    assert 'トヨタ自動車' in htmx_html


def test_thesis_next_earnings_uses_actual_date():
    """Thesis「次の決算まで」の検証予定日が、実際の次回決算日になる。"""
    from stockdiary.views_growth import _default_review_due_date

    user = User.objects.create_user('t_ne', 'tne@e.com', 'p')
    diary = StockDiary.objects.create(
        user=user, stock_name='A', stock_symbol='7203',
        first_purchase_date=date(2026, 1, 1))
    earnings = timezone.localdate() + timedelta(days=20)
    EarningsSchedule.objects.create(securities_code='7203', earnings_date=earnings)

    assert _default_review_due_date(diary, 'next_earnings') == earnings


def test_thesis_next_earnings_falls_back_without_schedule():
    """決算予定が無ければ従来の概算45日（基準=初回購入日）にフォールバック。"""
    from stockdiary.views_growth import _default_review_due_date

    user = User.objects.create_user('t_nf', 'tnf@e.com', 'p')
    purchased = timezone.localdate() - timedelta(days=10)
    diary = StockDiary.objects.create(
        user=user, stock_name='A', stock_symbol='7203',
        first_purchase_date=purchased)
    assert _default_review_due_date(diary, 'next_earnings') == purchased + timedelta(days=45)


def test_thesis_due_date_never_in_the_past():
    """検証予定日は必ず未来日になる（過去日への再アンカー）。

    なぜこのテストがあるか:
      _default_review_due_date は初回購入日を起点に horizon の日数を足していた
      ため、昔から保有している銘柄に後から仮説を立てると検証予定日が過去日になり、
      「仮説を作った瞬間から N日超過」の状態でホーム想起に出るバグがあった。
      期日が最初から嘘だと「検証期日＝答え合わせ」の能動トリガー（継続の核）が
      信頼されなくなるため、期日は今日起点に再アンカーする不変条件を固定する。
    """
    from stockdiary.views_growth import _default_review_due_date

    user = User.objects.create_user('t_past', 'tpast@e.com', 'p')
    diary = StockDiary.objects.create(
        user=user, stock_name='A', stock_symbol='7203',
        first_purchase_date=timezone.localdate() - timedelta(days=730))

    for horizon, days in [('next_earnings', 45), ('3m', 90), ('6m', 180), ('1y', 365), ('long', 365)]:
        due = _default_review_due_date(diary, horizon)
        assert due == timezone.localdate() + timedelta(days=days), horizon
        assert due > timezone.localdate(), horizon


def test_thesis_due_date_keeps_purchase_anchor_when_future():
    """購入日起点の期日が未来ならそのまま採用する（再アンカーしない）。"""
    from stockdiary.views_growth import _default_review_due_date

    user = User.objects.create_user('t_anchor', 'tanchor@e.com', 'p')
    purchased = timezone.localdate() - timedelta(days=30)
    diary = StockDiary.objects.create(
        user=user, stock_name='A', stock_symbol='7203',
        first_purchase_date=purchased)
    assert _default_review_due_date(diary, '3m') == purchased + timedelta(days=90)


# ---------------------------------------------------------------------------
# 日記一覧: 決算が近い順ソート・決算◯日以内フィルタ
# ---------------------------------------------------------------------------

def _make_diary_with_earnings(user, code, days_ahead):
    from stockdiary.models import StockDiary as SD
    d = SD.objects.create(user=user, stock_name=code, stock_symbol=code)
    if days_ahead is not None:
        EarningsSchedule.objects.create(
            securities_code=code, earnings_date=timezone.localdate() + timedelta(days=days_ahead))
    return d


def test_list_earnings_filter_within_days():
    """earnings=14 は次回決算が14日以内の銘柄だけに絞る（予定なしは除外）。"""
    from stockdiary.utils import apply_diary_filters
    from stockdiary.models import StockDiary as SD

    user = User.objects.create_user('l_f', 'lf@e.com', 'p')
    _make_diary_with_earnings(user, '7203', 5)    # in
    _make_diary_with_earnings(user, '6758', 20)   # out(>14)
    _make_diary_with_earnings(user, '9984', None)  # 予定なし → 除外

    qs = apply_diary_filters(SD.objects.filter(user=user), {'earnings': '14'}, user)
    codes = set(qs.values_list('stock_symbol', flat=True))
    assert codes == {'7203'}


def test_home_view_renders_with_earnings_sort_and_filter(client):
    """ホーム一覧が sort=earnings_asc / earnings=30 でも描画できる（Subquery経路）。"""
    user = User.objects.create_user('l_v', 'lv@e.com', 'p')
    client.force_login(user)
    _make_diary_with_earnings(user, '7203', 5)
    _make_diary_with_earnings(user, '6758', 40)

    resp = client.get(reverse('stockdiary:home'),
                      {'sort': 'earnings_asc', 'earnings': '30'})
    assert resp.status_code == 200
    # 30日以内(7203)は残り、40日先(6758)は絞り込みで除外
    assert b'7203' in resp.content
    assert b'6758' not in resp.content


def test_list_earnings_sort_nearest_first():
    """sort=earnings_asc は決算が近い順（予定なしは末尾）。"""
    from stockdiary.utils import apply_diary_filters
    from stockdiary.models import StockDiary as SD

    user = User.objects.create_user('l_s', 'ls@e.com', 'p')
    _make_diary_with_earnings(user, '6758', 40)
    _make_diary_with_earnings(user, '7203', 5)
    _make_diary_with_earnings(user, '9984', None)  # 予定なし

    qs = apply_diary_filters(SD.objects.filter(user=user), {'sort': 'earnings_asc'}, user)
    codes = list(qs.values_list('stock_symbol', flat=True))
    assert codes[0] == '7203' and codes[1] == '6758'
    assert codes[-1] == '9984'  # 予定なしは末尾


def test_detail_header_shows_next_earnings(client):
    """日記詳細ヘッダー（銘柄コード横）に次回決算予定日を表示する。"""
    user = User.objects.create_user('v_detail', 'vdt@e.com', 'p')
    client.force_login(user)
    diary = StockDiary.objects.create(
        user=user, stock_name='トヨタ自動車', stock_symbol='7203')
    EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=timezone.localdate() + timedelta(days=5),
        earnings_type='第1四半期')

    resp = client.get(reverse('stockdiary:detail', args=[diary.id]))
    assert resp.status_code == 200
    assert '次回決算'.encode() in resp.content
    assert 'あと5日'.encode() in resp.content


def test_detail_header_hides_next_earnings_when_none(client):
    """決算予定が無い銘柄では次回決算表示を出さない。"""
    user = User.objects.create_user('v_detail2', 'vdt2@e.com', 'p')
    client.force_login(user)
    diary = StockDiary.objects.create(
        user=user, stock_name='メモ株', stock_symbol='7203')

    resp = client.get(reverse('stockdiary:detail', args=[diary.id]))
    assert resp.status_code == 200
    assert '次回決算'.encode() not in resp.content


# ---------------------------------------------------------------------------
# ホームの想起（確定決算ではなく「決算予定日が近い」を出す）
# ---------------------------------------------------------------------------

def test_recall_surfaces_upcoming_earnings():
    """次回決算が14日以内なら想起キューに kind='earnings' が載る。"""
    from stockdiary.services.recall_service import RecallService

    today = timezone.localdate()
    user = User.objects.create_user('r_up', 'rup@e.com', 'p')
    diary = StockDiary.objects.create(
        user=user, stock_name='トヨタ自動車', stock_symbol='7203')
    EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=today + timedelta(days=5),
        earnings_type='第1四半期')

    # today を明示（RecallService は timezone.now().date() を使うため、
    # UTC/JST の日付境界でテストがぶれないように固定する）
    recall = RecallService.build(user, today=today)
    kinds = [c['kind'] for c in recall['queue']]
    assert 'earnings' in kinds
    assert 'disclosure' not in kinds  # 確定決算カードは載せない
    card = next(c for c in recall['queue'] if c['kind'] == 'earnings')
    assert card['diary_id'] == diary.id
    assert 'あと5日' in card['meta']
    assert recall['upcoming_earnings']


def test_recall_ignores_far_earnings():
    """次回決算が14日より先なら想起には出さない（近づいたら出る）。"""
    from stockdiary.services.recall_service import RecallService

    today = timezone.localdate()
    user = User.objects.create_user('r_far', 'rfar@e.com', 'p')
    StockDiary.objects.create(user=user, stock_name='A', stock_symbol='7203')
    EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=today + timedelta(days=40))

    recall = RecallService.build(user, today=today)
    assert recall['upcoming_earnings'] == []
    assert 'earnings' not in [c['kind'] for c in recall['queue']]


# ---------------------------------------------------------------------------
# 管理コマンド（手動実行・日付指定リカバリ）
# ---------------------------------------------------------------------------

def test_command_rejects_invalid_base_date(settings):
    """不正な日付指定はエラーメッセージを出して中断する（例外で落とさない）。"""
    from io import StringIO
    from django.core.management import call_command

    out = StringIO()
    call_command('sync_earnings_calendar', base_date='2026/01/01', stdout=out)
    assert '日付形式が不正' in out.getvalue()


def test_command_target_date_recovers_missed_reminder(settings):
    """--target-date で過去に送り逃した決算前日通知を後から送れる。"""
    from io import StringIO
    from django.core.management import call_command

    # APIキー未設定 → 保存はスキップされるが、通知ファンアウトは走る
    settings.EARNINGS_CALENDAR_API_SETTINGS = {'API_KEY': ''}
    user = User.objects.create_user('u_cmd', 'uc@e.com', 'p')
    StockDiary.objects.create(user=user, stock_name='A', stock_symbol='7203')
    earnings_day = timezone.localdate() + timedelta(days=7)
    schedule = EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=earnings_day)

    call_command('sync_earnings_calendar',
                 target_date=earnings_day.isoformat(), stdout=StringIO())

    log = NotificationLog.objects.get(user=user)
    assert log.earnings_schedule_id == schedule.id


def test_admin_run_sync_view_executes(settings, client):
    """管理画面の手動実行ビューが同期コマンドを走らせて決算予定を保存する。"""
    settings.EARNINGS_CALENDAR_API_SETTINGS = {'API_KEY': 'k'}
    today = timezone.localdate()
    admin_user = User.objects.create_superuser('boss', 'boss@e.com', 'p')
    client.force_login(admin_user)

    new_items = [{
        'securities_code': '7203', 'company_name': 'トヨタ',
        'earnings_date': today + timedelta(days=20),
        'earnings_type': '本決算', 'market_segment': 'プライム',
        'source_updated_at': '',
    }]
    url = reverse('admin:earnings_analysis_earningsschedule_run_sync')

    # フォーム表示（GET）
    assert client.get(url).status_code == 200

    # 実行（POST）→ リダイレクトし、決算予定が保存される
    with patch.object(EarningsCalendarAPIService, 'fetch_window', return_value=new_items):
        resp = client.post(url, {'days': '90', 'skip_notifications': '1'})
    assert resp.status_code == 302
    assert EarningsSchedule.objects.filter(securities_code='7203').exists()
