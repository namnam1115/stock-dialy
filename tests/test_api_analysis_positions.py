"""分析API positions エンドポイント & diary_detail のポジション拡張のテスト。

なぜこのテストがあるか:
  保有中銘柄の利確/損切り/継続/買い増しを判断するには「現在値→含み損益」が不可欠だが、
  従来の分析APIは平均取得単価・数量までしか返さず、現在値・含み損益・バリュエーションが
  無かった。positions/ 一覧と diary_detail への拡張でこれを埋めた。含み損益の算出式
  （(現在値−平均取得単価)×数量）と、価格取得のスキップ・保有のみ抽出が壊れないよう固定する。
  価格/バリュエーションは外部API（yfinance/Yahoo）依存のため monkeypatch で置き換える。
"""
import json
from decimal import Decimal

import pytest
from django.test import RequestFactory
from django.contrib.auth import get_user_model

from stockdiary import api_analysis
from stockdiary.models import StockDiary, Thesis, Transaction, Verdict

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def auth_settings(settings):
    settings.ANALYSIS_API_KEY = 'testkey'
    return settings


@pytest.fixture
def holding(db):
    """平均取得単価 2000 × 100株 の保有ポジション。"""
    user = User.objects.create_user(username='p_user', password='p', email='p@example.com')
    diary = StockDiary.objects.create(
        user=user, stock_name='トヨタ自動車', stock_symbol='7203', sector='輸送用機器', reason=''
    )
    Transaction.objects.create(
        diary=diary, transaction_type='buy',
        price=Decimal('2000.00'), quantity=Decimal('100'),
        transaction_date='2024-01-10',
    )
    diary.refresh_from_db()
    return diary


def _get(path, **params):
    return RequestFactory().get(path, params, HTTP_AUTHORIZATION='Bearer testkey')


# ------------------------------------------------------------------ #
#  positions/ 一覧
# ------------------------------------------------------------------ #

def test_positions_requires_auth(auth_settings, holding):
    """Bearer 認証が無ければ 401。"""
    req = RequestFactory().get('/api/analysis/positions/')
    assert api_analysis.positions(req).status_code == 401


def test_positions_computes_unrealized_profit(auth_settings, holding, monkeypatch):
    """現在値 2500 → 含み益 (2500-2000)*100 = 50000 円（+25%）。"""
    monkeypatch.setattr(api_analysis, '_fetch_current_price', lambda s: 2500.0)

    resp = api_analysis.positions(_get('/api/analysis/positions/'))
    assert resp.status_code == 200
    body = json.loads(resp.content)

    assert body['count'] == 1
    pos = body['positions'][0]
    assert pos['symbol'] == '7203'
    assert pos['current_price'] == 2500.0
    assert pos['market_value'] == 250000.0
    assert pos['cost_basis'] == 200000.0
    assert pos['unrealized_profit'] == 50000.0
    assert pos['unrealized_profit_rate'] == pytest.approx(25.0)

    # ポートフォリオ合計にも反映される
    assert body['portfolio']['total_unrealized_profit'] == 50000.0
    assert body['portfolio']['total_unrealized_profit_rate'] == pytest.approx(25.0)


def test_positions_handles_loss(auth_settings, holding, monkeypatch):
    """現在値 1800 → 含み損 -20000 円（-10%）。"""
    monkeypatch.setattr(api_analysis, '_fetch_current_price', lambda s: 1800.0)

    body = json.loads(api_analysis.positions(_get('/api/analysis/positions/')).content)
    pos = body['positions'][0]
    assert pos['unrealized_profit'] == -20000.0
    assert pos['unrealized_profit_rate'] == pytest.approx(-10.0)


def test_positions_excludes_non_holdings(auth_settings, holding, monkeypatch):
    """保有数量 0 の日記（メモ・売却済み）は positions に含めない。"""
    monkeypatch.setattr(api_analysis, '_fetch_current_price', lambda s: 2500.0)
    StockDiary.objects.create(
        user=holding.user, stock_name='メモ株', stock_symbol='9999', reason=''
    )  # current_quantity=0

    body = json.loads(api_analysis.positions(_get('/api/analysis/positions/')).content)
    symbols = {p['symbol'] for p in body['positions']}
    assert symbols == {'7203'}


def test_positions_price_can_be_skipped(auth_settings, holding, monkeypatch):
    """?price=0 で現在値取得をスキップし、含み損益は None。"""
    called = []
    monkeypatch.setattr(
        api_analysis, '_fetch_current_price',
        lambda s: called.append(s) or 2500.0
    )
    body = json.loads(
        api_analysis.positions(_get('/api/analysis/positions/', price='0')).content
    )
    pos = body['positions'][0]
    assert called == []  # 外部価格取得は呼ばれない
    assert pos['current_price'] is None
    assert pos['unrealized_profit'] is None


def test_positions_valuation_opt_in(auth_settings, holding, monkeypatch):
    """バリュエーションは既定OFF。?valuation=1 のときだけ付与する。"""
    monkeypatch.setattr(api_analysis, '_fetch_current_price', lambda s: 2500.0)
    monkeypatch.setattr(
        api_analysis, '_fetch_valuation',
        lambda s: {'per': 12.0, 'pbr': 1.1, 'roe': None, 'dividend_yield': 2.5,
                   'market_cap': None, 'note': ''}
    )

    off = json.loads(api_analysis.positions(_get('/api/analysis/positions/')).content)
    assert off['positions'][0]['valuation'] is None

    on = json.loads(
        api_analysis.positions(_get('/api/analysis/positions/', valuation='1')).content
    )
    assert on['positions'][0]['valuation']['per'] == 12.0


# ------------------------------------------------------------------ #
#  diary_detail のポジション拡張
# ------------------------------------------------------------------ #

def test_diary_detail_includes_position_metrics(auth_settings, holding, monkeypatch):
    """diary_detail が現在値・含み損益・時価を返す。"""
    monkeypatch.setattr(api_analysis, '_fetch_current_price', lambda s: 2500.0)
    monkeypatch.setattr(api_analysis, '_fetch_valuation', lambda s: None)

    req = _get('/api/analysis/diary/7203/', news='0', margin='0')
    body = json.loads(api_analysis.diary_detail(req, '7203').content)

    assert body['current_price'] == 2500.0
    assert body['unrealized_profit'] == 50000.0
    assert body['unrealized_profit_rate'] == pytest.approx(25.0)
    assert body['market_value'] == 250000.0


def test_diary_detail_exposes_theses_not_just_reason(auth_settings, holding, monkeypatch):
    """買った理由（仮説）は reason ではなく theses にある。diary_detail が theses を返す。

    reason は『企業説明』でエントリー仮説を含むとは限らないため、判定の主ソースとなる
    Thesis（claim/basis/worst_case/status）と検証結果（Verdict）を API が露出する必要がある。
    """
    monkeypatch.setattr(api_analysis, '_fetch_current_price', lambda s: 2500.0)
    monkeypatch.setattr(api_analysis, '_fetch_valuation', lambda s: None)

    thesis = Thesis.objects.create(
        diary=holding, claim='円安継続で輸出採算が改善する',
        basis='海外売上比率が高い', worst_case='急激な円高進行', horizon='6m',
    )
    Verdict.objects.create(
        thesis=thesis, hypothesis_result='hit', pnl_result='holding',
        decision_quality=4, learning='マクロ前提の確認は有効だった',
    )

    req = _get('/api/analysis/diary/7203/', news='0', margin='0')
    body = json.loads(api_analysis.diary_detail(req, '7203').content)

    assert len(body['theses']) == 1
    th = body['theses'][0]
    assert th['claim'] == '円安継続で輸出採算が改善する'
    assert th['worst_case'] == '急激な円高進行'
    assert th['status'] == '未検証'
    assert th['verdict']['hypothesis_result'] == '的中'
    assert th['verdict']['learning'] == 'マクロ前提の確認は有効だった'


def test_diary_detail_theses_empty_when_none_recorded(auth_settings, holding, monkeypatch):
    """エントリー仮説が未記録なら theses は空配列（reason があっても捏造しない）。"""
    monkeypatch.setattr(api_analysis, '_fetch_current_price', lambda s: 2500.0)
    monkeypatch.setattr(api_analysis, '_fetch_valuation', lambda s: None)
    holding.reason = '## 企業説明\nトヨタは自動車の会社'  # 企業説明のみ・仮説なし
    holding.save(update_fields=['reason'])

    req = _get('/api/analysis/diary/7203/', news='0', margin='0')
    body = json.loads(api_analysis.diary_detail(req, '7203').content)
    assert body['theses'] == []


def test_positions_reports_thesis_counts(auth_settings, holding, monkeypatch):
    """positions は仮説の有無をスクリーニングできるよう thesis_count を返す。"""
    monkeypatch.setattr(api_analysis, '_fetch_current_price', lambda s: 2500.0)
    Thesis.objects.create(diary=holding, claim='主張', status='open')
    Thesis.objects.create(diary=holding, claim='取り下げた主張', status='abandoned')

    body = json.loads(api_analysis.positions(_get('/api/analysis/positions/')).content)
    pos = body['positions'][0]
    assert pos['thesis_count'] == 2
    assert pos['open_thesis_count'] == 1


def test_diary_detail_price_can_be_skipped(auth_settings, holding, monkeypatch):
    """?price=0 で diary_detail の現在値取得をスキップ。"""
    monkeypatch.setattr(
        api_analysis, '_fetch_current_price',
        lambda s: pytest.fail('price fetch should be skipped')
    )
    monkeypatch.setattr(api_analysis, '_fetch_valuation', lambda s: None)

    req = _get('/api/analysis/diary/7203/', news='0', margin='0', price='0')
    body = json.loads(api_analysis.diary_detail(req, '7203').content)
    assert body['current_price'] is None
    assert body['unrealized_profit'] is None
