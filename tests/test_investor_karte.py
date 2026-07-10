"""Phase 8d: 投資家カルテ（自己理解）のテスト"""
import pytest

from django.urls import reverse

from stockdiary.models import StockDiary, Thesis, Verdict
from stockdiary.services.karte_service import build_investor_karte

pytestmark = pytest.mark.django_db(transaction=True)


def _verdict(diary, hyp, pnl, **kw):
    t = Thesis.objects.create(diary=diary, claim='c')
    return Verdict.objects.create(thesis=t, hypothesis_result=hyp, pnl_result=pnl, **kw)


class TestKarteService:
    def test_empty(self, user):
        k = build_investor_karte(user)
        assert k['has_content'] is False
        assert k['total'] == 0

    def test_quadrant_distribution_and_lucky_share(self, user):
        d1 = StockDiary.objects.create(user=user, stock_name='A', stock_symbol='1')
        d2 = StockDiary.objects.create(user=user, stock_name='B', stock_symbol='2')
        # 仮説外れ×利益（偶然の勝ち） + 仮説的中×利益（実力）
        _verdict(d1, Verdict.HYP_MISS, Verdict.PNL_PROFIT)
        _verdict(d2, Verdict.HYP_HIT, Verdict.PNL_PROFIT)
        k = build_investor_karte(user)
        counts = {q['key']: q['count'] for q in k['quadrants']}
        assert counts['lucky'] == 1
        assert counts['skill'] == 1
        assert k['total_wins'] == 2
        assert k['lucky_share'] == 50  # 勝ち2件のうち1件が偶然

    def test_recent_misses_include_source_and_similar_entries(self, user):
        """自然言語の自由記述は完全一致でしか数えられない（Counter方式は実運用でほぼ
        発火しない）ため、正確な回数を主張するのをやめ、表記が近い過去の見落としが
        あれば「どの銘柄の・いつの記録か」ごと similar に含めて実際に見られるようにした
        （フラグだけでは何と似ているのか確認できないというフィードバックを受けて変更）。"""
        d1 = StockDiary.objects.create(user=user, stock_name='A', stock_symbol='1')
        d2 = StockDiary.objects.create(user=user, stock_name='B', stock_symbol='2')
        _verdict(d1, Verdict.HYP_MISS, Verdict.PNL_LOSS, missed_factor='入るのが早い')
        _verdict(d2, Verdict.HYP_MISS, Verdict.PNL_LOSS, missed_factor='入るのが早い')
        k = build_investor_karte(user)
        assert k['recent_misses']
        top = k['recent_misses'][0]
        assert top['text'] == '入るのが早い'
        assert top['diary'].id == d2.id  # 検証は新しい順
        assert top['date'] is not None
        assert len(top['similar']) == 1
        assert top['similar'][0]['text'] == '入るのが早い'
        assert top['similar'][0]['diary'].id == d1.id

    def test_recent_misses_not_similar_when_unrelated(self, user):
        d1 = StockDiary.objects.create(user=user, stock_name='A', stock_symbol='1')
        d2 = StockDiary.objects.create(user=user, stock_name='B', stock_symbol='2')
        _verdict(d1, Verdict.HYP_MISS, Verdict.PNL_LOSS, missed_factor='入るのが早い')
        _verdict(d2, Verdict.HYP_MISS, Verdict.PNL_LOSS, missed_factor='為替の影響を過小評価していた')
        k = build_investor_karte(user)
        assert all(not m['similar'] for m in k['recent_misses'])

    def test_philosophy_prefers_repeatable(self, user):
        d1 = StockDiary.objects.create(user=user, stock_name='A', stock_symbol='1')
        _verdict(d1, Verdict.HYP_HIT, Verdict.PNL_PROFIT,
                 learning='テーマで持ち続ける', is_repeatable=True)
        k = build_investor_karte(user)
        assert any(p['text'] == 'テーマで持ち続ける' for p in k['philosophy'])

    def test_theme_rows_have_stars_and_bar(self, user, sample_tags):
        """参考デザインのテーマバー（★＝判断の質、bar_pct＝幅）用の集計が出ること。"""
        d1 = StockDiary.objects.create(user=user, stock_name='A', stock_symbol='1')
        d2 = StockDiary.objects.create(user=user, stock_name='B', stock_symbol='2')
        for d in (d1, d2):
            t = Thesis.objects.create(diary=d, claim='c')
            t.basis_tags.add(sample_tags[0])
            Verdict.objects.create(thesis=t, hypothesis_result=Verdict.HYP_HIT,
                                   pnl_result=Verdict.PNL_PROFIT, decision_quality=4)
        k = build_investor_karte(user)
        strong = k['strong_themes']
        assert strong, '的中率が高いタグは得意テーマに入る'
        assert strong[0]['stars'] == 4.0
        assert 6 <= strong[0]['bar_pct'] <= 100

    def test_diagnosis_is_built(self, user):
        """検証があれば、言葉で返す自己診断の一文が組み立てられること。"""
        d1 = StockDiary.objects.create(user=user, stock_name='A', stock_symbol='1')
        _verdict(d1, Verdict.HYP_HIT, Verdict.PNL_PROFIT, decision_quality=4)
        k = build_investor_karte(user)
        assert k['diagnosis']
        assert isinstance(k['diagnosis'][0], str)


class TestGrowthTrajectory:
    """成長の軌跡グラフ用データの回帰テスト（軸2）。

    なぜこのテストを足したか:
    _build_growth_trajectory() は月別の判断の質スコアと仮説的中率を集計して
    Chart.js に渡す形式で返す。2ヶ月未満は has_chart=False にして
    graceful degradation する設計を固定する。
    """

    def test_growth_key_exists_in_karte(self, user):
        """build_investor_karte() の返り値に growth キーが含まれる。"""
        k = build_investor_karte(user)
        assert 'growth' in k

    def test_empty_karte_has_no_chart(self, user):
        """Verdict が0件なら has_chart=False。"""
        k = build_investor_karte(user)
        assert k['growth']['has_chart'] is False
        assert k['growth']['total_learnings'] == 0

    def test_single_verdict_no_chart(self, user):
        """Verdict が1件（1ヶ月分のみ）なら has_chart=False。"""
        d = StockDiary.objects.create(user=user, stock_name='A', stock_symbol='1')
        _verdict(d, Verdict.HYP_HIT, Verdict.PNL_PROFIT, decision_quality=4)
        k = build_investor_karte(user)
        assert k['growth']['has_chart'] is False
        assert k['growth']['latest_quality'] == 4.0
        assert k['growth']['latest_hit_rate'] == 100

    def test_two_months_enables_chart(self, user):
        """異なる月に Verdict が存在すれば has_chart=True になる。"""
        from django.utils import timezone
        import datetime

        d = StockDiary.objects.create(user=user, stock_name='A', stock_symbol='1')

        # 2件の Verdict を異なる月の created_at で作る
        v1 = _verdict(d, Verdict.HYP_HIT, Verdict.PNL_PROFIT, decision_quality=3)
        v2 = _verdict(d, Verdict.HYP_MISS, Verdict.PNL_LOSS, decision_quality=4)

        # created_at を直接書き換えて別月にする
        two_months_ago = timezone.now() - datetime.timedelta(days=62)
        Verdict.objects.filter(pk=v1.pk).update(created_at=two_months_ago)

        k = build_investor_karte(user)
        g = k['growth']
        assert g['has_chart'] is True
        assert len(g['labels']) == 2
        assert len(g['quality']) == 2
        assert len(g['hit_rates']) == 2

    def test_learning_count_accumulated(self, user):
        """learning フィールドが入力された Verdict の数が total_learnings に反映される。"""
        d = StockDiary.objects.create(user=user, stock_name='A', stock_symbol='1')
        _verdict(d, Verdict.HYP_HIT, Verdict.PNL_PROFIT, learning='学びA')
        _verdict(d, Verdict.HYP_MISS, Verdict.PNL_LOSS)  # learning なし
        _verdict(d, Verdict.HYP_HIT, Verdict.PNL_PROFIT, learning='学びB')
        k = build_investor_karte(user)
        assert k['growth']['total_learnings'] == 2


class TestKarteView:
    def test_karte_renders(self, authenticated_client):
        r = authenticated_client.get(reverse('stockdiary:investor_karte'))
        assert r.status_code == 200
        assert 'karte' in r.context
