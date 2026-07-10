"""seed_library_demo 管理コマンドのテスト"""
import pytest

from django.core.management import call_command

from stockdiary.models import Thesis, Verdict
from stockdiary.services.karte_service import build_investor_karte

pytestmark = pytest.mark.django_db(transaction=True)


def test_seed_populates_thesis_verdict_and_karte(user):
    call_command('seed_library_demo', '--username', user.username)
    assert Thesis.objects.filter(diary__user=user).count() == 11
    assert Verdict.objects.filter(thesis__diary__user=user).count() == 9
    # 未検証（答え合わせ待ち含む）が2件
    assert Thesis.objects.filter(diary__user=user, status=Thesis.STATUS_OPEN).count() == 2

    karte = build_investor_karte(user)
    assert karte['has_content']
    # 4象限すべてに分布
    counts = {q['key']: q['count'] for q in karte['quadrants']}
    assert all(counts[k] >= 1 for k in ('skill', 'unlucky', 'lucky', 'discipline'))
    # 繰り返す見落とし（同一文言が2回→似た内容として拾われる）
    assert any(m['text'] == '入るのが早い' and m['similar'] for m in karte['recent_misses'])


def test_seed_handles_duplicate_symbol_diaries(user):
    # 同一銘柄コードの日記が2件あっても MultipleObjectsReturned で落ちない
    from stockdiary.models import StockDiary, Thesis
    StockDiary.objects.create(user=user, stock_symbol='7203', stock_name='トヨタ自動車(1)')
    StockDiary.objects.create(user=user, stock_symbol='7203', stock_name='トヨタ自動車(2)')
    call_command('seed_library_demo', '--username', user.username)
    # 7203 には仮説が1件だけ付く（先頭の日記に）
    assert Thesis.objects.filter(diary__user=user, diary__stock_symbol='7203').count() == 1


def test_seed_is_idempotent_with_clear(user):
    call_command('seed_library_demo', '--username', user.username)
    call_command('seed_library_demo', '--username', user.username, '--clear')
    # 二重投入されない
    assert Thesis.objects.filter(diary__user=user).count() == 11
    assert Verdict.objects.filter(thesis__diary__user=user).count() == 9
