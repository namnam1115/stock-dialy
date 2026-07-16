import logging

from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, TemplateView, View
from django.urls import reverse_lazy
from django.db.models import Q, Count, Avg, F, Sum, Min, Max, Case, When, Value, IntegerField, DecimalField
from django.db.models.functions import Coalesce
from django.db.models.functions import TruncMonth, ExtractWeekDay, Length
from django.utils import timezone
from datetime import datetime, timezone as dt_timezone
from django.utils.html import strip_tags
from django.utils.safestring import mark_safe
from django.template.defaultfilters import truncatechars_html
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django.http import JsonResponse, HttpResponse, Http404
from django.template.loader import render_to_string
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.conf import settings
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.core.cache import cache

from utils.mixins import ObjectNotFoundRedirectMixin
from .models import StockDiary, DiaryNote, DiaryNotification
from .models import Transaction, ReasonVersion
from .forms import StockDiaryForm, DiaryNoteForm, ThesisForm
from .utils import compute_related_strength, extract_lead, build_theme_recall
from company_master.models import CompanyMaster
from tags.models import Tag
from django.views.generic import FormView


from django.db import transaction as db_transaction
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, date
from collections import Counter, defaultdict, OrderedDict
import calendar
import chardet
import mimetypes
import os
import csv
import io
import html

logger = logging.getLogger(__name__)

# ページネーション定数
DIARY_LIST_PAGE_SIZE = 10        # 日記一覧 HTMX パーシャルおよびFBV
DIARY_LIST_INITIAL_SIZE = 4      # 日記一覧 CBV 初期表示
NOTIFICATION_LIST_PAGE_SIZE = 20 # 通知一覧

import json
import re
import statistics

from PIL import Image

# ==========================================
# ユーティリティ関数
# ==========================================

def get_mention_map(user):
    """ユーザーのstock_symbol→diary_idマップ（LocMemCacheで5分キャッシュ）"""
    cache_key = f'mention_map_u{user.id}'
    mention_map = cache.get(cache_key)
    if mention_map is None:
        rows = StockDiary.objects.filter(
            user=user
        ).exclude(stock_symbol='').values('id', 'stock_symbol')
        mention_map = {r['stock_symbol']: r['id'] for r in rows}
        cache.set(cache_key, mention_map, 300)
    return mention_map


def calculate_margin_ratio(outstanding_purchases, outstanding_sales):
    """信用倍率を計算する共通関数"""
    if outstanding_sales > 0:
        return round(outstanding_purchases / outstanding_sales, 2)
    return 0


def render_error_html(icon, title, message, show_retry=False):
    """エラーメッセージHTMLを生成する共通関数"""
    retry_button = '''
        <button class="btn btn-sm btn-outline-primary" onclick="window.location.reload()">
            <i class="bi bi-arrow-clockwise me-1"></i>再試行
        </button>
    ''' if show_retry else ''
    
    return f'''
    <div class="text-center py-4">
        <div class="text-muted">
            <i class="bi bi-{icon}" style="font-size: 2rem;"></i>
            <h6 class="mt-3">{title}</h6>
            <p class="mb-0 small">{message}</p>
            {retry_button}
        </div>
    </div>
    '''


def get_note_badge_class(note_type):
    """ノートタイプに応じたバッジクラスを取得"""
    badge_classes = {
        'analysis': 'bg-primary',
        'news': 'bg-info',
        'earnings': 'bg-success',
        'insight': 'bg-warning',
        'risk': 'bg-danger',
        'retrospective': 'bg-dark'
    }
    return badge_classes.get(note_type, 'bg-secondary')


def get_note_type_display(note_type):
    """ノートタイプの表示名を取得"""
    type_displays = {
        'analysis': '分析更新',
        'news': 'ニュース',
        'earnings': '決算情報',
        'insight': '新たな気づき',
        'risk': 'リスク要因',
        'retrospective': '振り返り'
    }
    return type_displays.get(note_type, 'その他')


# ==========================================
# ビュークラス
# ==========================================

class StockDiaryListView(LoginRequiredMixin, ListView):
    model = StockDiary
    template_name = 'stockdiary/home.html'
    context_object_name = 'diaries'
    paginate_by = DIARY_LIST_INITIAL_SIZE
    
    def get_queryset(self):
        from .utils import apply_diary_filters
        queryset = (
            StockDiary.objects.filter(user=self.request.user)
            .select_related('user')
            .prefetch_related('tags', 'notes', 'tag_directions')
        )
        return apply_diary_filters(queryset, self.request.GET, self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['tags'] = Tag.objects.filter(user=self.request.user)
        
        # 業種リストを取得（重複なし）
        sectors = StockDiary.objects.filter(
            user=self.request.user,
            sector__isnull=False
        ).exclude(sector='').values_list('sector', flat=True).distinct().order_by('sector')
        context['sectors'] = list(sectors)
        
        # 統計・最近のハッシュタグ算出用（除外日記は含めない）。
        # ※ 旧「カレンダー表示用」の context['all_diaries'] は、対応する
        #   カレンダーUI（FullCalendar）ごと撤去済みのデッドデータのため削除。
        all_diaries_qs = StockDiary.objects.filter(user=self.request.user, is_excluded=False)

        # 統計情報を1クエリにまとめて取得
        stats = all_diaries_qs.aggregate(
            active_count=Count('id', filter=Q(current_quantity__gt=0)),
            sold_count=Count('id', filter=Q(current_quantity=0, transaction_count__gt=0)),
            memo_count=Count('id', filter=Q(transaction_count=0)),
            total_profit=Sum('realized_profit'),
        )
        context['active_holdings_count'] = stats['active_count'] or 0
        context['realized_profit'] = stats['total_profit'] or Decimal('0')
        context['sold_count'] = stats['sold_count'] or 0
        context['memo_count'] = stats['memo_count'] or 0
        context['total_diary_count'] = (
            context['active_holdings_count']
            + context['sold_count']
            + context['memo_count']
        )
        context['excluded_count'] = StockDiary.objects.filter(
            user=self.request.user, is_excluded=True
        ).count()

        # 直近で使われているハッシュタグ（モバイル：タブ下のクイックチップ用）
        from .utils import get_all_hashtags_from_queryset
        context['recent_hashtags'] = get_all_hashtags_from_queryset(all_diaries_qs)[:5]

        # 検索パラメータを保持
        context['current_query'] = self.request.GET.urlencode()
        context['current_params'] = self.request.GET

        # ユーザー全体の既存タイトル（topic）リストを取得
        user_topics = DiaryNote.objects.filter(
            diary__user=self.request.user,
            topic__isnull=False
        ).exclude(topic='').values_list('topic', flat=True).distinct().order_by('topic')
        context['note_topics'] = list(user_topics)

        # 日記ごとの既存トピックは prefetch 済みの notes から Python 側で導出する。
        # （旧実装はカードごとに DiaryNote を再クエリする N+1 で、さらにループ内で
        #  logger.info を毎回出力していた。一覧表示のホットパスなので両方を解消する。）
        for diary in context['diaries']:
            diary.note_topics = sorted({n.topic for n in diary.notes.all() if n.topic})

        # 検索ヒット箇所（銘柄名／日記本文／継続記録）を各 diary に付与
        from .utils import annotate_search_matches
        annotate_search_matches(context['diaries'], self.request.GET.get('query', ''))

        # 次回決算（カードの決算予定バッジ用）。決算日は日記に持たせず、表示中の
        # ページ分だけ銘柄コードで EarningsSchedule を1クエリ join して付与する。
        from .views_earnings import attach_next_earnings
        attach_next_earnings(context['diaries'])

        # フォーム用のスピードダイアルアクション。
        # 記録動線（クイック記録・新規登録）のみに絞る（FB2）。
        # テンプレート/タグ管理はメニュー「設定・その他」と重複していたため FAB からは撤去。
        context['form_actions'] = [
            {
                'id': 'quick-add',
                'type': 'quick-add',
                'url': '#',
                'icon': 'bi-lightning-charge-fill',
                'label': 'クイック記録',
                'aria_label': '素早く投資記録を作成',
                'condition': True
            },
            {
                'type': 'add',
                'url': reverse_lazy('stockdiary:create'),
                'icon': 'bi-plus-lg',
                'label': '新規登録',
                'aria_label': '新規登録'
            },
        ]

        # クイック記録用に今日の日付を追加
        context['today'] = timezone.now().date()

        # リクエスト時想起カード（1年前の今日・振り返り未記入・新着開示）
        from .services.recall_service import RecallService
        context['recall'] = RecallService.build(self.request.user)

        # 連続記録日数（毎日開きたくなる仕掛け）
        context['record_streak'] = self._compute_record_streak(self.request.user)

        # 過去の学びの再浮上（Readwise的リサーフェス）。
        # 高重要 or 振り返りノートのうち、当日基準で1件を日替わりで提示する。
        context['resurfaced_note'] = self._pick_resurfaced_note(self.request.user)

        # 新規ユーザーの空状態（日記0件 & 絞り込みなし）。
        # この時は検索・フィルター群を隠してオンボーディングカードを表示する
        has_filters = any(
            self.request.GET.get(k) for k in
            ('query', 'hashtag', 'tag', 'sector', 'status',
             'transaction_date_range', 'date_range', 'disclosure')
        )
        context['is_empty_state'] = (
            not has_filters and context['total_diary_count'] == 0
            and context['excluded_count'] == 0
        )

        return context

    @staticmethod
    def _compute_record_streak(user):
        """直近の連続記録日数を返す（記録のあった日＝継続記録 or 日記作成日）。

        当日または前日に記録があれば「継続中」とみなし、そこから連続する
        カレンダー日を数える。記録がなければ 0。モデル変更は不要。
        """
        today = timezone.localdate()
        # 連続日数の表示用途では直近のみで十分。大量データでも全スキャンしないよう窓を限定。
        window_start = today - timedelta(days=400)
        activity_dates = set(
            DiaryNote.objects.filter(diary__user=user, date__gte=window_start)
            .values_list('date', flat=True)
        )
        for created in (
            StockDiary.objects.filter(user=user, created_at__date__gte=window_start)
            .values_list('created_at', flat=True)
        ):
            if created:
                activity_dates.add(timezone.localtime(created).date())

        if not activity_dates:
            return 0

        # 当日に記録が無くても、前日まで続いていれば連続中として数える
        anchor = today if today in activity_dates else today - timedelta(days=1)
        if anchor not in activity_dates:
            return 0

        streak = 0
        cursor = anchor
        while cursor in activity_dates:
            streak += 1
            cursor -= timedelta(days=1)
        return streak

    @staticmethod
    def _pick_resurfaced_note(user):
        """過去の学び（振り返り・気づき・リスクのノート）を当日基準で1件選んで返す。

        2週間以上前のノートを対象に、その日の通日番号で安定的に1件を選ぶ
        （リロードしても変わらず、日が変わると別の学びが浮上する）。
        該当が無ければ None。
        """
        cutoff = timezone.localdate() - timedelta(days=14)
        candidate_ids = list(
            DiaryNote.objects.filter(diary__user=user)
            .filter(note_type__in=['retrospective', 'insight', 'risk'])
            .filter(date__lte=cutoff)
            .order_by('id')
            .values_list('id', flat=True)
        )
        if not candidate_ids:
            return None
        idx = timezone.localdate().toordinal() % len(candidate_ids)
        return (
            DiaryNote.objects.select_related('diary')
            .filter(id=candidate_ids[idx])
            .first()
        )

    def get(self, request, *args, **kwargs):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            try:
                self.object_list = self.get_queryset()
                page_size = self.get_paginate_by(self.object_list)
                
                if page_size:
                    paginator = self.get_paginator(self.object_list, page_size)
                    page_number = request.GET.get('page', 1)
                    try:
                        page_obj = paginator.get_page(page_number)
                    except (EmptyPage, PageNotAnInteger):
                        page_obj = paginator.get_page(1)
                    
                    data = []
                    for diary in page_obj:
                        try:
                            diary_html = render_to_string('stockdiary/partials/diary_card.html', {
                                'diary': diary,
                                'request': request,
                                'forloop': {'counter': 1}
                            })
                            data.append(diary_html)
                        except Exception as e:
                            logger.warning("Error rendering diary %s: %s", diary.id, e, exc_info=True)
                            continue
                    
                    return JsonResponse({
                        'html': data,
                        'has_next': page_obj.has_next(),
                        'next_page': page_obj.next_page_number() if page_obj.has_next() else None
                    })
            except Exception as e:
                logger.error("AJAX request error: %s", e, exc_info=True)
                return JsonResponse({
                    'message': 'データの読み込み中にエラーが発生しました。'
                }, status=500)
        
        return super().get(request, *args, **kwargs)


class StockDiaryDetailView(ObjectNotFoundRedirectMixin, LoginRequiredMixin, DetailView):
    model = StockDiary
    template_name = 'stockdiary/detail.html'
    context_object_name = 'diary'
    redirect_url = 'stockdiary:home'
    not_found_message = "日記エントリーが見つかりません。削除された可能性があります。"
    
    def get_queryset(self):
        return StockDiary.objects.filter(user=self.request.user).select_related('user').prefetch_related(
            'notes', 'tags',
            'transactions', 'stock_splits', 'linked_diaries'
        )
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cash_only_stats'] = self.object.calculate_cash_only_stats()
        self._build_history_context(context)
        self._build_notes_context(context)
        self._build_related_context(context)
        self._build_retrospective_context(context)
        self._build_actions_context(context)
        self._build_json_export_context(context)
        context['today'] = timezone.now().date()
        context['mention_map'] = get_mention_map(self.request.user)
        # 次回決算予定日（ヘッダー表示用）。決算日は日記に持たせず、銘柄コードで
        # EarningsSchedule を引いて self.object.next_earnings に付与する。
        from .services.earnings_lookup import attach_next_earnings
        attach_next_earnings([self.object])
        from tags.models import Tag as _Tag
        context['thesis_form'] = ThesisForm(user=self.request.user)
        context['thesis_all_tags'] = list(
            _Tag.objects.filter(user=self.request.user).order_by('name').values('id', 'name')
        )
        return context

    def _build_history_context(self, context):
        """取引・株式分割の履歴と統合タイムラインをコンテキストに追加する。"""
        transactions = self.object.transactions.all().order_by('-transaction_date', '-created_at')
        stock_splits = self.object.stock_splits.all().order_by('-split_date')
        context['transactions'] = transactions
        context['stock_splits'] = stock_splits

        combined = [
            {'type': 'transaction', 'date': t.transaction_date, 'data': t}
            for t in transactions
        ] + [
            {'type': 'split', 'date': s.split_date, 'data': s}
            for s in stock_splits
        ]
        combined.sort(key=lambda x: x['date'], reverse=True)
        context['combined_history'] = combined

    def _build_notes_context(self, context):
        """継続記録・トピック・仮説・イベントタイムラインをコンテキストに追加する。"""
        combined = context['combined_history']

        context['note_form'] = DiaryNoteForm(initial={'date': timezone.now().date()})
        notes = self.object.notes.all().order_by('-date')
        context['notes'] = notes
        context['reason_versions'] = self.object.reason_versions.all()

        _dir_map = {td.tag_id: td.direction for td in self.object.tag_directions.all()}
        context['theme_tags'] = [
            {'tag': t, 'direction': _dir_map.get(t.id, '')}
            for t in self.object.tags.all()
        ]
        context['theses'] = (
            self.object.theses
            .select_related('verdict')
            .prefetch_related('basis_tags')
        )

        # 仮説タブ用: ユーザー全体の 2×2 判断傾向
        from .views_growth import _build_user_quadrants
        _total_verdicts, _user_quadrants = _build_user_quadrants(self.request.user)
        context['user_quadrants'] = _user_quadrants
        context['user_total_verdicts'] = _total_verdicts

        # 想起・ライブラリの「答え合わせをする」(?verify=<thesis_id>) は、検証フォームを
        # サーバー側で最初から描画する。JS の多段起動（タブ切替→HTMXボタンclick）に
        # 依存すると、読み込みタイミング次第でフォームが開かず肩透かしになるため。
        verify_id = self.request.GET.get('verify', '')
        if verify_id.isdigit():
            verify_thesis = (
                self.object.theses.select_related('verdict')
                .filter(pk=int(verify_id)).first()
            )
            if verify_thesis:
                from django.core.exceptions import ObjectDoesNotExist
                from .forms import VerdictForm
                from .views_growth import _suggest_pnl_result
                try:
                    _v_instance = verify_thesis.verdict
                except ObjectDoesNotExist:
                    _v_instance = None
                initial = {} if _v_instance else {'pnl_result': _suggest_pnl_result(self.object)}
                context['verify_thesis'] = verify_thesis
                context['verdict_form'] = VerdictForm(instance=_v_instance, initial=initial)

        # 取引・分割・継続記録を1本のイベント時系列に統合
        event_timeline = list(combined) + [
            {'type': 'note', 'date': n.date, 'data': n}
            for n in notes
        ]
        event_timeline.sort(key=lambda x: x['date'], reverse=True)
        context['event_timeline'] = event_timeline

        # テーマ別スレッド集約: 「振り返り」を先頭、未分類を末尾に固定
        grouped = OrderedDict()
        for note in notes:
            topic = note.topic or ''
            grouped.setdefault(topic, []).append(note)
            note._topic = topic
        if DiaryNote.RETROSPECTIVE_TOPIC in grouped:
            grouped.move_to_end(DiaryNote.RETROSPECTIVE_TOPIC, last=False)
        if '' in grouped:
            grouped.move_to_end('')
        context['notes_by_topic'] = grouped
        context['note_topics'] = [t for t in grouped if t]

    def _build_related_context(self, context):
        """同銘柄の関連日記・希少性スコア付き統合ビュー・想起パネルを追加する。"""
        # 銘柄コードが空の場合は空同士でまとめない
        if self.object.stock_symbol:
            all_related = StockDiary.objects.filter(
                user=self.request.user,
                stock_symbol=self.object.stock_symbol,
            ).order_by('first_purchase_date', 'created_at')
        else:
            all_related = StockDiary.objects.filter(id=self.object.id)
        context['related_diaries'] = all_related.exclude(id=self.object.id)

        manual_linked_ids = set(self.object.linked_diaries.values_list('id', flat=True))
        related_strength = compute_related_strength(self.object, self.request.user)
        related_unified = []
        for item in related_strength:
            d = item['diary']
            excerpt = extract_lead(d.reason or '', max_len=60)
            # 共有タグの方向から逆相関優先で集約
            correlation = None
            if any(v.get('correlation') == 'inverse' for v in item['via']):
                correlation = 'inverse'
            elif any(v.get('correlation') == 'positive' for v in item['via']):
                correlation = 'positive'
            related_unified.append({
                'diary': d,
                'via': item['via'],
                'reason_labels': [v.get('label', '') for v in item['via']],
                'score': item['score'],
                'is_manual': d.id in manual_linked_ids,
                'excerpt': excerpt,
                'correlation': correlation,
            })
        context['related_unified'] = related_unified
        # バックリンクは重いため関連タブの HTMX 遅延ロードで取得する
        context['theme_recall'] = build_theme_recall(
            related_unified, self.object, self.request.user
        )

    def _build_retrospective_context(self, context):
        """売却済みの場合のみ振り返り分析データをコンテキストに追加する。"""
        context['needs_retrospective'] = False
        if not self.object.is_sold_out:
            return

        transactions = context['transactions']
        notes = context['notes']
        buys = [t for t in transactions if t.transaction_type == 'buy']
        sells = [t for t in transactions if t.transaction_type == 'sell']
        total_buy_qty = sum((t.quantity for t in buys), Decimal('0'))
        total_sell_qty = sum((t.quantity for t in sells), Decimal('0'))
        first_buy = min((t.transaction_date for t in buys), default=None)
        last_sell = max((t.transaction_date for t in sells), default=None)
        avg_buy = (
            sum((t.amount for t in buys), Decimal('0')) / total_buy_qty
        ) if total_buy_qty else None
        avg_sell = (
            sum((t.amount for t in sells), Decimal('0')) / total_sell_qty
        ) if total_sell_qty else None

        context['retro_summary'] = {
            'first_buy': first_buy,
            'last_sell': last_sell,
            'holding_days': (last_sell - first_buy).days if first_buy and last_sell else None,
            'avg_buy': avg_buy,
            'avg_sell': avg_sell,
        }

        # 最後の売り以降の振り返りがなければ記入を促す
        if last_sell is not None:
            context['needs_retrospective'] = not any(
                n.note_type == 'retrospective' and n.date >= last_sell for n in notes
            )
        else:
            context['needs_retrospective'] = not any(
                n.note_type == 'retrospective' for n in notes
            )

        # 振り返りシートにプリフィルするMarkdownサマリー
        sym = self.object.currency_symbol
        lines = ['## この投資の記録（通算）']
        if first_buy and last_sell:
            lines.append(
                f"- 期間: {first_buy:%Y/%m/%d} 〜 {last_sell:%Y/%m/%d}"
                f"（{(last_sell - first_buy).days}日）"
            )
        if avg_buy is not None and avg_sell is not None:
            lines.append(f"- 平均買値 {sym}{avg_buy:,.1f} → 平均売値 {sym}{avg_sell:,.1f}")
        rp = self.object.realized_profit or Decimal('0')
        lines.append(f"- 実現損益: {'+' if rp >= 0 else ''}{sym}{rp:,.0f}")
        lines += ['', '## 結果と要因', '', '', '## 次に活かす教訓', '', '']
        context['retro_prefill'] = '\n'.join(lines)

    def _build_actions_context(self, context):
        """詳細ページのスピードダイアルアクション一覧をコンテキストに追加する。"""
        context['diary_actions'] = [
            {
                'id': 'add-note',
                'type': 'bottom-sheet',
                'sheet_id': 'addNoteSheet',
                'onclick': 'resetNoteFormToAdd();',
                'icon': 'bi-chat-dots',
                'label': '記録を追加',
                'aria_label': '新しい継続記録を追加',
                'condition': True,
            },
            {
                'id': 'add-transaction',
                'type': 'bottom-sheet',
                'sheet_id': 'addTransactionSheet',
                'icon': 'bi-cart-plus',
                'label': '取引を追加',
                'aria_label': '取引を追加',
                'condition': True,
            },
            {
                'id': 'add-split',
                'type': 'bottom-sheet',
                'sheet_id': 'addSplitSheet',
                'icon': 'bi-scissors',
                'label': '株式分割を追加',
                'aria_label': '株式分割を追加',
                'condition': True,
            },
            {
                'id': 'add-thesis',
                'type': 'bottom-sheet',
                'sheet_id': 'addThesisSheet',
                'icon': 'bi-lightbulb',
                'label': '新しい仮説を追加',
                'aria_label': '新しい仮説を追加',
                'condition': True,
            },
            {
                'id': 'back-to-home',
                'type': 'back',
                'url': reverse_lazy('stockdiary:home'),
                'icon': 'bi-arrow-left',
                'label': '戻る',
                'aria_label': '一覧に戻る',
                'condition': True,
            },
        ]

    def _build_json_export_context(self, context):
        """LLM分析用JSONエクスポートデータをコンテキストに追加する。"""
        from datetime import datetime as _dt

        diary = self.object

        tags = [
            {'name': item['tag'].name, 'direction': item['direction']}
            for item in context.get('theme_tags', [])
        ]

        notes = [
            {
                'date': n.date.isoformat(),
                'type': n.note_type,
                'topic': n.topic or '',
                'content': n.content,
            }
            for n in diary.notes.order_by('-date')
        ]

        theses = []
        for th in context.get('theses', []):
            verdict_data = None
            try:
                v = th.verdict
                verdict_data = {
                    'hypothesis_result': v.get_hypothesis_result_display(),
                    'pnl_result': v.get_pnl_result_display(),
                    'decision_quality': v.decision_quality,
                    'missed_factor': v.missed_factor or '',
                    'learning': v.learning or '',
                }
            except Exception as e:
                logger.debug('Verdict(検証結果)のJSON化に失敗: %s', e)
            theses.append({
                'claim': th.claim,
                'basis': th.basis or '',
                'basis_tags': [t.name for t in th.basis_tags.all()],
                'horizon': th.get_horizon_display(),
                'worst_case': th.worst_case or '',
                'status': th.get_status_display(),
                'review_due_date': th.review_due_date.isoformat() if th.review_due_date else None,
                'verdict': verdict_data,
            })

        related = [
            {
                'symbol': r['diary'].stock_symbol,
                'name': r['diary'].stock_name,
                'score': round(r['score'], 2),
                'via': r['reason_labels'],
                'correlation': r.get('correlation'),
            }
            for r in context.get('related_unified', [])
        ]

        margin = None
        if diary.stock_symbol:
            try:
                from stockdiary.api_analysis import _fetch_margin_data
                margin = _fetch_margin_data(diary.stock_symbol)
            except Exception as e:
                logger.debug('信用残データの取得に失敗: %s', e)

        if diary.current_quantity > 0:
            status = '保有中'
        elif diary.transaction_count > 0:
            status = '売却済み'
        else:
            status = 'メモ'

        context['diary_json_export'] = {
            'symbol': diary.stock_symbol or '',
            'name': diary.stock_name,
            'status': status,
            'sector': diary.sector or '',
            'tags': tags,
            'investment_reason': diary.reason or '',
            'notes': notes,
            'theses': theses,
            'related_stocks': related,
            'margin': margin,
            'current_quantity': float(diary.current_quantity),
            'avg_cost': float(diary.average_purchase_price) if diary.average_purchase_price else None,
            'realized_profit': float(diary.realized_profit),
            'first_purchase_date': diary.first_purchase_date.isoformat() if diary.first_purchase_date else None,
            'exported_at': _dt.now().strftime('%Y-%m-%d'),
        }


def _sync_hashtag_tags(diary, user):
    from stockdiary.utils import extract_hashtags_with_direction
    from stockdiary.tag_axis_config import get_master_axis_map
    from tags.models import Tag
    from .models import DiaryTagDirection

    # @タグ直後の矢印(↑/↓/→)から方向を確定する。reason を最優先とし、
    # reason に出たタグはノートで方向を上書きしない（reason 優先）。
    # direction=None は「矢印なし」＝既存の手動方向を温存する意味で使う。
    name_directions = {}   # name -> 'up'/'down'/'neutral' or None
    reason_names = set()
    for name, direction in extract_hashtags_with_direction(diary.reason or ''):
        reason_names.add(name)
        if name not in name_directions:
            name_directions[name] = direction
    for content in diary.notes.values_list('content', flat=True):
        for name, direction in extract_hashtags_with_direction(content or ''):
            if name in reason_names:
                continue  # reason 優先
            if name not in name_directions:
                name_directions[name] = direction
            elif name_directions[name] is None and direction is not None:
                name_directions[name] = direction

    found = set(name_directions.keys())

    # df 再計算は「追加されたタグ」と「解除されたタグ」両方を対象にする
    affected_names = set(diary.tags.values_list('name', flat=True)) | found

    # 本文に無いタグの紐付けを解除（方向属性 DiaryTagDirection も併せて削除）
    stale_tags = list(diary.tags.exclude(name__in=found))
    if stale_tags:
        diary.tags.remove(*stale_tags)
        diary.tag_directions.filter(tag__in=stale_tags).delete()

    # 標準タグ（MasterTag）に該当すればその軸、なければ個人ラベル（custom 軸）
    master_axis_map = get_master_axis_map()
    for name in found:
        tag, _ = Tag.objects.get_or_create(
            user=user, name=name,
            defaults={'axis': master_axis_map.get(name, 'custom')},
        )
        diary.tags.add(tag)
        # 矢印付きタグのみ方向を反映する。矢印なし（None）は手動設定を温存。
        direction = name_directions.get(name)
        if direction is not None:
            DiaryTagDirection.objects.update_or_create(
                diary=diary, tag=tag, defaults={'direction': direction},
            )

    # df（出現銘柄数）を追加・解除の両影響を反映して再計算
    if affected_names:
        for tag in Tag.objects.filter(user=user, name__in=affected_names):
            tag.df = tag.stockdiary_set.values('stock_symbol').distinct().count()
            tag.save(update_fields=['df'])


class StockDiaryCreateView(LoginRequiredMixin, CreateView):
    model = StockDiary
    form_class = StockDiaryForm
    template_name = 'stockdiary/diary_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs
    
    def form_valid(self, form):
        form.instance.user = self.request.user
        response = super().form_valid(form)
        
        # 画像ファイルの処理
        image_file = form.cleaned_data.get('image')
        if image_file:
            success = self.object.process_and_save_image(image_file)
            if not success:
                messages.warning(self.request, '日記は作成されましたが、画像の処理に失敗しました。')
                
        # 初回取引は作成フローから除去（取引は詳細ページで追加する）。
        messages.success(self.request, '日記を作成しました')

        _sync_hashtag_tags(self.object, self.request.user)
        cache.delete(f'mention_map_u{self.request.user.id}')

        # 検証ループの入口: 作成時に「確認の目印」が書かれていれば仮説(Thesis)を
        # そのまま作成する。書く動機が最も高い瞬間にループの起点を置くため
        # （後から詳細ページの仮説ビューを探して書きに行く負担をなくす）。
        checkpoint = (self.request.POST.get('thesis_checkpoint') or '').strip()
        if checkpoint:
            from .models import Thesis
            from .views_growth import _default_review_due_date
            direction = (self.request.POST.get('thesis_checkpoint_direction') or '').strip()
            if direction not in {c for c, _ in Thesis.CHECKPOINT_DIR_CHOICES}:
                direction = ''
            checkpoint = checkpoint[:200]
            Thesis.objects.create(
                diary=self.object,
                claim=Thesis.compose_claim(checkpoint, direction),
                checkpoint=checkpoint,
                checkpoint_direction=direction,
                horizon='next_earnings',
                review_due_date=_default_review_due_date(self.object, 'next_earnings'),
            )
        return response

    def get_success_url(self):
        return reverse('stockdiary:detail', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # スピードダイアルアクション
        context['form_actions'] = [
            {
                'type': 'submit',
                'icon': 'bi-check-lg',
                'label': '保存',
                'aria_label': '日記を保存',
                'condition': True
            },
            {
                'type': 'back',
                'url': reverse_lazy('stockdiary:home'),
                'icon': 'bi-arrow-left',
                'label': '戻る'
            }
        ]

        # 「確認の目印」の向き選択肢（作成時の仮説入口用）
        from .models import Thesis
        context['checkpoint_dir_choices'] = Thesis.CHECKPOINT_DIR_CHOICES

        return context


class StockDiaryUpdateView(ObjectNotFoundRedirectMixin, LoginRequiredMixin, UpdateView):
    model = StockDiary
    form_class = StockDiaryForm
    template_name = 'stockdiary/diary_form.html'
    redirect_url = 'stockdiary:home'
    not_found_message = "日記エントリーが見つかりません。削除された可能性があります。"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_actions'] = [
            {
                'type': 'back',
                'url': reverse_lazy('stockdiary:detail', kwargs={'pk': self.object.pk}),
                'icon': 'bi-arrow-left',
                'label': '戻る',
                'aria_label': '戻る' 
            },
            {
                'type': 'template',
                'url': reverse_lazy('analysis_template:list'),
                'icon': 'bi-clipboard-data',
                'label': 'テンプレート',
                'aria_label': 'テンプレート' 
            },
            {
                'type': 'tag',
                'url': reverse_lazy('tags:list'),
                'icon': 'bi-tags',
                'label': 'タグ管理',
                'aria_label': 'タグ管理' 
            }
        ]
        return context

    def get_queryset(self):
        return StockDiary.objects.filter(user=self.request.user)
    
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs
    
    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        
        if request.POST.get('clear_image'):
            success = self.object.delete_image()
            if not success:
                messages.warning(request, '画像の削除に失敗しました。')
        
        return super().post(request, *args, **kwargs)
    
    def form_valid(self, form):
        # 上書き保存の前に、DBの旧 reason を取得しておく（「見立て」の来歴退避用）。
        old_reason = (
            StockDiary.objects
            .filter(pk=self.object.pk)
            .values_list('reason', flat=True)
            .first()
        ) or ''

        response = super().form_valid(form)

        # reason に差分があれば旧版を自動スナップショット（明示操作不要・coalesce 付き）。
        ReasonVersion.snapshot_on_change(self.object, old_reason)

        image_file = form.cleaned_data.get('image')
        if image_file:
            success = self.object.process_and_save_image(image_file)
            if not success:
                messages.warning(self.request, '日記は更新されましたが、画像の処理に失敗しました。')

        _sync_hashtag_tags(self.object, self.request.user)
        messages.success(self.request, '日記を更新しました')
        cache.delete(f'mention_map_u{self.request.user.id}')
        return response

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        return form
    
    def get_success_url(self):
        return reverse_lazy('stockdiary:detail', kwargs={'pk': self.object.pk})


class StockDiaryDeleteView(LoginRequiredMixin, DeleteView):
    model = StockDiary
    template_name = 'stockdiary/diary_confirm_delete.html'
    success_url = reverse_lazy('stockdiary:home')
    
    def get_queryset(self):
        return StockDiary.objects.filter(user=self.request.user)

    def delete(self, request, *args, **kwargs):
        response = super().delete(request, *args, **kwargs)
        cache.delete(f'mention_map_u{request.user.id}')
        return response


def _add_note_form_error_messages(request, form):
    """継続記録フォームの検証エラーをメッセージとして表示する。
    以前は無言でリダイレクトしており、保存失敗が画面に出ず原因不明になっていた。"""
    for field, errors in form.errors.items():
        if field == '__all__':
            label = ''
        else:
            field_obj = form.fields.get(field)
            label = (field_obj.label or field) if field_obj else field
        text = ' '.join(errors)
        messages.error(request, f"{label}: {text}" if label else text)


class AddDiaryNoteView(LoginRequiredMixin, CreateView):
    """日記エントリーへの継続記録追加"""
    model = DiaryNote
    form_class = DiaryNoteForm
    http_method_names = ['post']
    
    def form_valid(self, form):
        diary_id = self.kwargs.get('pk')
        diary = get_object_or_404(StockDiary, id=diary_id, user=self.request.user)
        form.instance.diary = diary
        
        response = super().form_valid(form)

        # 参照書類IDを保存
        source_doc_id = self.request.POST.get('source_doc_id', '').strip()[:8]
        if source_doc_id:
            self.object.source_doc_id = source_doc_id
            self.object.save(update_fields=['source_doc_id'])

        # 継続記録本文の @タグを日記のタグへ同期（本文＋全継続記録が正）
        _sync_hashtag_tags(diary, self.request.user)

        image_file = self.request.FILES.get('image')
        if image_file:
            if image_file.size > 10 * 1024 * 1024:
                messages.error(self.request, '画像ファイルのサイズは10MB以下にしてください')
                return self.form_invalid(form)

            valid_formats = ['image/jpeg', 'image/jpg', 'image/png', 'image/gif', 'image/webp']
            if hasattr(image_file, 'content_type') and image_file.content_type not in valid_formats:
                messages.error(self.request, 'JPEG、PNG、GIF、WebP形式の画像ファイルのみアップロード可能です')
                return self.form_invalid(form)

            success = self.object.process_and_save_image(image_file)
            if not success:
                messages.warning(self.request, '継続記録は追加されましたが、画像の処理に失敗しました。')

        messages.success(self.request, "継続記録を追加しました")
        return response
    
    def get_success_url(self):
        return reverse_lazy('stockdiary:detail', kwargs={'pk': self.kwargs.get('pk')})

    def form_invalid(self, form):
        diary_id = self.kwargs.get('pk')
        _add_note_form_error_messages(self.request, form)
        return redirect('stockdiary:detail', pk=diary_id)


class UpdateDiaryNoteView(LoginRequiredMixin, UpdateView):
    """継続記録の編集"""
    model = DiaryNote
    form_class = DiaryNoteForm
    http_method_names = ['post']

    def get_queryset(self):
        return DiaryNote.objects.filter(diary__user=self.request.user)

    def form_valid(self, form):
        response = super().form_valid(form)

        source_doc_id = self.request.POST.get('source_doc_id', '').strip()[:8]
        if source_doc_id:
            self.object.source_doc_id = source_doc_id
            self.object.save(update_fields=['source_doc_id'])

        # 継続記録本文の @タグを日記のタグへ同期（本文＋全継続記録が正）
        _sync_hashtag_tags(self.object.diary, self.request.user)

        image_file = self.request.FILES.get('image')
        if image_file:
            if image_file.size > 10 * 1024 * 1024:
                messages.error(self.request, '画像ファイルのサイズは10MB以下にしてください')
                return self.form_invalid(form)

            valid_formats = ['image/jpeg', 'image/jpg', 'image/png', 'image/gif', 'image/webp']
            if hasattr(image_file, 'content_type') and image_file.content_type not in valid_formats:
                messages.error(self.request, 'JPEG、PNG、GIF、WebP形式の画像ファイルのみアップロード可能です')
                return self.form_invalid(form)

            success = self.object.process_and_save_image(image_file)
            if not success:
                messages.warning(self.request, '継続記録は更新されましたが、画像の処理に失敗しました。')

        messages.success(self.request, "継続記録を更新しました")
        return response

    def get_success_url(self):
        return reverse_lazy('stockdiary:detail', kwargs={'pk': self.kwargs.get('diary_pk')})

    def form_invalid(self, form):
        _add_note_form_error_messages(self.request, form)
        return redirect('stockdiary:detail', pk=self.kwargs.get('diary_pk'))


class DeleteDiaryNoteView(LoginRequiredMixin, DeleteView):
    """継続記録を削除するビュー"""
    model = DiaryNote
    template_name = 'stockdiary/note_confirm_delete.html'
    
    def get_queryset(self):
        return DiaryNote.objects.filter(diary__user=self.request.user)

    def form_valid(self, form):
        # 削除前に親日記を退避し、削除後に @タグを再同期
        diary = self.object.diary
        response = super().form_valid(form)
        _sync_hashtag_tags(diary, self.request.user)
        return response

    def get_success_url(self):
        diary_pk = self.kwargs.get('diary_pk')
        return reverse_lazy('stockdiary:detail', kwargs={'pk': diary_pk})
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['diary_pk'] = self.kwargs.get('diary_pk')
        return context


class DiaryTabContentView(LoginRequiredMixin, View):
    def get(self, request, diary_id, tab_type):
        try:
            diary = StockDiary.objects.get(id=diary_id, user=request.user)
            
            context = {
                'diary': diary,
                'diary_id': diary.id,
            }
            
            if tab_type == 'notes':
                html = self._render_notes_tab(diary)
            elif tab_type == 'details':
                html = self._render_details_tab(context)
            else:
                return JsonResponse({'error': '無効なタブタイプです'}, status=400)
            
            return JsonResponse({'html': html})
            
        except StockDiary.DoesNotExist:
            return JsonResponse({'error': '日記が見つかりません'}, status=404)
        except Exception as e:
            logger.error("Tab content error: %s", e, exc_info=True)
            return JsonResponse({'error': 'タブコンテンツの読み込みに失敗しました'}, status=500)

    def _render_notes_tab(self, diary):
        """継続記録タブのHTMLをテンプレートで生成"""
        raw_notes = diary.notes.all().order_by('-date')[:3]
        notes_count = diary.notes.count()

        notes = []
        for note in raw_notes:
            price_change = None
            price_change_class = ''
            if note.current_price and diary.purchase_price:
                price_change = (
                    (float(note.current_price) / float(diary.purchase_price)) - 1
                ) * 100
                price_change_class = 'text-success' if price_change > 0 else 'text-danger'
            note.badge_class = get_note_badge_class(note.note_type)
            note.price_change = price_change
            note.price_change_class = price_change_class
            notes.append(note)

        return render_to_string(
            'stockdiary/partials/_tab_notes.html',
            {'notes': notes, 'notes_count': notes_count, 'diary_id': diary.id},
        )
            
    
    def _render_details_tab(self, context):
        """取引情報タブのHTMLをテンプレートで生成"""
        diary = context['diary']

        if diary.current_quantity and diary.current_quantity > 0:
            total_investment = (
                float(diary.average_purchase_price) * float(diary.current_quantity)
                if diary.average_purchase_price else 0
            )
        else:
            total_investment = float(diary.total_buy_amount) if diary.total_buy_amount else 0

        profit = float(diary.realized_profit) if diary.realized_profit is not None else 0
        profit_rate = 0
        if diary.total_buy_amount and float(diary.total_buy_amount) > 0:
            profit_rate = (profit / float(diary.total_buy_amount)) * 100

        return render_to_string('stockdiary/partials/_tab_details.html', {
            'diary': diary,
            'total_investment': total_investment,
            'profit_class': 'profit' if profit > 0 else ('loss' if profit < 0 else 'text-muted'),
            'profit_sign': '+' if profit > 0 else '',
            'profit_rate': profit_rate,
        })


class DiarySummaryView(LoginRequiredMixin, TemplateView):
    """銘柄ごとの日記サマリー一覧（旧 StockListView を統合した銘柄一覧の正規ページ）"""
    template_name = 'stockdiary/diary_summary.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        search_query = self.request.GET.get('q', '').strip()
        sort_by = self.request.GET.get('sort', 'updated_desc')
        sector_filter = self.request.GET.get('sector', '').strip()

        diary_agg = (
            StockDiary.objects.filter(user=user, stock_symbol__isnull=False)
            .exclude(stock_symbol='')
            .values('stock_symbol')
            .annotate(
                stock_name=Max('stock_name'),
                sector=Max('sector'),
                latest_updated=Max('updated_at'),
                diary_count=Count('id'),
                active_count=Count('id', filter=Q(current_quantity__gt=0)),
                total_realized=Sum('cash_only_realized_profit'),
                latest_disclosure=Max('latest_disclosure_doc_type_name'),
            )
        )

        # 継続記録数（DiaryNote）を別クエリで集計（JOIN乗算を回避）
        note_count_map = dict(
            DiaryNote.objects.filter(
                diary__user=user,
                diary__stock_symbol__isnull=False,
            )
            .exclude(diary__stock_symbol='')
            .values('diary__stock_symbol')
            .annotate(cnt=Count('id'))
            .values_list('diary__stock_symbol', 'cnt')
        )

        # 仮説の検証期限切れ銘柄を集計（due state の判定用）
        from .models import Thesis
        from django.utils import timezone as tz
        due_symbols = set(
            Thesis.objects.filter(
                diary__user=user,
                status=Thesis.STATUS_OPEN,
                review_due_date__lte=tz.localdate(),
            ).values_list('diary__stock_symbol', flat=True)
        )

        # 取引回数を別クエリで集計（「向き合った量」の一要素。JOIN乗算を回避）
        txn_count_map = dict(
            Transaction.objects.filter(
                diary__user=user,
                diary__stock_symbol__isnull=False,
            )
            .exclude(diary__stock_symbol='')
            .values('diary__stock_symbol')
            .annotate(cnt=Count('id'))
            .values_list('diary__stock_symbol', 'cnt')
        )

        # 検証（Verdict）の的中数・総数を銘柄ごとに集計（「成果」の一要素）。
        # 的中の判定は metrics（意味定義の正本）に委譲する。
        from .models import Verdict
        from .services import metrics
        verdict_map = {}  # symbol -> {'hit': int, 'total': int}
        for r in (
            Verdict.objects.filter(
                thesis__diary__user=user,
                thesis__diary__stock_symbol__isnull=False,
            )
            .exclude(thesis__diary__stock_symbol='')
            .values('thesis__diary__stock_symbol', 'hypothesis_result')
        ):
            sym = r['thesis__diary__stock_symbol']
            d = verdict_map.setdefault(sym, {'hit': 0, 'total': 0})
            d['total'] += 1
            if metrics.is_hypothesis_hit(r['hypothesis_result']):
                d['hit'] += 1

        # CompanyMaster で未設定業種を補完（旧 StockListView から引き継ぎ）
        symbols = [row['stock_symbol'] for row in diary_agg]
        company_sector_map = {
            c.code: c.industry_name_33 or c.industry_name_17 or '未分類'
            for c in CompanyMaster.objects.filter(code__in=symbols)
        }

        summary_list = []
        for row in diary_agg:
            sector = row['sector'] or ''
            if not sector or sector == '未分類':
                sector = company_sector_map.get(row['stock_symbol'], '未分類')
            sym = row['stock_symbol']
            is_holding = row['active_count'] > 0
            # 状態分類: due(検証待ち) > live(保有中) > closed(終了)
            if sym in due_symbols:
                state = 'due'
            elif is_holding:
                state = 'live'
            else:
                state = 'closed'
            note_count = note_count_map.get(sym, 0)
            verdict = verdict_map.get(sym, {'hit': 0, 'total': 0})
            summary_list.append({
                'symbol': sym,
                'name': row['stock_name'],
                'sector': sector,
                'latest_date': row['latest_updated'],
                'note_count': note_count,
                'diary_count': row['diary_count'],
                # 「向き合った量」: 日記＋ノートの累計記録数と取引回数
                'record_count': row['diary_count'] + note_count,
                'txn_count': txn_count_map.get(sym, 0),
                'is_holding': is_holding,
                'state': state,
                'realized_profit': int(row['total_realized'] or 0),
                # 「成果」: 検証の的中数／総数（検証がある銘柄のみ意味を持つ）
                'verdict_hit': verdict['hit'],
                'verdict_total': verdict['total'],
                'verdict_label': f"{verdict['hit']}/{verdict['total']}" if verdict['total'] else '',
                'disclosure': row['latest_disclosure'] or '',
                'diary_id': None,
            })

        # 日記が1件の銘柄は詳細画面へ直接リンクするためpkを取得
        single_symbols = [s['symbol'] for s in summary_list if s['diary_count'] == 1]
        single_pk_map = dict(
            StockDiary.objects.filter(user=user, stock_symbol__in=single_symbols)
            .values_list('stock_symbol', 'id')
        )
        for s in summary_list:
            if s['diary_count'] == 1:
                s['diary_id'] = single_pk_map.get(s['symbol'])

        if search_query:
            summary_list = [
                s for s in summary_list
                if search_query.lower() in s['name'].lower()
                or search_query.lower() in s['symbol'].lower()
                or search_query.lower() in s['sector'].lower()
            ]

        # 業種リストは業種フィルター適用前に作成（業種で絞り込み中も選択肢を維持）
        sectors = sorted({s['sector'] for s in summary_list})

        if sector_filter:
            summary_list = [s for s in summary_list if s['sector'] == sector_filter]

        _epoch = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)
        sort_mapping = {
            'updated_desc': (lambda x: x['latest_date'] or _epoch, True),
            'updated_asc': (lambda x: x['latest_date'] or _epoch, False),
            'note_count_desc': (lambda x: x['note_count'], True),
            'note_count_asc': (lambda x: x['note_count'], False),
            'symbol': (lambda x: x['symbol'], False),
            'name': (lambda x: x['name'], False),
            'sector': (lambda x: x['sector'], False),
        }
        key_fn, reverse = sort_mapping.get(sort_by, sort_mapping['updated_desc'])
        summary_list.sort(key=key_fn, reverse=reverse)

        context['page_actions'] = [
            {
                'type': 'back',
                'url': reverse_lazy('stockdiary:home'),
                'icon': 'bi-arrow-left',
                'label': '戻る',
            },
        ]
        # 状態カウント（レンズ表示用）
        state_counts = {'due': 0, 'live': 0, 'closed': 0}
        for s in summary_list:
            state_counts[s['state']] = state_counts.get(s['state'], 0) + 1

        # レンズ
        lens = self.request.GET.get('lens', 'state')

        # テーマレンズ用グルーピング（タグは日記ごとに取得）
        from collections import defaultdict as _dd
        theme_groups = _dd(list)
        if lens == 'theme':
            # symbol→stock のマップを作成
            sym_map = {s['symbol']: s for s in summary_list}
            from .models import StockDiary as _SD
            from tags.models import Tag as _Tag
            diary_tags = (
                _SD.objects.filter(user=user, stock_symbol__in=list(sym_map.keys()))
                .prefetch_related('tags')
                .values('stock_symbol', 'tags__name')
            )
            added = set()
            for row in diary_tags:
                sym = row['stock_symbol']
                tag = row['tags__name'] or ''
                key = (sym, tag)
                if key not in added and sym in sym_map:
                    added.add(key)
                    theme_groups[tag].append(sym_map[sym])
            # タグなし銘柄を末尾に
            tagged = {s['symbol'] for key in added for s in [sym_map[key[0]]] if key[0] in sym_map}
            untagged = [s for s in summary_list if s['symbol'] not in tagged]
            for s in untagged:
                theme_groups[''].append(s)

        # 時系列・銘柄レンズの並び替え
        time_list = sorted(summary_list, key=lambda x: x['latest_date'] or _epoch, reverse=True)
        symbol_list = sorted(summary_list, key=lambda x: x['symbol'])

        # 銘柄索引：コード帯でグルーピング（本の索引のジャンプレール用）。
        # 日本株の4桁コードは先頭桁で束ね（例 7000番台）、外国株など英字始まりは
        # 頭文字で束ねる。symbol_list はコード昇順のため各グループは連続・整列済み。
        from collections import OrderedDict as _OrderedDict

        def _code_group(sym):
            s = (sym or '').strip()
            if s[:1].isdigit():
                return (s[0], f'{s[0]}000番台')
            c = (s[:1] or '#').upper()
            return (c, c)

        symbol_index_map = _OrderedDict()
        for s in symbol_list:
            gk, gl = _code_group(s['symbol'])
            if gk not in symbol_index_map:
                symbol_index_map[gk] = {'key': gk, 'label': gl, 'items': []}
            symbol_index_map[gk]['items'].append(s)
        symbol_index = list(symbol_index_map.values())

        # 銘柄別テーブル（量×成果の対比）の並び替え。
        # リストビューの lens/sort とは独立に、専用のソート軸を持つ。
        table_sort = self.request.GET.get('tsort', 'record_desc')
        table_sort_mapping = {
            'record_desc': (lambda x: x['record_count'], True),
            'txn_desc':    (lambda x: x['txn_count'], True),
            'profit_desc': (lambda x: x['realized_profit'], True),
            'profit_asc':  (lambda x: x['realized_profit'], False),
            'hit_desc':    (lambda x: (x['verdict_hit'] / x['verdict_total']) if x['verdict_total'] else -1, True),
            'symbol':      (lambda x: x['symbol'], False),
        }
        t_key, t_reverse = table_sort_mapping.get(table_sort, table_sort_mapping['record_desc'])
        table_list = sorted(summary_list, key=t_key, reverse=t_reverse)

        # レンズタブ定義
        lens_tabs = [
            ('state',  '状態',   None),
            ('theme',  'テーマ', None),
            ('time',   '時系列', None),
            ('symbol', '銘柄',   None),
        ]

        # 状態グルーピング順
        state_groups = [
            ('due',    '検証待ち'),
            ('live',   '保有中'),
            ('closed', '終了'),
        ]

        # 状態レンズ用グループ済みリスト
        state_grouped = [
            ('due',    '検証待ち', [s for s in summary_list if s['state'] == 'due']),
            ('live',   '保有中',   [s for s in summary_list if s['state'] == 'live']),
            ('closed', '終了',     [s for s in summary_list if s['state'] == 'closed']),
        ]

        context.update({
            'summary_list': summary_list if lens in ('state', 'theme') else (time_list if lens == 'time' else symbol_list),
            'table_list': table_list,
            'table_sort': table_sort,
            'table_sorts': [
                ('record_desc', '記録が多い'),
                ('txn_desc',    '取引が多い'),
                ('profit_desc', '損益（上位）'),
                ('profit_asc',  '損益（下位）'),
                ('hit_desc',    '的中率'),
                ('symbol',      'コード順'),
            ],
            'search_query': search_query,
            'sort_by': sort_by,
            'sectors': sectors,
            'sector_filter': sector_filter,
            'lens': lens,
            'lens_tabs': lens_tabs,
            'state_counts': state_counts,
            'state_grouped': state_grouped,
            'theme_groups': dict(theme_groups),
            'symbol_index': symbol_index,
        })
        return context


class ServeImageView(LoginRequiredMixin, View):
    """ユーザー認証付きの画像配信ビュー"""
    
    def get(self, request, diary_id, image_type, note_id=None):
        try:
            diary = get_object_or_404(StockDiary, id=diary_id, user=request.user)
            
            if image_type == 'diary':
                if not diary.image:
                    raise Http404("画像が見つかりません")
                image_field = diary.image
                
            elif image_type == 'note':
                if not note_id:
                    raise Http404("ノートIDが指定されていません")
                note = get_object_or_404(DiaryNote, id=note_id, diary=diary)
                if not note.image:
                    raise Http404("画像が見つかりません")
                image_field = note.image
            else:
                raise Http404("無効な画像タイプです")
            
            is_thumbnail = request.GET.get('thumbnail') == '1'
            if is_thumbnail:
                return self._serve_thumbnail(image_field, request)
            
            return self._serve_image(image_field)
            
        except Exception as e:
            logger.error("Image serving error: %s", e, exc_info=True)
            raise Http404("画像の配信中にエラーが発生しました")
    
    def _serve_image(self, image_field):
        """通常の画像を配信"""
        try:
            image_file = image_field.open('rb')
            file_path = image_field.name
            content_type, _ = mimetypes.guess_type(file_path)
            if not content_type:
                content_type = 'image/jpeg'
            
            response = HttpResponse(image_file.read(), content_type=content_type)
            response['Cache-Control'] = 'private, max-age=3600'
            
            filename = os.path.basename(file_path)
            response['Content-Disposition'] = f'inline; filename="{filename}"'
            
            image_file.close()
            return response
            
        except Exception as e:
            logger.error("Error serving image: %s", e, exc_info=True)
            raise Http404("画像ファイルが見つかりません")
    
    def _serve_thumbnail(self, image_field, request):
        """サムネイル画像を生成して配信"""
        try:
            width = int(request.GET.get('w', 300))
            height = int(request.GET.get('h', 200))
            
            width = min(max(width, 50), 800)
            height = min(max(height, 50), 600)
            
            image_file = image_field.open('rb')
            img = Image.open(image_file)
            
            if img.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                img = background
            
            img_ratio = img.width / img.height
            thumb_ratio = width / height
            
            if img_ratio > thumb_ratio:
                new_height = height
                new_width = int(height * img_ratio)
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                left = (new_width - width) // 2
                img = img.crop((left, 0, left + width, height))
            else:
                new_width = width
                new_height = int(width / img_ratio)
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                top = (new_height - height) // 2
                img = img.crop((0, top, width, top + height))
            
            output = io.BytesIO()
            
            try:
                img.save(output, format='WebP', quality=80, optimize=True)
                content_type = 'image/webp'
            except Exception:
                img.save(output, format='JPEG', quality=80, optimize=True)
                content_type = 'image/jpeg'
            
            output.seek(0)
            
            response = HttpResponse(output.getvalue(), content_type=content_type)
            response['Cache-Control'] = 'private, max-age=7200'
            
            image_file.close()
            return response
            
        except Exception as e:
            logger.warning("Error creating thumbnail: %s", e, exc_info=True)
            return self._serve_image(image_field)


# ==========================================
# ファンクションベースビュー
# ==========================================
def diary_list(request):
    """日記リストを表示するビュー（HTMX専用パーシャル）"""
    from .utils import apply_diary_filters, annotate_search_matches

    is_htmx = (
        request.headers.get('HX-Request') == 'true'
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    )
    if not is_htmx:
        return redirect(f'/stockdiary/?{request.GET.urlencode()}')

    try:
        queryset = (
            StockDiary.objects.filter(user=request.user)
            .select_related('user')
            .prefetch_related('tags', 'notes')
        )
        queryset = apply_diary_filters(queryset, request.GET, request.user)

        current_params = request.GET.copy()
        current_params.pop('page', None)

        paginator = Paginator(queryset, DIARY_LIST_PAGE_SIZE)
        try:
            diaries = paginator.page(request.GET.get('page', 1))
        except (PageNotAnInteger, EmptyPage):
            diaries = paginator.page(1)

        annotate_search_matches(diaries.object_list, request.GET.get('query', '').strip())

        sectors = (
            StockDiary.objects.filter(user=request.user, sector__isnull=False)
            .exclude(sector='')
            .values_list('sector', flat=True)
            .distinct()
            .order_by('sector')
        )

        return render(request, 'stockdiary/partials/diary_list.html', {
            'diaries': diaries,
            'page_obj': diaries,
            'tags': Tag.objects.filter(user=request.user),
            'sectors': list(sectors),
            'request': request,
            'current_params': current_params,
        })

    except Exception as e:
        logger.error("Diary list error: %s", e, exc_info=True)
        return HttpResponse(
            '<div class="alert alert-danger">日記リストの読み込みに失敗しました。</div>',
            status=500,
        )

def tab_content(request, diary_id, tab_type):
    """日記カードのタブコンテンツを表示するビュー"""
    try:
        try:
            diary = StockDiary.objects.prefetch_related(
                'notes', 'transactions'
            ).get(id=diary_id, user=request.user)
        except StockDiary.DoesNotExist:
            return HttpResponse(
                '<div class="alert alert-warning">指定された日記が見つかりません。</div>', 
                status=404
            )

        context = {
            'diary': diary,
            'is_detail_view': False,  # ホーム画面からの呼び出し
        }
        
        try:
            if tab_type == 'notes':
                notes = diary.notes.all().order_by('-date')[:3]
                context['notes'] = notes
                context['mention_map'] = get_mention_map(request.user)
                template_name = 'stockdiary/partials/tab_notes.html'

            elif tab_type == 'details':
                # 取引タブの処理を追加
                transactions = diary.transactions.all().order_by('-transaction_date', '-created_at')[:5]
                context['transactions'] = transactions
                context['transaction_count'] = diary.transaction_count
                template_name = 'stockdiary/partials/tab_details.html'
                        
            else:
                return HttpResponse(
                    '<div class="alert alert-warning">無効なタブタイプです。</div>', 
                    status=400
                )

            return render(request, template_name, context)

        except Exception as render_error:
            logger.error("タブレンダリングエラー: %s", render_error, exc_info=True)
            return HttpResponse(
                '<div class="alert alert-danger">タブコンテンツの読み込み中にエラーが発生しました。</div>',
                status=500
            )

    except Exception as e:
        logger.error("想定外のエラー: %s", e, exc_info=True)
        return HttpResponse(
            '<div class="alert alert-danger">予期せぬエラーが発生しました。</div>',
            status=500
        )

def calendar_view(request):
    """カレンダー全体ビュー"""
    today = timezone.now().date()
    month = today.month
    year = today.year
    
    user_diaries = StockDiary.objects.filter(user=request.user)
    
    return render(request, 'stockdiary/calendar.html', {
        'today': today,
        'month': month,
        'year': year,
        'diaries': user_diaries
    })


@login_required
@require_GET
def search_suggestion(request):
    """検索キーワードに基づいて提案を返す"""
    query = request.GET.get('query', '').strip()
    
    if len(query) < 2:
        return HttpResponse('')
    
    stock_matches = StockDiary.objects.filter(
        user=request.user
    ).filter(
        Q(stock_name__icontains=query) | 
        Q(stock_symbol__icontains=query)
    ).distinct().values('stock_name', 'stock_symbol')[:5]
    
    tag_matches = Tag.objects.filter(
        user=request.user, 
        name__icontains=query
    ).values('id', 'name')[:3]
    
    if not stock_matches and not tag_matches:
        return HttpResponse('')

    return render(request, 'stockdiary/partials/_search_suggestions.html', {
        'stock_matches': stock_matches,
        'tag_matches': tag_matches,
    })


def csrf_failure_view(request, reason=""):
    """CSRF失敗時のカスタムハンドラー"""
    demo_username = getattr(settings, 'DEMO_USERNAME', 'demo')
    if (hasattr(request, 'user') and
        request.user.is_authenticated and
        request.user.username == demo_username):

        messages.warning(
            request,
            "デモアカウントは共有のため、一時的なエラーが発生することがあります。"
            "ページを更新するか、もう一度お試しください。"
        )
        return redirect('stockdiary:home')

    return render(request, 'errors/csrf_failure.html', {
        'reason': reason,
    }, status=403)


@login_required
def api_stock_diaries(request, symbol):
    """特定の銘柄の日記一覧をJSON形式で返すAPI"""
    try:
        diaries = StockDiary.objects.filter(
            user=request.user,
            stock_symbol=symbol
        ).order_by('-created_at')
        
        diary_data = []
        for diary in diaries:
            tags = [tag.name for tag in diary.tags.all()]
            
            # ✅ 現物取引のみの統計を取得
            cash_stats = diary.calculate_cash_only_stats()
            
            diary_data.append({
                'id': diary.id,
                'first_purchase_date': diary.first_purchase_date.strftime('%Y年%m月%d日') if diary.first_purchase_date else None,
                'created_at': diary.created_at.strftime('%Y年%m月%d日'),
                'reason': diary.reason,
                'tags': tags,
                # 状態フラグ
                'is_memo': diary.is_memo,
                'is_holding': diary.is_holding,
                'is_sold_out': diary.is_sold_out,
                # 現物取引の統計
                'average_purchase_price': float(cash_stats['average_purchase_price']) if cash_stats['average_purchase_price'] else None,
                'current_quantity': float(cash_stats['current_quantity']) if cash_stats['current_quantity'] else None,
                'total_buy_amount': float(cash_stats['total_buy_amount']) if cash_stats['total_buy_amount'] else None,
                'total_sell_amount': float(cash_stats['total_sell_amount']) if cash_stats['total_sell_amount'] else None,
                'realized_profit': float(cash_stats['realized_profit']) if cash_stats['realized_profit'] else None,
                'transaction_count': diary.transaction_count,
                # 通貨表示用（為替変換なし・元通貨で表示）
                'currency_unit': diary.currency_unit,
            })
        
        return JsonResponse({
            'diaries': diary_data,
            'count': len(diary_data),
            'stock_symbol': symbol,
            'success': True
        })
        
    except Exception as e:
        # エラーハンドリング
        return JsonResponse({
            'error': str(e),
            'success': False
        }, status=500)
        
class NotificationListView(LoginRequiredMixin, TemplateView):
    """通知管理ページ - 予定の表示"""
    template_name = 'stockdiary/notification_list.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        notifications = DiaryNotification.objects.filter(
            diary__user=self.request.user,
            is_active=True
        ).select_related('diary').order_by('remind_at')
        
        # Get filter parameter from GET request
        filter_type = self.request.GET.get('filter', 'all')
        today = timezone.now()
        today_start = timezone.make_aware(timezone.datetime.combine(today.date(), timezone.datetime.min.time()))
        today_end = timezone.make_aware(timezone.datetime.combine(today.date(), timezone.datetime.max.time()))
        
        # Apply date filters
        if filter_type == 'today':
            notifications = notifications.filter(remind_at__gte=today_start, remind_at__lte=today_end)
        elif filter_type == 'upcoming':
            notifications = notifications.filter(remind_at__gte=today_start)
        
        # Paginate results
        paginator = Paginator(notifications, NOTIFICATION_LIST_PAGE_SIZE)
        page_number = self.request.GET.get('page', 1)
        
        try:
            page_obj = paginator.page(page_number)
        except (PageNotAnInteger, EmptyPage):
            page_obj = paginator.page(1)
        
        # Add preview information for each notification
        for notification in page_obj:
            notification.title = notification.diary.stock_name
            notification.sent_at = notification.remind_at
            notification.is_read = False  # 予定は未読状態
            notification.message_preview = notification.message[:100] if notification.message else '通知予定'
            if notification.message and len(notification.message) > 100:
                notification.message_preview += '...'
            notification.diary_url = reverse('stockdiary:detail', kwargs={'pk': notification.diary.pk})
        
        context.update({
            'notifications': page_obj,
            'filter_type': filter_type,
            'unread_count': DiaryNotification.objects.filter(
                diary__user=self.request.user,
                is_active=True,
                remind_at__gte=today_start
            ).count(),
        })
        
        
        context['page_actions'] = [
            {
                'type': 'back',
                'url': reverse_lazy('stockdiary:home'),
                'icon': 'bi-arrow-left',
                'label': '戻る'
            }
        ]    
        return context

@login_required
@require_POST
def toggle_exclude_diary(request, diary_id):
    """日記の除外フラグをトグルする（HTMX対応）"""
    diary = get_object_or_404(StockDiary, pk=diary_id, user=request.user)
    diary.is_excluded = not diary.is_excluded
    diary.save(update_fields=['is_excluded'])
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'stockdiary/partials/_exclude_toggle_button.html', {'diary': diary})
    return redirect('stockdiary:detail', pk=diary_id)


