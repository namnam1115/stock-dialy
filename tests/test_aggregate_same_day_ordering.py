"""同一日内の複数取引の処理順序に関する回帰テスト。

なぜこのテストを足したか:
証券CSV取込（views_trade_import.py）は受渡日のみで取引をソートしており、
同一受渡日内の行順序は実際の約定順を保証しない（楽天証券の実データで、
信用の決済取引が対応する新規建て取引より先にファイルへ記載されている実例を
確認した）。AggregateService の集計（_recalculate_all / _recalculate_cash_only）
は order_by('transaction_date', 'created_at') だったため、同日内は取込の
ファイル行順（created_at）にそのまま従っていた。

同日に「売り→買い」の順でCSV行が並ぶ現物のラウンドトリップがあると、売り取引を
保有数0の状態で処理してしまい、実現損益は計上されないまま total_sell_amount
だけ無条件に加算される（＝保有数超の売却として扱われる）バグがあった。これが
ダッシュボードの実現損益表示が大きくズレる根本原因だった。

現物取引は空売りができないため、同日内は必ず「買い→売り」の順で処理すれば
FIFO/移動平均計算が破綻しない。この不変条件を固定する。
"""
from datetime import date
from decimal import Decimal

import pytest

from stockdiary.models import StockDiary, Transaction


@pytest.mark.django_db(transaction=True)
class TestSameDayTransactionOrdering:
    def test_cash_only_sell_saved_before_buy_still_nets_correctly(self, user):
        """同日内で「売りが先に save() された」ケースでも、買い→売りの順で処理される。"""
        diary = StockDiary.objects.create(
            user=user,
            stock_symbol='7011',
            stock_name='三菱重工業',
            reason='テスト用',
            currency='JPY',
        )

        # CSV取込のファイル行順を模して、売りを先に save() する。
        # 受渡日が同じなので、修正前は created_at 順（＝この保存順）のまま処理され、
        # 保有数0のときに売りを処理する（オーバーセル）ことになっていた。
        Transaction.objects.create(
            diary=diary,
            transaction_type='sell',
            transaction_date=date(2024, 1, 15),
            price=Decimal('3151.10'),
            quantity=Decimal('100'),
            is_margin=False,
        )
        Transaction.objects.create(
            diary=diary,
            transaction_type='buy',
            transaction_date=date(2024, 1, 15),
            price=Decimal('3145.00'),
            quantity=Decimal('100'),
            is_margin=False,
        )

        diary.refresh_from_db()

        # 買い→売りの順で処理されるので、保有数超の売却にはならず、
        # 総売却額と実現損益の整合性が保たれる。
        assert diary.cash_only_current_quantity == Decimal('0.00')
        assert diary.cash_only_total_buy_amount == Decimal('314500.00')
        assert diary.cash_only_total_sell_amount == Decimal('315110.00')
        assert diary.cash_only_realized_profit == Decimal('610.00')

        # _recalculate_all（全取引）側も同じ不変条件を守る。
        assert diary.current_quantity == Decimal('0.00')
        assert diary.realized_profit == Decimal('610.00')

    def test_cash_only_multiple_same_day_round_trips_stay_consistent(self, user):
        """同日に複数のラウンドトリップがあっても、常に買い→売りの順で処理される。"""
        diary = StockDiary.objects.create(
            user=user,
            stock_symbol='7011',
            stock_name='三菱重工業',
            reason='テスト用',
            currency='JPY',
        )

        # ファイル順: 売り・売り・買い・買い（両方とも売りが先に記載されているケース）
        Transaction.objects.create(
            diary=diary, transaction_type='sell', transaction_date=date(2024, 3, 1),
            price=Decimal('1010'), quantity=Decimal('50'), is_margin=False,
        )
        Transaction.objects.create(
            diary=diary, transaction_type='sell', transaction_date=date(2024, 3, 1),
            price=Decimal('1030'), quantity=Decimal('50'), is_margin=False,
        )
        Transaction.objects.create(
            diary=diary, transaction_type='buy', transaction_date=date(2024, 3, 1),
            price=Decimal('1000'), quantity=Decimal('50'), is_margin=False,
        )
        Transaction.objects.create(
            diary=diary, transaction_type='buy', transaction_date=date(2024, 3, 1),
            price=Decimal('1020'), quantity=Decimal('50'), is_margin=False,
        )

        diary.refresh_from_db()

        # 買い100株が先に処理されてから売り100株が処理されるため、保有数は0に戻り、
        # 総売却額(102,000) は総投資額(101,000)の範囲内で消化される。
        assert diary.cash_only_current_quantity == Decimal('0.00')
        assert diary.cash_only_total_buy_amount == Decimal('101000.00')
        assert diary.cash_only_total_sell_amount == Decimal('102000.00')
        assert diary.cash_only_realized_profit == Decimal('1000.00')
