"""証券CSV取込（楽天・SBI）の回帰テスト。

なぜこのテストを足したか:
process_rakuten_csv / process_sbi_csv は、ループ内で取引を1件ずつ save() しており、
各 save() が Transaction.save() 経由で diary.update_aggregates()（フル O(N) 再集計）を
走らせていた。さらにループ終了後にも全 Diary を再集計しており、1 Diary あたり
N+1 回の再集計（実質 O(N^2)）になっていた。これを「ループ中は _defer_recalc で
自動再集計を抑制し、末尾で触れた Diary だけを1回ずつ再集計する」形に修正した。

ここでは (1) 集計結果が正しいこと、(2) 再集計がちょうど1回に収束していること
（＝個別 save() の自動再集計が抑制されていること）を回帰として固定する。
"""
from decimal import Decimal
from unittest.mock import patch

import pytest

from stockdiary.models import StockDiary, Transaction
from stockdiary.services.aggregate_service import AggregateService
from stockdiary import views_trade_import
from company_master.models import CompanyMaster


RAKUTEN_CSV = """受渡日,銘柄コード,銘柄名,売買区分,取引区分,数量［株］,単価［円］
2024/01/10,7203,トヨタ自動車,買付,現物,100,1000
2024/01/20,7203,トヨタ自動車,買付,現物,200,1100
2024/02/01,7203,トヨタ自動車,売付,現物,50,1200
"""


@pytest.mark.django_db
class TestRakutenImportAggregates:
    def test_aggregates_are_correct(self, user):
        result = views_trade_import.process_rakuten_csv(user, RAKUTEN_CSV, 'rakuten.csv')

        assert result['success_count'] == 3
        assert result['error_count'] == 0

        diary = StockDiary.objects.get(user=user, stock_symbol='7203')
        # 買 100 + 200 = 300、売 50 → 保有 250
        assert diary.total_bought_quantity == Decimal('300')
        assert diary.total_sold_quantity == Decimal('50')
        assert diary.current_quantity == Decimal('250')
        assert diary.transaction_count == 3
        assert Transaction.objects.filter(diary=diary).count() == 3

    def test_recalculation_collapses_to_one_call(self, user):
        """3取引の取込でも再集計はちょうど1回（個別 save() の自動再集計が抑制される）。

        修正前は各 save() ごとに recalc + 末尾で再度 recalc が走り、合計4回だった。
        """
        with patch.object(
            AggregateService, 'recalculate',
            wraps=AggregateService.recalculate,
        ) as spy:
            views_trade_import.process_rakuten_csv(user, RAKUTEN_CSV, 'rakuten.csv')
        assert spy.call_count == 1

    def test_reimport_is_idempotent(self, user):
        """同一CSVの再取込は上書き扱いになり、保有数が二重計上されない。"""
        views_trade_import.process_rakuten_csv(user, RAKUTEN_CSV, 'rakuten.csv')
        result2 = views_trade_import.process_rakuten_csv(user, RAKUTEN_CSV, 'rakuten.csv')

        assert result2['overwrite_count'] == 3
        diary = StockDiary.objects.get(user=user, stock_symbol='7203')
        assert diary.current_quantity == Decimal('250')
        assert Transaction.objects.filter(diary=diary).count() == 3


SBI_CSV = "\n".join([
    'dummy1', 'dummy2', 'dummy3', 'dummy4', 'dummy5', 'dummy6', 'dummy7',
    '受渡日,銘柄コード,銘柄,取引,市場,約定数量,約定単価',
    '2024/01/10,7203,トヨタ自動車,現物買,東証,100,1000',
])


@pytest.mark.django_db
class TestSectorAutoAssignFromCompanyMaster:
    """証券CSVインポート時に業種（sector）を CompanyMaster から自動設定する（ユーザー要望）。

    なぜこの実装か: 業種は本来 銘柄コードに紐づく値だが、参照のたびに
    CompanyMaster を引くとコストがかかるため、インポート時（ファイル内の
    distinct コードだけ1回）に StockDiary.sector へ書き込んで確定させる。
    銘柄コードが CompanyMaster に無い場合は空のまま＝詳細画面から任意で設定できる。
    """

    def test_rakuten_sets_sector_from_company_master_on_create(self, user):
        CompanyMaster.objects.create(code='7203', name='トヨタ自動車', industry_name_33='輸送用機器')

        views_trade_import.process_rakuten_csv(user, RAKUTEN_CSV, 'rakuten.csv')

        diary = StockDiary.objects.get(user=user, stock_symbol='7203')
        assert diary.sector == '輸送用機器'

    def test_rakuten_leaves_sector_blank_when_company_master_missing(self, user):
        # CompanyMaster に該当コードが無い（未上場・未取得等）
        views_trade_import.process_rakuten_csv(user, RAKUTEN_CSV, 'rakuten.csv')

        diary = StockDiary.objects.get(user=user, stock_symbol='7203')
        assert diary.sector == ''

    def test_rakuten_backfills_sector_on_existing_diary_without_sector(self, user):
        StockDiary.objects.create(user=user, stock_symbol='7203', stock_name='トヨタ自動車')
        CompanyMaster.objects.create(code='7203', name='トヨタ自動車', industry_name_33='輸送用機器')

        views_trade_import.process_rakuten_csv(user, RAKUTEN_CSV, 'rakuten.csv')

        diary = StockDiary.objects.get(user=user, stock_symbol='7203')
        assert diary.sector == '輸送用機器'

    def test_rakuten_does_not_overwrite_manually_set_sector(self, user):
        StockDiary.objects.create(
            user=user, stock_symbol='7203', stock_name='トヨタ自動車', sector='自分で設定した業種'
        )
        CompanyMaster.objects.create(code='7203', name='トヨタ自動車', industry_name_33='輸送用機器')

        views_trade_import.process_rakuten_csv(user, RAKUTEN_CSV, 'rakuten.csv')

        diary = StockDiary.objects.get(user=user, stock_symbol='7203')
        assert diary.sector == '自分で設定した業種'

    def test_sbi_sets_sector_from_company_master_on_create(self, user):
        CompanyMaster.objects.create(code='7203', name='トヨタ自動車', industry_name_33='輸送用機器')

        views_trade_import.process_sbi_csv(user, SBI_CSV, 'sbi.csv')

        diary = StockDiary.objects.get(user=user, stock_symbol='7203')
        assert diary.sector == '輸送用機器'
