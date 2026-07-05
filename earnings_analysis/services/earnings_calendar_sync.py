# earnings_analysis/services/earnings_calendar_sync.py
"""決算予定の同期・想起サービス

決算予定API（earnings_calendar_api）から当日〜90日後の決算発表予定を取得し、
EarningsSchedule を洗い替え保存する。あわせて決算前日（翌日決算）の銘柄を
記録中のユーザーへアプリ内通知をファンアウトする。

設計方針:
- 決算予定は EarningsSchedule（証券コードがキーの決算予定マスタ）を唯一の正とする。
  日記側へは事前計算カラムを持たせず、表示時に銘柄コードで都度 join する
  （日記とマスタの二重管理・日次コピーを避ける）。コード→決算の参照ヘルパーは
  stockdiary/views_earnings.py に置く
- 日次バッチ（sync_earnings_calendar コマンド）から呼び出す
- 通知は (user, earnings_schedule) の一意制約 + ignore_conflicts で重複送信を防ぐ
"""
import logging
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# ファンアウト時の bulk_create バッチサイズ
FANOUT_BATCH_SIZE = 500


def sync_earnings_calendar(days: int = 90, base_date=None,
                           with_estimates: bool = True) -> int:
    """決算予定APIから取得して EarningsSchedule を洗い替え保存する。

    取得期間（基準日〜days日後）の既存レコードを削除してから再投入するため、
    リスケ・取り下げがあっても未来分は常に最新のAPI内容と一致する。基準日より
    前のレコードは履歴として保持する。

    保存する未来分は2種類:
      - 確定分（is_estimated=False）: APIが返す直近の確定発表日（現状は約2週間先まで）
      - 予想分（is_estimated=True）: 各社の会計年度末から自前算出した次回以降の
        発表予想日（earnings_calendar_estimate）。提供元APIが予想日を返さなく
        なったため、確定分だけだと記録銘柄の多くがマスタから外れて日記に
        関連付かなくなる。予想分で残りの期間を埋める。毎回ゼロから再生成する
        ので、洗い替え方式のまま予想が陳腐化しない。

    Args:
        days: 取得期間（基準日からの日数）
        base_date: 取得基準日（既定=今日）。日次バッチが失敗した日のリカバリ実行で
            過去日を指定できる。
        with_estimates: 予想日の自前算出を行うか（既定 True。テスト等で無効化可）

    Returns:
        int: 保存した決算予定レコード数
    """
    from earnings_analysis.models import EarningsSchedule
    from earnings_analysis.services.earnings_calendar_api import (
        EarningsCalendarAPIService,
    )
    from earnings_analysis.services.earnings_calendar_estimate import (
        build_roster, build_estimates,
    )

    service = EarningsCalendarAPIService()
    if not service.is_configured:
        logger.warning('決算予定同期: APIキー未設定のためスキップ')
        return 0

    base_date = base_date or date.today()
    confirmed = service.fetch_window(days=days, start=base_date)

    # 予想分（確定分で埋まらない期間を自前算出で補完）
    estimates = []
    if with_estimates:
        try:
            history = service.fetch_history(months=13, end=base_date)
            roster = build_roster(history)
            confirmed_by_code = {}
            for item in confirmed:
                confirmed_by_code.setdefault(
                    item['securities_code'], []).append(item['earnings_date'])
            estimates = build_estimates(
                roster, confirmed_by_code, base_date,
                horizon_days=days,
                to_business_day=EarningsCalendarAPIService._to_business_day,
            )
        except Exception as e:
            # 予想の算出に失敗しても確定分の保存は続ける（決算前日通知等を止めない）
            logger.warning('決算予想日の算出に失敗（確定分のみ保存）: %s', e,
                           exc_info=True)
            estimates = []

    # 同一バッチ内のコード×日付の重複を排除（DBの一意制約に合わせる）。
    # 予想を先に入れ、確定を後から入れて上書き（同一日は確定を正とする）。
    deduped = {}
    for item in estimates:
        deduped[(item['securities_code'], item['earnings_date'])] = item
    for item in confirmed:
        deduped[(item['securities_code'], item['earnings_date'])] = item

    to_create = [
        EarningsSchedule(
            securities_code=item['securities_code'],
            company_name=item['company_name'],
            earnings_date=item['earnings_date'],
            is_estimated=item.get('is_estimated', True),
            earnings_type=item['earnings_type'],
            market_segment=item['market_segment'],
            source_updated_at=_parse_source_dt(item.get('source_updated_at')),
        )
        for item in deduped.values()
    ]

    with transaction.atomic():
        # 基準日以降のみ洗い替え。基準日より前は履歴として残す。
        EarningsSchedule.objects.filter(earnings_date__gte=base_date).delete()
        if to_create:
            EarningsSchedule.objects.bulk_create(
                to_create, batch_size=500, ignore_conflicts=True
            )

    logger.info('決算予定同期完了: 保存=%s件（確定=%s / 予想=%s）',
                len(to_create), len(confirmed), len(estimates))
    return len(to_create)


def fan_out_earnings_reminders(target_date=None) -> int:
    """翌日（決算前日）に決算を控える銘柄を記録中のユーザーへアプリ内通知する。

    - 対象は保有中・ウォッチ（メモ）を問わず、その銘柄の日記を持つユーザー全員
    - ユーザーごとに1通（同一銘柄の日記が複数あっても最新更新の日記に誘導）
    - (user, earnings_schedule) の一意制約 + ignore_conflicts で重複送信を防ぐ

    Args:
        target_date: 決算日（既定は翌日）。テスト用に上書き可能。

    Returns:
        int: 作成したアプリ内通知数
    """
    from stockdiary.models import StockDiary, NotificationLog
    from earnings_analysis.models import EarningsSchedule

    if target_date is None:
        target_date = date.today() + timedelta(days=1)

    schedules = list(
        EarningsSchedule.objects.filter(earnings_date=target_date)
    )
    if not schedules:
        return 0

    total = 0
    for schedule in schedules:
        ticker = _to_ticker(schedule.securities_code)
        if not ticker:
            continue

        rows = (
            StockDiary.objects
            .filter(stock_symbol=ticker, is_excluded=False)
            .order_by('user_id', '-updated_at')
            .values('id', 'user_id', 'stock_name')
            .iterator(chunk_size=1000)
        )

        # 既に同じ決算予定の通知を持つユーザーは除外（再実行で重複も二重カウントもしない）
        already_notified = set(
            NotificationLog.objects
            .filter(earnings_schedule=schedule)
            .values_list('user_id', flat=True)
        )

        logs = []
        seen_users = set(already_notified)
        type_label = f"（{schedule.earnings_type}）" if schedule.earnings_type else ''
        for row in rows:
            if row['user_id'] in seen_users:
                continue
            seen_users.add(row['user_id'])
            logs.append(NotificationLog(
                user_id=row['user_id'],
                earnings_schedule=schedule,
                title=f"📅 {row['stock_name']} は明日が決算予定です",
                message=(
                    f"{schedule.earnings_date.strftime('%Y/%m/%d')} に決算発表"
                    f"{type_label}が予定されています。決算前に仮説を見直しておきましょう。"
                ),
                url=f"/stockdiary/{row['id']}/",
            ))
            if len(logs) >= FANOUT_BATCH_SIZE:
                NotificationLog.objects.bulk_create(logs, ignore_conflicts=True)
                total += len(logs)
                logs = []

        if logs:
            NotificationLog.objects.bulk_create(logs, ignore_conflicts=True)
            total += len(logs)

    logger.info('決算前日通知ファンアウト完了: %s件', total)
    return total


def _to_ticker(securities_code: str) -> str:
    """証券コードを4桁の銘柄コードへ正規化する。"""
    code = (securities_code or '').strip()
    if len(code) == 5 and code.endswith('0'):
        return code[:4]
    return code


def _parse_source_dt(value):
    """提供元の更新日時文字列を timezone-aware datetime に変換（失敗時 None）。"""
    if not value:
        return None
    from django.utils.dateparse import parse_datetime, parse_date

    dt = parse_datetime(value)
    if dt is None:
        d = parse_date(value[:10])
        if d is None:
            return None
        from datetime import datetime, time
        dt = datetime.combine(d, time.min)
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_default_timezone())
    return dt
