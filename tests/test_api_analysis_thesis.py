"""分析API add_thesis（投資仮説の書き込み）のテスト。

なぜこのテストがあるか:
  ポジション判定で「エントリー仮説が未記録」の銘柄に、今からでも worst_case
  （崩れる条件＝損切り/縮小条件）を1つ定義できるようにした。worst_case の正しい
  置き場所は Thesis モデルで、review_due_date がホーム想起（答え合わせ待ち）を駆動する。
  分析API経由でも UI と同じ導出で Thesis を作れること、必須/選択の検証、horizon から
  review_due_date が自動補完されることを固定する。
"""
import json
from datetime import date, timedelta

import pytest
from django.test import RequestFactory
from django.contrib.auth import get_user_model

from stockdiary import api_analysis
from stockdiary.models import StockDiary, Thesis

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def auth_settings(settings):
    settings.ANALYSIS_API_KEY = 'testkey'
    settings.ANALYSIS_API_USER = 't_user'
    return settings


@pytest.fixture
def diary(db):
    user = User.objects.create_user(username='t_user', password='p', email='t@example.com')
    return StockDiary.objects.create(
        user=user, stock_name='CrowdStrike', stock_symbol='CRWD',
        reason='', first_purchase_date=date(2026, 1, 1),
    )


def _post(symbol, payload):
    return RequestFactory().post(
        f'/api/analysis/diary/{symbol}/thesis/',
        data=json.dumps(payload), content_type='application/json',
        HTTP_AUTHORIZATION='Bearer testkey',
    )


def test_requires_auth(auth_settings, diary):
    req = RequestFactory().post('/api/analysis/diary/CRWD/thesis/', data='{}',
                                content_type='application/json')
    assert api_analysis.add_thesis(req, 'CRWD').status_code == 401


def test_claim_is_required(auth_settings, diary):
    """claim（主張）が無ければ 400。"""
    resp = api_analysis.add_thesis(_post('CRWD', {'worst_case': 'x'}), 'CRWD')
    assert resp.status_code == 400
    assert 'claim' in json.loads(resp.content)['error']


def test_creates_thesis_with_worst_case(auth_settings, diary):
    """claim + worst_case を構造化して保存する。"""
    payload = {
        'claim': '純新規ARRの加速が続き、AIセキュリティ需要で高成長が持続する',
        'worst_case': '純新規ARRが2Q連続で減速、かつ株価が分割後安値$168を明確割れ',
        'basis': '事業の質・モートは業界最上位',
        'horizon': 'next_earnings',
        'review_due_date': '2026-08-26',
    }
    resp = api_analysis.add_thesis(_post('CRWD', payload), 'CRWD')
    assert resp.status_code == 201
    body = json.loads(resp.content)

    thesis = Thesis.objects.get(id=body['thesis_id'])
    assert thesis.diary == diary
    assert thesis.worst_case.startswith('純新規ARRが2Q連続で減速')
    assert thesis.status == Thesis.STATUS_OPEN
    assert thesis.review_due_date == date(2026, 8, 26)
    assert body['review_due_date'] == '2026-08-26'


def test_invalid_horizon_rejected(auth_settings, diary):
    resp = api_analysis.add_thesis(
        _post('CRWD', {'claim': 'x', 'horizon': 'weekly'}), 'CRWD'
    )
    assert resp.status_code == 400
    assert 'horizon' in json.loads(resp.content)['error']


def test_review_due_date_derived_from_horizon(auth_settings, diary):
    """review_due_date 省略時は horizon から自動補完（6m=180日）。

    基準は初回購入日だが、購入日起点の期日が過去になる場合（昔からの保有に
    後から仮説を立てる）は今日起点に再アンカーされる。「作った瞬間から期限切れ」の
    検証予定日を作らない不変条件（UIと同一ロジック・views_growth 側の回帰テストは
    tests/test_earnings_calendar.py::test_thesis_due_date_never_in_the_past）。
    フィクスチャの購入日(2026-01-01)+180日は既に過去のため、今日+180日になる。
    """
    resp = api_analysis.add_thesis(
        _post('CRWD', {'claim': '長期でARR200億ドルへ', 'horizon': '6m'}), 'CRWD'
    )
    assert resp.status_code == 201
    body = json.loads(resp.content)
    assert body['review_due_date'] == (date.today() + timedelta(days=180)).isoformat()


def test_thesis_appears_in_diary_detail(auth_settings, diary, monkeypatch):
    """作成した仮説が diary_detail の theses に現れる（往復）。"""
    monkeypatch.setattr(api_analysis, '_fetch_current_price', lambda s: None)
    monkeypatch.setattr(api_analysis, '_fetch_valuation', lambda s: None)

    api_analysis.add_thesis(
        _post('CRWD', {'claim': 'AI需要で高成長持続', 'worst_case': 'ARR減速'}), 'CRWD'
    )
    req = RequestFactory().get('/api/analysis/diary/CRWD/', {'news': '0', 'margin': '0'},
                               HTTP_AUTHORIZATION='Bearer testkey')
    body = json.loads(api_analysis.diary_detail(req, 'CRWD').content)
    assert len(body['theses']) == 1
    assert body['theses'][0]['claim'] == 'AI需要で高成長持続'
    assert body['theses'][0]['worst_case'] == 'ARR減速'


# ── 賭け化：確認の目印(checkpoint) の API 対応（→ docs/thesis_capture_redesign.md） ──

def test_horizon_defaults_to_next_earnings(auth_settings, diary):
    """horizon 省略時の既定は next_earnings（UI と揃える）。"""
    resp = api_analysis.add_thesis(_post('CRWD', {'claim': 'x'}), 'CRWD')
    assert resp.status_code == 201
    thesis = Thesis.objects.get(id=json.loads(resp.content)['thesis_id'])
    assert thesis.horizon == 'next_earnings'


def test_checkpoint_autogenerates_claim(auth_settings, diary):
    """claim 省略でも checkpoint＋direction から主張が自動生成される（UI と同じ）。"""
    resp = api_analysis.add_thesis(_post('CRWD', {
        'checkpoint': '次決算の純新規ARR', 'checkpoint_direction': 'up',
    }), 'CRWD')
    assert resp.status_code == 201
    body = json.loads(resp.content)
    thesis = Thesis.objects.get(id=body['thesis_id'])
    assert thesis.checkpoint == '次決算の純新規ARR'
    assert thesis.checkpoint_direction == 'up'
    assert thesis.claim == '次決算の純新規ARRが上がる'


def test_claim_or_checkpoint_required(auth_settings, diary):
    """claim も checkpoint も無ければ 400。"""
    resp = api_analysis.add_thesis(_post('CRWD', {'basis': 'x'}), 'CRWD')
    assert resp.status_code == 400


def test_invalid_checkpoint_direction_rejected(auth_settings, diary):
    resp = api_analysis.add_thesis(_post('CRWD', {
        'checkpoint': 'x', 'checkpoint_direction': 'sideways',
    }), 'CRWD')
    assert resp.status_code == 400
    assert 'checkpoint_direction' in json.loads(resp.content)['error']


def test_checkpoint_appears_in_diary_detail(auth_settings, diary, monkeypatch):
    """checkpoint が diary_detail の theses に現れる（テンプレが参照できる）。"""
    monkeypatch.setattr(api_analysis, '_fetch_current_price', lambda s: None)
    monkeypatch.setattr(api_analysis, '_fetch_valuation', lambda s: None)
    api_analysis.add_thesis(_post('CRWD', {
        'checkpoint': '次決算の営業CF', 'checkpoint_direction': 'up',
    }), 'CRWD')
    req = RequestFactory().get('/api/analysis/diary/CRWD/', {'news': '0', 'margin': '0'},
                               HTTP_AUTHORIZATION='Bearer testkey')
    body = json.loads(api_analysis.diary_detail(req, 'CRWD').content)
    assert body['theses'][0]['checkpoint'] == '次決算の営業CF'
    assert body['theses'][0]['checkpoint_direction'] == '上がる'
