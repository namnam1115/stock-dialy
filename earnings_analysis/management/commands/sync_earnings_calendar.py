# earnings_analysis/management/commands/sync_earnings_calendar.py
"""決算予定の日次同期コマンド

決算予定API（EDINET DB /v1/calendar）から当日〜90日後の決算発表予定を取得し、
EarningsSchedule を洗い替え保存する。あわせて決算前日（翌日決算）の通知を
ファンアウトする。決算日は日記側に持たせず、表示時に銘柄コードで都度参照する。

確定分（提供元APIが返す直近の確定発表日）に加え、予想分（各社の会計年度末から
四半期末＋約43日で自前算出。earnings_calendar_estimate）も生成してマスタを埋める。
提供元APIが予想日を返さなくなったため、確定分だけでは記録銘柄の多くが決算予定
マスタから外れ、日記に関連付かなくなる（それを予想分で補う）。

仕上げに、予想分のうち発表が近い記録銘柄だけ個社API（決算短信の実績）で答え合わせ
する（earnings_calendar_verify）。/v1/calendar（横断フィード）には提供元側の
インデックス漏れがあり、実際に開示済みの銘柄が丸ごと出てこないことがあるため
（実例: イオン8267）、四半期末+オフセットの自前算出だけでは数日ズレることがある。
個社APIは正確だが1銘柄=1リクエストのため、固定予算（既定60件・発表が近い順）で
機械的に打ち切り、無料枠を超えないようにする。

cron で毎日1回実行する想定（etc/cron.d/earnings-calendar 参照）。
無料枠（100リクエスト/日）に対し、本コマンドのAPI利用は確定窓（〜3）＋履歴取得
（〜15）＋個社答え合わせ（既定60）で 80 リクエスト前後。
"""
import logging
import traceback

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = '決算予定APIから当日〜90日後の決算予定を取得してDBへ同期する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days', type=int, default=90,
            help='取得期間（基準日からの日数、既定90日）',
        )
        parser.add_argument(
            '--base-date', type=str, default=None,
            help='取得基準日 YYYY-MM-DD（既定=今日）。日次バッチが失敗した日の'
                 'リカバリ実行に使う',
        )
        parser.add_argument(
            '--target-date', type=str, default=None,
            help='決算前日通知の対象決算日 YYYY-MM-DD（既定=翌日）。前日通知の'
                 '送り逃しをリカバリするときに指定',
        )
        parser.add_argument(
            '--skip-notifications', action='store_true',
            help='決算前日通知のファンアウトをスキップする',
        )
        parser.add_argument(
            '--verify-budget', type=int, default=None,
            help='予想日の個社API答え合わせに使うリクエスト予算（既定60件。'
                 '0で無効化）',
        )

    def handle(self, *args, **options):
        from earnings_analysis.services import (
            sync_earnings_calendar,
            fan_out_earnings_reminders,
            verify_estimates_via_company_api,
        )
        from earnings_analysis.services.earnings_calendar_verify import (
            DEFAULT_VERIFY_BUDGET,
        )

        days = options['days']
        skip_notifications = options['skip_notifications']
        verify_budget = options['verify_budget']
        if verify_budget is None:
            verify_budget = DEFAULT_VERIFY_BUDGET

        try:
            base_date = self._parse_date(options.get('base_date'))
            target_date = self._parse_date(options.get('target_date'))
        except ValueError as e:
            self.stdout.write(self.style.ERROR(str(e)))
            return

        base_label = base_date.isoformat() if base_date else '今日'
        self.stdout.write(f'決算予定同期開始（基準日={base_label}〜{days}日後）')

        try:
            saved = sync_earnings_calendar(days=days, base_date=base_date)
            self.stdout.write(self.style.SUCCESS(f'決算予定 保存: {saved}件'))
        except Exception as e:
            logger.error('決算予定同期エラー: %s', e, exc_info=True)
            self.stdout.write(self.style.ERROR(f'決算予定同期エラー: {e}'))
            self.stdout.write(traceback.format_exc())
            return

        # 予想分のうち発表が近い記録銘柄だけ、個社APIで答え合わせ
        if verify_budget > 0:
            try:
                verified = verify_estimates_via_company_api(budget=verify_budget)
                self.stdout.write(self.style.SUCCESS(f'予想の個社API答え合わせ: {verified}件'))
            except Exception as e:
                logger.warning('予想の個社API答え合わせエラー（スキップ）: %s', e,
                               exc_info=True)
                self.stdout.write(self.style.WARNING(f'個社API答え合わせスキップ: {e}'))

        # 決算前日通知のファンアウト
        if not skip_notifications:
            try:
                notified = fan_out_earnings_reminders(target_date=target_date)
                self.stdout.write(self.style.SUCCESS(f'決算前日通知: {notified}件'))
            except Exception as e:
                logger.warning('決算前日通知エラー（スキップ）: %s', e, exc_info=True)
                self.stdout.write(self.style.WARNING(f'決算前日通知スキップ: {e}'))

        self.stdout.write(self.style.SUCCESS('決算予定同期完了'))

    @staticmethod
    def _parse_date(value):
        """YYYY-MM-DD を date に。未指定は None。不正は ValueError。"""
        if not value:
            return None
        from datetime import datetime
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError:
            raise ValueError(f'日付形式が不正です（YYYY-MM-DD で指定）: {value}')
