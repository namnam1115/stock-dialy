"""信用のロング・ショート同時保有に関する回帰テスト。

なぜこのテストがあるか:
StockDiary の「全体」集計フィールド（current_quantity/realized_profit 等）は
符号付き単一カウンタで保有数を表していた。これだと「同じ銘柄でロングを
保有しながらショートも建てる」を表現できず、新規のショート建てを既存
ロングの決済と誤認する（あるいはその逆）バグがあった。

実在の証券会社CSV（楽天）で、同日に「信用新規買建→信用新規売建→
信用返済買付（売建の決済）→信用返済売付（買建の決済）」という4取引が、
ファイル上は決済が新規建てより先に並ぶ実例を確認した。Transaction に
margin_action（新規/返済）を追加し、AggregateService._recalculate_all で
ロング用カウンタ（現物+信用ロング、従来通り）とショート専用カウンタ
（margin_short_*）を完全に分離することで解消した。

この回帰テストは、実際の受渡金額（CSVの值）と一致することで検証する。
"""
from datetime import date
from decimal import Decimal

import pytest

from stockdiary.models import StockDiary, Transaction


@pytest.mark.django_db(transaction=True)
class TestMarginLongShortSeparation:
    def test_same_day_overlapping_long_and_short_with_explicit_margin_action(self, user):
        """同日に信用ロング・ショートを同時に持つ実例（楽天CSV 2024/4/10, 銘柄7011）。

        ファイル順（決済が新規建てより先）のまま save() しても、margin_action
        （CSV由来）でロング・ショートを正しく分離できることを確認する。
        受渡金額はCSVの実データと一致する（ロング決済+60円、ショート決済+560円）。
        """
        diary = StockDiary.objects.create(
            user=user, stock_symbol='7011', stock_name='三菱重工業',
            reason='テスト用', currency='JPY',
        )

        # ファイル順: ①ショート決済(買) ②ロング新規(買) ③ショート新規(売) ④ロング決済(売)
        Transaction.objects.create(
            diary=diary, transaction_type='buy', transaction_date=date(2024, 4, 10),
            price=Decimal('1351.10'), quantity=Decimal('100'),
            is_margin=True, margin_action='close',
        )
        Transaction.objects.create(
            diary=diary, transaction_type='buy', transaction_date=date(2024, 4, 10),
            price=Decimal('1338.40'), quantity=Decimal('100'),
            is_margin=True, margin_action='open',
        )
        Transaction.objects.create(
            diary=diary, transaction_type='sell', transaction_date=date(2024, 4, 10),
            price=Decimal('1356.70'), quantity=Decimal('100'),
            is_margin=True, margin_action='open',
        )
        Transaction.objects.create(
            diary=diary, transaction_type='sell', transaction_date=date(2024, 4, 10),
            price=Decimal('1339.00'), quantity=Decimal('100'),
            is_margin=True, margin_action='close',
        )

        diary.refresh_from_db()

        # ロング（現物/信用ロング用カウンタ）: 新規→決済で手仕舞い、損益+60円
        assert diary.current_quantity == Decimal('0.00')
        assert diary.realized_profit == Decimal('60.00')

        # ショート（専用カウンタ）: 新規→決済で手仕舞い、損益+560円
        assert diary.margin_short_quantity == Decimal('0.00')
        assert diary.margin_short_realized_profit == Decimal('560.00')

    def test_long_carried_over_while_short_closes_same_day(self, user):
        """ロングだけ日をまたいで持ち越し、ショートは同日で決済するケース。

        margin_action が無い（旧ロジックの）場合はロングの平均単価・実現損益が
        ショートの決済に汚染される（average_purchase_price が本来の1338.40では
        なく1344.75になる）。margin_action を明示すればこれを防げる。
        """
        diary = StockDiary.objects.create(
            user=user, stock_symbol='7012', stock_name='テスト2',
            reason='テスト用', currency='JPY',
        )
        Transaction.objects.create(
            diary=diary, transaction_type='buy', transaction_date=date(2024, 4, 10),
            price=Decimal('1351.10'), quantity=Decimal('100'),
            is_margin=True, margin_action='close',
        )
        Transaction.objects.create(
            diary=diary, transaction_type='buy', transaction_date=date(2024, 4, 10),
            price=Decimal('1338.40'), quantity=Decimal('100'),
            is_margin=True, margin_action='open',
        )
        Transaction.objects.create(
            diary=diary, transaction_type='sell', transaction_date=date(2024, 4, 10),
            price=Decimal('1356.70'), quantity=Decimal('100'),
            is_margin=True, margin_action='open',
        )
        diary.refresh_from_db()

        # ロングは翌日以降に持ち越し中（本来の建単価のまま）
        assert diary.current_quantity == Decimal('100.00')
        assert diary.average_purchase_price == Decimal('1338.40')
        assert diary.realized_profit == Decimal('0.00')

        # ショートは同日で決済済み
        assert diary.margin_short_quantity == Decimal('0.00')
        assert diary.margin_short_realized_profit == Decimal('560.00')

    def test_manual_entry_without_margin_action_still_works_for_single_direction(self, user):
        """手入力（margin_action未設定）でも、ロング・ショートを同時に持たない
        単純なケースでは従来通り正しく計算できる（フォールバック・ヒューリスティック）。
        """
        diary = StockDiary.objects.create(
            user=user, stock_symbol='7013', stock_name='テスト3',
            reason='テスト用', currency='JPY',
        )
        # 信用の新規売り（ショート）→ 返済買い、margin_action は未設定
        Transaction.objects.create(
            diary=diary, transaction_type='sell', transaction_date=date(2024, 5, 1),
            price=Decimal('3000.00'), quantity=Decimal('100'), is_margin=True,
        )
        Transaction.objects.create(
            diary=diary, transaction_type='buy', transaction_date=date(2024, 5, 10),
            price=Decimal('2800.00'), quantity=Decimal('100'), is_margin=True,
        )
        diary.refresh_from_db()

        assert diary.current_quantity == Decimal('0.00')
        assert diary.margin_short_quantity == Decimal('0.00')
        assert diary.margin_short_realized_profit == Decimal('20000.00')
        # ロング用カウンタは無関係のまま
        assert diary.realized_profit == Decimal('0.00')
