"""意思決定の質の指標セマンティックレイヤー（services.metrics）のテスト。

なぜこのテストがあるか:
「何を的中とみなすか」「何を勝ちとみなすか」「どの象限か」「得意/苦手の閾値」と
いった指標の定義が、従来 Verdict モデルと karte_service に分散・重複していた。
services.metrics を唯一の正本に集約したので、(1) 純粋関数・タクソノミが意図通りで
あること、(2) Verdict モデルがこの正本を参照し定義がドリフトしないこと、を固定する。
"""
import pytest

from stockdiary.services import metrics
from stockdiary.models import Verdict


class TestMetricsPure:
    """DB 不要の純粋関数・定数のテスト。"""

    @pytest.mark.parametrize('result,expected', [
        ('hit', True),
        ('partial', True),
        ('miss', False),
        ('unknown', False),
    ])
    def test_is_hypothesis_hit(self, result, expected):
        assert metrics.is_hypothesis_hit(result) is expected

    @pytest.mark.parametrize('result,expected', [
        ('profit', True),
        ('loss', False),
        ('flat', False),
        ('holding', False),
    ])
    def test_is_pnl_win(self, result, expected):
        assert metrics.is_pnl_win(result) is expected

    @pytest.mark.parametrize('hyp_ok,pnl_ok,quad', [
        (True, True, 'skill'),
        (True, False, 'unlucky'),
        (False, True, 'lucky'),
        (False, False, 'discipline'),
    ])
    def test_quadrant_of(self, hyp_ok, pnl_ok, quad):
        assert metrics.quadrant_of(hyp_ok, pnl_ok) == quad

    def test_quadrant_labels_derived_from_taxonomy(self):
        """QUADRANT_LABELS は QUADRANTS から導出され、重複定義を持たない。"""
        assert metrics.QUADRANT_LABELS == {k: label for k, label, _ in metrics.QUADRANTS}
        # quadrant_of が返す全 key がラベルを持つ（取りこぼしがない）
        for hyp_ok in (True, False):
            for pnl_ok in (True, False):
                assert metrics.quadrant_of(hyp_ok, pnl_ok) in metrics.QUADRANT_LABELS

    def test_thresholds_are_sane(self):
        assert metrics.HIT_RATE_WEAK < metrics.HIT_RATE_STRONG
        assert metrics.THEME_MIN_VERDICTS >= 1
        assert 0 < metrics.MISS_SIMILARITY_THRESHOLD <= 1

    def test_is_similar_miss(self):
        assert metrics.is_similar_miss('入るのが早い', '入るのが早い') is True
        assert metrics.is_similar_miss('決算前に手仕舞いできず', '決算前に利確できず') is True
        assert metrics.is_similar_miss('入るのが早い', '為替の影響を過小評価していた') is False


@pytest.mark.django_db(transaction=True)
class TestVerdictConsumesMetrics:
    """Verdict モデルがセマンティックレイヤーを参照し、定義がドリフトしないこと。"""

    def _verdict(self, sample_diary, hyp, pnl):
        from stockdiary.models import Thesis
        thesis = Thesis.objects.create(diary=sample_diary, claim='テスト主張')
        return Verdict.objects.create(thesis=thesis, hypothesis_result=hyp, pnl_result=pnl)

    @pytest.mark.parametrize('hyp,pnl,quad', [
        (Verdict.HYP_HIT, Verdict.PNL_PROFIT, 'skill'),
        (Verdict.HYP_PARTIAL, Verdict.PNL_LOSS, 'unlucky'),
        (Verdict.HYP_MISS, Verdict.PNL_PROFIT, 'lucky'),
        (Verdict.HYP_MISS, Verdict.PNL_LOSS, 'discipline'),
    ])
    def test_verdict_quadrant_matches_metrics(self, sample_diary, hyp, pnl, quad):
        v = self._verdict(sample_diary, hyp, pnl)
        assert v.quadrant == quad
        # モデルのラベルが正本のラベルと一致（モデル側に再ハードコードがないこと）
        assert v.quadrant_label == metrics.QUADRANT_LABELS[quad]

    def test_verdict_hyp_and_pnl_ok_delegate_to_metrics(self, sample_diary):
        v = self._verdict(sample_diary, Verdict.HYP_PARTIAL, Verdict.PNL_FLAT)
        assert v.hyp_ok is metrics.is_hypothesis_hit(Verdict.HYP_PARTIAL)
        assert v.pnl_ok is metrics.is_pnl_win(Verdict.PNL_FLAT)
