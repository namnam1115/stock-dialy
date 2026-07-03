"""分析API 読み取り系の「ユーザースコープ固定」の回帰テスト。

なぜこのテストがあるか:
  読み取り系（holdings/diary_detail/positions/list_diaries/portfolio）は当初ユーザーで
  絞っておらず、本番の複数ユーザーDBで**他ユーザーのデータを返してしまう**穴があった
  （実際に別ユーザー所有の銘柄をスクリーニングに混入させた）。読み取りを
  ANALYSIS_API_USER に固定し、他ユーザーのデータは一切返さない・設定未設定なら
  fail-closed（503）することを固定する。※実ユーザー名はテストに書かない（設定値のみ）。
"""
import json

import pytest
from django.test import RequestFactory
from django.contrib.auth import get_user_model

from stockdiary import api_analysis
from stockdiary.models import StockDiary, Transaction
from decimal import Decimal

User = get_user_model()
pytestmark = pytest.mark.django_db

SCOPE = 'scope_user'
OTHER = 'other_user'


@pytest.fixture
def two_users(db):
    """スコープ対象ユーザーと、見えてはいけない別ユーザー。"""
    scope = User.objects.create_user(username=SCOPE, password='p', email='s@example.com')
    other = User.objects.create_user(username=OTHER, password='p', email='o@example.com')
    return scope, other


@pytest.fixture
def settings_scoped(settings):
    settings.ANALYSIS_API_KEY = 'testkey'
    settings.ANALYSIS_API_USER = SCOPE
    return settings


def _get(path, **params):
    return RequestFactory().get(path, params, HTTP_AUTHORIZATION='Bearer testkey')


def _make_holding(user, symbol, price=Decimal('100')):
    d = StockDiary.objects.create(user=user, stock_name=symbol, stock_symbol=symbol, reason='')
    Transaction.objects.create(
        diary=d, transaction_type='buy', price=price, quantity=Decimal('10'),
        transaction_date='2026-01-05',
    )
    d.refresh_from_db()
    return d


def test_diary_detail_does_not_leak_other_user(settings_scoped, two_users):
    """別ユーザーしか持たない銘柄は 404（他人の日記を返さない）。"""
    _, other = two_users
    StockDiary.objects.create(user=other, stock_name='ZZZZ', stock_symbol='ZZZZ', reason='他人の秘密')

    resp = api_analysis.diary_detail(_get('/api/analysis/diary/ZZZZ/', news='0', margin='0',
                                          price='0', valuation='0'), 'ZZZZ')
    assert resp.status_code == 404


def test_diary_detail_returns_scope_user_when_symbol_shared(settings_scoped, two_users, monkeypatch):
    """同一銘柄を両ユーザーが持つ場合、スコープ対象ユーザーの日記だけを返す。"""
    scope, other = two_users
    StockDiary.objects.create(user=scope, stock_name='AAAA', stock_symbol='AAAA', reason='自分の理由')
    StockDiary.objects.create(user=other, stock_name='AAAA', stock_symbol='AAAA', reason='他人の理由')
    monkeypatch.setattr(api_analysis, '_fetch_current_price', lambda s: None)
    monkeypatch.setattr(api_analysis, '_fetch_valuation', lambda s: None)

    body = json.loads(api_analysis.diary_detail(
        _get('/api/analysis/diary/AAAA/', news='0', margin='0'), 'AAAA').content)
    assert body['investment_reason'] == '自分の理由'


def test_positions_excludes_other_user(settings_scoped, two_users, monkeypatch):
    """positions はスコープ対象ユーザーの保有だけを返す。"""
    scope, other = two_users
    _make_holding(scope, 'MINE')
    _make_holding(other, 'THEIRS')
    monkeypatch.setattr(api_analysis, '_fetch_current_price', lambda s: 150.0)

    body = json.loads(api_analysis.positions(_get('/api/analysis/positions/')).content)
    symbols = {p['symbol'] for p in body['positions']}
    assert symbols == {'MINE'}


def test_holdings_excludes_other_user(settings_scoped, two_users):
    scope, other = two_users
    _make_holding(scope, 'MINE')
    _make_holding(other, 'THEIRS')
    body = json.loads(api_analysis.holdings(_get('/api/analysis/holdings/')).content)
    assert {h['symbol'] for h in body['holdings']} == {'MINE'}


def test_list_diaries_excludes_other_user(settings_scoped, two_users):
    scope, other = two_users
    StockDiary.objects.create(user=scope, stock_name='MINE', stock_symbol='MINE', reason='')
    StockDiary.objects.create(user=other, stock_name='THEIRS', stock_symbol='THEIRS', reason='')
    body = json.loads(api_analysis.list_diaries(_get('/api/analysis/diaries/')).content)
    assert {d['symbol'] for d in body['diaries']} == {'MINE'}


def test_requesting_other_user_is_forbidden(settings_scoped, two_users, monkeypatch):
    """?user=<別ユーザー> は 403（設定ユーザー以外は指定不可）。"""
    monkeypatch.setattr(api_analysis, '_fetch_current_price', lambda s: 150.0)
    resp = api_analysis.positions(_get('/api/analysis/positions/', user=OTHER))
    assert resp.status_code == 403


def test_fail_closed_when_scope_user_unset(settings, two_users, monkeypatch):
    """ANALYSIS_API_USER 未設定なら 503 で fail-closed（全ユーザー露出を防ぐ）。"""
    settings.ANALYSIS_API_KEY = 'testkey'
    settings.ANALYSIS_API_USER = ''
    scope, other = two_users
    _make_holding(scope, 'MINE')
    _make_holding(other, 'THEIRS')
    monkeypatch.setattr(api_analysis, '_fetch_current_price', lambda s: 150.0)

    resp = api_analysis.positions(_get('/api/analysis/positions/'))
    assert resp.status_code == 503
    # データ配列を返していないこと（露出しない）
    assert 'positions' not in json.loads(resp.content)
