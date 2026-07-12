# stockdiary/models.py
from django.db import models
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.urls import reverse
from django.db.models import Sum, F, Q, Count
from django.utils import timezone
from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta
import os
import re
import uuid
import logging
from tags.models import Tag

# NULL文字や制御文字（タブ・改行・復帰を除くC0制御）を検出する。
# AIツールやPDFからのコピペで混入し、ProhibitNullCharactersValidator等で
# 弾かれて継続記録が無言で保存失敗する原因になるため除去する。
_CONTROL_CHARS_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')


def sanitize_text_content(text):
    """コピペ等で混入するNULL/制御文字を除去し、改行コードをLFに正規化する。

    改行をLFに揃えるのは、textareaのフォーム送信時にブラウザが改行を
    CRLFへ変換し、サーバ側のlen()がクライアントの文字数カウント（LF基準）
    より行数ぶん多くなって、3000字制限に無言で弾かれるのを防ぐため。
    （タブ・改行は保持する）"""
    if not text:
        return text
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return _CONTROL_CHARS_RE.sub('', text)

logger = logging.getLogger(__name__)


def get_diary_image_path(instance, filename):
    """日記画像のアップロードパスを生成"""
    ext = filename.split('.')[-1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    return f"diary_images/{instance.user.id}/{filename}"


def get_note_image_path(instance, filename):
    """継続記録画像のアップロードパスを生成"""
    ext = filename.split('.')[-1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    return f"note_images/{instance.diary.user.id}/{filename}"


class StockDiary(models.Model):
    """株式投資日記（基本情報のみ）"""
    CURRENCY_CHOICES = [
        ('JPY', '円'),
        ('USD', '米ドル'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    stock_symbol = models.CharField(max_length=50, blank=True, db_index=True, verbose_name='銘柄コード')
    stock_name = models.CharField(max_length=100, verbose_name='銘柄名')
    currency = models.CharField(
        max_length=3, choices=CURRENCY_CHOICES, default='JPY', verbose_name='通貨'
    )
    reason = models.TextField(verbose_name='背景', blank=True, max_length=5000)
    tags = models.ManyToManyField(Tag, blank=True)
    sector = models.CharField(max_length=50, blank=True, verbose_name='業種')
    image = models.ImageField(upload_to=get_diary_image_path, null=True, blank=True, help_text="日記に関連する画像")
    
    # 集計フィールド（自動計算）
    current_quantity = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='現在保有数')
    average_purchase_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='平均取得単価')
    total_cost = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='総取得原価')
    realized_profit = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='実現損益')

    # 取引統計
    total_bought_quantity = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='累計購入数')
    total_sold_quantity = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='累計売却数')
    total_buy_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='累計購入額')
    total_sell_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='累計売却額')
    transaction_count = models.IntegerField(default=0, verbose_name='取引回数')

    # 現物取引のみの集計フィールド（is_margin=False）
    cash_only_current_quantity = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='現物保有数')
    cash_only_average_purchase_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='現物平均取得単価')
    cash_only_total_cost = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='現物総原価')
    cash_only_realized_profit = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='現物実現損益')
    cash_only_total_bought_quantity = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='現物累計購入数')
    cash_only_total_sold_quantity = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='現物累計売却数')
    cash_only_total_buy_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='現物累計購入額')
    cash_only_total_sell_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='現物累計売却額')

    # 信用売り（ショート）建玉のみの集計フィールド。current_quantity（現物+信用ロング）
    # とは別建てで持つ理由: 同一銘柄でロングとショートを同時に保有すると、符号付き
    # 単一カウンタでは両者を区別できず、ショートの新規売りをロングの決済売りと
    # 誤認してしまうため（詳細は AggregateService._recalculate_all のコメント）。
    margin_short_quantity = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='信用売り建玉数')
    margin_short_average_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='信用売り平均建単価')
    margin_short_total_proceeds = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='信用売り建玉総代金')
    margin_short_realized_profit = models.DecimalField(max_digits=15, decimal_places=2, default=0, verbose_name='信用売り実現損益')

    # 日付情報
    first_purchase_date = models.DateField(null=True, blank=True, db_index=True, verbose_name='最初の購入日')
    last_transaction_date = models.DateField(null=True, blank=True, verbose_name='最後の取引日')
    
    # 関連日記（非対称M2M：対称性はアプリ層で管理）
    linked_diaries = models.ManyToManyField('self', blank=True, symmetrical=False, related_name='linked_from', verbose_name='関連日記')

    # 開示書類情報（EDINETから定期更新）
    latest_disclosure_date = models.DateField(
        null=True, blank=True, verbose_name='最新開示日'
    )
    latest_disclosure_doc_type_name = models.CharField(
        max_length=50, blank=True, verbose_name='最新開示種別'
    )

    # 除外フラグ（一覧・グラフから非表示にするが記録は保持）
    is_excluded = models.BooleanField(default=False, verbose_name='除外フラグ', db_index=True)

    # システム情報
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'first_purchase_date']),
            models.Index(fields=['user', 'stock_symbol']),
            models.Index(fields=['user', 'current_quantity']),
            models.Index(fields=['user', '-updated_at']),
            models.Index(fields=['user', 'is_excluded']),
        ]
        verbose_name = '株式日記'
        verbose_name_plural = '株式日記'
    
    def __str__(self):
        return f"{self.stock_name} ({self.stock_symbol})"

    @property
    def is_memo(self):
        """メモ記録かどうか（取引がない場合）"""
        return self.transaction_count == 0

    @property
    def is_holding(self):
        """保有中かどうか（プラス保有）"""
        return self.current_quantity > 0

    @property
    def is_sold_out(self):
        """売却済みかどうか（取引はあるが保有数ゼロ）"""
        return self.transaction_count > 0 and self.current_quantity == 0

    # ✅ 追加: 信用売り（ショート）かどうか
    @property
    def is_short(self):
        """信用売り（ショートポジション）かどうか"""
        return self.current_quantity < 0

    @property
    def currency_symbol(self):
        """通貨記号（¥ / $）"""
        return '$' if self.currency == 'USD' else '¥'

    @property
    def currency_unit(self):
        """通貨の接尾辞表示（円 / ドル）"""
        return 'ドル' if self.currency == 'USD' else '円'

    @property
    def recent_disclosure_status(self):
        """開示書類の直近度: 'new'(7日以内), 'recent'(30日以内), None"""
        if not self.latest_disclosure_date:
            return None
        from datetime import date
        days = (date.today() - self.latest_disclosure_date).days
        if days <= 7:
            return 'new'
        elif days <= 30:
            return 'recent'
        return None

    @property
    def disclosure_freshness(self):
        """recent_disclosure_status の別名。"""
        return self.recent_disclosure_status

    def update_aggregates(self):
        """集計フィールドを再計算して save() する。

        AggregateService.deferred() ブロック内では抑制され、ブロックを抜けるときに
        一度だけ再集計が走る（個別 save() による N 回のフル再集計を防ぐ）。
        """
        if getattr(self, '_defer_recalc', False):
            return
        from .services.aggregate_service import AggregateService
        AggregateService.recalculate(self)

    def process_and_save_image(self, image_file):
        """画像を非同期で圧縮・保存する。元画像をまず保存してからdjango-qでリサイズする。"""
        from .services.image_service import ImageService
        try:
            if self.image:
                self.image.delete(save=False)

            filename = f"{uuid.uuid4().hex}{os.path.splitext(image_file.name)[1]}"
            self.image.save(filename, image_file, save=True)

            from django_q.tasks import async_task
            async_task('stockdiary.tasks.compress_diary_image', self.id)
            return True

        except Exception as e:
            logger.error("Image upload failed for StockDiary(id=%s): %s", self.id, e, exc_info=True)
            # フォールバック: 同期圧縮
            try:
                image_file.seek(0)
                return ImageService.compress_and_save(self, image_file, max_size=(800, 600), quality=85)
            except Exception:
                return False

    def delete_image(self):
        """画像を削除"""
        try:
            if self.image:
                self.image.delete(save=False)
                self.image = None
                self.save(update_fields=['image'])
                return True
        except Exception as e:
            logger.error(f"Image deletion failed for StockDiary: {str(e)}", exc_info=True)
        return False

    def get_image_url(self):
        """画像URLを取得"""
        if self.image:
            return reverse('stockdiary:serve_image', kwargs={
                'diary_id': self.id,
                'image_type': 'diary'
            })
        return None

    def calculate_cash_only_stats(self):
        """現物取引（is_margin=False）のみの統計を返す（キャッシュ済みフィールドを使用）"""
        return {
            'current_quantity': self.cash_only_current_quantity,
            'average_purchase_price': self.cash_only_average_purchase_price,
            'total_cost': self.cash_only_total_cost,
            'realized_profit': self.cash_only_realized_profit,
            'total_bought_quantity': self.cash_only_total_bought_quantity,
            'total_sold_quantity': self.cash_only_total_sold_quantity,
            'total_buy_amount': self.cash_only_total_buy_amount,
            'total_sell_amount': self.cash_only_total_sell_amount,
        }
        
    @property
    def image_url(self):
        return self.get_image_url()


class Transaction(models.Model):
    """取引記録"""
    TRANSACTION_TYPES = [
        ('buy', '購入'),
        ('sell', '売却'),
    ]
    MARGIN_ACTIONS = [
        ('open', '新規'),
        ('close', '返済'),
    ]

    diary = models.ForeignKey(StockDiary, on_delete=models.CASCADE, related_name='transactions')
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPES, verbose_name='取引種別')
    transaction_date = models.DateField(verbose_name='取引日', db_index=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='単価')
    quantity = models.DecimalField(max_digits=15, decimal_places=2, verbose_name='数量')
    memo = models.TextField(blank=True, max_length=500, verbose_name='メモ')
    is_margin = models.BooleanField(default=False, verbose_name='信用取引')
    # 信用取引の新規/返済区分。is_margin=True のときだけ意味を持つ。
    # 「買い」「売り」だけでは新規建てと決済を区別できない
    # （新規売建と返済売りはどちらも type='sell'）ため、この区別がないと
    # 同一銘柄でロング・ショートを同時に保有した場合に集計が破綻する
    # （docs化はせず、AggregateService._recalculate_all のdocstringに詳細）。
    # CSV取込（証券会社の「取引区分」列）から設定する。手入力等で未設定
    # （null）の場合は、集計側で状態依存のヒューリスティックにフォールバックする。
    margin_action = models.CharField(
        max_length=10, choices=MARGIN_ACTIONS, null=True, blank=True, verbose_name='信用建玉区分'
    )
    
    # 取引時点の状態（参照用）
    quantity_after = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name='取引後保有数')
    average_price_after = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='取引後平均単価')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def amount(self):
        """取引金額（単価 × 数量）"""
        return (self.price or 0) * (self.quantity or 0)

    class Meta:
        ordering = ['-transaction_date', '-created_at']
        indexes = [
            models.Index(fields=['diary', 'transaction_date']),
        ]
        verbose_name = '取引'
        verbose_name_plural = '取引'

    def __str__(self):
        type_display = self.get_transaction_type_display()
        return f"{self.diary.stock_name} - {type_display} {self.quantity}株 @ {self.price}{self.diary.currency_unit}"

    def clean(self):
        """バリデーション"""
        super().clean()
        
        # 価格と数量は正の数
        if self.price is not None and self.price <= 0:
            raise ValidationError({'price': '価格は正の数を入力してください'})
        
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError({'quantity': '数量は正の数を入力してください'})

    def save(self, *args, **kwargs):
        # フルクリーンはスキップ（views.py で呼び出す）
        super().save(*args, **kwargs)
        # 保存後に日記の集計を更新
        if self.diary_id:
            self.diary.update_aggregates()

    def delete(self, *args, **kwargs):
        diary = self.diary
        super().delete(*args, **kwargs)
        # 削除後に日記の集計を更新
        diary.update_aggregates()

    @property
    def amount(self):
        """取引金額"""
        return self.price * self.quantity
        

class StockSplit(models.Model):
    """株式分割記録"""
    diary = models.ForeignKey(StockDiary, on_delete=models.CASCADE, related_name='stock_splits')
    split_date = models.DateField(verbose_name='分割実行日', db_index=True)
    split_ratio = models.DecimalField(max_digits=10, decimal_places=4, verbose_name='分割比率')
    memo = models.TextField(blank=True, max_length=500, verbose_name='メモ')
    is_applied = models.BooleanField(default=False, verbose_name='適用済み')
    applied_at = models.DateTimeField(null=True, blank=True, verbose_name='適用日時')
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-split_date']
        indexes = [
            models.Index(fields=['diary', 'split_date']),
        ]
        verbose_name = '株式分割'
        verbose_name_plural = '株式分割'

    def __str__(self):
        return f"{self.diary.stock_name} - {self.split_date} ({self.split_ratio}倍)"

    def clean(self):
        """バリデーション"""
        super().clean()
        
        if self.split_ratio <= 0:
            raise ValidationError({'split_ratio': '分割比率は正の数を入力してください'})
        
        # 適用済みの場合は削除・編集不可
        if self.is_applied and self.pk:
            old_split = StockSplit.objects.get(pk=self.pk)
            if old_split.is_applied:
                raise ValidationError('適用済みの分割情報は編集できません')

    def apply_split(self):
        from .services.aggregate_service import AggregateService

        # 対象取引（分割日より前）
        transactions = list(self.diary.transactions.filter(
            transaction_date__lt=self.split_date
        ))

        for tx in transactions:
            tx.quantity = tx.quantity * self.split_ratio
            tx.price = tx.price / self.split_ratio

        # bulk_update は Transaction.save() を経由しないため自動再集計が走らない。
        # deferred() で囲み、ブロック出口で一度だけ再集計する（O(N)→O(1)、かつ
        # 再集計の呼び忘れを構造的に防ぐ）。
        with AggregateService.deferred(self.diary):
            if transactions:
                Transaction.objects.bulk_update(transactions, ["quantity", "price"])

            # フラグ更新
            self.is_applied = True
            self.applied_at = timezone.now()
            self.save(update_fields=["is_applied", "applied_at"])


class DiaryNote(models.Model):
    """日記への継続的な追記"""
    # 振り返り(retrospective)ノートの固定テーマ。
    # テーマ別ビューで1スレッドに集約し、複数売買ラウンドの総括を時系列で束ねる
    RETROSPECTIVE_TOPIC = '振り返り'

    diary = models.ForeignKey(StockDiary, on_delete=models.CASCADE, related_name='notes')
    date = models.DateField()
    content = models.TextField(verbose_name='記録内容', blank=True, max_length=5000)
    current_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, 
                                       verbose_name='記録時点の価格')
    
    image = models.ImageField(upload_to=get_note_image_path, null=True, blank=True, help_text="継続記録に関連する画像")
    
    TYPE_CHOICES = [
        ('analysis', '分析更新'),
        ('news', 'ニュース'),
        ('earnings', '決算情報'),
        ('insight', '新たな気づき'),
        ('risk', 'リスク要因'),
        ('retrospective', '振り返り'),
        ('other', 'その他')
    ]
    note_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='analysis')

    topic = models.CharField('トピック', max_length=50, blank=True, default='', db_index=True,
                             help_text='分析テーマ（例: ナフサの影響）。空欄可')

    source_doc_id = models.CharField('参照書類ID', max_length=8, null=True, blank=True,
                                     help_text='関連するEDINET書類のdoc_id')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']
        verbose_name = '継続記録'
        verbose_name_plural = '継続記録'
    
    def __str__(self):
        return f"{self.diary.stock_name} - {self.date}"
    
    def clean(self):
        super().clean()
        self.content = sanitize_text_content(self.content)
        if self.content and len(self.content) > 5000:
            raise ValidationError({'content': '記録内容は5000文字以内で入力してください'})
        # 振り返りはテーマ未指定なら固定テーマでスレッド集約（明示指定は上書きしない）
        if self.note_type == 'retrospective' and not (self.topic or '').strip():
            self.topic = self.RETROSPECTIVE_TOPIC
    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        
        # 親日記のupdated_atを更新
        self.diary.updated_at = timezone.now()
        self.diary.save(update_fields=['updated_at'])

    def process_and_save_image(self, image_file):
        """画像を非同期で圧縮・保存する。元画像をまず保存してからdjango-qでリサイズする。"""
        from .services.image_service import ImageService
        try:
            if self.image:
                self.image.delete(save=False)

            filename = f"{uuid.uuid4().hex}{os.path.splitext(image_file.name)[1]}"
            self.image.save(filename, image_file, save=True)

            from django_q.tasks import async_task
            async_task('stockdiary.tasks.compress_note_image', self.id)
            return True

        except Exception as e:
            logger.error("Image upload failed for DiaryNote(id=%s): %s", self.id, e, exc_info=True)
            # フォールバック: 同期圧縮
            try:
                image_file.seek(0)
                return ImageService.compress_and_save(self, image_file, max_size=(600, 400), quality=80)
            except Exception:
                return False

    def delete_image(self):
        """画像を削除"""
        try:
            if self.image:
                self.image.delete(save=False)
                self.image = None
                self.save(update_fields=['image'])
                return True
        except Exception as e:
            logger.error(f"Note image deletion failed for DiaryNote: {str(e)}", exc_info=True)
        return False

    def get_image_url(self):
        """画像URLを取得"""
        if self.image:
            return reverse('stockdiary:serve_image', kwargs={
                'diary_id': self.diary.id,
                'image_type': 'note',
                'note_id': self.id
            })
        return None

    @property
    def image_url(self):
        return self.get_image_url()
    
    def get_price_change(self):
        """購入価格からの変動率を計算"""
        if self.current_price is not None and self.diary.average_purchase_price:
            change = ((self.current_price - self.diary.average_purchase_price) / self.diary.average_purchase_price) * 100
            return change
        return None

class PushSubscription(models.Model):
    """PWAのプッシュ通知サブスクリプション"""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE,
        related_name='push_subscriptions'
    )
    endpoint = models.URLField(max_length=500, unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    device_name = models.CharField(max_length=100, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'プッシュ通知サブスクリプション'
        verbose_name_plural = 'プッシュ通知サブスクリプション'
        indexes = [
            models.Index(fields=['user', 'is_active']),
        ]
    
    def __str__(self):
        return f'{self.user.username} - {self.device_name or "Unknown Device"}'


class DiaryNotification(models.Model):
    """日記の通知設定（リマインダーのみ）"""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    diary = models.ForeignKey(
        'StockDiary',
        on_delete=models.CASCADE,
        related_name='notifications'
    )
    remind_at = models.DateTimeField(verbose_name='通知日時')
    message = models.TextField(max_length=200, blank=True, verbose_name='メッセージ')
    is_active = models.BooleanField(default=True, verbose_name='有効')
    last_sent = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = '日記通知設定'
        verbose_name_plural = '日記通知設定'
        indexes = [
            models.Index(fields=['diary', 'is_active']),
        ]


class NotificationLog(models.Model):
    """通知送信履歴"""
    notification = models.ForeignKey(
        'DiaryNotification',
        on_delete=models.CASCADE,
        related_name='logs',
        null=True, blank=True
    )
    # 開示イベント由来のアプリ内通知（リマインダー以外）。
    # (user, disclosure_event) の一意制約でファンアウト時の重複を防ぐ
    disclosure_event = models.ForeignKey(
        'earnings_analysis.DisclosureEvent',
        on_delete=models.CASCADE,
        related_name='notification_logs',
        null=True, blank=True
    )
    # 決算予定由来の「決算前日」通知。(user, earnings_schedule) の一意制約で重複を防ぐ。
    # EarningsSchedule は日次同期で未来分を洗い替えする（行が消える）ため、送信済みの
    # 通知履歴が巻き添えで消えないよう SET_NULL にする（DisclosureEvent は追記専用で
    # 削除されないため CASCADE で問題ないが、こちらは削除され得る点が異なる）。
    earnings_schedule = models.ForeignKey(
        'earnings_analysis.EarningsSchedule',
        on_delete=models.SET_NULL,
        related_name='notification_logs',
        null=True, blank=True
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notification_logs'
    )
    title = models.CharField(max_length=100)
    message = models.TextField(max_length=500)
    url = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)
    is_clicked = models.BooleanField(default=False)
    sent_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = '通知履歴'
        verbose_name_plural = '通知履歴'
        ordering = ['-sent_at']
        indexes = [
            models.Index(fields=['user', 'is_read', '-sent_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'disclosure_event'],
                condition=models.Q(disclosure_event__isnull=False),
                name='uniq_user_disclosure_event',
            ),
            models.UniqueConstraint(
                fields=['user', 'earnings_schedule'],
                condition=models.Q(earnings_schedule__isnull=False),
                name='uniq_user_earnings_schedule',
            ),
        ]


class DiaryTagDirection(models.Model):
    """日記×タグ単位の方向属性（FR-4）。

    同一タグを共有しても方向が逆（例: 銀行は@金利上昇↑、REITは↓）の場合、
    正の関連ではなく逆相関候補として扱うためのメタデータ。
    """
    DIRECTION_UP      = 'up'
    DIRECTION_DOWN    = 'down'
    DIRECTION_NEUTRAL = 'neutral'

    DIRECTION_CHOICES = [
        (DIRECTION_UP,      '↑ プラス影響'),
        (DIRECTION_DOWN,    '↓ マイナス影響'),
        (DIRECTION_NEUTRAL, '→ 中立'),
    ]

    diary     = models.ForeignKey(
        'StockDiary', on_delete=models.CASCADE, related_name='tag_directions',
    )
    tag       = models.ForeignKey(
        'tags.Tag', on_delete=models.CASCADE, related_name='diary_directions',
    )
    direction = models.CharField(
        max_length=10, choices=DIRECTION_CHOICES, default=DIRECTION_NEUTRAL,
        verbose_name='影響方向',
    )

    class Meta:
        unique_together = ['diary', 'tag']
        verbose_name = 'タグ方向属性'
        verbose_name_plural = 'タグ方向属性'

    def __str__(self):
        return f'{self.diary.stock_name} × @{self.tag.name} ({self.direction})'


class Thesis(models.Model):
    """投資仮説（検証可能な主張）。

    reason（自由文の投資理由）は残しつつ、その隣に「答え合わせ可能な主張」を
    構造で持つ。review_due_date がホーム想起と継続のトリガーになる。
    成長OSの検証ループ（予想→結果→検証→学び）の起点。
    """
    diary = models.ForeignKey(StockDiary, on_delete=models.CASCADE, related_name='theses')
    claim = models.CharField('主張', max_length=500,
                             help_text='この投資の主張（複数行可）。例: 円安継続で輸出採算が改善する')
    basis_tags = models.ManyToManyField(Tag, blank=True, related_name='theses',
                                        verbose_name='根拠の軸')
    basis = models.TextField('根拠・理由', blank=True, max_length=1000,
                             help_text='なぜこの主張が成り立つと考えるか（文章）')

    # 「確認の目印」＝答え合わせで見るものを1つ先にコミットする（賭け化）。
    # 数値のモデル化ではなく観測点の事前コミット＝検証を「白紙から書く」から
    # 「決めた目印を答える」に軽くする（→ docs/thesis_capture_redesign.md）。
    CHECKPOINT_DIR_CHOICES = [
        ('up', '上がる'),
        ('down', '下がる'),
        ('flat', '横ばい'),
        ('happened', '実現する'),
        ('not_happened', '起きない'),
    ]
    checkpoint = models.CharField('確認の目印', max_length=200, blank=True,
                                  help_text='答え合わせで見るものを1つ。数字に限らずKPI・イベント結果・定性チェックでも可。例: 次決算の資金利益')
    checkpoint_direction = models.CharField('目印の向き', max_length=16,
                                            choices=CHECKPOINT_DIR_CHOICES, blank=True)

    HORIZON_CHOICES = [
        ('next_earnings', '次の決算まで'),
        ('3m', '3ヶ月'),
        ('6m', '6ヶ月'),
        ('1y', '1年'),
        ('long', '長期（1年超）'),
    ]
    horizon = models.CharField('想定検証期間', max_length=20, choices=HORIZON_CHOICES, default='next_earnings')
    worst_case = models.CharField('最悪のケース', max_length=300, blank=True,
                                  help_text='この仮説が崩れるとしたら何が起きたときか')
    review_due_date = models.DateField('検証予定日', null=True, blank=True, db_index=True)

    STATUS_OPEN = 'open'
    STATUS_VERIFIED = 'verified'
    STATUS_ABANDONED = 'abandoned'
    STATUS_CHOICES = [
        (STATUS_OPEN, '未検証'),
        (STATUS_VERIFIED, '検証済み'),
        (STATUS_ABANDONED, '取り下げ'),
    ]
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '仮説'
        verbose_name_plural = '仮説'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.diary.stock_name}: {self.claim[:30]}'

    @property
    def is_due(self):
        """検証予定日が到来した未検証の仮説か（ホーム想起の判定）"""
        if self.status != self.STATUS_OPEN or not self.review_due_date:
            return False
        return self.review_due_date <= timezone.localdate()

    @staticmethod
    def compose_claim(checkpoint, direction):
        """確認の目印＋向きから主張文を組み立てる（claim 未入力時の自動生成）。

        例: ('次決算の資金利益','up') → '次決算の資金利益が上がる'。
        向きが無ければ目印そのものを主張とする。目印も無ければ空。
        """
        checkpoint = (checkpoint or '').strip()
        if not checkpoint:
            return ''
        label = dict(Thesis.CHECKPOINT_DIR_CHOICES).get(direction or '', '')
        return f'{checkpoint}が{label}' if label else checkpoint


class Verdict(models.Model):
    """検証：仮説の当否を損益と分離して記録する。

    意思決定の質（仮説の当否・判断の質）と結果（損益）を別フィールドで持ち、
    その組み合わせ（2×2）を成長OSの心臓として可視化する。
    「仮説は正しいが損失」「仮説は外れたが利益」を一級市民にする。
    """
    thesis = models.OneToOneField(Thesis, on_delete=models.CASCADE, related_name='verdict')

    HYP_HIT = 'hit'
    HYP_PARTIAL = 'partial'
    HYP_MISS = 'miss'
    HYP_UNKNOWN = 'unknown'
    HYP_CHOICES = [
        (HYP_HIT, '的中'),
        (HYP_PARTIAL, '部分的中'),
        (HYP_MISS, '外れ'),
        (HYP_UNKNOWN, '判定不能'),
    ]
    hypothesis_result = models.CharField('仮説の当否', max_length=10, choices=HYP_CHOICES)

    PNL_PROFIT = 'profit'
    PNL_LOSS = 'loss'
    PNL_FLAT = 'flat'
    PNL_HOLDING = 'holding'
    PNL_CHOICES = [
        (PNL_PROFIT, '利益'),
        (PNL_LOSS, '損失'),
        (PNL_FLAT, 'ほぼ変わらず'),
        (PNL_HOLDING, '保有中'),
    ]
    pnl_result = models.CharField('損益の結果', max_length=10, choices=PNL_CHOICES)

    decision_quality = models.PositiveSmallIntegerField('判断の質', default=3,
                                                        help_text='1〜5。再現したい判断ほど高い')
    missed_factor = models.CharField('見落とした要因', max_length=300, blank=True)
    is_repeatable = models.BooleanField('再現したい判断', default=False)
    learning = models.CharField('学び', max_length=200, blank=True,
                                help_text='次に活かす一文（引用される学びの原子）')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '検証'
        verbose_name_plural = '検証'

    def __str__(self):
        return f'{self.thesis.diary.stock_name}: {self.get_hypothesis_result_display()} × {self.get_pnl_result_display()}'

    @property
    def hyp_ok(self):
        # 「何を的中とみなすか」の定義は services.metrics（セマンティックレイヤー）が正。
        from .services import metrics
        return metrics.is_hypothesis_hit(self.hypothesis_result)

    @property
    def pnl_ok(self):
        from .services import metrics
        return metrics.is_pnl_win(self.pnl_result)

    @property
    def quadrant(self):
        """意思決定の質 × 結果 の象限を返す。"""
        from .services import metrics
        return metrics.quadrant_of(self.hyp_ok, self.pnl_ok)

    @property
    def quadrant_label(self):
        from .services import metrics
        return metrics.QUADRANT_LABELS[self.quadrant]

    @property
    def stars(self):
        q = max(1, min(5, self.decision_quality or 0))
        return '★' * q + '☆' * (5 - q)


class ReasonVersion(models.Model):
    """「現在の見立て」(StockDiary.reason) の来歴（過去版）。

    reason はユーザーが上書き編集する単一スロット。保存時に内容差分があれば、
    上書き前の版をここへ自動スナップショットして残す（明示操作不要）。
    - 「判断の記録(Thesis/Verdict/retrospective)」ではなく **来歴(provenance)** であり、
      時系列(DiaryNote)とは別レイヤ。メインの継続記録一覧には混ぜず、
      専用「見立ての変遷」ビューと全文検索でのみ辿れる（汚さない×失わない）。
    - coalesce: 近接編集（同一セッション）の途中版を乱造しないよう、
      直近版が COALESCE_WINDOW 内なら新規版を作らない。
    詳細は docs/diary_recording_redesign.md。
    """
    # 近接編集をまとめる時間窓（この窓内の連続編集は版を増やさない）
    COALESCE_WINDOW = timedelta(minutes=30)

    diary = models.ForeignKey(
        StockDiary, on_delete=models.CASCADE, related_name='reason_versions',
        verbose_name='日記',
    )
    content = models.TextField('内容', blank=True, max_length=5000)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = '見立ての版'
        verbose_name_plural = '見立ての版'
        indexes = [
            models.Index(fields=['diary', '-created_at']),
        ]

    def __str__(self):
        return f'{self.diary.stock_name} 見立て版 @ {self.created_at:%Y-%m-%d %H:%M}'

    @classmethod
    def snapshot_on_change(cls, diary, previous_content):
        """reason 上書き保存の直後に呼ぶ。前版(previous_content)を必要なら退避する。

        作らない条件：
          - 差分なし（previous == current）
          - previous が実質空（残す価値のある前版が無い）
          - 直近版が COALESCE_WINDOW 内（同一編集セッションの途中版を増やさない）

        Returns: 作成した ReasonVersion または None。
        """
        previous = (previous_content or '').strip()
        current = (diary.reason or '').strip()
        if not previous or previous == current:
            return None

        latest = cls.objects.filter(diary=diary).order_by('-created_at').first()
        if latest and (timezone.now() - latest.created_at) < cls.COALESCE_WINDOW:
            return None

        return cls.objects.create(diary=diary, content=previous_content or '')
