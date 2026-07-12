"""ダッシュボードの合計「実現損益」が、日記ごとのFIFO計算値の単純合算になっていることの回帰テスト。

なぜこのテストがあるか:
以前の実装は「総売却額 − (総投資額 − 評価額)」という逆算式で合計の実現損益を
出していた（views_dashboard.py の total_realized_profit）。この式は本来、
各日記の FIFO/移動平均の実現損益と数学的に一致するはずだが、集計の前提
（総売却額と簿価計算の整合性）が崩れると大きくズレる（例: 証券CSVの同一
受渡日内の行順序が実際の約定順と異なるケース）。逆算をやめ、各日記が保持
する realized_profit をそのまま合算する方式に変更した。

総売却額（キャッシュ済みフィールド）が何らかの理由で実現損益と整合しない
状態になっても、ダッシュボードの合計実現損益は各日記の realized_profit の
合算のままであること（＝逆算式に引きずられないこと）を固定する。
"""
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from stockdiary.models import StockDiary, Transaction


@pytest.mark.django_db(transaction=True)
class TestDashboardRealizedProfitIsSummedNotDerived:
    def test_total_realized_profit_ignores_inconsistent_sell_amount(self, authenticated_client, user):
        diary = StockDiary.objects.create(
            user=user,
            stock_symbol='7203',
            stock_name='トヨタ自動車',
            reason='テスト用',
            currency='JPY',
        )
        Transaction.objects.create(
            diary=diary, transaction_type='buy',
            transaction_date=date(2024, 1, 10),
            price=Decimal('1000'), quantity=Decimal('100'), is_margin=False,
        )
        Transaction.objects.create(
            diary=diary, transaction_type='sell',
            transaction_date=date(2024, 2, 10),
            price=Decimal('1200'), quantity=Decimal('40'), is_margin=False,
        )
        diary.refresh_from_db()
        assert diary.cash_only_realized_profit == Decimal('8000.00')

        # 総売却額だけを不整合な値に書き換える（save() を経由せずキャッシュ済み
        # フィールドを直接壊す＝実運用で何らかの理由で整合性が崩れた状態を模す）。
        # 逆算式のままなら、合計の実現損益がこの不整合に引きずられてしまう。
        StockDiary.objects.filter(pk=diary.pk).update(
            cash_only_total_sell_amount=Decimal('200000.00')
        )

        resp = authenticated_client.get(reverse('stockdiary:dashboard'), {'period': 'all'})
        assert resp.status_code == 200
        # 逆算式なら 200,000 - (100,000 - 60,000) = 160,000 になってしまうところ、
        # 日記の realized_profit をそのまま合算した 8,000 になっていることを確認する。
        assert resp.context['total_realized_profit'] == pytest.approx(8000.0)
