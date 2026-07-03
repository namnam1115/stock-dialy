# earnings_analysis/models/earnings.py
"""決算予定（決算発表スケジュール）モデル

外部の決算予定API（EDINET DB の /v1/calendar 等）から日次バッチで取得した
「今後の決算発表予定日」をローカルDBに保持する。画面表示時はAPIを叩かず、
このテーブルのみを参照する（API障害・レスポンス速度の影響を受けない設計）。

データ取得は earnings_analysis/services/earnings_calendar_sync.py が担う。
このテーブルが決算予定の唯一の正（証券コードがキーのマスタ）。日記側には決算日を
持たせず、表示時に銘柄コードで都度 join して引く
（stockdiary/views_earnings.py の get_next_earnings_map / attach_next_earnings）。
"""
from django.db import models


class EarningsSchedule(models.Model):
    """1銘柄の1回分の決算発表予定。

    (securities_code, earnings_date) で一意。日次バッチは取得期間
    （当日〜90日後）のレコードを洗い替えするため、リスケや取り下げが
    あっても未来分は常に最新のAPI内容と一致する。
    """

    securities_code = models.CharField(
        '証券コード', max_length=5, db_index=True,
        help_text='証券コード（4桁または末尾0付き5桁）'
    )
    company_name = models.CharField('企業名', max_length=255, blank=True)
    earnings_date = models.DateField('決算予定日', db_index=True)
    # 予想日(estimated)か確定日(confirmed)か。将来分の多くは前年実績+約1年の
    # 予想で数日ずれる。既定は True（＝予想）とし、確定が取れたときだけ False。
    is_estimated = models.BooleanField('予想日フラグ', default=True, db_index=True)
    # 本決算・第1四半期・第2四半期 など。APIの値をそのまま保持する。
    earnings_type = models.CharField('決算種別', max_length=50, blank=True)
    market_segment = models.CharField('市場区分', max_length=50, blank=True)

    # APIが返す更新日時（あれば）。無ければ取得時刻で代替する。
    source_updated_at = models.DateTimeField('提供元更新日時', null=True, blank=True)

    created_at = models.DateTimeField('作成日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)

    class Meta:
        verbose_name = '決算予定'
        verbose_name_plural = '決算予定'
        ordering = ['earnings_date', 'securities_code']
        indexes = [
            models.Index(fields=['earnings_date', 'securities_code']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['securities_code', 'earnings_date'],
                name='uniq_earnings_code_date',
            ),
        ]

    def __str__(self):
        return f"{self.securities_code} {self.company_name} 決算予定 {self.earnings_date}"

    @property
    def ticker(self):
        """4桁の銘柄コード（末尾0付き5桁で来た場合に4桁へ正規化）。"""
        code = (self.securities_code or '').strip()
        if len(code) == 5 and code.endswith('0'):
            return code[:4]
        return code
