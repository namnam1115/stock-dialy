"""stockdiary/api.py の主要エンドポイントテスト。

外部 HTTP 依存のエンドポイント（get_stock_info 等）は requests.get を mock し、
データベース検索系（search_stock・industry）はそのまま実行する。
従来カバレッジ 21%。
"""
import pytest
import json
from unittest.mock import patch, MagicMock
from django.urls import reverse
from django.contrib.auth import get_user_model

from company_master.models import CompanyMaster

User = get_user_model()

pytestmark = pytest.mark.django_db(transaction=True)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def user(db):
    return User.objects.create_user(
        username='stock_api_user', password='pass', email='stockapi@example.com'
    )


@pytest.fixture
def authed_client(client, user):
    client.login(username='stock_api_user', password='pass')
    return client


@pytest.fixture
def company(db):
    return CompanyMaster.objects.create(
        code='7203',
        name='トヨタ自動車',
        market='プライム',
        industry_name_33='輸送用機器',
        industry_code_33='3650',
        industry_name_17='自動車・輸送機',
    )


# ---------------------------------------------------------------------------
# search_stock
# ---------------------------------------------------------------------------

class TestSearchStock:
    """銘柄検索 API テスト（オートコンプリート用）。"""

    def test_returns_matching_company_by_code(self, authed_client, company):
        """銘柄コードで前方一致検索できる。"""
        url = reverse('stockdiary:search_stock')
        resp = authed_client.get(url, {'query': '7203'})
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert any(c['code'] == '7203' for c in data['companies'])

    def test_returns_matching_company_by_name(self, authed_client, company):
        """銘柄名で部分一致検索できる。"""
        url = reverse('stockdiary:search_stock')
        resp = authed_client.get(url, {'query': 'トヨタ'})
        assert resp.status_code == 200
        data = resp.json()
        assert any(c['name'] == 'トヨタ自動車' for c in data['companies'])

    def test_returns_400_when_query_too_short(self, authed_client):
        """1文字以下のクエリは 400 を返す。"""
        url = reverse('stockdiary:search_stock')
        resp = authed_client.get(url, {'query': 'A'})
        assert resp.status_code == 400

    def test_returns_400_when_no_query(self, authed_client):
        """クエリなしは 400 を返す。"""
        url = reverse('stockdiary:search_stock')
        resp = authed_client.get(url)
        assert resp.status_code == 400

    def test_empty_result_for_no_match(self, authed_client):
        """一致なしのとき空リストを返す。"""
        url = reverse('stockdiary:search_stock')
        resp = authed_client.get(url, {'query': 'ZZZZZZ_nomatch'})
        data = resp.json()
        assert data['success'] is True
        assert data['companies'] == []
        assert data['count'] == 0

    def test_requires_login(self, client):
        url = reverse('stockdiary:search_stock')
        resp = client.get(url, {'query': 'トヨタ'})
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# get_industry_list
# ---------------------------------------------------------------------------

class TestGetIndustryList:
    """業種一覧 API テスト。"""

    def test_returns_industry_list(self, authed_client, company):
        url = reverse('stockdiary:api_industry_list')
        resp = authed_client.get(url)
        assert resp.status_code == 200
        data = resp.json()
        assert 'industries' in data
        names = [i['name'] for i in data['industries']]
        assert '輸送用機器' in names

    def test_requires_login(self, client):
        url = reverse('stockdiary:api_industry_list')
        resp = client.get(url)
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# get_industry_stocks
# ---------------------------------------------------------------------------

class TestGetIndustryStocks:
    """業種別銘柄一覧 API テスト。"""

    def test_returns_stocks_for_industry(self, authed_client, company):
        url = reverse('stockdiary:api_industry_stocks')
        resp = authed_client.get(url, {'industry_code': '3650'})
        assert resp.status_code == 200
        data = resp.json()
        assert any(c['code'] == '7203' for c in data['companies'])

    def test_returns_400_when_no_industry_code(self, authed_client):
        url = reverse('stockdiary:api_industry_stocks')
        resp = authed_client.get(url)
        assert resp.status_code == 400

    def test_requires_login(self, client):
        url = reverse('stockdiary:api_industry_stocks')
        resp = client.get(url, {'industry_code': '3650'})
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# get_stock_info (外部 HTTP を mock)
# ---------------------------------------------------------------------------

class TestGetStockInfo:
    """株価情報取得 API テスト（外部 HTTP は mock）。"""

    def _make_yahoo_response(self, price=2500.0, prev_close=2450.0):
        """Yahoo Finance API のレスポンス形式を模倣する。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'chart': {
                'result': [{
                    'meta': {
                        'regularMarketPrice': price,
                        'previousClose': prev_close,
                        'exchangeName': 'TSE',
                        'shortName': 'TOYOTA',
                    }
                }]
            }
        }
        return mock_resp

    def test_returns_stock_info_for_japanese_stock(self, authed_client, company):
        """日本株コードで会社情報と株価を返す。"""
        url = reverse('stockdiary:api_stock_info', args=['7203'])
        with patch('stockdiary.api.requests.get', return_value=self._make_yahoo_response()):
            resp = authed_client.get(url)
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert data['company_name'] == 'トヨタ自動車'
        assert data['price'] == 2500.0

    def test_returns_fallback_when_yahoo_fails(self, authed_client, company):
        """Yahoo API が失敗しても company_master のデータで応答する。"""
        url = reverse('stockdiary:api_stock_info', args=['7203'])
        with patch('stockdiary.api.requests.get', side_effect=Exception('timeout')):
            resp = authed_client.get(url)
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert data['company_name'] == 'トヨタ自動車'

    def test_returns_500_when_both_yahoo_and_master_fail(self, authed_client):
        """Yahoo API 失敗 + company_master にもデータなしで 500 を返す。"""
        url = reverse('stockdiary:api_stock_info', args=['9999'])
        with patch('stockdiary.api.requests.get', side_effect=Exception('not found')):
            resp = authed_client.get(url)
        assert resp.status_code == 500

    def test_requires_login(self, client):
        url = reverse('stockdiary:api_stock_info', args=['7203'])
        resp = client.get(url)
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# get_stock_price (外部 HTTP を mock)
# ---------------------------------------------------------------------------

class TestGetStockPrice:
    """株価取得 API テスト（外部 HTTP は mock）。"""

    def test_returns_price(self, authed_client):
        """正常なレスポンスのとき株価を返す。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'chart': {
                'result': [{
                    'meta': {
                        'regularMarketPrice': 3000.0,
                        'previousClose': 2950.0,
                    }
                }]
            }
        }
        url = reverse('stockdiary:api_stock_price', args=['7203'])
        with patch('stockdiary.api.requests.get', return_value=mock_resp):
            resp = authed_client.get(url)
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert data['price'] == 3000.0

    def test_requires_login(self, client):
        url = reverse('stockdiary:api_stock_price', args=['7203'])
        resp = client.get(url)
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# yfinance キャッシュ層（YahooFinanceService.cached_fetch）
# ---------------------------------------------------------------------------

class TestYahooFinanceCache:
    """外部API呼び出しの短TTLキャッシュと stale フォールバックのテスト。

    なぜ追加したか: yfinance/Yahoo Chart API はリクエスト経路で同期呼び出しされ、
    外部レイテンシがそのまま画面の体感速度になっていた（キャッシュ皆無）。
    さらに yfinance の断続障害時はその都度エラーになっていた。
    cached_fetch 導入で (1)同一銘柄の連続アクセスはキャッシュで吸収し
    (2)取得失敗時は期限内の旧値（stale）へフォールバックする。
    """

    def setup_method(self):
        from django.core.cache import cache
        cache.clear()

    def test_second_call_hits_cache(self):
        """2回目の呼び出しは fetch_fn を実行しない（フレッシュヒット）。"""
        from common.services.yahoo_finance_service import YahooFinanceService
        calls = []

        def fetch():
            calls.append(1)
            return {'price': 100}

        r1 = YahooFinanceService.cached_fetch('price', 'test:0001', fetch)
        r2 = YahooFinanceService.cached_fetch('price', 'test:0001', fetch)
        assert r1 == r2 == {'price': 100}
        assert len(calls) == 1

    def test_stale_fallback_on_exception(self):
        """フレッシュ期限切れ後に fetch が失敗したら stale コピーを返す。"""
        from django.core.cache import cache
        from common.services.yahoo_finance_service import YahooFinanceService

        YahooFinanceService.cached_fetch('price', 'test:0002', lambda: {'price': 200})
        # フレッシュだけ失効させる（stale コピーは残す）
        cache.delete('yf:price:test:0002')

        def broken():
            raise RuntimeError('yfinance down')

        result = YahooFinanceService.cached_fetch('price', 'test:0002', broken)
        assert result == {'price': 200}

    def test_empty_result_is_not_cached(self):
        """空結果はキャッシュされず、次回の呼び出しでリトライされる。"""
        from common.services.yahoo_finance_service import YahooFinanceService
        calls = []

        def fetch():
            calls.append(1)
            return {}

        YahooFinanceService.cached_fetch('financial', 'test:0003', fetch)
        YahooFinanceService.cached_fetch('financial', 'test:0003', fetch)
        assert len(calls) == 2

    def test_get_stock_price_view_uses_cache(self, authed_client):
        """get_stock_price は10分キャッシュを経由し、2回目は外部HTTPを呼ばない。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'chart': {'result': [{'meta': {'regularMarketPrice': 1234.0}}]}
        }
        url = reverse('stockdiary:api_stock_price', args=['9999'])
        with patch('stockdiary.api.requests.get', return_value=mock_resp) as mock_get:
            r1 = authed_client.get(url)
            r2 = authed_client.get(url)
        assert r1.json()['price'] == 1234.0
        assert r2.json()['price'] == 1234.0
        assert mock_get.call_count == 1

    def test_fetch_company_data_uses_cache(self):
        """fetch_company_data は24時間キャッシュを経由する。"""
        from decimal import Decimal
        from common.services.yahoo_finance_service import YahooFinanceService
        with patch.object(
            YahooFinanceService, '_fetch_company_data_uncached',
            return_value={'per': Decimal('12.3')},
        ) as mock_fetch:
            r1 = YahooFinanceService.fetch_company_data('7203')
            r2 = YahooFinanceService.fetch_company_data('7203')
        assert r1 == r2 == {'per': Decimal('12.3')}
        assert mock_fetch.call_count == 1
