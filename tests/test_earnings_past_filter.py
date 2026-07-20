"""「決算後（最近決算があった）」フィルタ・ソートの回帰テスト。

なぜこのテストを足したか:
振り返りは決算直後が最多だが、従来の導線は「決算が近い（前）」しか無く、
「直近で決算だった銘柄」を探せなかった。EarningsSchedule は基準日より前の
決算日を履歴として保持する（earnings_calendar_sync）ため、過去決算を相関参照して
- sort=earnings_desc（最近決算があった順）
- earnings_past=7/14/30（決算後N日以内で絞り込み）
を apply_diary_filters に追加した。この挙動を固定する。
"""
import datetime

import pytest
from django.utils import timezone

from stockdiary.models import StockDiary
from stockdiary.utils import apply_diary_filters
from earnings_analysis.models import EarningsSchedule


def _diary(user, code, name):
    return StockDiary.objects.create(user=user, stock_symbol=code, stock_name=name)


def _earnings(code, days_ago):
    """days_ago 日前に決算があった EarningsSchedule を作る。"""
    return EarningsSchedule.objects.create(
        securities_code=code,
        earnings_date=timezone.localdate() - datetime.timedelta(days=days_ago),
        earnings_type='本決算',
    )


@pytest.mark.django_db
class TestEarningsPastFilter:
    def test_filter_narrows_to_recently_reported(self, user):
        """earnings_past=14 は直近14日に決算があった銘柄だけに絞る。"""
        recent = _diary(user, '1111', '最近決算')
        old = _diary(user, '2222', '昔決算')
        _none = _diary(user, '3333', '決算予定なし')
        _earnings('1111', days_ago=3)
        _earnings('2222', days_ago=40)

        qs = apply_diary_filters(
            StockDiary.objects.filter(user=user), {'earnings_past': '14'}, user
        )
        ids = set(qs.values_list('id', flat=True))
        assert recent.id in ids
        assert old.id not in ids
        assert _none.id not in ids

    def test_sort_orders_most_recent_earnings_first(self, user):
        """sort=earnings_desc は最近決算があった銘柄を先頭にし、決算なしは末尾。"""
        d_far = _diary(user, '1111', '20日前')
        d_near = _diary(user, '2222', '2日前')
        d_none = _diary(user, '3333', '決算なし')
        _earnings('1111', days_ago=20)
        _earnings('2222', days_ago=2)

        qs = apply_diary_filters(
            StockDiary.objects.filter(user=user), {'sort': 'earnings_desc'}, user
        )
        ordered = list(qs.values_list('id', flat=True))
        # 直近決算(2日前) → 古い決算(20日前) → 決算なし の順
        assert ordered.index(d_near.id) < ordered.index(d_far.id)
        assert ordered.index(d_far.id) < ordered.index(d_none.id)

    def test_future_earnings_not_counted_as_past(self, user):
        """未来の決算予定しか無い銘柄は「決算後」フィルタに出ない（前後の非対称を守る）。"""
        d = _diary(user, '1111', '決算これから')
        EarningsSchedule.objects.create(
            securities_code='1111',
            earnings_date=timezone.localdate() + datetime.timedelta(days=5),
            earnings_type='本決算',
        )
        qs = apply_diary_filters(
            StockDiary.objects.filter(user=user), {'earnings_past': '30'}, user
        )
        assert d.id not in set(qs.values_list('id', flat=True))


@pytest.mark.django_db
class TestEarningsPastUI:
    def test_home_exposes_sort_and_filter(self, authenticated_client, sample_diary):
        """ホームのソート/フィルタUIに新導線が出る。"""
        from django.urls import reverse
        html = authenticated_client.get(reverse('stockdiary:home')).content.decode('utf-8')
        assert 'earnings_desc' in html          # ソート選択肢
        assert 'name="earnings_past"' in html   # フィルタ select
