# stockdiary/services/recall_service.py
"""
リクエスト時想起サービス

ホーム表示時にユーザー単位の軽量クエリで「想起のきっかけ」を組み立てる。
常駐バッチを持たず、アクセスがあったユーザーの分しか計算しない設計
（docs/improvement_plan.md 論点2「リクエスト時想起」を参照）。

提供する想起:
- 1年前の今日: 約1年前(±数日)の継続記録・日記作成
- 振り返り未記入: 売却完結したが retrospective ノートがない日記
- 決算予定日が近い: 次回決算が近い保有・ウォッチ銘柄（EarningsSchedule を
  銘柄コードで引くだけ）。決算後の「確定決算が出た」ではなく、決算前に前提を
  見直すための前向きな想起として想起キューに載せる
- 新着開示（disclosures キー）: 直近に開示書類が出た日記。想起キューには
  載せず、フィルタ等の補助データとして保持する
"""
from datetime import datetime, timedelta

from django.utils import timezone


# 「1年前の今日」の許容ウィンドウ（±日数）
ANNIVERSARY_WINDOW_DAYS = 3
# 開示を「決算レビュー待ち」として想起し続ける日数
# （対象は有報・半報のみ＝年2回。レビューを書くまで残す未処理タスクのため長めにとる）
DISCLOSURE_RECENT_DAYS = 30
# 次回決算を「近い」として想起する日数（決算前に前提を見直すトリガー）
UPCOMING_EARNINGS_DAYS = 14
# 各セクションの最大表示件数
SECTION_LIMIT = 3
# 想起キュー（スワイプ式の単一フォーカスカード）の最大件数
QUEUE_LIMIT = 6


class RecallService:
    """ホーム上部「今日の想起」カードのデータ組み立て"""

    @classmethod
    def build(cls, user, today=None):
        """
        想起カード用データを構築する。

        Returns:
            dict: {
                'anniversary': [{'diary', 'date', 'kind', 'snippet'}, ...],
                'unreviewed': [StockDiary, ...],
                'unreviewed_count': int,
                'disclosures': [StockDiary, ...],
                'has_content': bool,
            }
        """
        today = today or timezone.now().date()

        anniversary = cls._build_anniversary(user, today)
        unreviewed, unreviewed_count = cls._build_unreviewed(user)
        disclosures = cls._build_disclosures(user, today)
        upcoming = cls._build_upcoming_earnings(user, today)
        due_theses = cls._build_due_theses(user, today)

        # 4種の想起を「なぜ浮上したか（理由）」付きの単一キューへ統合する。
        # ZIP デザイン（ui_kits/app の RecallZone）に倣ったスワイプ式カード用。
        # 決算は「確定決算が出た（開示後）」ではなく「決算予定日が近い（決算前）」を載せる。
        queue = cls._build_queue(due_theses, upcoming, unreviewed, anniversary, today)

        return {
            'anniversary': anniversary,
            'unreviewed': unreviewed,
            'unreviewed_count': unreviewed_count,
            'disclosures': disclosures,
            'upcoming_earnings': upcoming,
            'due_theses': due_theses,
            'queue': queue,
            'has_content': bool(anniversary or unreviewed or upcoming or due_theses),
            # 折りたたみ時の見出しバッジ用の総件数（キューに載る想起の合計）
            'total_count': (len(anniversary) + unreviewed_count
                            + len(upcoming) + len(due_theses)),
        }

    @classmethod
    def _build_queue(cls, due_theses, upcoming, unreviewed, anniversary, today):
        """想起をスワイプ式の単一フォーカスキューへ統合する。

        各カードは「なぜ今これが浮上したか」の理由を持つ。優先度の高い
        「答え合わせ待ち」の仮説を先頭にし、決算予定 → 未検証 → 1年前と続ける。
        URL の解決はテンプレート側（kind / diary_id / thesis_id を見て分岐）。
        """
        from ..utils import extract_lead

        def code_of(diary):
            return getattr(diary, 'stock_symbol', '') or ''

        queue = []

        # 1) 検証予定日が来た仮説（答え合わせ待ち）— 最優先
        for t in due_theses:
            days = (today - t.created_at.date()).days if t.created_at else None
            if t.review_due_date == today:
                due_str = '今日'
            elif t.review_due_date and t.review_due_date < today:
                due_str = f'{(today - t.review_due_date).days}日 超過'
            elif t.review_due_date:
                due_str = t.review_due_date.strftime('%-m/%-d')
            else:
                due_str = ''
            meta = []
            if days is not None:
                meta.append(f'仮説を立てて {days}日')
            if due_str:
                meta.append(f'検証予定 {due_str}')
            queue.append({
                'kind': 'due',
                'reason': '検証予定日が来た',
                'reason_icon': 'calendar-check',
                'stock_name': t.diary.stock_name,
                'code': code_of(t.diary),
                'diary_id': t.diary.id,
                'thesis_id': t.id,
                'claim': t.claim,
                'meta': ' ・ '.join(meta),
            })

        # 2) 決算予定日が近い（決算前に前提を見直す）
        for r in upcoming:
            d = r['diary']
            ne = r['next']
            when = '本日' if ne.days_until == 0 else f'あと{ne.days_until}日'
            meta = [f'決算予定 {ne.date.strftime("%-m/%-d")}', when]
            if ne.type:
                meta.append(ne.type)
            queue.append({
                'kind': 'earnings',
                'reason': '決算予定日が近い',
                'reason_icon': 'calendar-event',
                'stock_name': d.stock_name,
                'code': code_of(d),
                'diary_id': d.id,
                'thesis_id': None,
                'claim': extract_lead(d.reason or '', max_len=80)
                         or '決算が近い。前提は生きているか、確かめておく。',
                'meta': ' ・ '.join(meta),
            })

        # 3) 売却済みで答え合わせ未記入
        for d in unreviewed:
            queue.append({
                'kind': 'unreviewed',
                'reason': '答え合わせが未記入',
                'reason_icon': 'check2-square',
                'stock_name': d.stock_name,
                'code': code_of(d),
                'diary_id': d.id,
                'thesis_id': None,
                'claim': extract_lead(d.reason or '', max_len=80)
                         or '売却済み。仮説は当たったか、判断の質を残す。',
                'meta': '売却済み ・ 答え合わせ未記入',
            })

        # 4) 1年前の今日
        for item in anniversary:
            d = item['diary']
            date = item.get('date')
            queue.append({
                'kind': 'anniversary',
                'reason': '1年前の今日',
                'reason_icon': 'stars',
                'stock_name': d.stock_name,
                'code': code_of(d),
                'diary_id': d.id,
                'thesis_id': None,
                'claim': item.get('snippet') or '1年前のあなたの記録。',
                'meta': (f'{date.strftime("%Y/%-m/%-d")} のあなたの記録'
                         if date else '1年前のあなたの記録'),
            })

        return queue[:QUEUE_LIMIT]

    @classmethod
    def _build_due_theses(cls, user, today):
        """検証予定日が到来した未検証の仮説（答え合わせ待ち）。

        成長OSの中核トリガー。損益ではなく「答え合わせ」がユーザーを毎日連れ戻す。
        """
        from ..models import Thesis

        return list(
            Thesis.objects
            .filter(
                diary__user=user,
                diary__is_excluded=False,
                status=Thesis.STATUS_OPEN,
                review_due_date__isnull=False,
                review_due_date__lte=today,
            )
            .select_related('diary')
            .order_by('review_due_date')[:SECTION_LIMIT]
        )

    @classmethod
    def _build_anniversary(cls, user, today):
        """約1年前(±ウィンドウ)の継続記録・日記作成を新しい順に返す"""
        from ..models import DiaryNote, StockDiary
        from ..utils import extract_lead

        target = today - timedelta(days=365)
        start = target - timedelta(days=ANNIVERSARY_WINDOW_DAYS)
        end = target + timedelta(days=ANNIVERSARY_WINDOW_DAYS)

        items = []

        notes = (
            DiaryNote.objects
            .filter(diary__user=user, diary__is_excluded=False, date__range=(start, end))
            .select_related('diary')
            .order_by('-date')[:SECTION_LIMIT]
        )
        for note in notes:
            # 振り返りは topic が固定テーマのため、中身が伝わる本文を優先する
            if note.note_type == 'retrospective':
                snippet = (note.content or '')[:60]
            else:
                snippet = (note.topic or note.content or '')[:60]
            items.append({
                'diary': note.diary,
                'date': note.date,
                'kind': 'note',
                'snippet': snippet,
            })

        if len(items) < SECTION_LIMIT:
            # created_at__date__range だと DB 側で全行に日付キャスト関数がかかり、
            # created_at のインデックスが使えず日記全件スキャンになる（想起のたびに
            # 発生し、日記件数に比例してライブラリ/ホームの表示が遅くなっていた）。
            # ローカル日付の範囲をタイムゾーン込みの datetime 範囲へ事前変換し、
            # created_at にそのまま range 比較できる形にしてインデックスを使わせる。
            tz = timezone.get_current_timezone()
            start_dt = timezone.make_aware(datetime.combine(start, datetime.min.time()), tz)
            end_dt = timezone.make_aware(datetime.combine(end + timedelta(days=1), datetime.min.time()), tz)
            diaries = (
                StockDiary.objects
                .filter(user=user, is_excluded=False, created_at__gte=start_dt, created_at__lt=end_dt)
                .order_by('-created_at')[:SECTION_LIMIT - len(items)]
            )
            for diary in diaries:
                items.append({
                    'diary': diary,
                    'date': diary.created_at.date(),
                    'kind': 'diary',
                    'snippet': extract_lead(diary.reason or '', max_len=60),
                })

        return items[:SECTION_LIMIT]

    @classmethod
    def _build_unreviewed(cls, user):
        """売却完結済みで振り返り(retrospective)未記入の日記。

        複数売買ラウンド対応: 「最後の売り取引以降に書かれた振り返り」が
        ある日記だけを記入済みとみなす（再売却したら再び未記入扱いに戻る）。
        """
        from django.db.models import Exists, OuterRef, Subquery
        from ..models import StockDiary, DiaryNote, Transaction

        # DiaryNote から見た親日記の最終売り日（DiaryNote.diary_id 経由で相関）
        last_sell = (
            Transaction.objects
            .filter(diary=OuterRef('diary_id'), transaction_type='sell')
            .order_by('-transaction_date')
            .values('transaction_date')[:1]
        )
        reviewed = DiaryNote.objects.filter(
            diary=OuterRef('pk'),
            note_type='retrospective',
            date__gte=Subquery(last_sell),
        )

        qs = (
            StockDiary.objects
            .filter(
                user=user,
                is_excluded=False,
                transaction_count__gt=0,
                current_quantity=0,
            )
            .annotate(_reviewed=Exists(reviewed))
            .filter(_reviewed=False)
            .order_by('-updated_at')
        )
        count = qs.count()
        return list(qs[:SECTION_LIMIT]), count

    @classmethod
    def _build_disclosures(cls, user, today):
        """確定決算（有報・半報）が出たのに決算レビュー未記入の日記。

        開示日以降に note_type='earnings' のノートを書いたら想起から消える。
        latest_disclosure_date は DisclosureSync が重要種別（有報・半報）のみで
        更新するため、ここでの種別判定は不要。
        """
        from django.db.models import Exists, OuterRef
        from ..models import StockDiary, DiaryNote

        cutoff = today - timedelta(days=DISCLOSURE_RECENT_DAYS)

        reviewed = DiaryNote.objects.filter(
            diary=OuterRef('pk'),
            note_type='earnings',
            date__gte=OuterRef('latest_disclosure_date'),
        )
        return list(
            StockDiary.objects
            .filter(user=user, is_excluded=False, latest_disclosure_date__gte=cutoff)
            .annotate(_reviewed=Exists(reviewed))
            .filter(_reviewed=False)
            .order_by('-latest_disclosure_date')[:SECTION_LIMIT]
        )

    @classmethod
    def _build_upcoming_earnings(cls, user, today):
        """次回決算が近い（UPCOMING_EARNINGS_DAYS 以内）保有・ウォッチ銘柄。

        決算日は日記に持たせず、EarningsSchedule を銘柄コードで引く
        （earnings_lookup.get_next_earnings_map）。決算後の「確定決算が出た」では
        なく、決算前に前提を見直すための前向きな想起。近い順に返す。

        Returns:
            list[dict]: [{'diary': StockDiary, 'next': NextEarnings}, ...]
        """
        from ..models import StockDiary
        from .earnings_lookup import get_next_earnings_map

        diaries = list(
            StockDiary.objects
            .filter(user=user, is_excluded=False, stock_symbol__regex=r'^\d{4}$')
            .only('id', 'stock_symbol', 'stock_name', 'reason')
        )
        if not diaries:
            return []

        mapping = get_next_earnings_map(
            {d.stock_symbol for d in diaries}, today=today
        )

        results = []
        for d in diaries:
            ne = mapping.get(d.stock_symbol)
            if ne and 0 <= ne.days_until <= UPCOMING_EARNINGS_DAYS:
                results.append({'diary': d, 'next': ne})
        results.sort(key=lambda r: r['next'].date)
        return results[:SECTION_LIMIT]
