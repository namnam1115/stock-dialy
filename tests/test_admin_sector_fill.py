"""admin画面の「業種未設定分をCompanyMasterから自動設定」アクションの回帰テスト。

なぜ追加したか: 既存の日記（証券CSVインポートより前に作成されたもの等）は業種が
空のまま残る。ユーザー要望で、admin画面から選択した日記に対して銘柄コードを
CompanyMasterに突き合わせ、業種を一括で設定できるようにした
（証券CSVインポート時の自動設定＝tests/test_trade_import.py と同じ方針：
既存値は上書きしない・CompanyMasterに無いコードはスキップ）。
"""
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
import pytest

from company_master.models import CompanyMaster
from stockdiary.admin import StockDiaryAdmin
from stockdiary.models import StockDiary


def _make_request():
    request = RequestFactory().post('/admin/stockdiary/stockdiary/')
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    MessageMiddleware(lambda r: None).process_request(request)
    return request


@pytest.fixture
def diary_admin():
    return StockDiaryAdmin(StockDiary, AdminSite())


@pytest.mark.django_db
class TestFillMissingSectors:
    def test_sets_sector_from_company_master_when_missing(self, user, diary_admin):
        CompanyMaster.objects.create(code='7203', name='トヨタ自動車', industry_name_33='輸送用機器')
        diary = StockDiary.objects.create(user=user, stock_symbol='7203', stock_name='トヨタ自動車')

        diary_admin.fill_missing_sectors(_make_request(), StockDiary.objects.filter(pk=diary.pk))

        diary.refresh_from_db()
        assert diary.sector == '輸送用機器'

    def test_does_not_overwrite_existing_sector(self, user, diary_admin):
        CompanyMaster.objects.create(code='7203', name='トヨタ自動車', industry_name_33='輸送用機器')
        diary = StockDiary.objects.create(
            user=user, stock_symbol='7203', stock_name='トヨタ自動車', sector='自分で設定した業種'
        )

        diary_admin.fill_missing_sectors(_make_request(), StockDiary.objects.filter(pk=diary.pk))

        diary.refresh_from_db()
        assert diary.sector == '自分で設定した業種'

    def test_skips_when_company_master_has_no_match(self, user, diary_admin):
        diary = StockDiary.objects.create(user=user, stock_symbol='9999', stock_name='不明銘柄')

        diary_admin.fill_missing_sectors(_make_request(), StockDiary.objects.filter(pk=diary.pk))

        diary.refresh_from_db()
        assert diary.sector == ''

    def test_skips_diaries_without_stock_symbol(self, user, diary_admin):
        diary = StockDiary.objects.create(user=user, stock_name='メモ日記')

        # 銘柄コードが無いのでクラッシュせずスキップされる
        diary_admin.fill_missing_sectors(_make_request(), StockDiary.objects.filter(pk=diary.pk))

        diary.refresh_from_db()
        assert diary.sector == ''

    def test_bulk_update_across_multiple_diaries(self, user, diary_admin):
        CompanyMaster.objects.create(code='7203', name='トヨタ自動車', industry_name_33='輸送用機器')
        CompanyMaster.objects.create(code='6758', name='ソニーG', industry_name_33='電気機器')
        d1 = StockDiary.objects.create(user=user, stock_symbol='7203', stock_name='トヨタ自動車')
        d2 = StockDiary.objects.create(user=user, stock_symbol='6758', stock_name='ソニーG')

        diary_admin.fill_missing_sectors(
            _make_request(), StockDiary.objects.filter(pk__in=[d1.pk, d2.pk])
        )

        d1.refresh_from_db()
        d2.refresh_from_db()
        assert d1.sector == '輸送用機器'
        assert d2.sector == '電気機器'
