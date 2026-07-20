"""通知管理ページ（NotificationListView / notification_list.html）の回帰テスト。

なぜこのテストを足したか:
継続利用しやすさの監査で、この予定リマインダー一覧に2つの不具合が見つかった。
(1) View は 20 件/ページでページ分割していたのに、テンプレートにページ送りの
    導線が一切無く、21 件目以降の予定が UI から到達不能だった。
(2) 全行が `class="unread"` と「予定あり」で固定描画され、read/unread のスタイル
    体系がまるごとデッドコードだった（近い予定/先の予定の識別ができない）。

以下を固定する:
- 予定が1ページ(20件)を超えたらページ送りナビが出て、2ページ目に到達できる
- 各行の状態バッジが「予定あり」固定ではなく、残り日数の実データ（本日/あとN日）になる
- filter パラメータがページ送りリンクに引き継がれる
"""
import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from stockdiary.models import DiaryNotification


def _make_reminders(diary, count, base_offset_days=1):
    """remind_at = 今日 + (base_offset + i) 日 の予定を count 件作る。"""
    now = timezone.now()
    for i in range(count):
        DiaryNotification.objects.create(
            diary=diary,
            remind_at=now + datetime.timedelta(days=base_offset_days + i),
            message=f'リマインダー{i}',
            is_active=True,
        )


@pytest.mark.django_db
class TestNotificationListPagination:
    def test_pager_appears_and_second_page_reachable(self, authenticated_client, sample_diary):
        """21件の予定でページ送りナビが出て、2ページ目に到達できる（従来は到達不能だった）。"""
        _make_reminders(sample_diary, 21)
        url = reverse('stockdiary:notification_list')

        resp = authenticated_client.get(url)
        assert resp.status_code == 200
        assert resp.context['notifications'].paginator.num_pages == 2
        # ページ送りナビが描画される（CSS クラス名ではなく nav 固有の aria-label で判定）
        assert 'aria-label="通知ページ送り"' in resp.content.decode('utf-8')
        # 2ページ目リンクが filter を引き継いでいる
        assert 'page=2' in resp.content.decode('utf-8')

        resp2 = authenticated_client.get(url, {'page': 2})
        assert resp2.status_code == 200
        assert resp2.context['notifications'].number == 2
        assert len(resp2.context['notifications'].object_list) == 1

    def test_no_pager_when_single_page(self, authenticated_client, sample_diary):
        """20件以内ならページ送りナビは出ない。"""
        _make_reminders(sample_diary, 3)
        resp = authenticated_client.get(reverse('stockdiary:notification_list'))
        assert 'aria-label="通知ページ送り"' not in resp.content.decode('utf-8')

    def test_filter_preserved_in_pager_links(self, authenticated_client, sample_diary):
        """filter=upcoming 指定時、ページ送りリンクが filter を保持する。"""
        _make_reminders(sample_diary, 21)
        resp = authenticated_client.get(
            reverse('stockdiary:notification_list'), {'filter': 'upcoming'}
        )
        assert 'filter=upcoming&page=2' in resp.content.decode('utf-8')


@pytest.mark.django_db
class TestNotificationTimingLabel:
    def test_timing_label_reflects_real_days(self, authenticated_client, sample_diary):
        """状態バッジが「予定あり」固定ではなく、残り日数の実データになる。"""
        now = timezone.now()
        DiaryNotification.objects.create(
            diary=sample_diary, remind_at=now, message='本日分', is_active=True,
        )
        DiaryNotification.objects.create(
            diary=sample_diary,
            remind_at=now + datetime.timedelta(days=5),
            message='先の分', is_active=True,
        )
        html = authenticated_client.get(
            reverse('stockdiary:notification_list')
        ).content.decode('utf-8')

        assert '予定あり' not in html  # デッドな固定文言が消えている
        assert '本日' in html
        assert 'あと5日' in html

    def test_soon_reminder_gets_accent_class(self, authenticated_client, sample_diary):
        """本日・明日の予定だけ is-soon（左アクセント）が付く。"""
        now = timezone.now()
        DiaryNotification.objects.create(
            diary=sample_diary,
            remind_at=now + datetime.timedelta(days=1),
            message='明日分', is_active=True,
        )
        html = authenticated_client.get(
            reverse('stockdiary:notification_list')
        ).content.decode('utf-8')
        assert 'notification-card is-soon' in html
