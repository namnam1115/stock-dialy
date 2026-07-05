"""
関連グラフの「関連度で間引く」ロジックのテスト。

- hub_weight: 逆頻度の重み（希少な関連ほど強い）
- get_tag_graph_data: エッジ・ハブへの weight / is_isolated 付与
- compute_related_strength: 希少性で重み付けした関連サマリー＋ノイズ除外
"""
import pytest

from stockdiary.models import StockDiary
from stockdiary.utils import (
    hub_weight,
    get_tag_graph_data,
    compute_related_strength,
)
from tags.models import Tag


def _diary(user, symbol, name, **kwargs):
    return StockDiary.objects.create(
        user=user, stock_symbol=symbol, stock_name=name, **kwargs
    )


class TestHubWeight:
    """逆頻度の重み付け（少数を結ぶハブほど強い）"""

    def test_rare_hub_is_strong(self):
        assert hub_weight(2) == 1.0
        assert hub_weight(3) == 0.5
        assert hub_weight(6) == 0.2

    def test_single_member_hub(self):
        assert hub_weight(1) == 1.0


@pytest.mark.django_db
class TestTagGraphWeights:
    def test_edges_and_nodes_carry_inverse_frequency_weight(self, user):
        tag = Tag.objects.create(user=user, name='半導体')
        d1 = _diary(user, '8035', '東京エレクトロン')
        d2 = _diary(user, '6857', 'アドバンテスト')
        d1.tags.add(tag)
        d2.tags.add(tag)

        qs = StockDiary.objects.filter(user=user).prefetch_related('tags')
        res = get_tag_graph_data(qs)

        node = next(n for n in res['tag_nodes'] if n['tag_name'] == '半導体')
        assert node['diary_count'] == 2
        assert node['weight'] == 1.0
        assert node['is_isolated'] is False
        assert all('weight' in e for e in res['edges'])

    def test_isolated_tag_flagged(self, user):
        tag = Tag.objects.create(user=user, name='単独')
        d = _diary(user, '9999', '単独銘柄')
        d.tags.add(tag)

        qs = StockDiary.objects.filter(user=user).prefetch_related('tags')
        node = get_tag_graph_data(qs)['tag_nodes'][0]
        assert node['diary_count'] == 1
        assert node['is_isolated'] is True


@pytest.mark.django_db
class TestTagGraphIsFlat:
    """関連グラフは「銘柄↔タグ」のフラット接続に統一する（TP1／R7）。

    タグ間の親子（Tag.parent）は語彙整理（タグ一覧の階層・tag_master 細分）には
    使うが、関連図の"接続"としては描かない。ハブ↔ハブの階層エッジ（tag_hierarchy）は
    接続を紛らわしくし振り返りの地図を読みにくくするため、実運用摩擦を起点に撤去した。
    このテストは「親子タグを両方使っても tag_hierarchy エッジが復活しない」ことを守る。
    """

    def test_no_hierarchy_edge_even_when_parent_and_child_both_used(self, user):
        """親タグ・子タグを両方の銘柄に付けても、ハブ↔ハブの親子エッジは出ない。"""
        parent = Tag.objects.create(user=user, name='エネルギー', axis='theme')
        child = Tag.objects.create(user=user, name='LNG', axis='theme', parent=parent)
        d1 = _diary(user, '1605', 'INPEX')
        d2 = _diary(user, '8058', '三菱商事')
        d1.tags.add(parent)
        d2.tags.add(child)

        qs = StockDiary.objects.filter(user=user).prefetch_related('tags')
        res = get_tag_graph_data(qs)

        # 親子エッジは一切生成しない
        assert not any(e['edge_type'] == 'tag_hierarchy' for e in res['edges'])
        # 一方で銘柄↔タグのフラット接続は従来どおり張られる
        tag_edges = [e for e in res['edges'] if e['edge_type'] == 'tag']
        assert {(e['source'], e['target']) for e in tag_edges} == {
            (d1.pk, f'tag_{parent.pk}'),
            (d2.pk, f'tag_{child.pk}'),
        }
        # ノードに親子の痕跡（parent_pk）も残さない
        assert all('parent_pk' not in n for n in res['tag_nodes'])

    def test_no_hierarchy_edge_between_unrelated_tags(self, user):
        t1 = Tag.objects.create(user=user, name='半導体', axis='theme')
        t2 = Tag.objects.create(user=user, name='脱炭素', axis='theme')
        d = _diary(user, '8035', '東京エレクトロン')
        d.tags.add(t1, t2)

        qs = StockDiary.objects.filter(user=user).prefetch_related('tags')
        res = get_tag_graph_data(qs)

        assert not any(e['edge_type'] == 'tag_hierarchy' for e in res['edges'])


@pytest.mark.django_db
class TestComputeRelatedStrength:
    def test_shared_rare_tag_links_diaries(self, user):
        tag1 = Tag.objects.create(user=user, name='半導体')
        tag2 = Tag.objects.create(user=user, name='製造装置')
        focal = _diary(user, '8035', '東京エレクトロン')
        other = _diary(user, '6857', 'アドバンテスト')
        # 3件目の無関係な日記を追加して N=3 にし、IDF = log(3/2) > 0 を確保
        _diary(user, '9999', '無関係銘柄')
        focal.tags.add(tag1, tag2)
        other.tags.add(tag1, tag2)

        res = compute_related_strength(focal, user)
        item = next(r for r in res if r['diary'].id == other.id)
        assert any(v['type'] == 'tag' for v in item['via'])
        assert item['score'] > 0

    def test_same_symbol_is_strong(self, user):
        focal = _diary(user, '7203', 'トヨタ自動車')
        other = _diary(user, '7203', 'トヨタ（旧メモ）')

        res = compute_related_strength(focal, user)
        item = next(r for r in res if r['diary'].id == other.id)
        assert any(v['type'] == 'symbol' for v in item['via'])

    def test_manual_link(self, user):
        focal = _diary(user, '1111', 'A社')
        other = _diary(user, '2222', 'B社')
        focal.linked_diaries.add(other)

        res = compute_related_strength(focal, user)
        item = next(r for r in res if r['diary'].id == other.id)
        assert any(v['type'] == 'manual' for v in item['via'])

    def test_mention_links_by_code(self, user):
        other = _diary(user, '7203', 'トヨタ自動車')
        focal = _diary(user, '7267', 'ホンダ', reason='競合の (7203) と比較')

        res = compute_related_strength(focal, user)
        item = next(r for r in res if r['diary'].id == other.id)
        assert any(v['type'] == 'mention' for v in item['via'])

    def test_excludes_other_users(self, user, another_user):
        tag = Tag.objects.create(user=user, name='共通テーマ')
        focal = _diary(user, '1', 'F社')
        focal.tags.add(tag)

        foreign_tag = Tag.objects.create(user=another_user, name='共通テーマ')
        foreign = _diary(another_user, '2', '他人の銘柄')
        foreign.tags.add(foreign_tag)

        res = compute_related_strength(focal, user)
        assert all(r['diary'].user_id == user.id for r in res)

    def test_overused_tag_is_filtered_as_noise(self, user, monkeypatch):
        # しきい値を 2 に下げ、3銘柄以上が共有するタグはノイズ扱いになることを確認
        monkeypatch.setattr('stockdiary.utils.RELATED_NOISE_MAX', 2)

        tag = Tag.objects.create(user=user, name='ありふれたタグ')
        focal = _diary(user, '500', 'F社')
        focal.tags.add(tag)

        d0 = _diary(user, '600', 'D0')
        d0.tags.add(tag)
        focal.linked_diaries.add(d0)  # 手動リンクで必ず出現させる
        d1 = _diary(user, '601', 'D1')
        d1.tags.add(tag)
        d2 = _diary(user, '602', 'D2')
        d2.tags.add(tag)

        res = compute_related_strength(focal, user)
        appearing = {r['diary'].id for r in res}

        # 手動リンクの d0 は出現するが、tag 経由ではない（付けすぎタグは除外）
        assert d0.id in appearing
        item = next(r for r in res if r['diary'].id == d0.id)
        assert all(v['type'] != 'tag' for v in item['via'])

        # タグのみで繋がる d1 / d2 はノイズ除外で出現しない
        assert d1.id not in appearing
        assert d2.id not in appearing

    def test_multidimensional_outranks_single(self, user):
        tag1 = Tag.objects.create(user=user, name='テーマ')
        tag2 = Tag.objects.create(user=user, name='成長株')
        focal = _diary(user, '9000', 'F社')
        focal.tags.add(tag1, tag2)

        weak = _diary(user, '9001', 'Weak社')
        weak.tags.add(tag1, tag2)  # MIN_SHARED_TAGS=2 を満たすため2タグ追加
        strong = _diary(user, '9002', 'Strong社')
        strong.tags.add(tag1, tag2)
        focal.linked_diaries.add(strong)  # strong は手動リンクも持つ

        res = compute_related_strength(focal, user)
        order = [r['diary'].id for r in res]
        assert order.index(strong.id) < order.index(weak.id)


@pytest.mark.django_db
class TestRelatedStrengthQueryBudget:
    """compute_related_strength のクエリ数が focal タグ数に比例しない回帰テスト。

    なぜこのテストがあるか:
    旧実装は focal タグ1つにつき1クエリ（others.filter(tags=tag)）を発行しており、
    theme_recall が概要タブに常設＝遅延ロード不可の detail ページで、タグ数ぶんの
    クエリが毎回走っていた。(tag_id, diary_id) を1クエリでまとめて取得する実装に
    変更したため、focal のタグ数を増やしてもクエリ総数が一定であることを固定する。
    """

    def _measure(self, owner, n_tags):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        focal = _diary(owner, '0001', 'Focal')
        for i in range(n_tags):
            tag = Tag.objects.create(user=owner, name=f'テーマ{i}', axis='theme')
            focal.tags.add(tag)
            other = _diary(owner, f'1{i:03d}', f'Other{i}')
            other.tags.add(tag)
        focal.refresh_from_db()
        with CaptureQueriesContext(connection) as ctx:
            compute_related_strength(focal, owner)
        return len(ctx.captured_queries)

    def test_query_count_does_not_grow_with_tag_count(self, user, another_user):
        # 別ユーザーで測ることで、片方の focal/other がもう片方のノイズにならないようにする
        q_few = self._measure(user, 2)
        q_many = self._measure(another_user, 12)
        # 旧実装ではタグ数差(10)ぶんクエリが増える。一定であることを固定する。
        assert q_many == q_few, f'tag数でクエリが増えている: few={q_few}, many={q_many}'
