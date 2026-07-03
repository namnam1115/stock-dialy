"""分析API diary_detail の信用倍率（margin_tracking 連携）のテスト。

なぜこのテストがあるか:
  企業分析で信用需給（信用倍率＝買い残/売り残）を使えるよう、
  diary_detail のレスポンスに margin_tracking.MarginData の最新値＋
  直近トレンドを載せた。その連携が壊れないことを固定する。
"""
import json
from datetime import date

import pytest
from django.test import RequestFactory
from django.contrib.auth import get_user_model

from stockdiary import api_analysis
from stockdiary.models import StockDiary
from margin_tracking.models import MarginData

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def auth_settings(settings):
    settings.ANALYSIS_API_KEY = 'testkey'
    settings.ANALYSIS_API_USER = 'm_user'  # diary フィクスチャの所有者にスコープ
    return settings


@pytest.fixture
def diary(db):
    user = User.objects.create_user(username='m_user', password='p', email='m@example.com')
    return StockDiary.objects.create(
        user=user, stock_name='関電工', stock_symbol='1942', reason=''
    )


def _get(symbol, **params):
    params.setdefault('news', '0')  # ニュース取得（ネットワーク）を回避
    return RequestFactory().get(
        f'/api/analysis/diary/{symbol}/', params, HTTP_AUTHORIZATION='Bearer testkey'
    )


def test_diary_detail_includes_margin_latest_and_history(auth_settings, diary):
    """MarginData があれば margin.latest と margin.history を返す。"""
    MarginData.objects.create(
        record_date=date(2026, 6, 12), stock_code='1942',
        short_balance=100, long_balance=150,  # 倍率 1.50
    )
    MarginData.objects.create(
        record_date=date(2026, 6, 19), stock_code='1942',
        short_balance=100, long_balance=300,  # 倍率 3.00（買い残増＝上値の重し方向）
    )

    response = api_analysis.diary_detail(_get('1942'), '1942')
    assert response.status_code == 200
    body = json.loads(response.content)

    assert body['margin'] is not None
    assert body['margin']['latest']['date'] == '2026-06-19'
    assert body['margin']['latest']['margin_ratio'] == 3.00
    # history は古い→新しい順
    dates = [h['date'] for h in body['margin']['history']]
    assert dates == ['2026-06-12', '2026-06-19']


def test_diary_detail_margin_none_when_no_data(auth_settings, diary):
    """信用残データが無い銘柄では margin は None。"""
    response = api_analysis.diary_detail(_get('1942'), '1942')
    body = json.loads(response.content)
    assert body['margin'] is None


def test_margin_can_be_skipped(auth_settings, diary):
    """?margin=0 で信用残の取得をスキップする。"""
    MarginData.objects.create(
        record_date=date(2026, 6, 19), stock_code='1942',
        short_balance=100, long_balance=300,
    )
    response = api_analysis.diary_detail(_get('1942', margin='0'), '1942')
    body = json.loads(response.content)
    assert body['margin'] is None
