# stockdiary/views_timeline.py
"""
全銘柄横断の思考タイムライン

継続記録・取引・日記作成・振り返りを日付降順の1本の時系列で表示する。
「あの時期の自分は何を考えていたか」を見返す想起の中核画面
（docs/improvement_plan.md 論点6）。

リクエスト時のユーザー単位クエリのみで構成し、常駐バッチを持たない。
各ソースの取得件数に上限を設けてメモリを抑える。
"""
from datetime import timedelta
from itertools import groupby

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Q
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import TemplateView

from .models import StockDiary, DiaryNote, Transaction
from .utils import extract_lead
from tags.models import Tag

PAGE_SIZE = 50
# 1ソースあたりの最大取得件数（期間フィルターと併用するメモリ上限）
SOURCE_CAP = 500

PERIOD_DAYS = {'1m': 30, '3m': 90, '1y': 365}

# イベント種別ごとの表示メタ（マーカー色・アイコン・ラベル）。
# ライト/ダーク双方で視認できる固定カラーを用いる。
KIND_UI = {
    'note': {'color': '#0891b2', 'icon': 'bi-journal-text', 'label': '継続記録'},
    'retrospective': {'color': '#7c3aed', 'icon': 'bi-arrow-counterclockwise', 'label': '振り返り'},
    'buy': {'color': '#10b981', 'icon': 'bi-arrow-down-circle-fill', 'label': '買'},
    'sell': {'color': '#ef4444', 'icon': 'bi-arrow-up-circle-fill', 'label': '売'},
    'diary': {'color': '#d97706', 'icon': 'bi-journal-plus', 'label': '日記作成'},
}


def _rel_label(d, today):
    """日付の相対表記（今日・昨日・N日前・N週間前・Nヶ月前・N年前）"""
    days = (today - d).days
    if days <= 0:
        return '今日'
    if days == 1:
        return '昨日'
    if days < 7:
        return f'{days}日前'
    if days < 30:
        return f'{days // 7}週間前'
    if days < 365:
        return f'{days // 30}ヶ月前'
    return f'{days // 365}年前'


def _fmt_time(dt):
    """イベントのタイムスタンプを H:i 表記に整形（無ければ空文字）"""
    if not dt:
        return ''
    return timezone.localtime(dt).strftime('%H:%M')

TYPE_CHOICES = [
    ('all', 'すべて'),
    ('note', '継続記録'),
    ('retrospective', '振り返り'),
    ('transaction', '取引'),
    ('diary', '日記作成'),
]

PERIOD_CHOICES = [
    ('1m', '1ヶ月'),
    ('3m', '3ヶ月'),
    ('1y', '1年'),
    ('all', 'すべて'),
]


class TimelineView(LoginRequiredMixin, TemplateView):
    template_name = 'stockdiary/timeline.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        period = self.request.GET.get('period', '3m')
        if period not in dict(PERIOD_CHOICES):
            period = '3m'
        etype = self.request.GET.get('type', 'all')
        if etype not in dict(TYPE_CHOICES):
            etype = 'all'
        try:
            tag_id = int(self.request.GET.get('tag', '') or 0) or None
        except (ValueError, TypeError):
            tag_id = None
        # キーワード検索（銘柄名・本文・トピック・取引メモを横断。TL1）
        q = self.request.GET.get('q', '').strip()[:100]

        since = None
        if period in PERIOD_DAYS:
            since = timezone.now().date() - timedelta(days=PERIOD_DAYS[period])

        events = self._collect_events(user, since, etype, tag_id, q)

        # 結果メタ用（フィルター後の全件・日数）
        total_count = len(events)
        total_days = len({e['date'] for e in events})

        paginator = Paginator(events, PAGE_SIZE)
        page_obj = paginator.get_page(self.request.GET.get('page', 1))

        # ページ内イベントを日付でグループ化（降順整列済み）
        today = timezone.localdate()
        event_groups = []
        for date, items in groupby(page_obj.object_list, key=lambda e: e['date']):
            items = list(items)
            event_groups.append({
                'date': date,
                'count': len(items),
                'rel': _rel_label(date, today),
                'events': items,
            })

        period_label = dict(PERIOD_CHOICES).get(period, '')

        context.update({
            'page_obj': page_obj,
            'events': page_obj.object_list,
            'event_groups': event_groups,
            'total_count': total_count,
            'total_days': total_days,
            'period': period,
            'period_label': period_label,
            'etype': etype,
            'tag_id': tag_id,
            'q': q,
            'period_choices': PERIOD_CHOICES,
            'type_choices': TYPE_CHOICES,
            'tags': Tag.objects.filter(user=user).order_by('name'),
            # 振り返り中にそのまま記録できるよう、他の主要ページと同じスピードダイアルを置く
            'page_actions': [
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
            ],
        })
        return context

    @staticmethod
    def _collect_events(user, since, etype, tag_id, q=''):
        """3ソース（継続記録・取引・日記作成）を統合して日付降順で返す"""
        events = []

        # 継続記録（振り返り含む）
        if etype in ('all', 'note', 'retrospective'):
            notes = DiaryNote.objects.filter(
                diary__user=user, diary__is_excluded=False,
            ).select_related('diary')
            if etype == 'retrospective':
                notes = notes.filter(note_type='retrospective')
            elif etype == 'note':
                notes = notes.exclude(note_type='retrospective')
            if since:
                notes = notes.filter(date__gte=since)
            if tag_id:
                notes = notes.filter(diary__tags__id=tag_id)
            if q:
                notes = notes.filter(
                    Q(content__icontains=q) | Q(topic__icontains=q)
                    | Q(diary__stock_name__icontains=q)
                )
            for n in notes.order_by('-date', '-created_at')[:SOURCE_CAP]:
                kind = 'retrospective' if n.note_type == 'retrospective' else 'note'
                events.append({
                    'kind': kind,
                    'date': n.date,
                    'time': _fmt_time(n.created_at),
                    'sort_key': (n.date, n.created_at),
                    'diary': n.diary,
                    'note': n,
                    'lead': extract_lead(n.content or '', max_len=120),
                    'ui': KIND_UI[kind],
                })

        # 取引
        if etype in ('all', 'transaction'):
            txs = Transaction.objects.filter(
                diary__user=user, diary__is_excluded=False,
            ).select_related('diary')
            if since:
                txs = txs.filter(transaction_date__gte=since)
            if tag_id:
                txs = txs.filter(diary__tags__id=tag_id)
            if q:
                txs = txs.filter(
                    Q(memo__icontains=q) | Q(diary__stock_name__icontains=q)
                )
            for t in txs.order_by('-transaction_date', '-created_at')[:SOURCE_CAP]:
                side = 'buy' if t.transaction_type == 'buy' else 'sell'
                events.append({
                    'kind': 'transaction',
                    'date': t.transaction_date,
                    'time': _fmt_time(t.created_at),
                    'sort_key': (t.transaction_date, t.created_at),
                    'diary': t.diary,
                    'transaction': t,
                    'ui': KIND_UI[side],
                })

        # 日記作成
        if etype in ('all', 'diary'):
            diaries = StockDiary.objects.filter(user=user, is_excluded=False)
            if since:
                diaries = diaries.filter(created_at__date__gte=since)
            if tag_id:
                diaries = diaries.filter(tags__id=tag_id)
            if q:
                diaries = diaries.filter(
                    Q(reason__icontains=q) | Q(stock_name__icontains=q)
                )
            for d in diaries.order_by('-created_at')[:SOURCE_CAP]:
                events.append({
                    'kind': 'diary',
                    'date': d.created_at.date(),
                    'time': _fmt_time(d.created_at),
                    'sort_key': (d.created_at.date(), d.created_at),
                    'diary': d,
                    'lead': extract_lead(d.reason or '', max_len=120),
                    'ui': KIND_UI['diary'],
                })

        events.sort(key=lambda e: e['sort_key'], reverse=True)
        return events
