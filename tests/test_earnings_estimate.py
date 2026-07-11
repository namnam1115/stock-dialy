"""決算発表予想日の自前算出のテスト。

なぜこのテストがあるか:
  決算カレンダーで「クリレスHD(3387)のメモしか自身の記録として出ない／日記に
  関連付かない」不具合があった。原因は、提供元API(edinetdb.jp /v1/calendar)が
  予想日(estimatedAnnouncementDate)を返さなくなり、確定した直近2週間分しか返さ
  なくなったこと。洗い替え同期がこの確定分だけでマスタを置き換えるため、直近に
  確定発表がある銘柄以外は決算予定マスタから外れ、日記に join できなくなった。

  対策として、各社の会計年度末(fiscalYearEnd)と決算種別から「四半期末＋約43日」で
  発表予想日を自前算出し、確定分と合わせてマスタを埋める。ここでは算出ロジックと、
  同期後に確定日を持たない保有銘柄が予想日で再び関連付くことを固定する。
"""
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from stockdiary.models import StockDiary
from earnings_analysis.models import EarningsSchedule
from earnings_analysis.services.earnings_calendar_api import (
    EarningsCalendarAPIService,
)
from earnings_analysis.services import earnings_calendar_sync as sync
from earnings_analysis.services.earnings_calendar_estimate import (
    build_roster,
    build_estimates,
    _minus_months,
    ANNOUNCE_OFFSET_DAYS,
)
from stockdiary.services.earnings_lookup import get_next_earnings_map

User = get_user_model()


# ---------------------------------------------------------------------------
# 四半期末の算出（会計年度末からの遡り）
# ---------------------------------------------------------------------------

def test_minus_months_returns_quarter_end():
    """会計年度末3/31から各四半期末を月末で算出する（9/6/3ヶ月前・当月）。"""
    fye = date(2027, 3, 31)
    assert _minus_months(fye, 0) == date(2027, 3, 31)   # 本決算
    assert _minus_months(fye, 3) == date(2026, 12, 31)  # 第3四半期末
    assert _minus_months(fye, 6) == date(2026, 9, 30)   # 第2四半期末
    assert _minus_months(fye, 9) == date(2026, 6, 30)   # 第1四半期末


# ---------------------------------------------------------------------------
# 予想日の算出（名簿→予想）
# ---------------------------------------------------------------------------

def test_build_estimates_predicts_next_quarter_from_fye():
    """会計年度末3/31・基準7/5なら、次の第1四半期(6/30末+43日)を予想する。"""
    roster = {'7203': {'fye': date(2026, 3, 31),
                       'company_name': 'トヨタ', 'market_segment': 'プライム'}}
    base = date(2026, 7, 5)
    ests = build_estimates(roster, {}, base, horizon_days=90)

    # 第1四半期末 2026-06-30 + 43日 = 2026-08-12
    q1 = [e for e in ests if e['earnings_type'] == '第１四半期']
    assert len(q1) == 1
    assert q1[0]['earnings_date'] == date(2026, 6, 30) + timedelta(days=ANNOUNCE_OFFSET_DAYS)
    assert q1[0]['is_estimated'] is True
    assert q1[0]['securities_code'] == '7203'


def test_build_estimates_skips_dates_outside_horizon():
    """基準日より前・horizon より先の予想は出さない。"""
    roster = {'0001': {'fye': date(2026, 3, 31),
                       'company_name': 'A', 'market_segment': ''}}
    base = date(2026, 7, 5)
    ests = build_estimates(roster, {}, base, horizon_days=30)  # 〜8/4 まで
    # 第1四半期予想(8/12)は horizon(30日=8/4)外 → 出ない
    assert ests == []


def test_build_estimates_suppresses_near_confirmed():
    """確定日の近傍(±30日)は予想を出さない（同一四半期の重複を防ぐ）。"""
    roster = {'3387': {'fye': date(2026, 5, 31),
                       'company_name': 'クリレス', 'market_segment': ''}}
    base = date(2026, 7, 5)
    # 第1四半期末 2026-08-31 + 43 ≒ 10月。本決算末5/31... ここでは確定日を
    # 予想の近くに置き、その予想が抑止されることを確認する。
    ests_no_conf = build_estimates(roster, {}, base, horizon_days=180)
    assert ests_no_conf, '前提: 確定なしなら予想が出る'
    target = ests_no_conf[0]['earnings_date']
    confirmed_by_code = {'3387': [target + timedelta(days=5)]}
    ests = build_estimates(roster, confirmed_by_code, base, horizon_days=180)
    assert all(e['earnings_date'] != target for e in ests)


def test_build_estimates_never_precedes_known_confirmed():
    """予想日が既知の確定日より前になる場合は出さない（確定が正）。

    バグ再現(#396「決算カレンダーのリストとカレンダーの予定が異なる」):
    確定発表が計算式(四半期末+43日)の予想日より30日超あとにずれると、
    近傍抑止(±30日)をすり抜けて「確定より前の予想日」が別レコードとして
    生き残ってしまう。get_next_earnings_map は日付が最も早いレコードを
    「次回決算」として採用するため、保有/ウォッチ一覧はこの誤った予想日を
    表示する一方、決算カレンダー本体は同じ銘柄の確定日を別の日に表示し、
    両者が食い違って見えていた。
    """
    roster = {'7203': {'fye': date(2026, 3, 31),
                       'company_name': 'トヨタ', 'market_segment': 'プライム'}}
    base = date(2026, 7, 5)
    # 第1四半期末 2026-06-30 + 43日 = 2026-08-12（予想）
    # 確定発表を予想より40日後に置く（近傍抑止の±30日を超える）
    confirmed_by_code = {'7203': [date(2026, 6, 30) + timedelta(days=ANNOUNCE_OFFSET_DAYS + 40)]}
    ests = build_estimates(roster, confirmed_by_code, base, horizon_days=180)
    q1 = [e for e in ests if e['earnings_type'] == '第１四半期']
    assert q1 == []


def test_build_roster_computes_per_company_offset_from_own_history():
    """会社ごとの実発表日から「四半期末→発表日」の日数(中央値)を逆算する。

    なぜこのテストがあるか:
      全社一律「四半期末+43日」で予想すると、fiscalYearEnd が同じ会社（3月決算の
      大半）が同一日に団子状態で集中してしまい、かつ開示が速い会社（例: イオンは
      実績40日）の予想日が実際より数日遅れて出る不具合があった。会社自身の過去の
      実発表日（history）から個社の開示ペースを逆算し、標本が十分あればそちらを
      優先するよう修正した。
    """
    history = [
        # イオン(8267): 2月決算、実績は四半期末から一貫して40日で開示
        {'securities_code': '8267', 'fiscal_year_end': '2026-02-28',
         'company_name': 'イオン', 'market_segment': 'プライム',
         'earnings_date': date(2025, 7, 10), 'earnings_type': '第１四半期'},
        {'securities_code': '8267', 'fiscal_year_end': '2026-02-28',
         'company_name': 'イオン', 'market_segment': 'プライム',
         'earnings_date': date(2025, 10, 9), 'earnings_type': '第２四半期'},
    ]
    roster = build_roster(history)
    # 第1四半期末(2025-05-31)+40日=2025-07-10 / 第2四半期末(2025-08-31)+40日=2025-10-10（1日近似）
    assert roster['8267']['offset_days'] in (39, 40)


def test_build_roster_uses_single_sample_offset():
    """自社実績が1件でも、個社オフセットを優先して使う。

    提供元の履歴APIは直近1件（多くは本決算）しか返さない会社が大半（実測:
    全社の約9割が1件のみ）。2件以上を要求すると大半の会社が個社実績を使えず、
    fiscalYearEndが同じ会社（3月決算の大半）が全社共通43日で同一日に団子状態に
    集中してしまう。1件でも自社実績の方が無関係な他社の中央値より信頼できる。
    """
    history = [
        {'securities_code': '7203', 'fiscal_year_end': '2026-03-31',
         'company_name': 'トヨタ', 'market_segment': 'プライム',
         'earnings_date': date(2025, 8, 5), 'earnings_type': '第１四半期'},
    ]
    roster = build_roster(history)
    # 第1四半期末(2025-06-30)からの実績日数 = 36日（全社共通43日ではない）
    assert roster['7203']['offset_days'] == 36


def test_build_roster_falls_back_to_default_offset_without_earnings_date():
    """実発表日(earnings_date)を持たない履歴しか無い会社は、全社共通の43日を使う。"""
    history = [
        {'securities_code': '7203', 'fiscal_year_end': '2026-03-31',
         'company_name': 'トヨタ', 'market_segment': 'プライム'},
    ]
    roster = build_roster(history)
    assert roster['7203']['offset_days'] == ANNOUNCE_OFFSET_DAYS


def test_build_estimates_uses_personalized_offset():
    """個社オフセットが求まっていれば、予想日はそのオフセットで算出される。

    イオン(8267)は実績40日開示のため、全社共通43日より3日早い予想日になる。
    """
    roster = {'8267': {'fye': date(2026, 2, 28), 'company_name': 'イオン',
                       'market_segment': 'プライム', 'offset_days': 40}}
    base = date(2026, 7, 5)
    ests = build_estimates(roster, {}, base, horizon_days=90)
    q1 = [e for e in ests if e['earnings_type'] == '第１四半期']
    assert len(q1) == 1
    # 第1四半期末 2026-05-31 + 40日 = 2026-07-10
    assert q1[0]['earnings_date'] == date(2026, 5, 31) + timedelta(days=40)


def test_build_roster_picks_latest_fiscal_year_end():
    """同一コードの履歴からは最も新しい会計年度末を採用する。"""
    history = [
        {'securities_code': '0001', 'fiscal_year_end': '2025-03-31',
         'company_name': '旧', 'market_segment': 'スタンダード'},
        {'securities_code': '0001', 'fiscal_year_end': '2026-03-31',
         'company_name': '新', 'market_segment': 'プライム'},
        {'securities_code': '0002', 'fiscal_year_end': '',  # FYE欠損は除外
         'company_name': 'X', 'market_segment': ''},
    ]
    roster = build_roster(history)
    assert set(roster) == {'0001'}
    assert roster['0001']['fye'] == date(2026, 3, 31)
    assert roster['0001']['company_name'] == '新'


# ---------------------------------------------------------------------------
# 同期への統合（バグの回帰: 確定日のない保有銘柄が予想で再び関連付く）
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_sync_estimates_reassociate_diary_without_confirmed_date(settings):
    """確定発表日を持たない保有銘柄でも、予想日でマスタに載り日記へ関連付く。

    バグ再現: 確定分(fetch_window)には 3387 だけ・7203 は無い状況。予想分の
    自前算出により 7203 にも決算予定が付き、get_next_earnings_map が引ける。
    """
    settings.EARNINGS_CALENDAR_API_SETTINGS = {'API_KEY': 'k'}
    base = date(2026, 7, 5)

    user = User.objects.create_user(
        username='u', email='u@example.com', password='x')
    StockDiary.objects.create(
        user=user, stock_symbol='7203', stock_name='トヨタ', current_quantity=100)

    # 確定分: 3387 のみ（直近確定）。7203 は返らない。
    confirmed = [{
        'securities_code': '3387', 'company_name': 'クリレス',
        'earnings_date': base + timedelta(days=9),
        'earnings_type': '第１四半期', 'market_segment': 'プライム',
        'source_updated_at': '', 'is_estimated': False,
    }]
    # 履歴: 7203 と 3387 の会計年度末（予想算出の名簿）
    history = [
        {'securities_code': '7203', 'fiscal_year_end': '2026-03-31',
         'company_name': 'トヨタ', 'market_segment': 'プライム',
         'earnings_date': date(2026, 5, 8), 'earnings_type': '本決算'},
        {'securities_code': '3387', 'fiscal_year_end': '2026-05-31',
         'company_name': 'クリレス', 'market_segment': 'プライム',
         'earnings_date': date(2026, 4, 10), 'earnings_type': '第３四半期'},
    ]

    with patch.object(EarningsCalendarAPIService, 'fetch_window',
                      return_value=confirmed), \
         patch.object(EarningsCalendarAPIService, 'fetch_history',
                      return_value=history):
        saved = sync.sync_earnings_calendar(days=90, base_date=base)

    assert saved >= 2  # 確定(3387) + 予想(7203 の第1四半期)

    # 7203 は確定日が無くても予想日で載っている
    assert EarningsSchedule.objects.filter(
        securities_code='7203', is_estimated=True).exists()

    # 日記(7203)が決算予定に join できる（関連付け復活）
    mapping = get_next_earnings_map(['7203'], today=base)
    assert '7203' in mapping
    assert mapping['7203'].is_estimated is True
    # 7203 は本決算の実績(2026-05-08 = 期末2026-03-31+38日)から個社オフセット38日を
    # 逆算し、それを使って第1四半期末 6/30 + 38日 = 8/7 と予想する（全社共通43日ではない）
    assert mapping['7203'].date == date(2026, 6, 30) + timedelta(days=38)


@pytest.mark.django_db
def test_sync_confirmed_overrides_estimate_same_code(settings):
    """同一銘柄で確定日があれば、その四半期の予想は出さず確定を正とする。"""
    settings.EARNINGS_CALENDAR_API_SETTINGS = {'API_KEY': 'k'}
    base = date(2026, 7, 5)

    # 7203 の第1四半期を確定日として与える（予想 8/12 の近傍）
    confirmed = [{
        'securities_code': '7203', 'company_name': 'トヨタ',
        'earnings_date': date(2026, 8, 6),
        'earnings_type': '第１四半期', 'market_segment': 'プライム',
        'source_updated_at': '', 'is_estimated': False,
    }]
    history = [{
        'securities_code': '7203', 'fiscal_year_end': '2026-03-31',
        'company_name': 'トヨタ', 'market_segment': 'プライム',
        'earnings_date': date(2026, 5, 8), 'earnings_type': '本決算',
    }]

    with patch.object(EarningsCalendarAPIService, 'fetch_window',
                      return_value=confirmed), \
         patch.object(EarningsCalendarAPIService, 'fetch_history',
                      return_value=history):
        sync.sync_earnings_calendar(days=90, base_date=base)

    rows = EarningsSchedule.objects.filter(
        securities_code='7203', earnings_date__gte=base)
    # 8/6 の確定のみ。近傍(±30日)の予想 8/12 は抑止される。
    assert rows.count() == 1
    row = rows.first()
    assert row.earnings_date == date(2026, 8, 6)
    assert row.is_estimated is False
