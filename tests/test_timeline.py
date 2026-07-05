"""全銘柄横断タイムラインのテスト"""
import pytest
from datetime import date, timedelta
from decimal import Decimal

from django.urls import reverse

from stockdiary.models import StockDiary, DiaryNote, Transaction
from stockdiary.views_timeline import TimelineView

pytestmark = pytest.mark.django_db(transaction=True)


class TestTimelineView:

    def test_requires_login(self, client):
        response = client.get(reverse('stockdiary:timeline'))
        assert response.status_code == 302

    def test_page_renders(self, authenticated_client, sample_diary):
        response = authenticated_client.get(reverse('stockdiary:timeline'))
        assert response.status_code == 200
        assert 'タイムライン' in response.content.decode()

    def test_all_event_kinds_shown(self, authenticated_client, sample_diary_with_transaction):
        DiaryNote.objects.create(
            diary=sample_diary_with_transaction, date=date.today(),
            content='決算が良かった', note_type='analysis',
        )
        response = authenticated_client.get(reverse('stockdiary:timeline'))
        html = response.content.decode()
        assert '日記作成' in html       # StockDiary 作成イベント
        assert '決算が良かった' in html  # 継続記録
        assert '買' in html             # 取引

    def test_events_sorted_desc(self, user, sample_diary):
        old = DiaryNote.objects.create(
            diary=sample_diary, date=date.today() - timedelta(days=10), content='古い記録',
        )
        new = DiaryNote.objects.create(
            diary=sample_diary, date=date.today(), content='新しい記録',
        )
        events = TimelineView._collect_events(user, None, 'all', None)
        dates = [e['date'] for e in events]
        assert dates == sorted(dates, reverse=True)
        assert events[0]['note'].id == new.id

    def test_retrospective_filter(self, user, sample_sold_diary):
        DiaryNote.objects.create(
            diary=sample_sold_diary, date=date.today(),
            content='通常の分析', note_type='analysis',
        )
        DiaryNote.objects.create(
            diary=sample_sold_diary, date=date.today(),
            content='高値掴みの反省', note_type='retrospective',
        )
        events = TimelineView._collect_events(user, None, 'retrospective', None)
        assert len(events) == 1
        assert events[0]['kind'] == 'retrospective'

    def test_period_filter(self, user, sample_diary):
        DiaryNote.objects.create(
            diary=sample_diary, date=date.today() - timedelta(days=200), content='昔の記録',
        )
        since = date.today() - timedelta(days=90)
        events = TimelineView._collect_events(user, since, 'note', None)
        assert events == []

    def test_tag_filter(self, user, sample_diary, sample_tags):
        sample_diary.tags.add(sample_tags[0])
        other = StockDiary.objects.create(
            user=user, stock_symbol='9984', stock_name='ソフトバンクグループ',
        )
        DiaryNote.objects.create(diary=sample_diary, date=date.today(), content='タグあり')
        DiaryNote.objects.create(diary=other, date=date.today(), content='タグなし')
        events = TimelineView._collect_events(user, None, 'note', sample_tags[0].id)
        assert len(events) == 1
        assert events[0]['diary'].id == sample_diary.id

    def test_other_user_excluded(self, user, another_user):
        d = StockDiary.objects.create(
            user=another_user, stock_symbol='7203', stock_name='トヨタ自動車',
        )
        DiaryNote.objects.create(diary=d, date=date.today(), content='他人の記録')
        events = TimelineView._collect_events(user, None, 'all', None)
        assert events == []

    def test_excluded_diary_not_shown(self, user, sample_diary):
        sample_diary.is_excluded = True
        sample_diary.save(update_fields=['is_excluded'])
        DiaryNote.objects.create(diary=sample_diary, date=date.today(), content='除外済み')
        events = TimelineView._collect_events(user, None, 'all', None)
        assert events == []

    def test_transaction_event_fields(self, user, sample_diary_with_transaction):
        events = TimelineView._collect_events(user, None, 'transaction', None)
        assert len(events) == 1
        e = events[0]
        assert e['kind'] == 'transaction'
        assert e['transaction'].quantity == Decimal('100')


class TestTimelineKeywordSearch:
    """タイムラインのキーワード検索（TL1）のテスト。

    なぜ追加したか: タイムラインは「あのとき何を考えていたか」を探す画面なのに
    period/type/tag フィルタしかなく、言葉で探すには home へ戻って時系列文脈を
    失うしかなかった。?q= で銘柄名・本文・トピック・取引メモを横断検索できるようにした。
    """

    def test_q_matches_note_content(self, user, sample_diary):
        DiaryNote.objects.create(
            diary=sample_diary, date=date.today(), content='ホルムズ海峡の地政学リスクを検討',
        )
        DiaryNote.objects.create(
            diary=sample_diary, date=date.today(), content='決算メモ',
        )
        events = TimelineView._collect_events(user, None, 'note', None, q='ホルムズ')
        assert len(events) == 1
        assert 'ホルムズ' in events[0]['note'].content

    def test_q_matches_stock_name_across_sources(self, user, sample_diary_with_transaction):
        """銘柄名でのヒットは継続記録・取引・日記作成の全ソースに効く。"""
        DiaryNote.objects.create(
            diary=sample_diary_with_transaction, date=date.today(), content='続報メモ',
        )
        name = sample_diary_with_transaction.stock_name
        events = TimelineView._collect_events(user, None, 'all', None, q=name)
        kinds = {e['kind'] for e in events}
        assert 'note' in kinds
        assert 'transaction' in kinds
        assert 'diary' in kinds

    def test_q_no_match_returns_empty(self, user, sample_diary):
        DiaryNote.objects.create(diary=sample_diary, date=date.today(), content='内容')
        events = TimelineView._collect_events(user, None, 'all', None, q='存在しない語XYZ')
        assert events == []

    def test_q_via_http_and_input_rendered(self, authenticated_client, sample_diary):
        """?q= はHTTP経由でも効き、検索入力欄が値を保持して描画される。"""
        DiaryNote.objects.create(
            diary=sample_diary, date=date.today(), content='ユニークキーワードABC',
        )
        url = reverse('stockdiary:timeline')
        html = authenticated_client.get(url, {'q': 'ユニークキーワードABC'}).content.decode()
        assert 'ユニークキーワードABC' in html
        assert 'name="q"' in html
        html2 = authenticated_client.get(url, {'q': 'ヒットしない語'}).content.decode()
        assert 'ユニークキーワードABC' not in html2
