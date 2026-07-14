"""成長OS（投資家の自己理解）のビュー群。

views.py から責務分割（原則: 小さく分離）。検証ループ（Thesis→Verdict→Learning）と
その想起・集約画面：AnnualReview / Library / InvestorKarte と karte_block /
thesis_edit / thesis_verify。urls.py は `from . import views_growth` で参照する。
"""
import logging
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView

from .models import DiaryNote, StockDiary, Thesis, Verdict
from .forms import ThesisForm, VerdictForm
from .utils import extract_lead
from .services.karte_service import build_investor_karte
from .views_dashboard import build_tag_performance

logger = logging.getLogger(__name__)


class AnnualReviewView(LoginRequiredMixin, TemplateView):
    """年間／四半期レビュー（蓄積の喜び）。

    その期間に「書いた記録・学び」を読み物として振り返り、通算の勝ち筋タグを添える。
    数値分析ではなく「今年、自分は何を考え、何を学んだか」を読み返す体験。
    """
    template_name = 'stockdiary/review.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        today = timezone.localdate()

        period = self.request.GET.get('period', 'year')
        if period == 'quarter':
            q = (today.month - 1) // 3          # 0..3
            start_month = q * 3 + 1
            start = date(today.year, start_month, 1)
            end_month = start_month + 2
            if end_month >= 12:
                end = date(today.year, 12, 31)
            else:
                end = date(today.year, end_month + 1, 1) - timedelta(days=1)
            period_label = f"{today.year}年 第{q + 1}四半期"
        else:
            period = 'year'
            start = date(today.year, 1, 1)
            end = date(today.year, 12, 31)
            period_label = f"{today.year}年"

        # 期間スコープの「書いた量」
        new_diaries_count = StockDiary.objects.filter(
            user=user, created_at__date__gte=start, created_at__date__lte=end
        ).count()
        notes_qs = DiaryNote.objects.filter(
            diary__user=user, date__gte=start, date__lte=end
        )
        notes_written_count = notes_qs.count()
        retrospectives_count = notes_qs.filter(note_type='retrospective').count()

        # 今年の学び（振り返り・気づき・リスク）。スニペットは RecallService と同じ方針で抽出
        learnings = []
        for note in (
            notes_qs.filter(note_type__in=['retrospective', 'insight', 'risk'])
            .select_related('diary')
            .order_by('-date')[:12]
        ):
            if note.note_type == 'retrospective':
                snippet = (note.content or '')[:110]
            else:
                snippet = (note.content or note.topic or '')[:110]
            learnings.append({'note': note, 'snippet': snippet})

        # 勝ち筋タグ（通算）。realized_profit はライフタイム値のため「通算」と明示して使う
        tag_analysis = build_tag_performance(
            StockDiary.objects.filter(user=user).prefetch_related('tags')
        )
        winning_tags = [t for t in tag_analysis if t['realized_profit'] > 0][:6]
        struggling_tags = [t for t in tag_analysis if t['realized_profit'] < 0][:4]

        context.update({
            'period': period,
            'period_label': period_label,
            'new_diaries_count': new_diaries_count,
            'notes_written_count': notes_written_count,
            'retrospectives_count': retrospectives_count,
            'total_records': new_diaries_count + notes_written_count,
            'learnings': learnings,
            'winning_tags': winning_tags,
            'struggling_tags': struggling_tags,
            'has_content': bool(new_diaries_count or notes_written_count or learnings or winning_tags),
            'today': today,
        })
        return context


class LibraryView(LoginRequiredMixin, TemplateView):
    """ライブラリ（知識アーカイブ）。時系列一覧ではなく、学び・テーマ・仮説で再利用する場所。

    「2025年4月の記事」ではなく「海運で失敗した記録」を探せるようにする。
    銘柄・時系列の軸は既存（home / diary_summary / timeline）へ委譲し再発明しない。
    """
    template_name = 'stockdiary/library.html'

    def _is_htmx(self):
        return (
            self.request.headers.get('HX-Request') == 'true'
            or self.request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        )

    def get_template_names(self):
        if self._is_htmx():
            # タブ/検索/タグ切替は #lib-content（軸別データ）だけを返す。
            # 今日の見直し・レンズ件数はここに含めない＝再計算しない
            # （axis に依存しないデータを毎回作り直していたのが遅さの主因だった）。
            return ['stockdiary/partials/library_axis_body.html']
        return [self.template_name]

    def get_context_data(self, **kwargs):
        from django.db.models import Count, Q as _Q
        from tags.models import Tag

        context = super().get_context_data(**kwargs)
        user = self.request.user
        today = timezone.localdate()
        axis = self.request.GET.get('axis', 'learning')
        q = self.request.GET.get('q', '').strip()
        tag_id = self.request.GET.get('tag', '').strip()
        context['axis'] = axis if axis in ('learning', 'theme', 'thesis', 'time') else 'learning'
        context['q'] = q
        context['active_tag'] = tag_id

        if not self._is_htmx():
            # ── レンズ別の件数（4レンズのタブに常時表示）──
            # フルページ読み込み時のみ計算する。タブ切替(HTMX)のたびに
            # 全レンズ分を数え直すのは axis に依存しない無駄な再計算のため。
            learning_total = (
                Verdict.objects.filter(thesis__diary__user=user).exclude(learning='').count()
            )
            theme_total = (
                Tag.objects.filter(user=user)
                .annotate(n=Count('stockdiary', distinct=True)).filter(n__gt=0).count()
            )
            thesis_total = (
                Thesis.objects.filter(diary__user=user, verdict__isnull=True).count()
                + Verdict.objects.filter(thesis__diary__user=user).count()
            )
            time_total = DiaryNote.objects.filter(diary__user=user).count()
            context['counts'] = {
                'learning': learning_total, 'theme': theme_total,
                'thesis': thesis_total, 'time': time_total,
            }

            # ── 今日の見直し（現在の出来事 × 過去の仮説）— 想起から実現可能な手掛かりだけ ──
            # RecallService.build は複数クエリを伴うため、同様にフルページ読み込み時のみ。
            context['today_cues'] = self._build_today_cues(user, today)

        if axis == 'theme':
            context['theme_rows'] = (
                Tag.objects.filter(user=user)
                .annotate(n=Count('stockdiary', distinct=True))
                .filter(n__gt=0)
                .order_by('-n', 'name')
            )

        elif axis == 'thesis':
            # 答え合わせ待ち（検証予定日が来た）／ 生きている（未検証・期日前）に分ける
            open_theses = list(
                Thesis.objects.filter(diary__user=user, verdict__isnull=True)
                .select_related('diary').order_by('review_due_date')
            )
            due, live = [], []
            for t in open_theses:
                if t.review_due_date and t.review_due_date <= today:
                    overdue_days = (today - t.review_due_date).days
                    t.is_overdue = overdue_days > 0
                    t.due_label = '今日' if overdue_days == 0 else f'{overdue_days}日超過'
                    due.append(t)
                else:
                    if t.review_due_date:
                        ahead = (t.review_due_date - today).days
                        t.due_label = f'{ahead}日後'
                    else:
                        t.due_label = ''
                    live.append(t)
            context['due_theses'] = due
            context['live_theses'] = live
            # 後方互換（未検証の仮説全体）
            context['open_theses'] = open_theses
            verdicts = list(
                Verdict.objects.filter(thesis__diary__user=user)
                .select_related('thesis', 'thesis__diary').order_by('-created_at')
            )
            context['hit_verdicts'] = [v for v in verdicts if v.hyp_ok]
            context['miss_verdicts'] = [v for v in verdicts if not v.hyp_ok]

        elif axis == 'time':
            # 時系列レンズ：銘柄をまたいだ継続記録の時間軸
            from .utils import extract_lead
            notes = (
                DiaryNote.objects.filter(diary__user=user, diary__is_excluded=False)
                .select_related('diary').order_by('-date')[:40]
            )
            timeline = []
            for n in notes:
                body = n.topic if (n.note_type != 'retrospective' and n.topic) else extract_lead(n.content or '', max_len=90)
                timeline.append({
                    'date': n.date,
                    'stock': n.diary.stock_name,
                    'code': n.diary.stock_symbol,
                    'kind': n.get_note_type_display(),
                    'body': body,
                    'live': (n.diary.current_quantity or 0) > 0,
                    'diary_id': n.diary.id,
                })
            context['timeline'] = timeline

        else:  # learning（既定）: 検証から残った学びの索引
            verdicts = (
                Verdict.objects.filter(thesis__diary__user=user)
                .exclude(learning='')
                .select_related('thesis', 'thesis__diary')
                .prefetch_related('thesis__basis_tags')
                .order_by('-created_at')
            )
            if tag_id.isdigit():
                verdicts = verdicts.filter(thesis__basis_tags__id=int(tag_id))
            if q:
                verdicts = verdicts.filter(
                    _Q(learning__icontains=q)
                    | _Q(missed_factor__icontains=q)
                    | _Q(thesis__claim__icontains=q)
                    | _Q(thesis__diary__stock_name__icontains=q)
                )
            context['learnings'] = list(verdicts[:60])
            # 絞り込み用のテーマタグ
            context['filter_tags'] = (
                Tag.objects.filter(user=user, theses__verdict__isnull=False)
                .distinct().order_by('name')
            )
        return context

    @staticmethod
    def _build_today_cues(user, today):
        """今日の見直し（現在の出来事 × 過去の仮説）。

        出来事検知（テーマの転換等）は本体未実装のため、想起から確実に作れる
        手掛かり（検証予定日が来た仮説・1年前の今日）だけを並べる。
        """
        from django.urls import reverse
        from .services.recall_service import RecallService

        recall = RecallService.build(user, today)
        cues = []
        for t in recall.get('due_theses', [])[:2]:
            cues.append({
                'icon': 'calendar-check', 'tone': 'unlucky',
                'head': f'{t.diary.stock_name} の検証予定日',
                'body': f'「{t.claim}」— 答え合わせの頃合いです。',
                'cta': '答え合わせをする',
                'url': f"{reverse('stockdiary:detail', args=[t.diary.id])}?verify={t.id}#karte-block",
            })
        for item in recall.get('anniversary', [])[:1]:
            d = item['diary']
            snippet = (item.get('snippet') or '').strip()
            cues.append({
                'icon': 'stars', 'tone': 'skill',
                'head': f'1年前の今日 — {d.stock_name}',
                'body': (f'「{snippet}」当時の自分は、いまどう見えるか。' if snippet
                         else '当時の自分は、いまどう見えるか。'),
                'cta': '当時の記録を読む',
                'url': reverse('stockdiary:detail', args=[d.id]),
            })
        return cues[:3]


class InvestorKarteView(LoginRequiredMixin, TemplateView):
    """投資家カルテ（自己理解）。成績評価ではなく「どういう投資家か」を返す。

    検証（Verdict）を母数に、意思決定の質×結果の2×2分布・テーマ別の的中傾向・
    繰り返す見落とし・投資哲学（再現したい学び）を可視化する。
    """
    template_name = 'stockdiary/karte.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from .services.karte_service import build_investor_karte
        context['karte'] = build_investor_karte(self.request.user)
        return context


# ============================================================
# Phase 8a: 検証ループ（予想→結果→検証→学び）
# ============================================================

def _suggest_pnl_result(diary):
    """既存の損益集計から Verdict.pnl_result の初期値を推定する。"""
    stats = diary.calculate_cash_only_stats()
    if stats['current_quantity'] and stats['current_quantity'] > 0:
        return Verdict.PNL_HOLDING
    realized = stats['realized_profit'] or 0
    if realized > 0:
        return Verdict.PNL_PROFIT
    if realized < 0:
        return Verdict.PNL_LOSS
    return Verdict.PNL_FLAT


def _default_review_due_date(diary, horizon):
    """horizon から検証予定日を補完する（基準は初回購入日 or 今日）。

    horizon='next_earnings'（次の決算まで）は、実際の次回決算日
    （EarningsSchedule をコードで参照）があればそれを採用し、無ければ従来の
    概算45日にフォールバックする。これにより決算後の「答え合わせ待ち」想起が
    正確なタイミングで発火する。

    購入日起点の計算が過去日になる場合（昔からの保有に後から仮説を立てる）は
    今日を起点に再アンカーする。「作った瞬間から期限切れ」の仮説を作らない＝
    想起（検証期日）の信頼性を守るための不変条件。
    """
    today = timezone.localdate()
    if horizon == 'next_earnings' and diary.stock_symbol:
        from .services.earnings_lookup import get_next_earnings_map
        ne = get_next_earnings_map({diary.stock_symbol}).get(diary.stock_symbol)
        if ne:
            return ne.date
    base = diary.first_purchase_date or today
    days = {'next_earnings': 45, '3m': 90, '6m': 180, '1y': 365, 'long': 365}.get(horizon, 180)
    due = base + timedelta(days=days)
    if due <= today:
        due = today + timedelta(days=days)
    return due


def _build_user_quadrants(user):
    """ユーザー全体の 2×2 判断傾向を集計して返す（_karte_block 用）。"""
    from . import services
    quad_counts = {key: 0 for key, _, _ in services.metrics.QUADRANTS}
    total = 0
    for v in Verdict.objects.filter(
        thesis__diary__user=user,
        thesis__diary__is_excluded=False,
    ).only('hypothesis_result', 'pnl_result'):
        total += 1
        quad_counts[v.quadrant] += 1
    highlight = max(quad_counts, key=quad_counts.get) if total else None
    return total, [
        {'key': key, 'label': label, 'axis': axis,
         'count': quad_counts[key], 'is_highlight': key == highlight}
        for key, label, axis in services.metrics.QUADRANTS
    ]


def _render_karte_block(request, diary):
    """検証ループのブロック（仮説→結果→検証→学び）を再描画する。"""
    theses = (
        diary.theses
        .select_related('verdict')
        .prefetch_related('basis_tags')
    )
    total_verdicts, user_quadrants = _build_user_quadrants(request.user)
    return render(request, 'stockdiary/partials/_karte_block.html', {
        'diary': diary,
        'theses': theses,
        'user_quadrants': user_quadrants,
        'user_total_verdicts': total_verdicts,
    })


@login_required
def karte_block(request, diary_id):
    """検証ループのブロックを再描画して返す（フォームのキャンセル用 GET）。"""
    diary = get_object_or_404(StockDiary, pk=diary_id, user=request.user)
    return _render_karte_block(request, diary)


@login_required
def thesis_edit(request, diary_id, thesis_id=None):
    """仮説（Thesis）の作成・編集（HTMX）。thesis_id なしで新規作成、あれば既存編集。"""
    diary = get_object_or_404(StockDiary, pk=diary_id, user=request.user)
    instance = None
    if thesis_id:
        instance = get_object_or_404(Thesis, pk=thesis_id, diary=diary)

    # 記録タブの『仮説』ボトムシートからは通常POST（HX-Requestなし）で届く。
    # その場合は他の入力（取引・株式分割シート）と同様に保存→詳細へリダイレクトし、
    # 仮説ビューに着地させる。インライン編集（HTMX）は従来どおり部分テンプレートを返す。
    is_htmx = request.headers.get('HX-Request') == 'true'

    if request.method == 'POST':
        form = ThesisForm(request.POST, instance=instance, user=request.user)
        if form.is_valid():
            thesis = form.save(commit=False)
            thesis.diary = diary
            if not thesis.review_due_date:
                thesis.review_due_date = _default_review_due_date(diary, thesis.horizon)
            thesis.save()
            form.save_m2m()
            if is_htmx:
                return _render_karte_block(request, diary)
            messages.success(request, '仮説を記録しました')
            url = reverse('stockdiary:detail', kwargs={'pk': diary.id})
            return redirect(f'{url}?view=thesis')
        elif not is_htmx:
            # シート（通常POST）でのバリデーションエラーは他シートと同じくメッセージで通知し戻す
            for field, errs in form.errors.items():
                for err in errs:
                    messages.error(request, f'{field}: {err}')
            url = reverse('stockdiary:detail', kwargs={'pk': diary.id})
            return redirect(f'{url}?view=thesis')
    else:
        form = ThesisForm(instance=instance, user=request.user)

    from tags.models import Tag as _Tag
    all_tags = list(_Tag.objects.filter(user=request.user).order_by('name').values('id', 'name'))
    selected_ids = list(instance.basis_tags.values_list('id', flat=True)) if instance else []
    return render(request, 'stockdiary/partials/_thesis_form.html', {
        'diary': diary, 'form': form, 'thesis': instance,
        'all_tags': all_tags, 'selected_tag_ids': selected_ids,
    })


@login_required
def thesis_verify(request, diary_id, thesis_id):
    """検証（Verdict）の記録（HTMX）。仮説の当否を損益と分離して残す。"""
    diary = get_object_or_404(StockDiary, pk=diary_id, user=request.user)
    thesis = get_object_or_404(Thesis, pk=thesis_id, diary=diary)
    try:
        instance = thesis.verdict
    except Verdict.DoesNotExist:
        instance = None

    if request.method == 'POST':
        form = VerdictForm(request.POST, instance=instance)
        if form.is_valid():
            verdict = form.save(commit=False)
            verdict.thesis = thesis
            verdict.save()
            if verdict.pnl_result == Verdict.PNL_HOLDING:
                # 中間検証: 保有中の答え合わせは仮説を閉じない。検証予定日を
                # 先送りして見張りを続け、確定損益（利益/損失/変わらず）が出た
                # 答え合わせで verified に閉じる。閉じてしまうと is_due の母集団から
                # 外れ、保有継続中こそ必要な想起が二度と来なくなるため。
                thesis.status = Thesis.STATUS_OPEN
                thesis.review_due_date = _default_review_due_date(diary, thesis.horizon)
                thesis.save(update_fields=['status', 'review_due_date', 'updated_at'])
            else:
                thesis.status = Thesis.STATUS_VERIFIED
                thesis.save(update_fields=['status', 'updated_at'])
            return _render_karte_block(request, diary)
    else:
        initial = {} if instance else {'pnl_result': _suggest_pnl_result(diary)}
        form = VerdictForm(instance=instance, initial=initial)

    return render(request, 'stockdiary/partials/_verdict_form.html', {
        'diary': diary, 'thesis': thesis, 'form': form,
    })
