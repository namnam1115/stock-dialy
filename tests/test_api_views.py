"""stockdiary/api_views.py の主要エンドポイントテスト。

関連日記検索・追加・削除、ハッシュタグ検索、自分の日記検索、
グラフデータ API を優先的にカバーする（従来カバレッジ 15%）。
"""
import pytest
import json
from django.urls import reverse
from django.contrib.auth import get_user_model
from decimal import Decimal
from datetime import date

from stockdiary.models import StockDiary, DiaryNote, Transaction, NotificationLog, DiaryNotification

User = get_user_model()

pytestmark = pytest.mark.django_db(transaction=True)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def user(db):
    return User.objects.create_user(
        username='apiv_user', password='pass', email='apiv@example.com'
    )


@pytest.fixture
def other_user(db):
    return User.objects.create_user(
        username='apiv_other', password='pass', email='apiv_other@example.com'
    )


@pytest.fixture
def diary(user):
    return StockDiary.objects.create(
        user=user, stock_symbol='7203', stock_name='トヨタ自動車', reason='長期保有'
    )


@pytest.fixture
def diary2(user):
    return StockDiary.objects.create(
        user=user, stock_symbol='9984', stock_name='ソフトバンクG', reason='成長期待'
    )


@pytest.fixture
def authed_client(client, user):
    client.login(username='apiv_user', password='pass')
    return client


# ---------------------------------------------------------------------------
# search_related_diaries
# ---------------------------------------------------------------------------

class TestSearchRelatedDiaries:
    """関連日記候補の検索 API テスト。"""

    def test_returns_matching_diaries(self, authed_client, diary, diary2):
        """クエリに一致する自分の日記を返す。"""
        url = reverse('stockdiary:api_related_search', args=[diary.id])
        resp = authed_client.get(url, {'q': 'ソフトバンク'})
        assert resp.status_code == 200
        data = resp.json()
        ids = [d['id'] for d in data['diaries']]
        assert diary2.id in ids

    def test_excludes_self(self, authed_client, diary):
        """自分自身の日記は候補に含まれない。"""
        url = reverse('stockdiary:api_related_search', args=[diary.id])
        resp = authed_client.get(url, {'q': 'トヨタ'})
        data = resp.json()
        ids = [d['id'] for d in data['diaries']]
        assert diary.id not in ids

    def test_excludes_already_linked(self, authed_client, diary, diary2):
        """既に関連付け済みの日記は候補に含まれない。"""
        diary.linked_diaries.add(diary2)
        url = reverse('stockdiary:api_related_search', args=[diary.id])
        resp = authed_client.get(url, {'q': 'ソフトバンク'})
        data = resp.json()
        ids = [d['id'] for d in data['diaries']]
        assert diary2.id not in ids

    def test_empty_query_returns_empty(self, authed_client, diary):
        """クエリが空のとき空リストを返す。"""
        url = reverse('stockdiary:api_related_search', args=[diary.id])
        resp = authed_client.get(url, {'q': ''})
        assert resp.json() == {'diaries': []}

    def test_other_users_diary_not_returned(self, authed_client, diary, other_user):
        """他ユーザーの日記は候補に含まれない。"""
        other_diary = StockDiary.objects.create(
            user=other_user, stock_symbol='7203', stock_name='トヨタ'
        )
        url = reverse('stockdiary:api_related_search', args=[diary.id])
        resp = authed_client.get(url, {'q': 'トヨタ'})
        ids = [d['id'] for d in resp.json()['diaries']]
        assert other_diary.id not in ids

    def test_requires_login(self, client, diary):
        url = reverse('stockdiary:api_related_search', args=[diary.id])
        resp = client.get(url, {'q': 'トヨタ'})
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# add_related_diary
# ---------------------------------------------------------------------------

class TestAddRelatedDiary:
    """関連日記追加 API テスト（双方向リンク）。"""

    def test_adds_bidirectional_link(self, authed_client, diary, diary2):
        """追加すると双方向にリンクが張られる。"""
        url = reverse('stockdiary:api_related_add', args=[diary.id])
        resp = authed_client.post(
            url,
            data=json.dumps({'related_id': diary2.id}),
            content_type='application/json',
        )
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        # 双方向確認
        assert diary.linked_diaries.filter(id=diary2.id).exists()
        assert diary2.linked_diaries.filter(id=diary.id).exists()

    def test_returns_400_for_self_link(self, authed_client, diary):
        """自分自身を関連付けしようとすると 400 を返す。"""
        url = reverse('stockdiary:api_related_add', args=[diary.id])
        resp = authed_client.post(
            url,
            data=json.dumps({'related_id': diary.id}),
            content_type='application/json',
        )
        assert resp.status_code == 400

    def test_returns_400_for_invalid_body(self, authed_client, diary):
        """不正な JSON は 400 を返す。"""
        url = reverse('stockdiary:api_related_add', args=[diary.id])
        resp = authed_client.post(url, data='not json', content_type='application/json')
        assert resp.status_code == 400

    def test_requires_login(self, client, diary, diary2):
        url = reverse('stockdiary:api_related_add', args=[diary.id])
        resp = client.post(
            url,
            data=json.dumps({'related_id': diary2.id}),
            content_type='application/json',
        )
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# remove_related_diary
# ---------------------------------------------------------------------------

class TestRemoveRelatedDiary:
    """関連日記削除 API テスト（双方向解除）。"""

    def test_removes_bidirectional_link(self, authed_client, diary, diary2):
        """解除すると双方向のリンクが消える。"""
        diary.linked_diaries.add(diary2)
        diary2.linked_diaries.add(diary)

        url = reverse('stockdiary:api_related_remove', args=[diary.id, diary2.id])
        resp = authed_client.post(url)
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        assert not diary.linked_diaries.filter(id=diary2.id).exists()
        assert not diary2.linked_diaries.filter(id=diary.id).exists()

    def test_requires_login(self, client, diary, diary2):
        url = reverse('stockdiary:api_related_remove', args=[diary.id, diary2.id])
        resp = client.post(url)
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# search_my_diaries
# ---------------------------------------------------------------------------

class TestSearchMyDiaries:
    """自分の日記検索 API テスト（FAB クイック記録用）。"""

    def test_returns_all_when_no_query(self, authed_client, diary, diary2):
        """クエリなしのとき最近の日記を返す。"""
        url = reverse('stockdiary:api_search_my_diaries')
        resp = authed_client.get(url)
        assert resp.status_code == 200
        data = resp.json()
        assert 'diaries' in data
        ids = [d['id'] for d in data['diaries']]
        assert diary.id in ids
        assert diary2.id in ids

    def test_filters_by_query(self, authed_client, diary, diary2):
        """クエリに一致する日記のみ返す。"""
        url = reverse('stockdiary:api_search_my_diaries')
        resp = authed_client.get(url, {'q': 'ソフトバンク'})
        data = resp.json()
        ids = [d['id'] for d in data['diaries']]
        assert diary2.id in ids
        assert diary.id not in ids

    def test_does_not_return_other_users_diaries(self, authed_client, other_user):
        """他ユーザーの日記は返さない。"""
        other_diary = StockDiary.objects.create(
            user=other_user, stock_symbol='8001', stock_name='伊藤忠商事'
        )
        url = reverse('stockdiary:api_search_my_diaries')
        resp = authed_client.get(url, {'q': '伊藤忠'})
        ids = [d['id'] for d in resp.json()['diaries']]
        assert other_diary.id not in ids

    def test_requires_login(self, client):
        url = reverse('stockdiary:api_search_my_diaries')
        resp = client.get(url)
        assert resp.status_code == 302

    def test_response_includes_topics_field(self, authed_client, diary):
        """レスポンスに topics フィールドが含まれる。"""
        DiaryNote.objects.create(
            diary=diary, date=date.today(), content='テスト', topic='円安動向'
        )
        url = reverse('stockdiary:api_search_my_diaries')
        resp = authed_client.get(url, {'q': 'トヨタ'})
        entry = next(d for d in resp.json()['diaries'] if d['id'] == diary.id)
        assert 'topics' in entry
        assert '円安動向' in entry['topics']


# ---------------------------------------------------------------------------
# get_hashtags
# ---------------------------------------------------------------------------

class TestGetHashtags:
    """ハッシュタグ補完 API テスト。"""

    def test_returns_200(self, authed_client):
        url = reverse('stockdiary:api_hashtags')
        resp = authed_client.get(url)
        assert resp.status_code == 200
        assert 'hashtags' in resp.json()

    def test_requires_login(self, client):
        url = reverse('stockdiary:api_hashtags')
        resp = client.get(url)
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# diary_graph_data
# ---------------------------------------------------------------------------

class TestDiaryGraphData:
    """日記関連グラフデータ API テスト。"""

    def test_returns_nodes_and_edges(self, authed_client, diary):
        url = reverse('stockdiary:api_diary_graph_data')
        resp = authed_client.get(url)
        assert resp.status_code == 200
        data = resp.json()
        assert 'nodes' in data
        assert 'edges' in data

    def test_status_filter_holding(self, authed_client, diary):
        """status=holding でも 200 を返す（クラッシュしない）。"""
        url = reverse('stockdiary:api_diary_graph_data')
        resp = authed_client.get(url, {'status': 'holding'})
        assert resp.status_code == 200

    def test_edge_mode_manual(self, authed_client, diary, diary2):
        """edge_modes=manual でリンクノードとエッジが返る。"""
        diary.linked_diaries.add(diary2)
        url = reverse('stockdiary:api_diary_graph_data')
        resp = authed_client.get(url, {'edge_modes': 'manual'})
        assert resp.status_code == 200

    def test_requires_login(self, client):
        url = reverse('stockdiary:api_diary_graph_data')
        resp = client.get(url)
        assert resp.status_code == 302

    def test_empty_state_does_not_crash(self, authed_client):
        """日記が1件もなくてもクラッシュしない。"""
        url = reverse('stockdiary:api_diary_graph_data')
        resp = authed_client.get(url)
        assert resp.status_code == 200

    def test_no_tag_hierarchy_edge_through_api(self, authed_client, user):
        """親子タグを両方使っても、API はタグ間の親子エッジ(tag_hierarchy)を返さない。

        R7（TP1）でグラフを「銘柄↔タグ」のフラット接続に統一し、ハブ↔ハブの
        親子エッジを撤去した。語彙の親子は残るが、関連図の接続としては描かない。
        """
        from tags.models import Tag
        parent = Tag.objects.create(user=user, name='エネルギー', axis='theme')
        child = Tag.objects.create(user=user, name='LNG', axis='theme', parent=parent)
        diaries = [
            StockDiary.objects.create(user=user, stock_symbol=f'100{i}', stock_name=f'銘柄{i}')
            for i in range(4)
        ]
        diaries[0].tags.add(parent)
        diaries[1].tags.add(parent)
        diaries[2].tags.add(child)
        diaries[3].tags.add(child)

        url = reverse('stockdiary:api_diary_graph_data')
        resp = authed_client.get(url, {'edge_modes': 'tag'})
        assert resp.status_code == 200
        data = resp.json()
        assert not any(e.get('edge_type') == 'tag_hierarchy' for e in data['edges'])
        # フラットな銘柄↔タグ接続は従来どおり返る
        assert any(e.get('edge_type') == 'tag' for e in data['edges'])


class TestGraphNeighborsAPI:
    """ドリルダウン探索の近傍 API（graph_neighbors・TG-DD）。

    設計: docs/graph_drilldown_redesign.md
    """

    def test_tag_node_returns_neighbors(self, authed_client, user):
        from tags.models import Tag
        parent = Tag.objects.create(user=user, name='半導体', axis='theme')
        child = Tag.objects.create(user=user, name='AI半導体', axis='theme', parent=parent)
        d = StockDiary.objects.create(user=user, stock_symbol='6758', stock_name='ソニーG')
        d.tags.add(parent)

        url = reverse('stockdiary:api_graph_neighbors')
        resp = authed_client.get(url, {'node': f'tag:{parent.pk}'})
        assert resp.status_code == 200
        data = resp.json()
        assert data['node']['id'] == f'tag:{parent.pk}'
        ids = {n['id'] for n in data['neighbors']}
        assert f'tag:{child.pk}' in ids   # 子タグ
        assert 'stock:6758' in ids        # 銘柄

    def test_stock_node_returns_tags_with_detail_url(self, authed_client, user):
        from tags.models import Tag
        t = Tag.objects.create(user=user, name='半導体', axis='theme')
        d = StockDiary.objects.create(user=user, stock_symbol='6758', stock_name='ソニーG')
        d.tags.add(t)

        url = reverse('stockdiary:api_graph_neighbors')
        resp = authed_client.get(url, {'node': 'stock:6758'})
        assert resp.status_code == 200
        data = resp.json()
        assert data['node']['detail_url'].endswith(f'/{d.pk}/')
        assert {n['id'] for n in data['neighbors']} == {f'tag:{t.pk}'}

    def test_sector_node_returns_stocks(self, authed_client, user):
        StockDiary.objects.create(
            user=user, stock_symbol='6758', stock_name='ソニーG', sector='電気機器'
        )
        StockDiary.objects.create(
            user=user, stock_symbol='7203', stock_name='トヨタ自動車', sector='輸送用機器'
        )

        url = reverse('stockdiary:api_graph_neighbors')
        resp = authed_client.get(url, {'node': 'sector:電気機器'})
        assert resp.status_code == 200
        data = resp.json()
        assert data['node']['id'] == 'sector:電気機器'
        assert {n['id'] for n in data['neighbors']} == {'stock:6758'}

    def test_stock_node_includes_sector_neighbor(self, authed_client, user):
        StockDiary.objects.create(
            user=user, stock_symbol='6758', stock_name='ソニーG', sector='電気機器'
        )

        url = reverse('stockdiary:api_graph_neighbors')
        resp = authed_client.get(url, {'node': 'stock:6758'})
        assert resp.status_code == 200
        data = resp.json()
        assert 'sector:電気機器' in {n['id'] for n in data['neighbors']}

    def test_unknown_node_returns_404(self, authed_client):
        url = reverse('stockdiary:api_graph_neighbors')
        resp = authed_client.get(url, {'node': 'stock:NOPE'})
        assert resp.status_code == 404

    def test_requires_login(self, client):
        url = reverse('stockdiary:api_graph_neighbors')
        resp = client.get(url, {'node': 'tag:1'})
        assert resp.status_code == 302


class TestExploreGraphPage:
    """explore（要素で探索）ページの入口データ。タグ・銘柄の両方から開始できる（TG-DD）。"""

    def test_entry_items_include_tags_and_stocks(self, authed_client, user):
        import json
        import re
        from tags.models import Tag
        t = Tag.objects.create(user=user, name='半導体', axis='theme')
        d = StockDiary.objects.create(user=user, stock_symbol='6758', stock_name='ソニーG')
        d.tags.add(t)

        url = reverse('stockdiary:explore_graph')
        resp = authed_client.get(url)
        assert resp.status_code == 200
        body = resp.content.decode()
        m = re.search(r'id="exg-items-data"[^>]*>(.*?)</script>', body, re.S)
        items = json.loads(m.group(1))
        ids = {i['id']: i for i in items}
        # タグ起点と銘柄起点の両方が入口に並ぶ
        assert ids[f'tag:{t.pk}']['type'] == 'tag'
        assert ids['stock:6758']['type'] == 'stock'
        assert ids['stock:6758']['symbol'] == '6758'

    def test_event_and_custom_tags_excluded_from_entry(self, authed_client, user):
        import json
        import re
        from tags.models import Tag
        theme = Tag.objects.create(user=user, name='半導体', axis='theme')
        evt = Tag.objects.create(user=user, name='決算注目', axis='event')
        d = StockDiary.objects.create(user=user, stock_symbol='6758', stock_name='ソニーG')
        d.tags.add(theme, evt)

        resp = authed_client.get(reverse('stockdiary:explore_graph'))
        body = resp.content.decode()
        items = json.loads(re.search(r'id="exg-items-data"[^>]*>(.*?)</script>', body, re.S).group(1))
        ids = {i['id'] for i in items}
        assert f'tag:{theme.pk}' in ids
        assert f'tag:{evt.pk}' not in ids  # event 軸は入口に出さない

    def test_entry_items_include_sectors(self, authed_client, user):
        import json
        import re
        StockDiary.objects.create(
            user=user, stock_symbol='6758', stock_name='ソニーG', sector='電気機器'
        )

        resp = authed_client.get(reverse('stockdiary:explore_graph'))
        body = resp.content.decode()
        items = json.loads(re.search(r'id="exg-items-data"[^>]*>(.*?)</script>', body, re.S).group(1))
        ids = {i['id']: i for i in items}
        assert ids['sector:電気機器']['type'] == 'sector'
        assert ids['sector:電気機器']['label'] == '電気機器'


# ---------------------------------------------------------------------------
# list_diary_notifications
# ---------------------------------------------------------------------------

class TestListDiaryNotifications:
    """日記通知設定一覧 API テスト。"""

    def test_returns_empty_list_when_no_notifications(self, authed_client, diary):
        url = reverse('stockdiary:api_list_diary_notifications', args=[diary.id])
        resp = authed_client.get(url)
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert data['notifications'] == []
        assert data['count'] == 0

    def test_returns_notification_list(self, authed_client, diary):
        from django.utils import timezone as tz
        DiaryNotification.objects.create(
            diary=diary,
            remind_at=tz.now() + __import__('datetime').timedelta(days=1),
            message='確認してください',
        )
        url = reverse('stockdiary:api_list_diary_notifications', args=[diary.id])
        resp = authed_client.get(url)
        data = resp.json()
        assert data['count'] == 1
        assert data['notifications'][0]['message'] == '確認してください'

    def test_requires_login(self, client, diary):
        url = reverse('stockdiary:api_list_diary_notifications', args=[diary.id])
        resp = client.get(url)
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# get_notification_logs
# ---------------------------------------------------------------------------

class TestGetNotificationLogs:
    """通知履歴取得 API テスト。"""

    def test_returns_empty_logs_when_none(self, authed_client):
        url = reverse('api_notification_logs')
        resp = authed_client.get(url)
        assert resp.status_code == 200
        data = resp.json()
        assert 'logs' in data
        assert 'unread_count' in data

    def test_returns_existing_logs(self, authed_client, user):
        NotificationLog.objects.create(
            user=user, title='テスト通知', message='内容', url='/stockdiary/'
        )
        url = reverse('api_notification_logs')
        resp = authed_client.get(url)
        data = resp.json()
        assert len(data['logs']) == 1
        assert data['logs'][0]['title'] == 'テスト通知'

    def test_unread_filter(self, authed_client, user):
        NotificationLog.objects.create(
            user=user, title='未読', message='', url='/', is_read=False
        )
        NotificationLog.objects.create(
            user=user, title='既読', message='', url='/', is_read=True
        )
        url = reverse('api_notification_logs')
        resp = authed_client.get(url, {'unread': 'true'})
        data = resp.json()
        titles = [l['title'] for l in data['logs']]
        assert '未読' in titles
        assert '既読' not in titles

    def test_requires_login(self, client):
        url = reverse('api_notification_logs')
        resp = client.get(url)
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# mark_notification_read / mark_all_read
# ---------------------------------------------------------------------------

class TestMarkNotificationRead:
    """通知既読 API テスト。"""

    def test_marks_single_notification_read(self, authed_client, user):
        log = NotificationLog.objects.create(
            user=user, title='通知', message='', url='/', is_read=False
        )
        url = reverse('api_mark_notification_read', args=[log.id])
        resp = authed_client.post(url)
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        log.refresh_from_db()
        assert log.is_read is True

    def test_other_users_log_returns_404(self, authed_client, other_user):
        log = NotificationLog.objects.create(
            user=other_user, title='他ユーザー通知', message='', url='/'
        )
        url = reverse('api_mark_notification_read', args=[log.id])
        resp = authed_client.post(url)
        assert resp.status_code == 404

    def test_mark_all_read(self, authed_client, user):
        NotificationLog.objects.create(user=user, title='A', message='', url='/', is_read=False)
        NotificationLog.objects.create(user=user, title='B', message='', url='/', is_read=False)
        url = reverse('api_mark_all_read')
        resp = authed_client.post(url)
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        assert NotificationLog.objects.filter(user=user, is_read=False).count() == 0

    def test_requires_login(self, client):
        url = reverse('api_mark_all_read')
        resp = client.post(url)
        assert resp.status_code == 302
