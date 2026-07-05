import pytest
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
from decimal import Decimal
from datetime import date, timedelta
import json

from stockdiary.models import StockDiary, Transaction, DiaryNote
from tags.models import Tag

User = get_user_model()

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.django_db(transaction=True)
class TestStockDiaryListView:
    """日記一覧ビューのテスト"""
    
    def setup_method(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        # テスト用日記を複数作成
        self.diary1 = StockDiary.objects.create(
            user=self.user,
            stock_symbol='7203',
            stock_name='トヨタ自動車',
            reason='長期保有目的',
            sector='輸送用機器'
        )
        
        # 取引を追加（保有中）
        Transaction.objects.create(
            diary=self.diary1,
            transaction_type='buy',
            transaction_date=date.today(),
            price=Decimal('2000.00'),
            quantity=Decimal('100')
        )
        
        self.diary2 = StockDiary.objects.create(
            user=self.user,
            stock_symbol='9984',
            stock_name='ソフトバンクグループ',
            reason='高配当狙い',
            sector='情報・通信業'
        )
        
        # 売却済み
        Transaction.objects.create(
            diary=self.diary2,
            transaction_type='buy',
            transaction_date=date.today() - timedelta(days=10),
            price=Decimal('5000.00'),
            quantity=Decimal('50')
        )
        Transaction.objects.create(
            diary=self.diary2,
            transaction_type='sell',
            transaction_date=date.today() - timedelta(days=5),
            price=Decimal('5500.00'),
            quantity=Decimal('50')
        )
        
        # メモのみ
        self.diary3 = StockDiary.objects.create(
            user=self.user,
            stock_symbol='6758',
            stock_name='ソニーグループ',
            reason='監視銘柄。今後の動向を注視'
        )
    
    def test_home_view_authenticated(self, client):
        """認証済みユーザーのホーム画面表示"""
        client.login(username='testuser', password='testpass123')
        response = client.get(reverse('stockdiary:home'))
        
        assert response.status_code == 200
        assert 'diaries' in response.context
    
    def test_home_view_unauthenticated(self, client):
        """未認証ユーザーのリダイレクト"""
        response = client.get(reverse('stockdiary:home'))
        
        # ログインページにリダイレクトされる
        assert response.status_code == 302
        assert 'login' in response.url
    
    def test_filter_by_status_active(self, client):
        """保有中フィルター"""
        client.login(username='testuser', password='testpass123')
        response = client.get(reverse('stockdiary:home') + '?status=active')
        
        assert response.status_code == 200
        # 保有中の銘柄のみ表示される（diary1のみ）
        diaries = list(response.context['diaries'])
        assert len(diaries) == 1
        assert diaries[0].id == self.diary1.id
    
    def test_filter_by_status_sold(self, client):
        """売却済みフィルター"""
        client.login(username='testuser', password='testpass123')
        response = client.get(reverse('stockdiary:home') + '?status=sold')
        
        assert response.status_code == 200
        diaries = list(response.context['diaries'])
        assert len(diaries) == 1
        assert diaries[0].id == self.diary2.id
    
    def test_filter_by_status_memo(self, client):
        """メモのみフィルター"""
        client.login(username='testuser', password='testpass123')
        response = client.get(reverse('stockdiary:home') + '?status=memo')
        
        assert response.status_code == 200
        diaries = list(response.context['diaries'])
        assert len(diaries) == 1
        assert diaries[0].id == self.diary3.id
    
    def test_search_by_stock_name(self, client):
        """銘柄名で検索"""
        client.login(username='testuser', password='testpass123')
        response = client.get(reverse('stockdiary:home') + '?query=トヨタ')
        
        assert response.status_code == 200
        diaries = list(response.context['diaries'])
        assert len(diaries) == 1
        assert diaries[0].stock_name == 'トヨタ自動車'


@pytest.mark.django_db(transaction=True)
class TestStockDiaryDetailView:
    """日記詳細ビューのテスト"""
    
    def setup_method(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.diary = StockDiary.objects.create(
            user=self.user,
            stock_symbol='7203',
            stock_name='トヨタ自動車',
            reason='長期保有',
            sector='輸送用機器'
        )
        
        # 取引を追加
        self.transaction1 = Transaction.objects.create(
            diary=self.diary,
            transaction_type='buy',
            transaction_date=date.today() - timedelta(days=10),
            price=Decimal('2000.00'),
            quantity=Decimal('100')
        )
        
        self.transaction2 = Transaction.objects.create(
            diary=self.diary,
            transaction_type='buy',
            transaction_date=date.today() - timedelta(days=5),
            price=Decimal('2200.00'),
            quantity=Decimal('50')
        )
    
    def test_detail_view_displays_diary(self, client):
        """日記詳細の表示"""
        client.login(username='testuser', password='testpass123')
        url = reverse('stockdiary:detail', kwargs={'pk': self.diary.pk})
        response = client.get(url)
        
        assert response.status_code == 200
        assert response.context['diary'] == self.diary
    
    def test_detail_view_displays_transactions(self, client):
        """取引履歴の表示"""
        client.login(username='testuser', password='testpass123')
        url = reverse('stockdiary:detail', kwargs={'pk': self.diary.pk})
        response = client.get(url)
        
        assert response.status_code == 200
        transactions = response.context['transactions']
        assert transactions.count() == 2
    
    def test_detail_view_other_user_diary(self, client):
        """他ユーザーの日記へのアクセス（404）"""
        # 別のユーザーを作成
        other_user = User.objects.create_user(
            username='otheruser',
            email='other@example.com',
            password='otherpass123'
        )
        
        client.login(username='otheruser', password='otherpass123')
        url = reverse('stockdiary:detail', kwargs={'pk': self.diary.pk})
        response = client.get(url)
        
        # 404またはリダイレクト
        assert response.status_code in [302, 404]


@pytest.mark.django_db(transaction=True)
class TestStockDiaryCreateView:
    """日記作成ビューのテスト"""
    
    def setup_method(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
    
    def test_create_view_get(self, client):
        """作成画面の表示"""
        client.login(username='testuser', password='testpass123')
        response = client.get(reverse('stockdiary:create'))
        
        assert response.status_code == 200
        assert 'form' in response.context
    
    def test_create_diary_without_transaction(self, client):
        """取引なしで日記を作成"""
        client.login(username='testuser', password='testpass123')
        
        data = {
            'stock_name': 'テスト株式会社',
            'stock_symbol': '9999',
            'reason': 'テスト理由',
            'sector': 'テスト業種',
            'add_initial_purchase': False
        }
        
        response = client.post(reverse('stockdiary:create'), data)
        
        # リダイレクトまたは成功
        assert response.status_code in [200, 302]
        
        # 日記が作成されたことを確認
        diary = StockDiary.objects.filter(stock_name='テスト株式会社').first()
        assert diary is not None
        assert diary.user == self.user
        assert diary.is_memo is True
    
    def test_create_diary_ignores_legacy_purchase_fields(self, client):
        """初回取引は作成フローから除去済み：旧フィールドを送っても取引は作られない"""
        client.login(username='testuser', password='testpass123')

        data = {
            'stock_name': 'テスト株式会社2',
            'stock_symbol': '9998',
            'reason': 'テスト理由2',
            # 旧・初回購入フィールド（現在は無視される）
            'add_initial_purchase': True,
            'initial_purchase_date': date.today().strftime('%Y-%m-%d'),
            'initial_purchase_price': '3000.00',
            'initial_purchase_quantity': '200'
        }

        response = client.post(reverse('stockdiary:create'), data)

        assert response.status_code in [200, 302]

        # 日記は作成されるが、取引は作られない（取引は詳細ページで追加する方針）
        diary = StockDiary.objects.filter(stock_name='テスト株式会社2').first()
        assert diary is not None
        assert diary.transactions.count() == 0
        assert diary.is_memo is True


@pytest.mark.django_db(transaction=True)
class TestTransactionManagement:
    """取引管理のテスト"""
    
    def setup_method(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.diary = StockDiary.objects.create(
            user=self.user,
            stock_symbol='7203',
            stock_name='トヨタ自動車',
            reason='テスト用'
        )
    
    def test_add_transaction(self, client):
        """取引の追加"""
        client.login(username='testuser', password='testpass123')
        
        url = reverse('stockdiary:add_transaction', kwargs={'diary_id': self.diary.pk})
        data = {
            'transaction_type': 'buy',
            'transaction_date': date.today().strftime('%Y-%m-%d'),
            'price': '2500.00',
            'quantity': '150',
            'memo': 'テスト購入'
        }
        
        response = client.post(url, data)
        
        # リダイレクト
        assert response.status_code == 302
        
        # 取引が追加されたことを確認
        assert Transaction.objects.filter(diary=self.diary).count() == 1
        
        transaction = Transaction.objects.first()
        assert transaction.transaction_type == 'buy'
        assert transaction.quantity == Decimal('150')
        assert transaction.price == Decimal('2500.00')
    
    def test_delete_transaction(self, client):
        """取引の削除"""
        client.login(username='testuser', password='testpass123')
        
        # 取引を作成
        transaction = Transaction.objects.create(
            diary=self.diary,
            transaction_type='buy',
            transaction_date=date.today(),
            price=Decimal('2000.00'),
            quantity=Decimal('100')
        )
        
        url = reverse('stockdiary:delete_transaction', kwargs={'transaction_id': transaction.pk})
        response = client.post(url)
        
        # リダイレクト
        assert response.status_code == 302
        
        # 取引が削除されたことを確認
        assert Transaction.objects.filter(pk=transaction.pk).count() == 0


@pytest.mark.django_db(transaction=True)
class TestDiaryNoteManagement:
    """継続記録管理のテスト"""
    
    def setup_method(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.diary = StockDiary.objects.create(
            user=self.user,
            stock_symbol='9984',
            stock_name='ソフトバンクグループ',
            reason='テスト用'
        )
    
    def test_add_diary_note(self, client):
        """継続記録の追加"""
        client.login(username='testuser', password='testpass123')
        
        url = reverse('stockdiary:add_note', kwargs={'pk': self.diary.pk})
        data = {
            'date': date.today().strftime('%Y-%m-%d'),
            'content': '四半期決算が好調だった',
            'current_price': '5500.00',
            'note_type': 'earnings',
        }
        
        response = client.post(url, data)
        
        # リダイレクト
        assert response.status_code == 302
        
        # 継続記録が追加されたことを確認
        assert DiaryNote.objects.filter(diary=self.diary).count() == 1
        
        note = DiaryNote.objects.first()
        assert note.content == '四半期決算が好調だった'
        assert note.note_type == 'earnings'


@pytest.mark.django_db(transaction=True)
class TestCSVUpload:
    """CSVアップロード機能のテスト"""
    
    def setup_method(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
    
    def test_trade_upload_view_get(self, client):
        """アップロード画面の表示"""
        client.login(username='testuser', password='testpass123')
        response = client.get(reverse('stockdiary:trade_upload'))
        
        assert response.status_code == 200
        assert 'form' in response.context


@pytest.mark.django_db(transaction=True)
class TestTagManagement:
    """タグ管理のテスト"""
    
    def setup_method(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
    
    def test_create_tag(self, client):
        """タグの作成"""
        client.login(username='testuser', password='testpass123')
        
        data = {'name': '長期投資', 'axis': 'theme'}
        response = client.post(reverse('tags:create'), data)
        
        # リダイレクト
        assert response.status_code == 302
        
        # タグが作成されたことを確認
        tag = Tag.objects.filter(name='長期投資', user=self.user).first()
        assert tag is not None
    
    def test_tag_list_view(self, client):
        """タグ一覧の表示"""
        client.login(username='testuser', password='testpass123')

        # タグを作成
        Tag.objects.create(user=self.user, name='配当狙い')
        Tag.objects.create(user=self.user, name='成長株')

        response = client.get(reverse('tags:list'))

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 検索バグ回帰テスト（#fix: 2026-06-20 のバグ修正に対応）
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestDiaryListHTMXEndpoint:
    """diary_list HTMX エンドポイントの検索・フィルター・ソートテスト。

    修正したバグ：
    - status='all' を送っても正しく全件表示される
    - status デフォルト（未指定）は全件表示（保有中限定にならない）
    - tag フィルターが正しく機能する
    - sort が正しく機能する
    - transaction_date_range フィルターが transaction_date 基準で動作する
    """

    HTMX_HEADERS = {
        'HTTP_HX_REQUEST': 'true',
        'HTTP_HX_TARGET': 'diary-container',
    }

    def setup_method(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )

        # 保有中
        self.diary_active = StockDiary.objects.create(
            user=self.user,
            stock_symbol='7203',
            stock_name='トヨタ自動車',
            reason='長期保有',
            sector='輸送用機器',
        )
        Transaction.objects.create(
            diary=self.diary_active,
            transaction_type='buy',
            transaction_date=date.today(),
            price=Decimal('2000.00'),
            quantity=Decimal('100'),
        )
        from stockdiary.services.aggregate_service import AggregateService
        AggregateService.recalculate(self.diary_active)

        # 売却済み
        self.diary_sold = StockDiary.objects.create(
            user=self.user,
            stock_symbol='9984',
            stock_name='ソフトバンクグループ',
            reason='高配当狙い',
            sector='情報・通信業',
        )
        Transaction.objects.create(
            diary=self.diary_sold,
            transaction_type='buy',
            transaction_date=date.today() - timedelta(days=10),
            price=Decimal('5000.00'),
            quantity=Decimal('50'),
        )
        Transaction.objects.create(
            diary=self.diary_sold,
            transaction_type='sell',
            transaction_date=date.today() - timedelta(days=5),
            price=Decimal('5500.00'),
            quantity=Decimal('50'),
        )
        AggregateService.recalculate(self.diary_sold)

        # メモのみ
        self.diary_memo = StockDiary.objects.create(
            user=self.user,
            stock_symbol='6758',
            stock_name='ソニーグループ',
            reason='監視銘柄',
        )

        self.url = reverse('stockdiary:diary_list')

    def _get(self, client, params=''):
        client.login(username='testuser', password='testpass123')
        return client.get(self.url + params, **self.HTMX_HEADERS)

    # ── ステータスフィルター ──────────────────────────────────────────────

    def test_status_all_shows_every_diary(self, client):
        """status=all は全日記（保有中・売却済み・メモ）を返す。"""
        response = self._get(client, '?status=all')
        assert response.status_code == 200
        ids = {d.id for d in response.context['diaries']}
        assert self.diary_active.id in ids
        assert self.diary_sold.id in ids
        assert self.diary_memo.id in ids

    def test_status_default_shows_all_not_only_active(self, client):
        """status パラメータ未指定のとき、保有中だけでなく全件が返る。

        バグ前は hidden input のデフォルトが 'active' だったため、
        HTMX 送信のたびに保有中のみに絞られていた。
        """
        response = self._get(client, '')
        assert response.status_code == 200
        ids = {d.id for d in response.context['diaries']}
        # 保有中・売却済み・メモが全て含まれるべき
        assert self.diary_active.id in ids
        assert self.diary_sold.id in ids
        assert self.diary_memo.id in ids

    def test_status_active_filters_to_holding_only(self, client):
        """status=active は保有中のみを返す。"""
        response = self._get(client, '?status=active')
        assert response.status_code == 200
        ids = {d.id for d in response.context['diaries']}
        assert self.diary_active.id in ids
        assert self.diary_sold.id not in ids
        assert self.diary_memo.id not in ids

    def test_status_sold_filters_to_sold_only(self, client):
        """status=sold は売却済みのみを返す。"""
        response = self._get(client, '?status=sold')
        assert response.status_code == 200
        ids = {d.id for d in response.context['diaries']}
        assert self.diary_active.id not in ids
        assert self.diary_sold.id in ids
        assert self.diary_memo.id not in ids

    def test_status_memo_filters_to_memo_only(self, client):
        """status=memo はメモのみ日記だけを返す。"""
        response = self._get(client, '?status=memo')
        assert response.status_code == 200
        ids = {d.id for d in response.context['diaries']}
        assert self.diary_active.id not in ids
        assert self.diary_sold.id not in ids
        assert self.diary_memo.id in ids

    # ── タグフィルター ────────────────────────────────────────────────────

    def test_tag_filter_returns_only_tagged_diary(self, client):
        """tag パラメータで指定したタグを持つ日記のみ返す。

        バグ前はサイドバーの data-target が未設定で tag 値が
        HTMX フォームに同期されず、フィルターが機能しなかった。
        """
        tag = Tag.objects.create(user=self.user, name='長期投資')
        self.diary_active.tags.add(tag)

        response = self._get(client, f'?tag={tag.id}')
        assert response.status_code == 200
        ids = {d.id for d in response.context['diaries']}
        assert self.diary_active.id in ids
        assert self.diary_sold.id not in ids
        assert self.diary_memo.id not in ids

    def test_tag_filter_with_no_match_returns_empty(self, client):
        """どの日記にも付いていないタグで絞り込むと0件になる。"""
        tag = Tag.objects.create(user=self.user, name='未使用タグ')
        response = self._get(client, f'?tag={tag.id}')
        assert response.status_code == 200
        assert len(response.context['diaries']) == 0

    # ── ソート ───────────────────────────────────────────────────────────

    def test_sort_by_name_returns_alphabetical_order(self, client):
        """sort=name は銘柄名のアルファベット順に並ぶ。

        バグ前は sidebarSortFilter の data-target が未設定で
        sort 値が HTMX フォームに届かず、ソートが機能しなかった。
        """
        response = self._get(client, '?sort=name')
        assert response.status_code == 200
        names = [d.stock_name for d in response.context['diaries']]
        assert names == sorted(names)

    def test_sort_by_symbol_returns_code_order(self, client):
        """sort=symbol は銘柄コード順に並ぶ。"""
        response = self._get(client, '?sort=symbol')
        assert response.status_code == 200
        symbols = [d.stock_symbol for d in response.context['diaries']]
        assert symbols == sorted(symbols)

    # ── transaction_date_range フィルター ────────────────────────────────

    def test_transaction_date_range_uses_transaction_date(self, client):
        """transaction_date_range フィルターは transaction_date 基準で動作する。

        バグ前は created_at 基準で絞り込んでいたため、
        取引日指定のフィルターが正しく機能しなかった。
        """
        # diary_active の取引日は今日（1週間以内）
        # diary_sold の最終取引日は5日前（1週間以内）
        # → 両方とも 1w フィルターに引っかかるべき
        response = self._get(client, '?transaction_date_range=1w&status=all')
        assert response.status_code == 200
        ids = {d.id for d in response.context['diaries']}
        assert self.diary_active.id in ids
        assert self.diary_sold.id in ids
        # メモ日記（取引なし）は含まれない
        assert self.diary_memo.id not in ids

    def test_transaction_date_range_excludes_old_transactions(self, client):
        """古い取引のみ持つ日記は transaction_date_range=1w に含まれない。"""
        diary_old = StockDiary.objects.create(
            user=self.user,
            stock_symbol='1234',
            stock_name='古い銘柄',
            reason='テスト',
        )
        Transaction.objects.create(
            diary=diary_old,
            transaction_type='buy',
            transaction_date=date.today() - timedelta(days=30),
            price=Decimal('1000.00'),
            quantity=Decimal('10'),
        )
        from stockdiary.services.aggregate_service import AggregateService
        AggregateService.recalculate(diary_old)

        response = self._get(client, '?transaction_date_range=1w&status=all')
        assert response.status_code == 200
        ids = {d.id for d in response.context['diaries']}
        assert diary_old.id not in ids


@pytest.mark.django_db(transaction=True)
class TestHomeViewStatusDefault:
    """ホーム画面（全ページ）でのステータスデフォルト動作テスト。"""

    def setup_method(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )

        self.diary_active = StockDiary.objects.create(
            user=self.user, stock_symbol='7203', stock_name='トヨタ自動車', reason='長期')
        Transaction.objects.create(
            diary=self.diary_active, transaction_type='buy',
            transaction_date=date.today(), price=Decimal('2000'), quantity=Decimal('100'))
        from stockdiary.services.aggregate_service import AggregateService
        AggregateService.recalculate(self.diary_active)

        self.diary_memo = StockDiary.objects.create(
            user=self.user, stock_symbol='6758', stock_name='ソニー', reason='監視')

    def test_home_default_shows_all_diaries(self, client):
        """status パラメータなしのホーム画面は全日記を表示する（保有中限定でない）。

        バグ前はバックエンドのデフォルトが 'all' でも、
        フロントエンドの hidden input が 'active' をデフォルトにしていたため、
        HTMX 送信のたびに保有中のみに絞られるという不一致があった。
        バックエンドの全ページビューは 'all' デフォルトで正しく動作することを確認する。
        """
        client.login(username='testuser', password='testpass123')
        response = client.get(reverse('stockdiary:home'))
        assert response.status_code == 200
        ids = {d.id for d in response.context['diaries']}
        assert self.diary_active.id in ids
        assert self.diary_memo.id in ids

    def test_home_status_all_explicit_shows_all(self, client):
        """status=all を明示的に指定しても全件表示される。"""
        client.login(username='testuser', password='testpass123')
        response = client.get(reverse('stockdiary:home') + '?status=all')
        assert response.status_code == 200
        ids = {d.id for d in response.context['diaries']}
        assert self.diary_active.id in ids


@pytest.mark.django_db(transaction=True)
class TestDiaryTabContentXSS:
    """DiaryTabContentView (_render_notes_tab) の XSS 対策テスト。

    note.content をエスケープせずに f-string で HTML に埋め込んでいたため、
    <script> 等を含むノート本文がそのまま JSON レスポンスに含まれ、
    クライアントが innerHTML で挿入すると XSS になっていた。
    修正後は django.utils.html.escape() でエスケープされることを確認する。
    """

    def setup_method(self):
        self.user = User.objects.create_user(
            username='xss_test_user', password='pass', email='xss@example.com'
        )
        self.diary = StockDiary.objects.create(
            user=self.user,
            stock_symbol='1234',
            stock_name='テスト株式',
            reason='テスト',
        )

    def test_script_tag_in_note_content_is_escaped(self, client):
        """<script> タグを含む note.content が JSON レスポンス内でエスケープされる。"""
        malicious = '<script>alert("XSS")</script>'
        DiaryNote.objects.create(
            diary=self.diary,
            date=date.today(),
            content=malicious,
        )
        client.login(username='xss_test_user', password='pass')
        url = reverse('stockdiary:api_tab_content', args=[self.diary.id, 'notes'])
        response = client.get(url)
        assert response.status_code == 200
        body = response.content.decode()
        # エスケープ済みならスクリプトタグが生の状態で含まれない
        assert '<script>' not in body
        assert '&lt;script&gt;' in body

    def test_html_attribute_injection_in_note_content_is_escaped(self, client):
        """" onerror= 等の HTML 属性インジェクションが無効化される。"""
        malicious = '"onmouseover="alert(1)'
        DiaryNote.objects.create(
            diary=self.diary,
            date=date.today(),
            content=malicious,
        )
        client.login(username='xss_test_user', password='pass')
        url = reverse('stockdiary:api_tab_content', args=[self.diary.id, 'notes'])
        response = client.get(url)
        assert response.status_code == 200
        body = response.content.decode()
        assert '&quot;' in body or '&#x27;' in body or '"onmouseover=' not in body

    def test_newline_in_note_content_becomes_br(self, client):
        """改行は <br> に変換され、テキスト自体はエスケープされる。"""
        DiaryNote.objects.create(
            diary=self.diary,
            date=date.today(),
            content='line1\nline2',
        )
        client.login(username='xss_test_user', password='pass')
        url = reverse('stockdiary:api_tab_content', args=[self.diary.id, 'notes'])
        response = client.get(url)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert '<br>' in data['html']
        assert 'line1' in data['html']
        assert 'line2' in data['html']
        # <script> 等が含まれる場合もエスケープされるはず（念のため確認）
        assert '<script>' not in data['html']

@pytest.mark.django_db
class TestHashtagAutocompleteSingleLoad:
    """hashtag-autocomplete.js の二重ロード回帰テスト。

    なぜこのバグが起きたか: base.html がクイック記録シート用に
    hashtag-autocomplete.js を全ページで読み込むようになった後も、
    detail.html / diary_form.html がページ個別に同スクリプトを読み込み続けていた。
    同スクリプトはトップレベルで `const HASHTAG_AXIS_META` を宣言しており、
    クラシックスクリプトの二重実行は「Identifier has already been declared」の
    SyntaxError になる（2回目のロードが丸ごと無効化され、コンソールに常時エラーが出る）。
    ページ側の重複 include を撤去したので、描画 HTML 中の出現が1回であることを固定する。
    """

    def test_detail_loads_hashtag_autocomplete_once(self, authenticated_client, sample_diary):
        html = authenticated_client.get(
            reverse('stockdiary:detail', kwargs={'pk': sample_diary.pk})
        ).content.decode()
        assert html.count('js/hashtag-autocomplete.js') == 1

    def test_diary_form_loads_hashtag_autocomplete_once(self, authenticated_client):
        html = authenticated_client.get(reverse('stockdiary:create')).content.decode()
        assert html.count('js/hashtag-autocomplete.js') == 1
