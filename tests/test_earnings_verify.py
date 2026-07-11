"""決算予想日の個社API答え合わせ（earnings_calendar_verify）のテスト。

なぜこのテストがあるか:
  日次同期の母体である /v1/calendar（横断フィード）には提供元側のインデックス
  漏れがあり、実際に開示済みの銘柄が丸ごと出てこないことがあった（実例:
  イオン8267の2026-07-10開示が /v1/calendar に一切載っていなかった）。個社API
  （/companies/{edinet_code}/earnings）は正確だが1銘柄=1リクエスト（無料枠
  100件/日）を消費するため、対象（全ユーザーの記録銘柄の和集合）が決算集中期に
  数百件規模になっても、発表が近い順に固定予算まで処理して機械的に打ち切り、
  無料枠を絶対に超えないことを固定する。
"""
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from stockdiary.models import StockDiary
from earnings_analysis.models import EarningsSchedule, Company
from earnings_analysis.services.earnings_calendar_api import (
    EarningsCalendarAPIService,
)
from earnings_analysis.services.earnings_calendar_verify import (
    verify_estimates_via_company_api,
)

User = get_user_model()
pytestmark = pytest.mark.django_db


def _make_user_with_diary(username, code):
    user = User.objects.create_user(
        username=username, email=f'{username}@e.com', password='x')
    StockDiary.objects.create(
        user=user, stock_symbol=code, stock_name=code, current_quantity=1)
    return user


def test_returns_zero_when_unconfigured(settings):
    settings.EARNINGS_CALENDAR_API_SETTINGS = {'API_KEY': ''}
    assert verify_estimates_via_company_api() == 0


def test_returns_zero_with_zero_budget(settings):
    settings.EARNINGS_CALENDAR_API_SETTINGS = {'API_KEY': 'k'}
    assert verify_estimates_via_company_api(budget=0) == 0


def test_returns_zero_without_recorded_symbols(settings):
    settings.EARNINGS_CALENDAR_API_SETTINGS = {'API_KEY': 'k'}
    EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=date.today() + timedelta(days=5),
        is_estimated=True)
    assert verify_estimates_via_company_api() == 0


def test_verify_updates_estimate_to_confirmed_within_match_window(settings):
    """個社実績が予想の±30日以内なら、同じ四半期の答え合わせとして確定に昇格する。"""
    settings.EARNINGS_CALENDAR_API_SETTINGS = {'API_KEY': 'k'}
    today = date.today()
    _make_user_with_diary('u1', '8267')
    schedule = EarningsSchedule.objects.create(
        securities_code='8267', earnings_date=today + timedelta(days=2),
        earnings_type='第１四半期', is_estimated=True, company_name='イオン')

    actual_date = today  # 予想(+2日)から2日以内 → 答え合わせ対象
    with patch.object(EarningsCalendarAPIService, 'resolve_edinet_code',
                      return_value='E03061'), \
         patch.object(EarningsCalendarAPIService, 'fetch_latest_disclosure',
                      return_value={'earnings_date': actual_date, 'quarter': 1,
                                    'fiscal_year_end': '2027-02-28'}):
        updated = verify_estimates_via_company_api(budget=10, today=today)

    assert updated == 1
    schedule.refresh_from_db()
    assert schedule.is_estimated is False
    assert schedule.earnings_date == actual_date
    # edinet_code がキャッシュされる
    assert Company.objects.filter(edinet_code='E03061', securities_code='8267').exists()


def test_verify_skips_when_actual_date_far_from_estimate(settings):
    """個社実績が予想から30日超離れていたら別四半期とみなし、上書きしない。"""
    settings.EARNINGS_CALENDAR_API_SETTINGS = {'API_KEY': 'k'}
    today = date.today()
    _make_user_with_diary('u2', '7203')
    schedule = EarningsSchedule.objects.create(
        securities_code='7203', earnings_date=today + timedelta(days=40),
        earnings_type='第１四半期', is_estimated=True, company_name='トヨタ')

    far_date = today - timedelta(days=10)  # 前四半期の実績など、予想から50日離れている
    with patch.object(EarningsCalendarAPIService, 'resolve_edinet_code',
                      return_value='E02144'), \
         patch.object(EarningsCalendarAPIService, 'fetch_latest_disclosure',
                      return_value={'earnings_date': far_date, 'quarter': 4,
                                    'fiscal_year_end': '2026-03-31'}):
        updated = verify_estimates_via_company_api(budget=10, today=today)

    assert updated == 0
    schedule.refresh_from_db()
    assert schedule.is_estimated is True
    assert schedule.earnings_date == today + timedelta(days=40)


def test_verify_reuses_cached_edinet_code_without_extra_search(settings):
    """Companyマスタに edinet_code があれば、検索(resolve_edinet_code)を呼ばない。"""
    settings.EARNINGS_CALENDAR_API_SETTINGS = {'API_KEY': 'k'}
    today = date.today()
    _make_user_with_diary('u3', '8267')
    Company.objects.create(
        edinet_code='E03061', securities_code='8267', company_name='イオン')
    EarningsSchedule.objects.create(
        securities_code='8267', earnings_date=today + timedelta(days=2),
        earnings_type='第１四半期', is_estimated=True, company_name='イオン')

    with patch.object(EarningsCalendarAPIService, 'resolve_edinet_code') as resolve_mock, \
         patch.object(EarningsCalendarAPIService, 'fetch_latest_disclosure',
                      return_value={'earnings_date': today, 'quarter': 1,
                                    'fiscal_year_end': '2027-02-28'}):
        verify_estimates_via_company_api(budget=10, today=today)
        resolve_mock.assert_not_called()


def test_verify_prioritizes_nearest_dates_and_stops_at_budget(settings):
    """発表が近い順に処理し、予算に達したらそれ以降は個社APIを呼ばない。

    クラスタ（決算集中期）で対象銘柄が急増しても、無料枠を超えないことの固定。
    """
    settings.EARNINGS_CALENDAR_API_SETTINGS = {'API_KEY': 'k'}
    today = date.today()
    for i, code in enumerate(['1001', '1002', '1003']):
        _make_user_with_diary(f'u_budget_{i}', code)
        EarningsSchedule.objects.create(
            securities_code=code, earnings_date=today + timedelta(days=i + 1),
            earnings_type='第１四半期', is_estimated=True, company_name=code)

    call_count = {'resolve': 0}

    def fake_resolve(self, sec_code):
        call_count['resolve'] += 1
        return f'E{sec_code}'

    with patch.object(EarningsCalendarAPIService, 'resolve_edinet_code', fake_resolve), \
         patch.object(EarningsCalendarAPIService, 'fetch_latest_disclosure',
                      return_value=None):
        # budget=1: 最初の1件（最も発表が近い1001）の検索だけで予算を使い切る
        verify_estimates_via_company_api(budget=1, today=today)

    assert call_count['resolve'] == 1
