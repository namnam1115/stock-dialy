"""検索結果のタイトル優先ソートのテスト（apply_diary_filters）。

なぜこのテストがあるか:
  全文検索は銘柄名・コードに加え本文・ノート・見立ての変遷も対象のため、
  既定ソート（-updated_at）のままだと「本文だけ一致した最近更新の日記」が
  銘柄名一致より上に来てしまい、関連性の低い結果が最上位に出ていた。
  検索語があり明示ソートが無いときは、タイトル（銘柄名・コード）一致を
  第1キーに前置するよう修正した。その並び順を固定する。
"""
from datetime import date, timedelta

import pytest
from django.utils import timezone
from django.contrib.auth import get_user_model

from stockdiary.models import StockDiary, DiaryNote
from stockdiary.utils import apply_diary_filters

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def user(db):
    return User.objects.create_user(username='search_user', password='p', email='s@example.com')


def _diary(user, name, symbol):
    return StockDiary.objects.create(user=user, stock_name=name, stock_symbol=symbol, reason='')


def test_title_match_ranks_above_body_only_match(user):
    """銘柄名一致は、本文だけ一致した最近更新の日記より上に来る。"""
    toyota = _diary(user, 'トヨタ自動車', '7203')           # タイトル一致
    nissan = _diary(user, '日産自動車', '7201')             # 本文のみ一致
    DiaryNote.objects.create(
        diary=nissan, content='競合のトヨタの動向をメモ', topic='',
        note_type='analysis', date=date(2026, 7, 1),
    )
    # 本文一致側(nissan)をより新しく更新 → 修正前は -updated_at で上に来ていた
    StockDiary.objects.filter(pk=nissan.pk).update(updated_at=timezone.now())
    StockDiary.objects.filter(pk=toyota.pk).update(updated_at=timezone.now() - timedelta(days=5))

    qs = apply_diary_filters(StockDiary.objects.filter(user=user), {'query': 'トヨタ'}, user)
    results = list(qs)

    assert toyota in results and nissan in results
    assert results[0] == toyota  # タイトル一致が最上位


def test_symbol_match_also_prioritized(user):
    """銘柄コード一致もタイトル優先の対象。"""
    hit = _diary(user, 'アドバンテスト', '6857')
    other = _diary(user, 'トヨタ自動車', '7203')
    DiaryNote.objects.create(
        diary=other, content='6857 についての言及', topic='',
        note_type='analysis', date=date(2026, 7, 1),
    )
    StockDiary.objects.filter(pk=other.pk).update(updated_at=timezone.now())
    StockDiary.objects.filter(pk=hit.pk).update(updated_at=timezone.now() - timedelta(days=5))

    qs = apply_diary_filters(StockDiary.objects.filter(user=user), {'query': '6857'}, user)
    assert list(qs)[0] == hit


def test_explicit_sort_is_respected(user):
    """ユーザーが明示ソート(name)を選んだ場合はタイトル優先を挟まない。"""
    a = _diary(user, 'あ銘柄', '1111')
    z = _diary(user, 'ん銘柄', '9999')
    # 両方タイトル一致する共通語で検索しつつ name 昇順を指定
    for d in (a, z):
        d.reason = '共通キーワード'
        d.save()
    qs = apply_diary_filters(
        StockDiary.objects.filter(user=user), {'query': '共通キーワード', 'sort': 'name'}, user
    )
    results = list(qs)
    # name 昇順（あ→ん）が保たれる
    assert results == [a, z]
