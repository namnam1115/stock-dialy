"""分析API list_diaries（スクリーニング）のテスト。

なぜこのテストがあるか:
  「記録銘柄（保有/売却/メモ横断）からタグ・業種で買い時候補を探す」ため、
  全日記を列挙し信用倍率を付与する list_diaries を追加した。
  絞り込み（tags/status）と margin 付与が壊れないことを固定する。
"""
import json
from datetime import date

import pytest
from django.test import RequestFactory
from django.contrib.auth import get_user_model

from stockdiary import api_analysis
from stockdiary.models import StockDiary, Transaction
from tags.models import Tag
from margin_tracking.models import MarginData

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def auth_settings(settings):
    settings.ANALYSIS_API_KEY = 'testkey'
    settings.ANALYSIS_API_USER = 'scr_user'  # diary フィクスチャの所有者にスコープ
    return settings


@pytest.fixture
def user(db):
    return User.objects.create_user(username='scr_user', password='p', email='s@example.com')


def _mk(user, symbol, name, tag_names=()):
    d = StockDiary.objects.create(user=user, stock_name=name, stock_symbol=symbol, reason='')
    for t in tag_names:
        tag, _ = Tag.objects.get_or_create(user=user, name=t)
        d.tags.add(tag)
    return d


def _get(**params):
    return RequestFactory().get(
        '/api/analysis/diaries/', params, HTTP_AUTHORIZATION='Bearer testkey'
    )


def test_list_all_diaries_with_margin(auth_settings, user):
    _mk(user, '6857', 'アドバンテスト', ['半導体', 'AI'])
    _mk(user, '8035', '東京エレクトロン', ['半導体'])
    _mk(user, '7203', 'トヨタ自動車', ['世界トップ'])
    MarginData.objects.create(
        record_date=date(2026, 6, 19), stock_code='6857', short_balance=100, long_balance=274
    )

    response = api_analysis.list_diaries(_get())
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body['count'] == 3
    by_symbol = {d['symbol']: d for d in body['diaries']}
    assert by_symbol['6857']['margin_ratio'] == 2.74
    assert by_symbol['8035']['margin_ratio'] is None  # データ無し
    assert by_symbol['6857']['status'] == 'メモ'


def test_filter_by_tags_or(auth_settings, user):
    _mk(user, '6857', 'アドバンテスト', ['半導体', 'AI'])
    _mk(user, '7203', 'トヨタ自動車', ['世界トップ'])
    _mk(user, '1942', '関電工', ['データセンター', 'AI'])

    body = json.loads(api_analysis.list_diaries(_get(tags='半導体,AI')).content)
    symbols = {d['symbol'] for d in body['diaries']}
    assert symbols == {'6857', '1942'}  # 半導体 or AI を持つもの（OR）


def test_filter_by_status(auth_settings, user):
    memo = _mk(user, '6857', 'アドバンテスト')
    held = _mk(user, '8035', '東京エレクトロン')
    held.current_quantity = 100
    held.transaction_count = 1
    held.save()

    body = json.loads(api_analysis.list_diaries(_get(status='holding')).content)
    assert {d['symbol'] for d in body['diaries']} == {'8035'}

    body = json.loads(api_analysis.list_diaries(_get(status='memo')).content)
    assert {d['symbol'] for d in body['diaries']} == {'6857'}
