"""前回（直前）決算の取得のテスト（earnings_lookup.get_previous_earnings_map）。

なぜこのテストがあるか:
  「過去のスケジュールは消さず、直前がいつだったか分かるように」という要望に対応。
  同期は当日以降のみ洗い替えるため過去分は履歴として残る。その履歴から
  「当日より前で最も新しい」決算日を引き、確定(実績)/予想を区別して返す。
"""
from datetime import date

import pytest

from earnings_analysis.models import EarningsSchedule
from stockdiary.services.earnings_lookup import (
    get_previous_earnings_map,
    attach_next_earnings,
)

pytestmark = pytest.mark.django_db

TODAY = date(2026, 7, 3)


def _sched(code, d, is_estimated=True, etype='本決算'):
    return EarningsSchedule.objects.create(
        securities_code=code, company_name='テスト', earnings_date=d,
        is_estimated=is_estimated, earnings_type=etype,
    )


def test_previous_returns_most_recent_past():
    _sched('7203', date(2026, 4, 30))
    _sched('7203', date(2026, 1, 30))
    _sched('7203', date(2026, 8, 5))  # 未来は対象外

    m = get_previous_earnings_map({'7203'}, today=TODAY)
    assert '7203' in m
    assert m['7203'].date == date(2026, 4, 30)
    assert m['7203'].days_ago == (TODAY - date(2026, 4, 30)).days


def test_previous_flags_confirmed_vs_estimated():
    _sched('6857', date(2026, 5, 9), is_estimated=False)  # 実績
    _sched('8035', date(2026, 5, 10), is_estimated=True)  # 予想のまま経過

    m = get_previous_earnings_map({'6857', '8035'}, today=TODAY)
    assert m['6857'].is_estimated is False
    assert m['8035'].is_estimated is True


def test_no_past_returns_absent():
    _sched('7203', date(2026, 8, 5))  # 未来のみ
    m = get_previous_earnings_map({'7203'}, today=TODAY)
    assert '7203' not in m


def test_attach_with_previous_sets_prev_earnings():
    from django.contrib.auth import get_user_model
    from stockdiary.models import StockDiary
    User = get_user_model()
    user = User.objects.create_user(username='pe_user', password='p', email='pe@example.com')
    diary = StockDiary.objects.create(user=user, stock_name='トヨタ', stock_symbol='7203', reason='')
    _sched('7203', date(2026, 4, 30), is_estimated=False)

    attach_next_earnings([diary], today=TODAY, with_previous=True)
    assert diary.prev_earnings is not None
    assert diary.prev_earnings.date == date(2026, 4, 30)
    assert diary.prev_earnings.is_estimated is False
