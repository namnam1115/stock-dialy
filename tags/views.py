# tags/views.py
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, DetailView
from django.urls import reverse_lazy
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404, render, redirect
from django.http import HttpResponseBadRequest
from django.db.models import Count, Q
from .models import Tag
from django import forms
from datetime import datetime

from stockdiary.tag_axis_config import AXIS_COLORS, AXIS_LABELS

from subscriptions.mixins import SubscriptionLimitCheckMixin

# 軸（グループ）の表示順・短縮ラベル・アイコン。色とフルラベルは tag_axis_config を流用。
AXIS_ORDER = ['theme', 'macro', 'business_model', 'capital_policy', 'risk', 'event', 'custom']
AXIS_ICONS = {
    'theme':          'bi-lightning-charge-fill',
    'macro':          'bi-globe2',
    'business_model': 'bi-gear-fill',
    'capital_policy': 'bi-cash-stack',
    'risk':           'bi-exclamation-triangle-fill',
    'event':          'bi-calendar-event-fill',
    'custom':         'bi-tag-fill',
}
AXIS_SHORT = {
    'theme':          'テーマ',
    'macro':          'マクロ',
    'business_model': 'BM',
    'capital_policy': '資本政策',
    'risk':           'リスク',
    'event':          'イベント',
    'custom':         'ラベル',
}
# 軸ピッカー（タグ作成/編集フォーム）用の補足説明
AXIS_DESC = {
    'theme':          'AI・半導体・脱炭素など',
    'macro':          '金利・為替・景気サイクル',
    'business_model': '稼ぎ方・成長ロジック',
    'capital_policy': '配当・株主還元・ROE',
    'risk':           '地政学・規制・需給変化',
    'event':          '決算・一過性イベント',
    'custom':         '監視中・保有候補など個人管理用',
}

# タグ方向トグルの選択肢（バッジ表示用ラベル）
DIRECTION_TOGGLE_CHOICES = [('up', '▲+'), ('down', '▼−'), ('neutral', '→中立')]


def get_axis_meta():
    """軸ピッカー・チップ描画に使う軸メタ情報を表示順で返す。"""
    return [
        {
            'key':   key,
            'label': AXIS_LABELS.get(key, key),
            'short': AXIS_SHORT.get(key, key),
            'color': AXIS_COLORS.get(key, '#6b7280'),
            'icon':  AXIS_ICONS.get(key, 'bi-tag-fill'),
            'desc':  AXIS_DESC.get(key, ''),
        }
        for key in AXIS_ORDER
    ]


class TagForm(forms.ModelForm):
    class Meta:
        model = Tag
        fields = ['name', 'axis', 'parent']
        widgets = {
            'name':   forms.TextInput(attrs={
                'class': 'tf-input', 'placeholder': '例: AI、金利低下、高配当…',
                'autocomplete': 'off', 'maxlength': 50,
            }),
            'axis':   forms.Select(attrs={'class': 'form-select'}),
            'parent': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['parent'].required = False
        self.fields['parent'].empty_label = '（なし）'
        if user is not None:
            # 親タグにできるのは「親を持たない（＝ルート）タグ」のみ。
            # 3階層以上のネストを防ぎ、常に2階層（親→子）に保つ。
            qs = Tag.objects.filter(user=user, parent__isnull=True)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            self.fields['parent'].queryset = qs.order_by('axis', 'name')
        else:
            self.fields['parent'].queryset = Tag.objects.none()

    def clean(self):
        cleaned_data = super().clean()
        parent = cleaned_data.get('parent')
        axis = cleaned_data.get('axis')
        if parent:
            if self.instance.pk and self.instance.children.exists():
                raise forms.ValidationError('子タグを持つタグを、別のタグの子タグにはできません。')
            if axis and parent.axis != axis:
                raise forms.ValidationError('親タグと同じ分析グループ（軸）を選択してください。')
        return cleaned_data

class TagListView(LoginRequiredMixin, ListView):
    model = Tag
    template_name = 'tags/tag_list.html'
    context_object_name = 'tags'
    
    def get_queryset(self):
        # 各タグの記録数（紐づく日記件数）を注釈。Meta.ordering により axis, name 順。
        return Tag.objects.filter(user=self.request.user).select_related('parent').annotate(
            record_count=Count('stockdiary', distinct=True)
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        tags = list(context['tags'])

        # 各タグに「背景ありのユニーク銘柄数（背景台帳の件数）」を付与
        tag_book_counts = {}
        for tag in tags:
            diaries = tag.stockdiary_set.filter(
                reason__isnull=False
            ).exclude(reason='').values('stock_symbol', 'stock_name')
            book_count = len({(d['stock_symbol'], d['stock_name']) for d in diaries})
            tag.book_count = book_count
            tag_book_counts[tag.id] = book_count
        context['tag_book_counts'] = tag_book_counts

        unused_tags = [t for t in tags if (t.record_count or 0) == 0]
        for t in unused_tags:
            t.axis_color = AXIS_COLORS.get(t.axis, '#6b7280')
            t.axis_short = AXIS_SHORT.get(t.axis, t.axis)

        # 統計（4カード分 + 未使用）
        context['tag_stats'] = {
            'total':         len(tags),
            'used':          sum(1 for t in tags if (t.record_count or 0) > 0),
            'total_records': sum((t.record_count or 0) for t in tags),
            'axes_in_use':   len({t.axis for t in tags}),
            'unused':        len(unused_tags),
        }
        context['unused_tags'] = unused_tags

        # 軸（グループ）ごとにまとめる。親タグの直後に子タグを並べて階層を表現する。
        axis_groups = []
        for key in AXIS_ORDER:
            group_tags = [t for t in tags if t.axis == key]
            if not group_tags:
                continue
            roots = [t for t in group_tags if t.parent_id is None]
            children_by_parent = {}
            for t in group_tags:
                if t.parent_id is not None:
                    children_by_parent.setdefault(t.parent_id, []).append(t)

            ordered_tags = []
            for root in roots:
                root.child_tags = children_by_parent.get(root.id, [])
                ordered_tags.append(root)
                ordered_tags.extend(root.child_tags)

            axis_groups.append({
                'key':   key,
                'label': AXIS_LABELS.get(key, key),
                'short': AXIS_SHORT.get(key, key),
                'color': AXIS_COLORS.get(key, '#6b7280'),
                'icon':  AXIS_ICONS.get(key, 'bi-tag-fill'),
                'count': len(group_tags),
                'tags':  ordered_tags,
            })
        context['axis_groups'] = axis_groups

        # スピードダイアルのアクションを定義
        analytics_actions = [
            {
                'type': 'back',
                'url': reverse_lazy('stockdiary:home'),
                'icon': 'bi-arrow-left',
                'label': '戻る'
            },
            {
                'type': 'add',
                'url': reverse_lazy('tags:create'),
                'icon': 'bi-plus-lg',
                'label': '新規作成'
            }
        ]
        context['page_actions'] = analytics_actions
        return context

class TagDetailView(LoginRequiredMixin, DetailView):
    model = Tag
    template_name = 'tags/tag_detail.html'
    context_object_name = 'tag'
    
    def get_queryset(self):
        return Tag.objects.filter(user=self.request.user)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tag = self.object
        
        # このタグが設定されている日記を取得
        diaries = tag.stockdiary_set.select_related('user').prefetch_related('tags').order_by('-created_at')
        
        # 銘柄ごとにグループ化
        stock_groups = {}
        for diary in diaries:
            symbol = diary.stock_symbol or 'unknown'
            if symbol not in stock_groups:
                stock_groups[symbol] = {
                    'symbol': diary.stock_symbol,
                    'name': diary.stock_name,
                    'sector': diary.sector,
                    'diaries': [],
                    'total_entries': 0,
                    'active_holdings': 0,
                    'completed_sales': 0,
                    'memo_entries': 0,
                    'latest_date': None,
                    'earliest_date': None
                }
            
            stock_groups[symbol]['diaries'].append(diary)
            stock_groups[symbol]['total_entries'] += 1

            if diary.is_holding:
                stock_groups[symbol]['active_holdings'] += 1
            elif diary.is_sold_out:
                stock_groups[symbol]['completed_sales'] += 1
            elif diary.is_memo:
                stock_groups[symbol]['memo_entries'] += 1

            # latest_date と earliest_date の更新
            if stock_groups[symbol]['latest_date'] is None or diary.created_at > stock_groups[symbol]['latest_date']:
                stock_groups[symbol]['latest_date'] = diary.created_at

            if stock_groups[symbol]['earliest_date'] is None or diary.created_at < stock_groups[symbol]['earliest_date']:
                stock_groups[symbol]['earliest_date'] = diary.created_at
                
        # 銘柄リストをソート（最新日付順）
        stock_list = list(stock_groups.values())

        # latest_date と earliest_date が None の場合に適切な値を設定
        stock_list.sort(key=lambda x: x['latest_date'] or datetime.min, reverse=True)

        # 各銘柄グループに方向（DiaryTagDirection）を付与し、プラス/マイナス件数を集計
        from stockdiary.models import DiaryTagDirection
        dir_by_diary = dict(
            DiaryTagDirection.objects.filter(tag=tag, diary__in=diaries)
            .values_list('diary_id', 'direction')
        )
        plus_count = minus_count = neutral_count = 0
        for stock in stock_list:
            rep = next((d for d in stock['diaries'] if d.is_holding), stock['diaries'][0])
            direction = dir_by_diary.get(rep.id, 'neutral')
            stock['direction'] = direction
            stock['rep_diary_id'] = rep.id
            if direction == 'up':
                plus_count += 1
            elif direction == 'down':
                minus_count += 1
            else:
                neutral_count += 1

            # 銘柄カード用の代表ステータスと実現損益（売却済みの集計）
            if stock['active_holdings'] > 0:
                stock['status'] = 'holding'
            elif stock['completed_sales'] > 0:
                stock['status'] = 'sold'
            else:
                stock['status'] = 'memo'
            group_profit = sum(
                float(d.cash_only_realized_profit or 0)
                for d in stock['diaries'] if d.is_sold_out
            )
            stock['profit'] = group_profit if stock['completed_sales'] > 0 else None
            stock['records'] = stock['total_entries']

        # パフォーマンス統計（売却済み銘柄ベース）
        sold_profits = [
            float(d.cash_only_realized_profit or 0)
            for d in diaries
            if d.is_sold_out and d.cash_only_realized_profit is not None
        ]
        sold_count = len(sold_profits)
        winning_count = sum(1 for p in sold_profits if p > 0)
        total_profit = sum(sold_profits)
        win_rate = round(winning_count / sold_count * 100, 1) if sold_count > 0 else None

        # 統計情報
        stats = {
            'total_diaries': diaries.count(),
            'unique_stocks': len(stock_groups),
            'active_holdings': sum(stock['active_holdings'] for stock in stock_list),
            'completed_sales': sum(stock['completed_sales'] for stock in stock_list),
            'memo_entries': sum(stock['memo_entries'] for stock in stock_list),
            'total_profit': total_profit,
            'win_rate': win_rate,
            'winning_count': winning_count,
            'sold_count': sold_count,
            'plus_count': plus_count,
            'minus_count': minus_count,
            'neutral_count': neutral_count,
        }

        # プラス影響とマイナス影響が混在＝逆相関（ヘッジ）の候補
        has_hedge = plus_count > 0 and minus_count > 0

        # 保有状況フィルター
        status_filter = self.request.GET.get('status', 'all')
        if status_filter == 'active':
            stock_list = [stock for stock in stock_list if stock['active_holdings'] > 0]
        elif status_filter == 'sold':
            stock_list = [stock for stock in stock_list if stock['completed_sales'] > 0]
        elif status_filter == 'memo':
            stock_list = [stock for stock in stock_list if stock['memo_entries'] > 0]

        # 方向フィルター（追い風/向かい風/中立）
        direction_filter = self.request.GET.get('direction', 'all')
        if direction_filter in ('up', 'down', 'neutral'):
            stock_list = [stock for stock in stock_list if stock.get('direction') == direction_filter]

        # 検索フィルター
        search_query = self.request.GET.get('q', '').strip()
        if search_query:
            stock_list = [
                stock for stock in stock_list
                if (stock['name'] and search_query.lower() in stock['name'].lower()) or
                (stock['symbol'] and search_query.lower() in stock['symbol'].lower()) or
                (stock['sector'] and search_query.lower() in stock['sector'].lower())
            ]

        context.update({
            'stock_list': stock_list,
            'stats': stats,
            'status_filter': status_filter,
            'direction_filter': direction_filter,
            'has_hedge': has_hedge,
            'search_query': search_query,
            'dir_choices': DIRECTION_TOGGLE_CHOICES,
            'axis_color': AXIS_COLORS.get(tag.axis, '#7c3aed'),
            'axis_label': AXIS_LABELS.get(tag.axis, AXIS_SHORT.get(tag.axis, 'タグ')),
            'axis_icon': AXIS_ICONS.get(tag.axis, 'bi-tag-fill'),
        })
        
        # スピードダイアル用のアクション
        context['page_actions'] = [
            {
                'type': 'back',
                'url': reverse_lazy('tags:list'),
                'icon': 'bi-arrow-left',
                'label': '戻る'
            },
            {
                'type': 'edit',
                'url': reverse_lazy('tags:update', kwargs={'pk': tag.pk}),
                'icon': 'bi-pencil',
                'label': 'タグ編集'
            },
            {
                'type': 'add',
                'url': reverse_lazy('stockdiary:create'),
                'icon': 'bi-plus-lg',
                'label': '新規作成'
            }
        ]
        
        return context

def _parent_candidates(user, exclude_pk=None):
    """親タグ候補（ルートタグのみ）を JS の軸別ピッカー用に返す。

    テンプレート側で {{ ...|json_script }} を使い、タグ名に含まれうる
    HTML特殊文字（例: </script>）が安全にエスケープされた状態で埋め込む。
    """
    qs = Tag.objects.filter(user=user, parent__isnull=True)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    qs = qs.annotate(record_count=Count('stockdiary', distinct=True)).order_by('axis', 'name')
    return [
        {'id': t.id, 'name': t.name, 'axis': t.axis, 'count': t.record_count}
        for t in qs
    ]


class TagCreateView(SubscriptionLimitCheckMixin, LoginRequiredMixin, CreateView):
    model = Tag
    form_class = TagForm
    template_name = 'tags/tag_form.html'
    success_url = reverse_lazy('tags:list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.user = self.request.user
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context['axis_meta'] = get_axis_meta()
        context['existing_tags'] = _parent_candidates(user)
        # スピードダイアルのアクションを定義
        analytics_actions = [
            {
                'type': 'back',
                'url': reverse_lazy('tags:list'),
                'icon': 'bi-arrow-left',
                'label': '戻る'
            }
        ]
        context['page_actions'] = analytics_actions
        return context

class TagUpdateView(LoginRequiredMixin, UpdateView):
    model = Tag
    form_class = TagForm
    template_name = 'tags/tag_form.html'
    success_url = reverse_lazy('tags:list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_queryset(self):
        return Tag.objects.filter(user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context['axis_meta'] = get_axis_meta()
        context['existing_tags'] = _parent_candidates(user, exclude_pk=self.object.pk)
        context['can_be_child'] = not self.object.children.exists()
        # このタグ自身がルート（親を持たない）なら「既存タグをまとめて子タグにする」を出せる
        context['is_root'] = self.object.parent_id is None
        if context['is_root']:
            context['assignable_children'] = list(
                _child_candidates_qs(user, self.object)
                .order_by('name')
                .values('id', 'name', 'record_count', 'parent_id')
            )
        else:
            context['assignable_children'] = []
        # スピードダイアルのアクションを定義
        analytics_actions = [
            {
                'type': 'back',
                'url': reverse_lazy('tags:list'),
                'icon': 'bi-arrow-left',
                'label': '戻る'
            }
        ]
        context['page_actions'] = analytics_actions
        return context

class TagDeleteView(LoginRequiredMixin, DeleteView):
    model = Tag
    template_name = 'tags/tag_confirm_delete.html'
    success_url = reverse_lazy('tags:list')

    def get_queryset(self):
        return Tag.objects.filter(user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        # スピードダイアルのアクションを定義
        analytics_actions = [
            {
                'type': 'back',
                'url': reverse_lazy('tags:list'),
                'icon': 'bi-arrow-left',
                'label': '戻る'
            }
        ]
        context['page_actions'] = analytics_actions
        return context


class TagBookView(LoginRequiredMixin, DetailView):
    """
    このタグで残した背景（reason）を、考えの変化（継続記録）まで通読する読み物ビュー。
    """
    model = Tag
    template_name = 'tags/tag_book.html'
    context_object_name = 'tag'

    def get_queryset(self):
        return Tag.objects.filter(user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from stockdiary.views import get_mention_map
        tag = self.object

        # 並び順（既定: 新しい順 / ?order=asc で古い順）
        order = 'asc' if self.request.GET.get('order') == 'asc' else 'desc'
        if order == 'asc':
            date_order, sec_order = 'last_transaction_date', 'created_at'
        else:
            date_order, sec_order = '-last_transaction_date', '-created_at'

        # 背景(reason)がある日記のみ。継続記録（notes）も prefetch して通読できるようにする
        diaries = tag.stockdiary_set.filter(
            reason__isnull=False
        ).exclude(
            reason=''
        ).select_related('user').prefetch_related('notes').order_by(date_order, sec_order)

        # 銘柄ごとに代表1件（重複排除）
        seen_stocks = set()
        entries = []
        sold_profits = []
        for diary in diaries:
            stock_key = f"{diary.stock_symbol}_{diary.stock_name}"
            if stock_key in seen_stocks:
                continue
            seen_stocks.add(stock_key)

            if diary.is_holding:
                status = 'holding'
            elif diary.is_sold_out:
                status = 'sold'
            else:
                status = 'memo'

            profit = float(diary.cash_only_realized_profit or 0) if status == 'sold' else None
            if status == 'sold':
                sold_profits.append(float(diary.cash_only_realized_profit or 0))

            # 継続記録は時系列（古い→新しい）で「考えの変化」を追えるように
            notes = sorted(diary.notes.all(), key=lambda n: (n.date, n.created_at))

            entries.append({
                'diary': diary,
                'status': status,
                'profit': profit,
                'notes': notes,
            })

        context.update({
            'entries': entries,
            'order': order,
            'reason_count': len(entries),
            'mention_map': get_mention_map(self.request.user),
            # 軽量サマリー（読み物の補助。後方互換のため ledger_stats も維持）
            'ledger_stats': {
                'total_entries': len(entries),
                'sold_count': len(sold_profits),
                'winning_count': sum(1 for p in sold_profits if p > 0),
                'total_profit': sum(sold_profits),
            },
        })

        context['page_actions'] = [
            {
                'type': 'back',
                'url': reverse_lazy('tags:detail', kwargs={'pk': tag.pk}),
                'icon': 'bi-arrow-left',
                'label': '戻る'
            }
        ]

        return context


@login_required
@require_POST
def set_tag_direction(request, pk):
    """日記×タグの方向（プラス/マイナス/中立）を設定する HTMX エンドポイント。

    同一銘柄・同一タグの全日記に同じ方向を反映し、関連グラフのエッジ着色を
    銘柄単位で一貫させる。更新後の方向コントロール部分テンプレートを返す。
    """
    from stockdiary.models import StockDiary, DiaryTagDirection

    tag = get_object_or_404(Tag, pk=pk, user=request.user)
    direction = request.POST.get('direction', '')
    diary_id = request.POST.get('diary_id')

    valid_directions = {
        DiaryTagDirection.DIRECTION_UP,
        DiaryTagDirection.DIRECTION_DOWN,
        DiaryTagDirection.DIRECTION_NEUTRAL,
    }
    if direction not in valid_directions or not diary_id:
        return HttpResponseBadRequest('invalid direction or diary_id')

    diary = get_object_or_404(StockDiary, pk=diary_id, user=request.user)

    targets = StockDiary.objects.filter(user=request.user, tags=tag)
    if diary.stock_symbol:
        targets = targets.filter(stock_symbol=diary.stock_symbol)
    else:
        targets = targets.filter(pk=diary.pk)

    for d in targets:
        DiaryTagDirection.objects.update_or_create(
            diary=d, tag=tag, defaults={'direction': direction},
        )

    return render(request, 'tags/partials/_direction_control.html', {
        'tag_pk': tag.pk,
        'diary_id': diary.pk,
        'direction': direction,
        'dir_choices': DIRECTION_TOGGLE_CHOICES,
    })


def _child_candidates_qs(user, parent):
    """親候補(parent)の子タグにできる/なっているタグの queryset。

    「同じ軸のルートタグで、自身は子を持たない」もの（2階層制限。TagForm.clean()
    と同じ制約）に加え、既に parent の子であるタグも含める。一覧に現在の子タグと
    追加候補を同居させ、チェックの有無だけで追加/解除を一括指定できるようにするため。
    """
    return (
        Tag.objects.filter(user=user, axis=parent.axis)
        .filter(Q(parent__isnull=True) | Q(parent=parent))
        .exclude(pk=parent.pk)
        .annotate(child_count=Count('children', distinct=True), record_count=Count('stockdiary', distinct=True))
        .filter(child_count=0)
    )


@login_required
@require_POST
def bulk_assign_children(request, pk):
    """既存タグ(pk)を親として、選択した複数タグをまとめて子タグにする/解除する。

    子タグ側の編集フォームで親を1件ずつ選ぶ方式だと、既存タグを複数まとめて
    同じ親にぶら下げたい・外したいときに手間がかかる（相談を受けて追加）。
    候補一覧には現在の子タグも表示されるため、チェックを外して送信すれば解除できる
    （表示された候補のうち、送信時にチェックが外れていた既存の子だけを解除対象とする）。
    """
    parent = get_object_or_404(Tag, pk=pk, user=request.user, parent__isnull=True)
    checked_ids = set(request.POST.getlist('child_ids'))

    candidates = list(_child_candidates_qs(request.user, parent))
    to_assign_ids = [t.pk for t in candidates if str(t.pk) in checked_ids and t.parent_id != parent.pk]
    to_detach_ids = [t.pk for t in candidates if str(t.pk) not in checked_ids and t.parent_id == parent.pk]

    assigned = Tag.objects.filter(pk__in=to_assign_ids).update(parent=parent) if to_assign_ids else 0
    detached = Tag.objects.filter(pk__in=to_detach_ids, parent=parent).update(parent=None) if to_detach_ids else 0

    parts = []
    if assigned:
        parts.append(f'{assigned}件を子タグに追加')
    if detached:
        parts.append(f'{detached}件を子タグから解除')
    if parts:
        messages.success(request, '、'.join(parts) + f'しました（「{parent.name}」）。')
    else:
        messages.info(request, '変更はありませんでした。')
    return redirect('tags:update', pk=parent.pk)


@login_required
@require_POST
def bulk_delete_unused_tags(request):
    """未使用タグ（どの日記にも紐づいていないタグ）をまとめて削除する。"""
    ids = request.POST.getlist('tag_ids')
    tags = Tag.objects.filter(user=request.user, pk__in=ids).annotate(
        record_count=Count('stockdiary', distinct=True)
    ).filter(record_count=0)
    count = tags.count()
    if count:
        tags.delete()
        messages.success(request, f'{count}件の未使用タグを削除しました。')
    else:
        messages.info(request, '削除できる未使用タグが選択されていません。')
    return redirect('tags:list')