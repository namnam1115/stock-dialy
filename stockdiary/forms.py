# stockdiary/forms.py
from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import ProhibitNullCharactersValidator, MaxLengthValidator
from .models import StockDiary, Transaction, StockSplit, DiaryNote, Thesis, Verdict, sanitize_text_content
from .utils import detect_currency
from .services.migration_export_service import SECTION_CHOICES as _SECTION_CHOICES
from decimal import Decimal


class StockDiaryForm(forms.ModelForm):
    """日記作成・編集フォーム（基本情報のみ）"""
    
    # 画像アップロード用フィールド
    image = forms.FileField(
        required=False,
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': 'image/jpeg,image/jpg,image/png,image/gif,image/webp',
            'title': '画像ファイル（JPEG, PNG, GIF, WebP）のみ'
        }),
        help_text="日記に関連する画像（チャート、スクリーンショット等）"
    )
        
    # 初回取引は作成フローから除去（取引は詳細ページで追加する）。
    # docs/diary_recording_redesign.md の方針に基づく。

    # 同一銘柄の日記が既にある場合の重複作成許可フラグ
    # （通常は既存日記への追記を促し、ユーザーが明示した場合のみ重複を許可する）
    allow_duplicate = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.HiddenInput(attrs={'id': 'id_allow_duplicate'})
    )

    class Meta:
        model = StockDiary
        # memo は廃止（書く場所を reason=背景 / DiaryNote=時系列の追記 の2層に整理）。
        # reason＝背景（どんな会社か・着目したニュース/テーマ）。購入理由・決算・ニュースの
        # 都度の記録は DiaryNote の topic スレッドへ（docs/diary_recording_redesign.md §3）。
        # 既存データは detail で読み取り専用表示のみ（移行マイグレーションは行わない）
        fields = [
            'stock_symbol', 'stock_name', 'currency', 'reason',
            'sector'
        ]
        widgets = {
            'stock_symbol': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': '日本株: 7203 など',
                'maxlength': '50',
            }),
            'currency': forms.Select(attrs={'class': 'form-select'}),
            'stock_name': forms.TextInput(attrs={
                'class': 'form-control', 
                'required': 'required',
                'maxlength': '100',
            }),
            'reason': forms.Textarea(attrs={
                'rows': 10,
                'class': 'form-control',
                'maxlength': '5000',
                'id': 'id_reason',
                'placeholder': 'どんな会社か・着目したニュースやテーマなど、この記録の背景を書く（Markdown対応）\n\n📝 見出し: # 見出し\n🏷️ タグ: @成長株 @配当 @長期保有\n\n例:\n## どんな会社か\n成長性が高く、配当も安定している。\nタグ: @成長株 @配当\n\n※購入理由や決算・ニュースの都度の記録は、継続記録（トピック）に残します。'
            }),
            'sector': forms.TextInput(attrs={
                'class': 'form-control',
                'maxlength': '50',
                'placeholder': '例: 電気機器'
            }),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super(StockDiaryForm, self).__init__(*args, **kwargs)
        self.user = user
        # 重複候補（clean で設定。テンプレートが既存日記への導線表示に使う）
        self.duplicate_diary = None
        self.duplicate_retrospective_count = 0

        # ラベル設定
        self.fields['stock_symbol'].label = "銘柄コード（任意）"
        self.fields['stock_symbol'].help_text = "日本株コードは円建て、それ以外は米ドル建てとして自動判定します。"
        self.fields['stock_symbol'].required = False
        self.fields['currency'].label = "通貨"
        self.fields['currency'].help_text = "銘柄コードから自動判定されます。必要に応じて変更できます。"
        self.fields['currency'].required = False
        self.fields['reason'].label = "背景"
        self.fields['reason'].help_text = "どんな会社か・着目したニュースやテーマなど、この記録の背景。購入理由や決算・ニュースの都度の追記は継続記録（トピック）に残します。Markdown対応・@タグで検索可（例: @成長株 @配当）"

    def clean_stock_name(self):
        """銘柄名のバリデーション"""
        stock_name = self.cleaned_data.get('stock_name')
        if stock_name and len(stock_name) > 100:
            raise ValidationError('銘柄名は100文字以内で入力してください。')
        return stock_name

    def clean_reason(self):
        """背景のバリデーション"""
        reason = self.cleaned_data.get('reason')
        if reason and len(reason) > 5000:
            raise ValidationError('背景は5000文字以内で入力してください。')
        return reason

    def clean_stock_symbol(self):
        """銘柄コードのバリデーション"""
        stock_symbol = self.cleaned_data.get('stock_symbol')
        if stock_symbol and len(stock_symbol) > 50:
            raise ValidationError('銘柄コードは50文字以内で入力してください。')
        return stock_symbol

    def clean_sector(self):
        """業種のバリデーション"""
        sector = self.cleaned_data.get('sector')
        if sector and len(sector) > 50:
            raise ValidationError('業種は50文字以内で入力してください。')
        return sector

    def clean(self):
        """重複日記チェック（初回取引は作成フローから除去済み）"""
        cleaned_data = super().clean()

        # 新規作成時のみ: 同一銘柄の既存日記があれば追記を促す
        # （allow_duplicate が明示された場合のみ重複作成を許可）
        if not self.instance.pk and self.user:
            from .utils import find_duplicate_diaries
            duplicates = find_duplicate_diaries(
                self.user,
                stock_symbol=cleaned_data.get('stock_symbol') or '',
                stock_name=cleaned_data.get('stock_name') or '',
            )
            self.duplicate_diary = duplicates.first()
            # 再エントリー時の教訓想起用（テンプレートが警告パネルに表示）
            self.duplicate_retrospective_count = (
                self.duplicate_diary.notes.filter(note_type='retrospective').count()
                if self.duplicate_diary else 0
            )
            if self.duplicate_diary and not cleaned_data.get('allow_duplicate'):
                self.add_error(
                    'stock_symbol',
                    'この銘柄の日記が既にあります。考えの変化は既存日記の「継続記録」への追記がおすすめです。'
                    'それでも別の日記として作成する場合は、下の案内から「新しい日記として作成」を選んでください。'
                )

        return cleaned_data

    def save(self, commit=True):
        diary = super().save(commit=False)
        # 新規作成時は銘柄コードから通貨を自動判定（編集時はフォームの値を尊重＝手動上書き可）
        if diary._state.adding:
            diary.currency = detect_currency(diary.stock_symbol)
        if commit:
            diary.save()
            self.save_m2m()
        return diary


class TransactionForm(forms.ModelForm):
    """取引追加・編集フォーム"""
    
    class Meta:
        model = Transaction
        fields = ['transaction_type', 'transaction_date', 'price', 'quantity', 'memo']
        widgets = {
            'transaction_type': forms.Select(attrs={
                'class': 'form-select',
                'required': 'required'
            }),
            'transaction_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'form-control',
                'required': 'required'
            }),
            'price': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.01',
                'min': '0.01',
                'max': '9999999.99',  # 最大999万円
                'placeholder': '例: 1000.00',
                'required': 'required'
            }),
            'quantity': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '1',
                'min': '1',
                'max': '99999999',  # 最大9999万株
                'placeholder': '例: 100',
                'required': 'required'
            }),
            'memo': forms.Textarea(attrs={
                'rows': 3,
                'class': 'form-control',
                'placeholder': '取引に関するメモ（任意）',
                'maxlength': '500'
            }),
        }
        labels = {
            'transaction_type': '取引種別',
            'transaction_date': '取引日',
            'price': '単価',
            'quantity': '数量（株）',
            'memo': 'メモ'
        }
        help_texts = {
            'price': '1株あたりの単価を入力',
            'quantity': '取引する株数を入力',
            'memo': '取引に関する補足情報（500文字以内）'
        }

    def __init__(self, *args, **kwargs):
        self.diary = kwargs.pop('diary', None)
        super(TransactionForm, self).__init__(*args, **kwargs)

        # 取引対象の日記の通貨に合わせて単価ラベルを切り替える
        if self.diary is not None:
            self.fields['price'].label = f'単価（{self.diary.currency_unit}）'

        # 初期値設定（新規作成時）
        if not self.instance.pk:
            from django.utils import timezone
            self.fields['transaction_date'].initial = timezone.now().date()
            self.fields['transaction_type'].initial = 'buy'

    def clean_price(self):
        """単価のバリデーション"""
        price = self.cleaned_data.get('price')
        if price is not None:
            if price <= 0:
                raise ValidationError('単価は正の数を入力してください')
            if price > Decimal('9999999.99'):
                raise ValidationError('単価は9999999.99以下で入力してください')
        return price

    def clean_quantity(self):
        """数量のバリデーション"""
        quantity = self.cleaned_data.get('quantity')
        if quantity is not None:
            if quantity <= 0:
                raise ValidationError('数量は正の数を入力してください')
            if quantity > Decimal('99999999'):
                raise ValidationError('数量は9999万株以下で入力してください')
        return quantity

    def clean_memo(self):
        """メモのバリデーション"""
        memo = self.cleaned_data.get('memo')
        if memo and len(memo) > 500:
            raise ValidationError('メモは500文字以内で入力してください')
        return memo

    def clean(self):
        """売却時の保有数チェック"""
        cleaned_data = super().clean()
        transaction_type = cleaned_data.get('transaction_type')
        quantity = cleaned_data.get('quantity')
        
        if transaction_type == 'sell' and quantity and self.diary:
            # 現在の保有数を取得
            current_holdings = self.diary.current_quantity
            
            # 編集時は元の取引を除外して計算
            if self.instance.pk:
                old_transaction = Transaction.objects.get(pk=self.instance.pk)
                if old_transaction.transaction_type == 'sell':
                    current_holdings += old_transaction.quantity
            
            if quantity > current_holdings:
                raise ValidationError({
                    'quantity': f'保有数（{current_holdings}株）を超える売却はできません'
                })
        
        return cleaned_data


class StockSplitForm(forms.ModelForm):
    """株式分割フォーム"""
    
    class Meta:
        model = StockSplit
        fields = ['split_date', 'split_ratio', 'memo']
        widgets = {
            'split_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'form-control',
                'required': 'required'
            }),
            'split_ratio': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.0001',
                'min': '0.0001',
                'placeholder': '例: 2.0（1→2株の分割）',
                'required': 'required'
            }),
            'memo': forms.Textarea(attrs={
                'rows': 3,
                'class': 'form-control',
                'placeholder': '分割に関するメモ（任意）',
                'maxlength': '500'
            }),
        }
        labels = {
            'split_date': '分割実行日',
            'split_ratio': '分割比率',
            'memo': 'メモ'
        }
        help_texts = {
            'split_date': '株式分割が実行される日付',
            'split_ratio': '1株が何株になるか（例: 1→2株なら「2.0」）',
            'memo': '分割に関する補足情報（500文字以内）'
        }

    def __init__(self, *args, **kwargs):
        super(StockSplitForm, self).__init__(*args, **kwargs)
        
        # 初期値設定
        if not self.instance.pk:
            from django.utils import timezone
            self.fields['split_date'].initial = timezone.now().date()

    def clean_split_ratio(self):
        """分割比率のバリデーション"""
        split_ratio = self.cleaned_data.get('split_ratio')
        if split_ratio is not None and split_ratio <= 0:
            raise ValidationError('分割比率は正の数を入力してください')
        return split_ratio

    def clean_memo(self):
        """メモのバリデーション"""
        memo = self.cleaned_data.get('memo')
        if memo and len(memo) > 500:
            raise ValidationError('メモは500文字以内で入力してください')
        return memo


class DiaryNoteForm(forms.ModelForm):
    """継続記録フォーム"""
    
    class Meta:
        model = DiaryNote
        fields = ['date', 'note_type', 'topic', 'content', 'current_price']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'note_type': forms.Select(attrs={'class': 'form-select'}),
            'topic': forms.TextInput(attrs={'class': 'form-control', 'maxlength': '50'}),
            'content': forms.Textarea(attrs={
                'rows': 5,
                'class': 'form-control',
                'maxlength': '5000',
            }),
            'current_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # NULL文字除去・最大長チェックはclean_contentでサニタイズ・改行正規化
        # してから行う。フィールドレベルの検証を残すと、送信時にCRLFへ変換された
        # 生の値で文字数超過と判定され、無言で弾かれてしまうため外す。
        self.fields['content'].validators = [
            v for v in self.fields['content'].validators
            if not isinstance(v, (ProhibitNullCharactersValidator, MaxLengthValidator))
        ]

    def clean_content(self):
        """記録内容のバリデーション"""
        content = sanitize_text_content(self.cleaned_data.get('content'))
        if content and len(content) > 5000:
            raise ValidationError('記録内容は5000文字以内で入力してください。')
        return content

class TradeUploadForm(forms.Form):
    """取引履歴アップロードフォーム"""
    BROKER_CHOICES = [
        ('rakuten', '楽天証券'),
        ('sbi', 'SBI証券'),  # 将来的に対応
    ]
    
    broker = forms.ChoiceField(
        label='証券会社',
        choices=BROKER_CHOICES,
        initial='rakuten',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    
    csv_file = forms.FileField(
        label='取引履歴CSVファイル',
        help_text='楽天証券からダウンロードした取引履歴CSVファイルを選択してください',
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.csv'
        })
    )
    
    def clean_csv_file(self):
        csv_file = self.cleaned_data.get('csv_file')

        if csv_file:
            # ファイルサイズチェック（10MB以下）
            if csv_file.size > 10 * 1024 * 1024:
                raise forms.ValidationError('ファイルサイズは10MB以下にしてください')

            # ファイル拡張子チェック
            if not csv_file.name.endswith('.csv'):
                raise forms.ValidationError('CSVファイルを選択してください')

        return csv_file


class DataExportForm(forms.Form):
    """日記データの移行エクスポートフォーム（形式選択）"""
    FORMAT_CHOICES = [
        ('json', 'JSON（完全移行・推奨）'),
        ('csv', 'CSV（ZIP・表計算編集向け）'),
    ]

    export_format = forms.ChoiceField(
        label='出力形式',
        choices=FORMAT_CHOICES,
        initial='json',
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'})
    )


class SelectiveExportForm(forms.Form):
    """機能ごとのエクスポートフォーム（含める関連データを選択）。

    日記本体（銘柄・投資理由）は常に含め、ここで選んだ関連データのみを追加する。
    LLM に渡して分析する用途で、不要なデータを削ってファイルを軽くするのが目的（Issue #356）。
    """
    sections = forms.MultipleChoiceField(
        label='含める項目（日記本体は常に含まれます）',
        choices=_SECTION_CHOICES,
        initial=['notes'],
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
    )


class DataImportForm(forms.Form):
    """日記データの移行インポートフォーム"""
    data_file = forms.FileField(
        label='移行データファイル',
        help_text='カブログでエクスポートした .json または .zip ファイルを選択してください',
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.json,.zip'
        })
    )

    def clean_data_file(self):
        data_file = self.cleaned_data.get('data_file')

        if data_file:
            # ファイルサイズチェック（20MB以下）
            if data_file.size > 20 * 1024 * 1024:
                raise forms.ValidationError('ファイルサイズは20MB以下にしてください')

            name = (data_file.name or '').lower()
            if not (name.endswith('.json') or name.endswith('.zip')):
                raise forms.ValidationError('.json または .zip ファイルを選択してください')

        return data_file


class ThesisForm(forms.ModelForm):
    """投資仮説（検証可能な主張）の作成・編集フォーム。"""

    class Meta:
        model = Thesis
        fields = ['checkpoint', 'checkpoint_direction', 'claim',
                  'worst_case', 'horizon', 'review_due_date', 'basis_tags', 'basis']
        widgets = {
            'checkpoint': forms.TextInput(attrs={
                'class': 'form-control', 'maxlength': 200,
                'placeholder': '例: 次決算の資金利益 / 販売台数 / TOBの成否',
            }),
            'checkpoint_direction': forms.Select(attrs={'class': 'form-select'}),
            'claim': forms.Textarea(attrs={
                'class': 'form-control', 'maxlength': 500, 'rows': 3,
                'placeholder': '空欄なら「目印＋向き」から自動で作ります。自由に書きたいときはここへ（例: 円安継続で輸出採算が改善する）',
            }),
            'basis_tags': forms.SelectMultiple(attrs={'class': 'form-select'}),
            'basis': forms.Textarea(attrs={
                'class': 'form-control', 'maxlength': 1000, 'rows': 4,
                'placeholder': 'なぜそう考えるか（根拠・理由）を文章で。例: 受注残が積み上がり、来期も二桁増益が見込める',
            }),
            'horizon': forms.Select(attrs={'class': 'form-select'}),
            'worst_case': forms.TextInput(attrs={
                'class': 'form-control', 'maxlength': 300,
                'placeholder': '例: 為替が円高に反転し、想定の前提が崩れたとき',
            }),
            'review_due_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            from tags.models import Tag
            self.fields['basis_tags'].queryset = Tag.objects.filter(user=user)
        self.fields['basis_tags'].required = False
        self.fields['basis'].required = False
        self.fields['worst_case'].required = False
        self.fields['review_due_date'].required = False
        self.fields['checkpoint'].required = False
        self.fields['checkpoint_direction'].required = False
        # claim は任意化：目印から自動生成する経路を許すため（clean で担保）
        self.fields['claim'].required = False

    def clean(self):
        """賭け化：claim 未入力なら「確認の目印＋向き」から主張を自動生成する。

        目印も主張も無ければエラー（少なくとも一方は必要）。自由記述したい人は
        claim を直接書けばよく、それがそのまま自由記述モードの逃げ道になる。
        """
        cleaned = super().clean()
        claim = (cleaned.get('claim') or '').strip()
        checkpoint = cleaned.get('checkpoint') or ''
        direction = cleaned.get('checkpoint_direction') or ''
        if not claim:
            claim = Thesis.compose_claim(checkpoint, direction)
            cleaned['claim'] = claim
        if not claim:
            self.add_error('checkpoint',
                           '確認の目印か主張のどちらかを入力してください。')
        return cleaned


class VerdictForm(forms.ModelForm):
    """検証（仮説の当否を損益と分離して記録）フォーム。"""

    class Meta:
        model = Verdict
        fields = ['hypothesis_result', 'pnl_result', 'decision_quality',
                  'missed_factor', 'is_repeatable', 'learning']
        widgets = {
            'hypothesis_result': forms.RadioSelect(),
            'pnl_result': forms.RadioSelect(),
            # decision_quality はテンプレート側で星タップUI（hidden input）として描画する。
            # ここでウィジェットは指定しない（IntegerField として整数をそのまま受ける）。
            'missed_factor': forms.TextInput(attrs={
                'class': 'form-control', 'maxlength': 300,
                'placeholder': '例: 為替より米金利の影響を過小評価していた',
            }),
            'learning': forms.TextInput(attrs={
                'class': 'form-control', 'maxlength': 200,
                'placeholder': '例: 為替単独を根拠に投資しない',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 答え合わせの主質問は「仮説の当否＋学び」の2つ。それ以外は任意にして
        # 最小入力で保存できるようにする（検証の心理的コストを下げる）
        for f in ('missed_factor', 'is_repeatable', 'learning', 'decision_quality'):
            self.fields[f].required = False
        # RadioSelect の先頭の空選択肢（---------）を除去
        self.fields['hypothesis_result'].choices = Verdict.HYP_CHOICES
        self.fields['pnl_result'].choices = Verdict.PNL_CHOICES

    def clean_decision_quality(self):
        # 未入力はモデル既定の3（普通）に落とす
        return self.cleaned_data.get('decision_quality') or 3