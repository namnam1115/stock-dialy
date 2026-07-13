"""Phase 8c: 知識ライブラリ（学び/テーマ/仮説/時系列）のテスト"""
from datetime import timedelta

import pytest

from django.urls import reverse
from django.utils import timezone

from stockdiary.models import StockDiary, Thesis, Verdict, DiaryNote

pytestmark = pytest.mark.django_db(transaction=True)


def _learning(diary, text, hyp=Verdict.HYP_HIT, pnl=Verdict.PNL_PROFIT):
    t = Thesis.objects.create(diary=diary, claim='c')
    return Verdict.objects.create(thesis=t, hypothesis_result=hyp, pnl_result=pnl, learning=text)


class TestLibrary:
    def test_learning_axis_default(self, authenticated_client, user):
        d = StockDiary.objects.create(user=user, stock_name='日本郵船', stock_symbol='9101')
        _learning(d, '海運は運賃サイクルを読む')
        r = authenticated_client.get(reverse('stockdiary:library'))
        assert r.status_code == 200
        assert r.context['axis'] == 'learning'
        assert any(v.learning == '海運は運賃サイクルを読む' for v in r.context['learnings'])

    def test_learning_search_by_keyword(self, authenticated_client, user):
        d1 = StockDiary.objects.create(user=user, stock_name='日本郵船', stock_symbol='9101')
        d2 = StockDiary.objects.create(user=user, stock_name='トヨタ', stock_symbol='7203')
        _learning(d1, '海運は運賃サイクルを読む')
        _learning(d2, '為替単独を根拠にしない')
        r = authenticated_client.get(reverse('stockdiary:library'), {'q': '海運'})
        texts = [v.learning for v in r.context['learnings']]
        assert '海運は運賃サイクルを読む' in texts
        assert '為替単独を根拠にしない' not in texts

    def test_thesis_axis_groups(self, authenticated_client, user):
        d1 = StockDiary.objects.create(user=user, stock_name='A', stock_symbol='1')
        d2 = StockDiary.objects.create(user=user, stock_name='B', stock_symbol='2')
        Thesis.objects.create(diary=d1, claim='未検証の主張')  # open
        _learning(d2, '的中の学び', hyp=Verdict.HYP_HIT)       # hit
        r = authenticated_client.get(reverse('stockdiary:library'), {'axis': 'thesis'})
        assert r.status_code == 200
        assert len(r.context['open_theses']) == 1
        assert len(r.context['hit_verdicts']) == 1

    def test_theme_axis(self, authenticated_client, user, sample_tags):
        d = StockDiary.objects.create(user=user, stock_name='A', stock_symbol='1')
        d.tags.add(sample_tags[0])
        r = authenticated_client.get(reverse('stockdiary:library'), {'axis': 'theme'})
        assert r.status_code == 200
        assert any(t.n >= 1 for t in r.context['theme_rows'])

    def test_thesis_split_due_and_live(self, authenticated_client, user):
        """ZIP の LibraryZone に合わせ、未検証の仮説を「答え合わせ待ち（検証予定日が来た）」と
        「生きている（期日前）」に分ける。検証予定日の有無で振り分けられること。"""
        d = StockDiary.objects.create(user=user, stock_name='A', stock_symbol='1')
        today = timezone.localdate()
        Thesis.objects.create(diary=d, claim='期日が来た', review_due_date=today)             # due
        Thesis.objects.create(diary=d, claim='まだ先', review_due_date=today + timedelta(days=30))  # live
        r = authenticated_client.get(reverse('stockdiary:library'), {'axis': 'thesis'})
        assert r.status_code == 200
        assert [t.claim for t in r.context['due_theses']] == ['期日が来た']
        assert [t.claim for t in r.context['live_theses']] == ['まだ先']
        # 後方互換キー（未検証全体）も維持
        assert len(r.context['open_theses']) == 2

    def test_lens_counts_present(self, authenticated_client, user):
        r = authenticated_client.get(reverse('stockdiary:library'))
        assert r.status_code == 200
        counts = r.context['counts']
        for key in ('learning', 'theme', 'thesis', 'time'):
            assert key in counts

    def test_time_axis_lists_notes(self, authenticated_client, user):
        d = StockDiary.objects.create(user=user, stock_name='A', stock_symbol='1')
        DiaryNote.objects.create(diary=d, date=timezone.localdate(),
                                 note_type='insight', topic='気づき', content='本文')
        r = authenticated_client.get(reverse('stockdiary:library'), {'axis': 'time'})
        assert r.status_code == 200
        assert r.context['axis'] == 'time'
        assert len(r.context['timeline']) == 1
        assert r.context['timeline'][0]['stock'] == 'A'

    def test_htmx_request_returns_partial_only(self, authenticated_client, user):
        """レンズタブ切替のたびにフルページを再読込していた問題の回帰テスト。
        HTMXリクエスト時は #lib-content の断片のみを返し、base.html のレイアウト
        （ヘッダー・見出し等）を含めない。通常リクエストはフルページを返す。"""
        r_full = authenticated_client.get(reverse('stockdiary:library'), {'axis': 'theme'})
        assert r_full.status_code == 200
        assert b'id="lib-content"' in r_full.content
        assert '知識ライブラリ'.encode() in r_full.content

        r_htmx = authenticated_client.get(
            reverse('stockdiary:library'), {'axis': 'theme'},
            HTTP_HX_REQUEST='true',
        )
        assert r_htmx.status_code == 200
        assert b'id="lib-content"' in r_htmx.content
        assert b'<html' not in r_htmx.content
        assert '知識ライブラリ'.encode() not in r_htmx.content
