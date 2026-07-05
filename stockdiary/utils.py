"""
stockdiary app utility functions
"""
import re
from typing import List, Set, Dict, Any, Tuple
from collections import defaultdict


def extract_hashtags(text: str) -> List[str]:
    """
    テキストからハッシュタグを抽出する（@記号を使用）

    Args:
        text: 検索対象のテキスト

    Returns:
        抽出されたハッシュタグのリスト（@を除く、重複なし）

    Examples:
        >>> extract_hashtags("投資理由 @成長株 @配当")
        ['成長株', '配当']
        >>> extract_hashtags("# 見出し\\n@成長株")
        ['成長株']

    ルール:
        - @の後に日本語、英数字、アンダースコアが続く場合にハッシュタグとして扱う
        - マークダウンと競合しない
    """
    if not text:
        return []

    # ハッシュタグのパターン: @の後に日本語、英数字、アンダースコア、& が続く
    # [\u3040-\u309F] - ひらがな
    # [\u30A0-\u30FF] - カタカナ
    # [\u4E00-\u9FFF] - 漢字
    # [a-zA-Z0-9_&] - 英数字・アンダースコア・&（「M&A」のような表記に対応）
    pattern = r'@([\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uFF66-\uFF9Fa-zA-Z0-9_&]+)'

    matches = re.findall(pattern, text)

    # 重複を除去して順序を保持
    seen = set()
    unique_tags = []
    for tag in matches:
        if tag not in seen:
            seen.add(tag)
            unique_tags.append(tag)

    return unique_tags


# @タグ直後の方向矢印 → DiaryTagDirection.direction
_TAG_DIRECTION_ARROWS = {'↑': 'up', '↓': 'down', '→': 'neutral'}


def extract_hashtags_with_direction(text: str):
    """@タグと、直後に付く方向矢印(↑/↓/→)を分離して抽出する。

    タグ名は無方向で返す（例: "@円安↑" → ("円安", "up")）。これにより
    関連付けの語彙（@円安）を割らずに、向きだけを別レイヤ（DiaryTagDirection）へ
    渡せる。矢印が無い場合は direction=None を返し、呼び出し側で既存の手動方向を
    温存できるようにする。

    Returns:
        (タグ名, 'up'|'down'|'neutral'|None) の重複なしリスト（出現順）
    """
    if not text:
        return []

    # extract_hashtags と同じ語彙クラス＋直後の任意の方向矢印を捕捉する
    pattern = (
        r'@([぀-ゟ゠-ヿ一-鿿ｦ-ﾟa-zA-Z0-9_&]+)'
        r'([↑↓→]?)'
    )
    results = []
    seen = set()
    for name, arrow in re.findall(pattern, text):
        if name in seen:
            continue
        seen.add(name)
        results.append((name, _TAG_DIRECTION_ARROWS.get(arrow)))
    return results


def is_japanese_stock(code: str) -> bool:
    """日本株コードか判定（例: 7203, 262A, 1234D など数字+任意1文字）"""
    if not code:
        return False
    return bool(re.fullmatch(r'\d{3,4}[A-Z]?', code, re.IGNORECASE))


def detect_currency(stock_symbol: str) -> str:
    """銘柄コードから通貨を判定する。

    日本株コード（数字3〜4桁+任意1文字）なら 'JPY'、
    それ以外（米国株ティッカー等）は 'USD' とみなす。

    Args:
        stock_symbol: 銘柄コード（例: '7203', 'AAPL'）

    Returns:
        'JPY' または 'USD'
    """
    return 'JPY' if is_japanese_stock(stock_symbol) else 'USD'


def get_all_hashtags_from_queryset(queryset) -> List[dict]:
    """
    QuerySetから全てのハッシュタグを抽出し、使用回数とともに返す

    Args:
        queryset: StockDiaryのQuerySet

    Returns:
        [{'tag': 'タグ名', 'count': 使用回数}, ...] の形式のリスト
        使用回数の降順でソート
    """
    from collections import Counter

    all_tags = []
    for diary in queryset:
        if diary.reason:
            tags = extract_hashtags(diary.reason)
            all_tags.extend(tags)

    # カウントして辞書形式に変換
    tag_counter = Counter(all_tags)

    result = [
        {'tag': tag, 'count': count}
        for tag, count in tag_counter.most_common()
    ]

    return result


def search_diaries_by_hashtag(queryset, hashtag: str):
    """
    ハッシュタグで日記を絞り込む

    Args:
        queryset: StockDiaryのQuerySet
        hashtag: 検索するハッシュタグ（@あり/なし両方対応）

    Returns:
        絞り込まれたQuerySet
    """
    from django.db.models import Q

    # @を除去
    tag = hashtag.lstrip('@').strip()

    if not tag:
        return queryset

    # 投資理由（reason）と継続記録（DiaryNote.content）の両方から @タグ を検索
    # 前後に空白や改行がある場合も考慮
    return queryset.filter(
        Q(reason__icontains=f'@{tag}') |
        Q(notes__content__icontains=f'@{tag}')
    ).distinct()


def find_duplicate_diaries(user, stock_symbol: str = '', stock_name: str = '', exclude_pk=None):
    """同一ユーザーの既存日記から重複候補を返す。

    日記はユーザー×銘柄で一意ではないため、新規作成時に既存日記への
    追記を促す目的で使う。銘柄コードがあればコード一致を優先し、
    コードが空の場合のみ銘柄名の完全一致（大文字小文字無視）で探す。

    Args:
        user: 対象ユーザー
        stock_symbol: 銘柄コード（空可）
        stock_name: 銘柄名（空可）
        exclude_pk: 除外する日記ID（編集時に自分自身を除く用途）

    Returns:
        StockDiary の QuerySet（更新日時降順）
    """
    from .models import StockDiary

    symbol = (stock_symbol or '').strip()
    name = (stock_name or '').strip()

    qs = StockDiary.objects.filter(user=user)
    if symbol:
        qs = qs.filter(stock_symbol__iexact=symbol)
    elif name:
        qs = qs.filter(stock_symbol='', stock_name__iexact=name)
    else:
        return StockDiary.objects.none()

    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.order_by('-updated_at')


def find_backlinks(diary, user, limit=10):
    """この日記の銘柄に言及している他の記録（バックリンク）を返す。

    対象:
    - 他日記の投資理由・メモ内の銘柄コード言及（(7203) / （7203） 形式）
    - 他日記の継続記録（DiaryNote.content）内の言及
    - 手動リンクの逆方向（linked_from）のうち双方向化されていないもの
      （関連日記APIは双方向登録するため、通常は古いデータの救済用）

    同一銘柄の日記（重複日記）は自己言及になるため対象外。

    Args:
        diary: フォーカスする StockDiary
        user: 対象ユーザー
        limit: 最大件数

    Returns:
        list[dict]: {'diary', 'source'('reason'|'note'|'link'), 'date', 'snippet'} の
                    日付降順リスト
    """
    import re as _re
    from django.db.models import Q
    from .models import StockDiary, DiaryNote

    symbol = (diary.stock_symbol or '').strip()
    items = []

    if symbol:
        mention_re = _re.compile(r'[（(]' + _re.escape(symbol) + r'[）)]')

        # 本文（投資理由・メモ）での言及
        candidates = (
            StockDiary.objects
            .filter(user=user)
            .exclude(pk=diary.pk)
            .exclude(stock_symbol__iexact=symbol)
            .filter(Q(reason__icontains=symbol))
            .only('id', 'stock_name', 'stock_symbol', 'reason', 'updated_at')
        )
        for d in candidates:
            text = f"{d.reason or ''}"
            m = mention_re.search(text)
            if not m:
                continue
            items.append({
                'diary': d,
                'source': 'reason',
                'date': d.updated_at.date(),
                'snippet': _make_search_snippet(text, m.group(0)),
            })

        # 継続記録での言及
        notes = (
            DiaryNote.objects
            .filter(diary__user=user, content__icontains=symbol)
            .exclude(diary=diary)
            .exclude(diary__stock_symbol__iexact=symbol)
            .select_related('diary')
            .order_by('-date')[:50]
        )
        for note in notes:
            m = mention_re.search(note.content or '')
            if not m:
                continue
            items.append({
                'diary': note.diary,
                'source': 'note',
                'date': note.date,
                'snippet': _make_search_snippet(note.content, m.group(0)),
            })

    # 手動リンクの逆方向（非対称データの救済）
    asymmetric = diary.linked_from.exclude(
        id__in=diary.linked_diaries.values_list('id', flat=True)
    ).only('id', 'stock_name', 'stock_symbol', 'updated_at')
    for d in asymmetric:
        items.append({
            'diary': d,
            'source': 'link',
            'date': d.updated_at.date(),
            'snippet': '',
        })

    items.sort(key=lambda x: x['date'], reverse=True)
    return items[:limit]


def split_search_terms(query: str) -> List[str]:
    """検索文字列を空白（半角・全角）で分割して語のリストを返す。"""
    return [t for t in re.split(r'[\s　]+', (query or '').strip()) if t]


def apply_diary_search(queryset, query: str):
    """日記の全文検索を適用する。

    銘柄名・銘柄コード・投資理由・メモ・業種に加え、
    継続記録（DiaryNote.content / topic）と「見立ての変遷」（ReasonVersion.content）も
    横断して検索する。
    空白区切りの複数語は AND 検索（全ての語をいずれかのフィールドに含む日記）。
    `@タグ` のみが入力された場合は search_diaries_by_hashtag に委譲する。

    Args:
        queryset: StockDiary の QuerySet
        query: 検索文字列

    Returns:
        絞り込まれた QuerySet（notes 結合による重複は distinct で排除）
    """
    from django.db.models import Q

    q = (query or '').strip()
    if not q:
        return queryset

    # 単独の @タグ はハッシュタグ検索（reason + 継続記録）に委譲
    if q.startswith('@') and ' ' not in q and '　' not in q:
        return search_diaries_by_hashtag(queryset, q)

    # 語ごとに .filter() を重ねることで AND 検索にする。
    # 1回の .filter() に Q をまとめると notes/transactions の同一行が
    # 全語を含む場合しかヒットしないため、語ごとに別 JOIN にする必要がある
    for term in split_search_terms(q):
        queryset = queryset.filter(
            Q(stock_name__icontains=term) |
            Q(stock_symbol__icontains=term) |
            Q(reason__icontains=term) |
            Q(sector__icontains=term) |
            Q(notes__content__icontains=term) |
            Q(notes__topic__icontains=term) |
            Q(reason_versions__content__icontains=term) |
            Q(transactions__memo__icontains=term)
        )
    return queryset.distinct()


def apply_diary_filters(queryset, params, user):
    """GET パラメータに基づいて日記 QuerySet にフィルター・ソートを適用する。

    StockDiaryListView.get_queryset() と diary_list FBV の両方で使われる共通ロジック。
    params は request.GET (QueryDict) を想定する。

    Args:
        queryset: StockDiary の QuerySet（user 絞り込み済みであること）
        params: request.GET など dict-like オブジェクト
        user: リクエストユーザー（transaction_date_range フィルターで参照）

    Returns:
        フィルター・ソート済みの QuerySet（distinct 適用済み）
    """
    from datetime import timedelta
    from django.db.models import Count, Q as _Q, F as _F, OuterRef, Subquery
    from django.utils import timezone as _tz
    from .models import Transaction as _Transaction
    from .utils import apply_diary_search, search_diaries_by_hashtag

    DATE_RANGE_DAYS = {'1w': 7, '1m': 30, '3m': 90, '6m': 180, '1y': 365}

    # 全文検索
    query = params.get('query', '').strip()
    if query:
        queryset = apply_diary_search(queryset, query)

    # ハッシュタグフィルター
    hashtag = params.get('hashtag', '').strip()
    if hashtag:
        queryset = search_diaries_by_hashtag(queryset, hashtag)

    # タグフィルター
    tag_id = params.get('tag', '')
    if tag_id:
        try:
            queryset = queryset.filter(tags__id=int(tag_id))
        except (ValueError, TypeError):
            pass

    # 業種フィルター
    sector = params.get('sector', '').strip()
    if sector:
        queryset = queryset.filter(sector__iexact=sector)

    # 保有状態フィルター
    status = params.get('status', 'all')
    if status == 'active':
        queryset = queryset.filter(current_quantity__gt=0)
    elif status == 'sold':
        queryset = queryset.filter(
            _Q(current_quantity=0) | _Q(current_quantity__lt=0),
            transaction_count__gt=0,
        )
    elif status == 'memo':
        queryset = queryset.filter(transaction_count=0)
    elif status == 'excluded':
        queryset = queryset.filter(is_excluded=True)
    if status != 'excluded':
        queryset = queryset.filter(is_excluded=False)

    # 日付範囲フィルター（first_purchase_date 基準）
    date_range = params.get('date_range', '')
    if date_range in DATE_RANGE_DAYS:
        start_date = _tz.now().date() - timedelta(days=DATE_RANGE_DAYS[date_range])
        queryset = queryset.filter(
            _Q(first_purchase_date__gte=start_date) |
            _Q(first_purchase_date__isnull=True, created_at__gte=start_date)
        )

    # 取引日基準フィルター
    transaction_date_range = params.get('transaction_date_range', '')
    if transaction_date_range in DATE_RANGE_DAYS:
        start_date = _tz.now().date() - timedelta(days=DATE_RANGE_DAYS[transaction_date_range])
        diary_ids = _Transaction.objects.filter(
            diary__user=user,
            transaction_date__gte=start_date,
        ).values_list('diary_id', flat=True).distinct()
        queryset = queryset.filter(id__in=diary_ids)

    # 開示書類更新フィルター
    disclosure = params.get('disclosure', '')
    if disclosure in ('new', 'recent'):
        days = 7 if disclosure == 'new' else 30
        cutoff = _tz.now().date() - timedelta(days=days)
        queryset = queryset.filter(latest_disclosure_date__gte=cutoff)

    # 未記録フィルター
    if params.get('no_notes', ''):
        queryset = queryset.annotate(note_count=Count('notes')).filter(note_count=0)

    # ソート
    sort = params.get('sort', '')
    # 決算予定フィルタ/ソートで使う「次回決算日」。日記に決算日は持たせず、
    # EarningsSchedule（証券コードがキーの決算予定マスタ・/calendar は4桁で返す）を
    # コードで相関サブクエリ参照して annotate する。要求時のみ付与する。
    earnings = params.get('earnings', '')
    earnings_days = {'7': 7, '14': 14, '30': 30}.get(earnings)
    if earnings_days is not None or sort == 'earnings_asc':
        from earnings_analysis.models import EarningsSchedule
        _today = _tz.now().date()
        _next_e = (
            EarningsSchedule.objects
            .filter(securities_code=OuterRef('stock_symbol'), earnings_date__gte=_today)
            .order_by('earnings_date')
            .values('earnings_date')[:1]
        )
        queryset = queryset.annotate(_next_earnings_date=Subquery(_next_e))
        if earnings_days is not None:
            queryset = queryset.filter(
                _next_earnings_date__range=(_today, _today + timedelta(days=earnings_days))
            )

    sort_map = {
        'name': ['stock_name'],
        'symbol': ['stock_symbol'],
        'date_asc': [_F('first_purchase_date').asc(nulls_last=True), 'created_at'],
        'date_desc': [_F('first_purchase_date').desc(nulls_last=True), '-created_at'],
        'profit_desc': ['-realized_profit'],
        'profit_asc': ['realized_profit'],
        'transaction_count_desc': ['-transaction_count', '-updated_at'],
        'transaction_count_asc': ['transaction_count', 'updated_at'],
        'total_cost_desc': ['-total_cost', '-updated_at'],
        'total_cost_asc': ['total_cost', 'updated_at'],
        # 決算が近い順（決算予定が無い銘柄は末尾）
        'earnings_asc': [_F('_next_earnings_date').asc(nulls_last=True), '-updated_at'],
    }

    # タイトル（銘柄名・コード）一致を優先する。全文検索は本文・ノート等も
    # 対象のため、既定ソートのままだと「本文だけ一致した最近更新の日記」が
    # 銘柄名一致より上に来てしまう。検索語があり、ユーザーが明示ソートを
    # 選んでいない（既定）ときのみ、タイトル一致を第1キーに前置する。
    from django.db.models import Case, When, Value, IntegerField
    title_priority = []
    if query and not sort and not (query.startswith('@') and ' ' not in query and '　' not in query):
        title_q = _Q()
        for term in split_search_terms(query):
            title_q &= (_Q(stock_name__icontains=term) | _Q(stock_symbol__icontains=term))
        if title_q:
            queryset = queryset.annotate(
                _title_match=Case(
                    When(title_q, then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                )
            )
            title_priority = ['-_title_match']

    queryset = queryset.order_by(*title_priority, *sort_map.get(sort, ['-updated_at']))

    return queryset.distinct()


def _make_search_snippet(text: str, query: str, radius: int = 40) -> str:
    """検索語の周辺を切り出した抜粋テキストを返す（ハイライトは呼び出し側で行う）。"""
    if not text or not query:
        return ''
    lower = text.lower()
    idx = lower.find(query.lower())
    if idx == -1:
        return text[:radius * 2].strip()
    start = max(0, idx - radius)
    end = min(len(text), idx + len(query) + radius)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = '…' + snippet
    if end < len(text):
        snippet = snippet + '…'
    return snippet


def annotate_search_matches(diaries, query: str):
    """表示中の日記リストに、検索語がどこにヒットしたかの情報を付与する。

    各 diary に以下の属性を設定する:
        - match_name:         銘柄名・銘柄コードに一致したか（bool）
        - match_body:         投資理由・メモ・業種に一致したか（bool）
        - match_note:         一致した継続記録（DiaryNote）または None
        - match_note_count:   一致した継続記録の件数（int）
        - match_note_snippet: 一致した継続記録本文の抜粋（str）

    queryset は prefetch_related('notes') 済みであることを前提とし、
    追加クエリを発行しない（表示中のページ分のみを対象に Python で判定）。

    Args:
        diaries: StockDiary の反復可能オブジェクト（ページの object_list 等）
        query: 検索文字列

    Returns:
        diaries（同じオブジェクトを属性付きで返す）
    """
    q = (query or '').strip()
    # @タグ検索は @ を除いた本体で照合する。複数語はいずれかの語のヒットを表示対象とする
    terms = [
        (t.lstrip('@').strip() or t).lower() if t.startswith('@') else t.lower()
        for t in split_search_terms(q)
    ]
    for diary in diaries:
        if not terms:
            diary.match_name = False
            diary.match_body = False
            diary.match_note = None
            diary.match_note_count = 0
            diary.match_note_snippet = ''
            continue

        diary.match_name = any(
            t in (diary.stock_name or '').lower()
            or t in (diary.stock_symbol or '').lower()
            for t in terms
        )
        diary.match_body = any(
            t in (diary.reason or '').lower()
            or t in (diary.sector or '').lower()
            for t in terms
        )

        matched_notes = [
            note for note in diary.notes.all()
            if any(
                t in (note.content or '').lower() or t in (note.topic or '').lower()
                for t in terms
            )
        ]
        diary.match_note = matched_notes[0] if matched_notes else None
        diary.match_note_count = len(matched_notes)
        if matched_notes:
            content = matched_notes[0].content or ''
            hit_term = next((t for t in terms if t in content.lower()), terms[0])
            diary.match_note_snippet = _make_search_snippet(content, hit_term)
        else:
            diary.match_note_snippet = ''

    return diaries


def hub_weight(diary_count: int) -> float:  # noqa: keep for sector/hashtag modes
    """ハブの次数(diary_count)から逆頻度の重みを算出する。

    少数の銘柄しか結ばないハブ（希少な関連）ほど強く、
    全銘柄に付くようなハブ（ノイズ）ほど 0 に近づく。

        N=2  -> 1.0   （2銘柄だけを結ぶ＝最も強い関連）
        N=3  -> 0.5
        N=6  -> 0.2
        N=11 -> 0.1
    """
    return round(1.0 / max(diary_count - 1, 1), 4)


def get_tag_graph_data(diaries_qs) -> Dict[str, Any]:
    """
    タグハブノードと diary→tag エッジを生成する。

    各エッジ・ハブには逆頻度の `weight`（希少な関連ほど大）と、
    他銘柄に繋がらないハブを示す `is_isolated` を付与する。

    グラフは「銘柄↔タグ」のフラット接続に統一する。タグ間の親子（Tag.parent）は
    タグ一覧の階層表示や tag_master の語彙整理には使うが、関連図の"接続"としては
    描かない（ハブ↔ハブの階層エッジは接続を紛らわしくし振り返りの地図を読みにくく
    するため。R7 の実運用摩擦で撤去）。

    Args:
        diaries_qs: prefetch_related('tags') 済みの StockDiary QuerySet

    Returns:
        {
            'tag_nodes': [{'id': 'tag_<pk>', 'node_type': 'tag', 'tag_name': str,
                           'tag_pk': int, 'diary_count': int, 'weight': float, 'is_isolated': bool}],
            'edges':     [{'source': <diary_id>, 'target': 'tag_<pk>', 'edge_type': 'tag', 'weight': float}]
        }
    """
    tag_diary_count: Dict[int, int] = defaultdict(int)
    tag_meta: Dict[int, dict] = {}
    edges: List[dict] = []

    for diary in diaries_qs:
        for tag in diary.tags.all():
            tag_diary_count[tag.pk] += 1
            if tag.pk not in tag_meta:
                tag_meta[tag.pk] = {
                    'name': tag.name,
                    'axis': getattr(tag, 'axis', 'theme'),
                }
            edges.append({
                'source': diary.pk,
                'target': f'tag_{tag.pk}',
                'edge_type': 'tag',
                'axis': getattr(tag, 'axis', 'theme'),
            })

    # 方向属性（DiaryTagDirection）を一括取得してエッジに付与
    # ── マクロ/テーマのハブから「追い風(up)/向かい風(down)」が放射状に分かれて見えるようにする
    from .models import DiaryTagDirection
    dir_map: Dict[tuple, str] = {}
    diary_ids = {e['source'] for e in edges}
    if diary_ids and tag_meta:
        for diary_id, tag_id, direction in DiaryTagDirection.objects.filter(
            diary_id__in=diary_ids, tag_id__in=tag_meta.keys()
        ).values_list('diary_id', 'tag_id', 'direction'):
            dir_map[(diary_id, tag_id)] = direction

    # 全カウント確定後に逆頻度の重みを付与
    for e in edges:
        pk = int(e['target'].split('_', 1)[1])
        e['weight'] = hub_weight(tag_diary_count[pk])
        e['direction'] = dir_map.get((e['source'], pk), 'neutral')

    tag_nodes = [
        {
            'id': f'tag_{pk}',
            'node_type': 'tag',
            'tag_name': meta['name'],
            'tag_pk': pk,
            'axis': meta.get('axis', 'theme'),
            'diary_count': tag_diary_count[pk],
            'weight': hub_weight(tag_diary_count[pk]),
            'is_isolated': tag_diary_count[pk] <= 1,
        }
        for pk, meta in tag_meta.items()
    ]

    return {'tag_nodes': tag_nodes, 'edges': edges}


# explore（ドリルダウン探索）で近傍から外す軸。get_tag_graph_data と方針を揃える。
_EXPLORE_EXCLUDED_AXES = ('event', 'custom')


def _explore_tag_visible(tag, noise_max) -> bool:
    """explore で隣接タグを見せてよいか（event/custom 軸・df 上限超えを除外）。"""
    if getattr(tag, 'axis', 'theme') in _EXPLORE_EXCLUDED_AXES:
        return False
    return (getattr(tag, 'df', 0) or 0) <= noise_max


def _explore_tag_neighbor(tag, rel) -> dict:
    """explore グラフのタグ隣接1件を dict 化する。"""
    return {
        'id': f'tag:{tag.pk}', 'type': 'tag', 'label': tag.name,
        'axis': getattr(tag, 'axis', 'theme'), 'rel': rel,
    }


def get_graph_neighbors(user, node_id: str):
    """ドリルダウン探索グラフで、1ノードの近傍を返す（オンデマンド展開用・TG-DD）。

    node_id: 'tag:<pk>' または 'stock:<symbol>'。
    返り値: {'node': {...}, 'neighbors': [...]} ／ 見つからなければ None。

    - tag ノードの近傍 = 親タグ + 子タグ（`Tag.parent` 由来）+ そのタグの銘柄。
    - stock ノードの近傍 = その銘柄（user の同一 symbol 日記群）に付いた全タグ。
    - ノイズ除外は get_tag_graph_data と同方針（event/custom 軸・df > RELATED_NOISE_MAX）。
    設計: docs/graph_drilldown_redesign.md
    """
    from django.urls import reverse
    from tags.models import Tag
    from .models import StockDiary
    from .tag_axis_config import RELATED_NOISE_MAX

    kind, _sep, raw = (node_id or '').partition(':')

    if kind == 'tag':
        try:
            tag = Tag.objects.select_related('parent').get(user=user, pk=int(raw))
        except (Tag.DoesNotExist, ValueError, TypeError):
            return None
        node = {'id': f'tag:{tag.pk}', 'type': 'tag', 'label': tag.name,
                'axis': getattr(tag, 'axis', 'theme')}
        neighbors = []
        # 親タグ（あれば・表示可なら）
        if tag.parent_id and _explore_tag_visible(tag.parent, RELATED_NOISE_MAX):
            neighbors.append(_explore_tag_neighbor(tag.parent, 'parent'))
        # 子タグ
        for child in tag.children.all():
            if _explore_tag_visible(child, RELATED_NOISE_MAX):
                neighbors.append(_explore_tag_neighbor(child, 'child'))
        # そのタグの銘柄（distinct symbol）
        seen = {}
        for symbol, name in (
            tag.stockdiary_set.filter(user=user)
            .values_list('stock_symbol', 'stock_name')
        ):
            if not symbol or symbol in seen:
                continue
            seen[symbol] = name or symbol
        for symbol, name in seen.items():
            neighbors.append({'id': f'stock:{symbol}', 'type': 'stock',
                              'label': name, 'symbol': symbol, 'rel': 'tagged'})
        return {'node': node, 'neighbors': neighbors}

    if kind == 'stock':
        symbol = raw
        diaries = list(
            StockDiary.objects.filter(user=user, stock_symbol=symbol)
            .prefetch_related('tags')
        )
        if not symbol or not diaries:
            return None
        name = next((d.stock_name for d in diaries if d.stock_name), symbol)
        rep = max(diaries, key=lambda d: d.pk)  # 代表 diary（詳細遷移先）
        node = {'id': f'stock:{symbol}', 'type': 'stock', 'label': name,
                'symbol': symbol,
                'detail_url': reverse('stockdiary:detail', kwargs={'pk': rep.pk})}
        neighbors = []
        seen_tag_ids = set()
        for d in diaries:
            for tag in d.tags.all():
                if tag.id in seen_tag_ids:
                    continue
                seen_tag_ids.add(tag.id)
                if _explore_tag_visible(tag, RELATED_NOISE_MAX):
                    neighbors.append(_explore_tag_neighbor(tag, 'tag'))
        return {'node': node, 'neighbors': neighbors}

    return None


def get_sector_graph_data(diaries_qs, company_sector_map: Dict[str, str] = None) -> Dict[str, Any]:
    """
    業種ハブノードと diary→sector エッジを生成する。

    Args:
        diaries_qs: StockDiary QuerySet（sector フィールドを含む）
        company_sector_map: stock_symbol -> 業種名 の辞書（sector が空の日記を補完する）

    Returns:
        {
            'sector_nodes': [{'id': 'sec_<name>', 'node_type': 'sector', 'sector_name': str, 'diary_count': int}],
            'edges':        [{'source': <diary_id>, 'target': 'sec_<name>', 'edge_type': 'sector'}]
        }
    """
    sector_diary_count: Dict[str, int] = defaultdict(int)
    edges: List[dict] = []
    UNKNOWN = '未分類'

    for diary in diaries_qs:
        sector_name = (diary.sector or '').strip()
        # sector が未設定の場合は company_sector_map から補完
        if not sector_name and company_sector_map:
            sector_name = company_sector_map.get(diary.stock_symbol or '', '')
        sector_name = sector_name or UNKNOWN
        sector_id = f'sec_{sector_name}'
        sector_diary_count[sector_name] += 1
        edges.append({
            'source': diary.pk,
            'target': sector_id,
            'edge_type': 'sector',
        })

    # 全カウント確定後に逆頻度の重みを付与
    for e in edges:
        name = e['target'].split('_', 1)[1]
        e['weight'] = hub_weight(sector_diary_count[name])

    sector_nodes = [
        {
            'id': f'sec_{name}',
            'node_type': 'sector',
            'sector_name': name,
            'diary_count': count,
            'weight': hub_weight(count),
            'is_isolated': count <= 1,
        }
        for name, count in sector_diary_count.items()
    ]

    return {'sector_nodes': sector_nodes, 'edges': edges}


def get_hashtag_graph_data(
    diaries_qs,
    note_limit: int | None = None,
    user_tag_axis_map: Dict[str, str] | None = None,
    user_tag_dir_map: Dict[tuple, str] | None = None,
) -> Dict[str, Any]:
    """
    @ハッシュタグが共通する日記同士をエッジで繋ぐ。
    ハッシュタグをハブノードとして追加する。
    reason フィールドに加え、継続記録（DiaryNote.content）も対象にする。

    Args:
        diaries_qs: prefetch_related('notes') 済みの StockDiary QuerySet
        note_limit: 各日記の継続記録を直近N件に制限する。None で全件。
        user_tag_axis_map: Tag M2M から取得した {タグ名: 軸} マップ（軸オーバーライド用）
        user_tag_dir_map: {(diary_id, タグ名): 方向} マップ。@ハッシュタグは
            _sync_hashtag_tags で同名の Tag(M2M) に同期されるため、その Tag に
            設定された方向（DiaryTagDirection）をエッジに付与し、タグモードと
            同様に追い風(up)/向かい風(down)で着色できるようにする。

    軸決定の優先順位:
        1. user_tag_axis_map（Tag M2M でユーザーが明示設定）
        2. 標準タグ（MasterTag, get_master_axis_map()）
        3. デフォルト 'theme'

    Returns:
        {
            'hashtag_nodes': [{'id': 'ht_<tag>', 'node_type': 'hashtag', 'tag_name': str,
                               'diary_count': int, 'axis': str, 'is_isolated': bool}],
            'edges':         [{'source': <diary_id>, 'target': 'ht_<tag>',
                               'edge_type': 'hashtag', 'axis': str}]
        }
    """
    from .tag_axis_config import get_master_axis_map
    master_axis_map = get_master_axis_map()

    def _axis(name: str) -> str:
        if user_tag_axis_map and name in user_tag_axis_map:
            return user_tag_axis_map[name]
        return master_axis_map.get(name, 'theme')

    ht_diary_count: Dict[str, int] = defaultdict(int)
    edges: List[dict] = []

    for diary in diaries_qs:
        # 同一日記からの重複エッジを防ぐため、この日記で発見済みのタグを追跡
        seen_tags: Set[str] = set()

        texts = [diary.reason or '']
        try:
            notes = diary.notes.all()
            if note_limit is not None:
                # prefetch済みのデータをPythonでソートして件数制限（直近N件）
                notes = sorted(notes, key=lambda n: n.date, reverse=True)[:note_limit]
            texts.extend(note.content for note in notes if note.content)
        except AttributeError:
            pass

        for text in texts:
            for tag in extract_hashtags(text):
                if tag in seen_tags:
                    continue
                seen_tags.add(tag)
                ht_id = f'ht_{tag}'
                ht_diary_count[tag] += 1
                edges.append({
                    'source': diary.pk,
                    'target': ht_id,
                    'edge_type': 'hashtag',
                    'axis': _axis(tag),
                })

    # 全カウント確定後に逆頻度の重みと方向（追い風/向かい風）を付与
    for e in edges:
        tag = e['target'].split('_', 1)[1]
        e['weight'] = hub_weight(ht_diary_count[tag])
        e['direction'] = (
            user_tag_dir_map.get((e['source'], tag), 'neutral')
            if user_tag_dir_map else 'neutral'
        )

    hashtag_nodes = [
        {
            'id': f'ht_{tag}',
            'node_type': 'hashtag',
            'tag_name': tag,
            'diary_count': count,
            'axis': _axis(tag),
            'weight': hub_weight(count),
            'is_isolated': count <= 1,
        }
        for tag, count in ht_diary_count.items()
    ]

    return {'hashtag_nodes': hashtag_nodes, 'edges': edges}


def extract_stock_mentions(text: str) -> List[str]:
    """
    テキストから証券コードを抽出する（括弧内の数字・英数字）

    対応フォーマット:
        - (9101)  / （9101）  : 日本株・旧形式（4〜6桁数字）
        - (285A)  / （285A）  : 日本株・新形式（3〜4桁数字＋大文字1字）
        - (AAPL)  / （AAPL）  : 米国株・ETF（大文字2〜6字）

    Examples:
        >>> extract_stock_mentions("日本郵船(9101) / キオクシア(285A)")
        ['9101', '285A']
        >>> extract_stock_mentions("参考: AAPL(AAPL) / (USD)")
        ['AAPL', 'USD']
    """
    if not text:
        return []

    # 半角・全角括弧内の証券コードを抽出:
    #   \d{4,6}        : 純数字 4〜6桁（旧形式日本株）
    #   \d{3,4}[A-Z]   : 数字3〜4桁＋大文字1字（新形式 285A 等）
    #   [A-Z]{2,6}     : 大文字のみ 2〜6字（米国株・ETF）
    pattern = r'[（(](\d{4,6}|\d{3,4}[A-Z]|[A-Z]{2,6})[）)]'
    matches = re.findall(pattern, text)

    seen: Set[str] = set()
    unique: List[str] = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            unique.append(m)
    return unique


def mention_excerpt(text: str, symbol: str, radius: int = 40) -> str:
    """テキスト中の銘柄コード `(symbol)` 周辺の抜粋を返す（エッジ根拠表示用）"""
    if not text:
        return ''
    m = re.search(r'[（(]' + re.escape(symbol) + r'[）)]', text)
    if not m:
        return ''
    start = max(0, m.start() - radius)
    end = min(len(text), m.end() + radius)
    body = ' '.join(text[start:end].split())
    return ('…' if start > 0 else '') + body + ('…' if end < len(text) else '')


def get_mention_graph_data(
    primary_diaries,
    symbol_to_diary_id: Dict[str, int],
) -> Dict[str, Any]:
    """
    日記テキスト（reason）内の銘柄コードメンションによるエッジを生成。

    Args:
        primary_diaries: StockDiary のリスト（reason フィールドを含む）
        symbol_to_diary_id: stock_symbol -> diary_pk の辞書（全ユーザー日記）

    Returns:
        {
            'edges':                [{'source': diary_pk, 'target': diary_pk,
                                      'edge_type': 'mention', 'excerpt': str}],
            'mentioned_diary_ids':  primary 外のメンション先 diary ID の set
        }
        source は「言及した側」、excerpt は言及箇所周辺の本文抜粋。
    """
    edges: List[dict] = []
    edge_set: Set[Tuple[int, int]] = set()
    mentioned_diary_ids: Set[int] = set()
    primary_id_set = {d.pk for d in primary_diaries}

    for diary in primary_diaries:
        text = (diary.reason or '')
        for symbol in extract_stock_mentions(text):
            target_id = symbol_to_diary_id.get(symbol)
            if not target_id or target_id == diary.pk:
                continue
            key = (min(diary.pk, target_id), max(diary.pk, target_id))
            if key not in edge_set:
                edge_set.add(key)
                edges.append({
                    'source': diary.pk,
                    'target': target_id,
                    'edge_type': 'mention',
                    'weight': 0.9,  # 本文の明示的言及は強い関連
                    'excerpt': mention_excerpt(text, symbol),
                })
            if target_id not in primary_id_set:
                mentioned_diary_ids.add(target_id)

    return {'edges': edges, 'mentioned_diary_ids': mentioned_diary_ids}


# この数を超える銘柄が共有するタグ／業種／@タグは「付けすぎ＝ノイズ」とみなし、
# 関連サマリーから除外する（関連付けしすぎで見にくくなる問題への対策）。
RELATED_NOISE_MAX = 25


def compute_related_strength(focal, user, limit: int = 12) -> List[dict]:
    """フォーカル日記に対する他日記の「関連の強さ」を軸重み×IDF で算出する。

    [Phase 1+2 更新]
    - 共通タグのスコアを「軸重み × IDF」で計算（希少なテーマタグほど強く）
    - 最小条件: イベント軸以外の共有タグ2つ以上、または高希少テーマタグ1つ
    - イベント軸タグは関連度計算から除外

    Returns:
        score 降順の [{'diary': StockDiary, 'score': float,
                       'via': [{'type': str, 'label': str}]}] （最大 limit 件）
    """
    import math
    from django.db.models import Q
    from .models import StockDiary, DiaryNote
    from .tag_axis_config import (
        AXIS_WEIGHTS, MIN_SHARED_TAGS, RELATED_NOISE_MAX,
        MIN_N_FOR_THEMES, HIGH_RARITY_MAX_OTHERS, IMPLICIT_THEME_DISCOUNT,
    )

    scores: Dict[int, float] = defaultdict(float)
    reasons: Dict[int, List[dict]] = defaultdict(list)
    diary_cache: Dict[int, Any] = {}

    others = StockDiary.objects.filter(user=user).exclude(id=focal.id)
    # IDF 計算用の総銘柄数（ポートフォリオ全体）
    N = StockDiary.objects.filter(user=user, is_excluded=False).count() or 1
    # P1: 小標本では per-user IDF が不安定なため、テーマ段階を無効化する
    themes_enabled = N >= MIN_N_FOR_THEMES

    def _add(did: int, score: float, via: dict) -> None:
        scores[did] += score
        reasons[did].append(via)

    # 1. 同一銘柄コード（同じ会社の別エントリ）= 最も強い
    if focal.stock_symbol:
        for d in others.filter(stock_symbol=focal.stock_symbol):
            diary_cache[d.id] = d
            _add(d.id, 1.0, {'type': 'symbol', 'label': '同一銘柄'})

    # 2. 手動リンク（双方向）= 明示的意図で最強
    manual_ids = (
        set(focal.linked_diaries.values_list('id', flat=True))
        | set(focal.linked_from.values_list('id', flat=True))
    )
    if manual_ids:
        for d in others.filter(id__in=manual_ids):
            diary_cache[d.id] = d
            _add(d.id, 1.0, {'type': 'manual', 'label': '手動リンク'})

    # 3. 共通タグ（軸重み × IDF + 最小条件 + ノイズ除外）
    focal_tags = list(focal.tags.select_related('parent').all())
    # イベント軸・ラベル軸は関連度計算から除外
    effective_focal_tags = [t for t in focal_tags if getattr(t, 'axis', 'theme') not in ('event', 'custom')]

    if effective_focal_tags and themes_enabled:
        # tag_id → 共有している他日記のset（ノイズ上限でフィルタ済み）。
        # 旧実装は focal タグ1つにつき1クエリ（others.filter(tags=tag)）で、
        # タグ数ぶんのクエリが detail ロード毎に走っていた（theme_recall が常設の
        # ため遅延もできない）。(tag_id, diary_id) ペアを1クエリでまとめて取得し、
        # Python 側でタグごとに集約する。
        focal_tag_id_set = {t.id for t in effective_focal_tags}
        tag_to_ids: Dict[int, set] = defaultdict(set)
        for t_id, d_id in (
            others.filter(tags__in=focal_tag_id_set).values_list('tags__id', 'id')
        ):
            # WHERE で focal タグに限定済みだが、念のため focal タグのみ採用する
            if t_id in focal_tag_id_set:
                tag_to_ids[t_id].add(d_id)
        tag_diary_map: Dict[int, set] = {
            t_id: ids for t_id, ids in tag_to_ids.items()
            if ids and len(ids) <= RELATED_NOISE_MAX
        }

        # diary_id → 共有タグリスト
        shared_tag_map: Dict[int, List] = defaultdict(list)
        tag_by_id = {t.id: t for t in effective_focal_tags}
        for tag_id, diary_ids in tag_diary_map.items():
            for did in diary_ids:
                shared_tag_map[did].append(tag_by_id[tag_id])

        # 方向属性（DiaryTagDirection）を取得し、共有タグごとに順相関/逆相関を判定する材料にする
        from .models import DiaryTagDirection as _DTD
        focal_dir = dict(
            _DTD.objects.filter(diary=focal, tag_id__in=tag_diary_map.keys())
            .values_list('tag_id', 'direction')
        )
        _other_ids = set()
        for _ids in tag_diary_map.values():
            _other_ids |= _ids
        other_dir: Dict[tuple, str] = {}
        if _other_ids:
            for d_id, t_id, _dir in _DTD.objects.filter(
                diary_id__in=_other_ids, tag_id__in=tag_diary_map.keys()
            ).values_list('diary_id', 'tag_id', 'direction'):
                other_dir[(d_id, t_id)] = _dir

        for did, shared_tags in shared_tag_map.items():
            # 最小条件チェック（FR-6）
            # P1: 高希少は IDF閾値でなく絶対df基準（focal以外で HIGH_RARITY_MAX_OTHERS 銘柄以下）
            theme_tags = [t for t in shared_tags if getattr(t, 'axis', 'theme') == 'theme']
            has_high_rarity_theme = any(
                len(tag_diary_map.get(t.id, set())) <= HIGH_RARITY_MAX_OTHERS
                for t in theme_tags
            )

            if len(shared_tags) < MIN_SHARED_TAGS and not has_high_rarity_theme:
                continue

            # スコア: Σ w_axis(t) × idf(t)   ※ P1: 加算平滑化 ln((N+1)/(df+1))
            for tag in shared_tags:
                axis = getattr(tag, 'axis', 'theme')
                w = AXIS_WEIGHTS.get(axis, 0.5)
                df = max(tag.df, 1) if getattr(tag, 'df', 0) > 0 else len(tag_diary_map.get(tag.id, set())) + 1
                idf = math.log((N + 1) / (df + 1))
                axis_labels = {
                    'theme': 'テーマ', 'business_model': 'BM',
                    'risk': 'リスク', 'capital_policy': '資本政策',
                    'macro': 'マクロ', 'event': 'イベント',
                }
                # 方向が双方とも非中立なら順相関（同方向）/逆相関（逆方向）を判定
                fdir = focal_dir.get(tag.id, 'neutral')
                odir = other_dir.get((did, tag.id), 'neutral')
                correlation = None
                if fdir in ('up', 'down') and odir in ('up', 'down'):
                    correlation = 'positive' if fdir == odir else 'inverse'
                dir_suffix = {'positive': '・順', 'inverse': '・逆'}.get(correlation, '')
                label = f'@{tag.name}（{axis_labels.get(axis, axis)}{dir_suffix}）'
                via = {'type': 'tag', 'label': label, 'key': tag.name, 'axis': axis}
                if correlation:
                    via['correlation'] = correlation
                _add(did, w * idf, via)

    # 4. 同業種（希少なほど強い・付けすぎは除外）
    sector = (focal.sector or '').strip()
    if sector:
        ids = list(others.filter(sector=sector).values_list('id', flat=True))
        if ids and len(ids) <= RELATED_NOISE_MAX:
            w = hub_weight(len(ids) + 1)
            for did in ids:
                _add(did, w, {'type': 'sector', 'label': f'同業種「{sector}」', 'key': f'sector:{sector}'})

    # 5. 共通 @ハッシュタグ（軸重み × IDF でスコアリング）
    from .tag_axis_config import get_master_axis_map, AXIS_LABELS as _AXIS_LABELS
    _master_ht_axis = get_master_axis_map()
    try:
        from tags.models import Tag as _TagModel
        _user_ht_axis = dict(_TagModel.objects.filter(user=user).values_list('name', 'axis'))
    except Exception:
        _user_ht_axis = {}

    def _ht_axis(name: str) -> str:
        if name in _user_ht_axis:
            return _user_ht_axis[name]
        return _master_ht_axis.get(name, 'custom')

    # focal のテキストから @タグ抽出（event軸・ラベル軸除外）
    focal_hashtags = {h for h in extract_hashtags(focal.reason or '') if _ht_axis(h) not in ('event', 'custom')}
    for note in DiaryNote.objects.filter(diary=focal).only('content'):
        focal_hashtags |= {h for h in extract_hashtags(note.content or '') if _ht_axis(h) not in ('event', 'custom')}

    # Phase 3（Tag M2M）で既にスコア済みのタグはダブルカウントを避ける
    m2m_tag_names = {t.name for t in focal.tags.all()}
    focal_hashtags = focal_hashtags - m2m_tag_names

    if focal_hashtags and themes_enabled:
        # 全他日記の reason から @タグを一括取得して ht_df とマップを構築
        ht_df: Dict[str, int] = defaultdict(int)
        other_ht_map: Dict[int, Set[str]] = {}
        for row in others.only('id', 'reason'):
            tags_in = {h for h in extract_hashtags(row.reason or '') if _ht_axis(h) not in ('event', 'custom')}
            other_ht_map[row.id] = tags_in
            for h in tags_in:
                ht_df[h] += 1

        for did, other_tags in other_ht_map.items():
            shared = focal_hashtags & other_tags
            if not shared:
                continue
            # ノイズ除外
            shared = {h for h in shared if ht_df.get(h, 0) <= RELATED_NOISE_MAX}
            if not shared:
                continue
            # 最小条件（2+ 共有 or 高希少テーマ）※ P1: 高希少は絶対df基準
            theme_shared = {h for h in shared if _ht_axis(h) == 'theme'}
            high_rarity = any(
                ht_df.get(h, 0) <= HIGH_RARITY_MAX_OTHERS
                for h in theme_shared
            )
            if len(shared) < MIN_SHARED_TAGS and not high_rarity:
                continue
            # スコア: Σ w_axis × IDF   ※ P1: 加算平滑化 ln((N+1)/(df+1))
            for tag_name in shared:
                axis = _ht_axis(tag_name)
                w = AXIS_WEIGHTS.get(axis, 0.5)
                idf = math.log((N + 1) / (ht_df.get(tag_name, 0) + 1))
                axis_label = _AXIS_LABELS.get(axis, axis)
                _add(did, w * idf, {'type': 'tag', 'label': f'@{tag_name}（{axis_label}）',
                                    'key': tag_name, 'axis': axis})

    # 5b. 推定テーマ: 本文の素のテーマ語（@無し）で関連付ける = 低重み・要裏付け（P2）
    #     - focal側はテーマを明示(@/タグ)していてもよい。相手側が「素の本文で言及」している場合に拾う。
    #       （相手が @テーマ で書いていれば 5. が担当するので二重計上しない＝ n not in other_ht）
    #     - 誤検出抑制: 具体語(3文字以上)のみ／裏付け要件＝2語以上 or 業種一致 or focalが明示済みのテーマ
    if themes_enabled:
        implicit_vocab = {
            name: axis
            for name, axis in {**_master_ht_axis, **_user_ht_axis}.items()
            if axis not in ('event', 'custom') and len(name) >= 3
        }
        focal_text = focal.reason or ''
        for note in DiaryNote.objects.filter(diary=focal).only('content'):
            focal_text += ' ' + (note.content or '')
        # focal が（@/タグ/素の本文 のいずれかで）言及しているテーマ語
        focal_themes = {n for n in implicit_vocab if n in focal_text}
        focal_explicit = m2m_tag_names | focal_hashtags  # focal が意図的に付けたテーマ
        if focal_themes:
            focal_sector = (focal.sector or '').strip()
            impl_df: Dict[str, int] = defaultdict(int)
            other_impl_map: Dict[int, tuple] = {}
            for row in others.only('id', 'reason', 'sector'):
                otext = row.reason or ''
                other_ht = set(extract_hashtags(otext))
                # 相手が素の本文で言及（@で書いていれば 5. が担当）
                shared = {n for n in focal_themes if n in otext and n not in other_ht}
                if shared:
                    other_impl_map[row.id] = (shared, (row.sector or '').strip())
                    for n in shared:
                        impl_df[n] += 1
            for did, (shared, osector) in other_impl_map.items():
                # 裏付け要件: 2語以上 / 業種一致 / focalが明示済みのテーマ（意図的なので単独でも可）
                corroborated = (
                    len(shared) >= 2
                    or (focal_sector and osector == focal_sector)
                    or bool(shared & focal_explicit)
                )
                if not corroborated:
                    continue
                for name in shared:
                    if impl_df.get(name, 0) > RELATED_NOISE_MAX:
                        continue
                    axis = implicit_vocab[name]
                    w = AXIS_WEIGHTS.get(axis, 0.5)
                    idf = math.log((N + 1) / (impl_df.get(name, 0) + 1))
                    _add(did, w * idf * IMPLICIT_THEME_DISCOUNT,
                         {'type': 'tag', 'label': f'{name}（推定）', 'key': name,
                          'axis': axis, 'estimated': True})

    # 6. 銘柄コード言及（双方向）= 明示的参照で強い
    mentioned = extract_stock_mentions(focal.reason or '')
    if mentioned:
        for d in others.filter(stock_symbol__in=mentioned):
            diary_cache[d.id] = d
            _add(d.id, 0.9, {'type': 'mention', 'label': '本文で言及'})
    if focal.stock_symbol:
        sym = focal.stock_symbol
        token_q = (
            Q(reason__icontains=f'({sym})') | Q(reason__icontains=f'（{sym}）')
        )
        for d in others.filter(token_q):
            diary_cache[d.id] = d
            _add(d.id, 0.9, {'type': 'mention', 'label': '相手から言及'})

    # まだ取得していない日記オブジェクトをまとめて取得
    missing = [did for did in scores if did not in diary_cache]
    if missing:
        for d in StockDiary.objects.filter(id__in=missing):
            diary_cache[d.id] = d

    # P3: 異種スコアの単純和で順位が逆転しないようティア優先にする。
    #     Tier A(同一銘柄/手動/言及) > Tier B(テーマ) > Tier C(業種)。ティア内のみスコア比較。
    TIER_A_TYPES = {'symbol', 'manual', 'mention'}
    result: List[dict] = []
    for did, score in scores.items():
        d = diary_cache.get(did)
        if not d:
            continue
        vias = reasons[did]
        via_types = {v.get('type') for v in vias}
        if via_types & TIER_A_TYPES:
            tier = 0
        elif 'tag' in via_types:
            tier = 1
        else:
            tier = 2
        result.append({'diary': d, 'score': round(score, 4), 'via': vias, 'tier': tier})
    result.sort(key=lambda x: (x['tier'], -x['score']))
    return result[:limit]


def _extract_section(text: str, headings: List[str]) -> str:
    """Markdown見出し（## など）で区切られた本文から、指定見出しのセクション本文を返す。"""
    out: List[str] = []
    capturing = False
    for raw in text.splitlines():
        s = raw.strip()
        h = re.match(r'^#{1,6}\s*(.+?)\s*$', s)
        if h:
            title = h.group(1)
            if any(k in title for k in headings):
                capturing = True
                continue
            if capturing:
                break  # 次の見出しに到達したら終了
        elif capturing:
            out.append(raw)
    return '\n'.join(out).strip()


def extract_lead(text: str, max_len: int = 120) -> str:
    """投資理由/継続記録から「意味のある先頭1〜2文」を返す。

    Markdownのノイズ（見出し・引用注記・テーブル・空のラベル箇条書き・全角括弧のガイダンス）を
    除去する。テンプレ運用者の場合は `## ひとこと要約` 等の結論セクションを優先する。
    どんな書き方でも何かしらの本文が返るようにし、結論を構造で強制しない（規律ゼロ運用に対応）。
    """
    if not text:
        return ''

    def _clean(body: str) -> str:
        lines: List[str] = []
        for raw in body.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith('#') or line.startswith('>') or line.startswith('|'):
                continue
            # 箇条書きの先頭記号を外す
            m = re.match(r'^[-*]\s+(.*)$', line)
            if m:
                line = m.group(1).strip()
            # 太字ラベルのみ/値が空の箇条書き（**項目**: ）を除外、値があれば値を採用
            lab = re.match(r'^\*\*[^*]+\*\*\s*[:：]?\s*(.*)$', line)
            if lab is not None:
                val = lab.group(1).strip()
                if not val:
                    continue
                line = val
            # 全角/半角の括弧だけで構成されたガイダンス行を除外
            if re.fullmatch(r'[（(].*[)）]', line):
                continue
            line = re.sub(r'^[#>*\-\s]+', '', line).strip()
            line = re.sub(r'\*\*|`', '', line)  # 残った強調記号を除去
            if len(line) < 2:
                continue
            lines.append(line)
            if len(' '.join(lines)) >= max_len:
                break
        return ' '.join(lines).strip()

    # 要約セクションを優先。ただし要約が未記入（ガイダンス行のみ等）で空になる場合は
    # 本文全体へフォールバックし、別見出しに書かれた内容を取りこぼさない。
    summary = _extract_section(text, ['要約', '結論ひとこと', '総合評価'])
    result = _clean(summary) if summary else ''
    if not result:
        result = _clean(text)

    if len(result) > max_len:
        result = result[:max_len].rstrip() + '…'
    return result


def _diary_outcome(d) -> dict:
    """銘柄の損益/状態を「参考」表示用に返す。

    P4(アウトカム・バイアス回避): 主役にせず淡色で添える前提。
    現在株価を保持していないため、保有中の含み損益は出さない（誤った確定感を与えない）。
    """
    if d.is_memo:
        return {'label': 'メモ', 'tone': 'muted'}
    if d.is_short:
        return {'label': '信用売り', 'tone': 'muted'}
    if d.is_sold_out:
        rp = float(d.realized_profit or 0)
        base = float(d.total_buy_amount or 0)
        sign = '+' if rp >= 0 else ''
        tone = 'up' if rp >= 0 else 'down'
        if base > 0:
            return {'label': f'売却済 {sign}{rp / base * 100:.1f}%', 'tone': tone}
        return {'label': f'売却済 {sign}{rp:,.0f}{d.currency_unit}', 'tone': tone}
    return {'label': '保有中', 'tone': 'muted'}


def build_theme_recall(related_unified: List[dict], focal, user) -> dict:
    """関連銘柄リストを「テーマ別の過去判断」に組み替える（想起パネル用）。

    既に算出済みの related_unified を再利用し、再計算しない。
    各メンバーに 冒頭文(主役)・直近ノート・損益(参考)・方向の見立て を添える。
    戻り値: {'primary': [bucket], 'more': [bucket], 'total': int, 'is_empty': bool, 'sector_only': bool}
    （primary=常時表示の主要テーマ、more=折り畳む推定/同業種テーマ）
    is_empty のときのみオンボーディング用に
    focal_theme_tags / focal_label_tags / suggested_themes を追加で返す。
    """
    from .models import DiaryNote
    from .tag_axis_config import AXIS_COLORS

    THEME_TYPES = {'tag', 'sector'}
    SECTOR_COLOR = '#b45309'

    member_ids = [it['diary'].id for it in related_unified]
    notes_by_diary: Dict[int, list] = defaultdict(list)
    if member_ids:
        for n in (DiaryNote.objects
                  .filter(diary_id__in=member_ids)
                  .order_by('-date', '-id')
                  .only('diary_id', 'content', 'date')):
            notes_by_diary[n.diary_id].append(n)

    themes: Dict[str, dict] = {}
    has_theme_bucket = False
    # focal に対する最強の関連スコア（相対的な「関連度%」算出の基準）
    max_score = max((it.get('score', 0) for it in related_unified), default=0) or 1.0

    for it in related_unified:
        d = it['diary']
        lead = extract_lead(d.reason or '')
        latest = None
        for n in notes_by_diary.get(d.id, []):
            txt = extract_lead(n.content or '')
            if txt:
                latest = {'text': txt, 'date': n.date}
                break
        outcome = _diary_outcome(d)

        for v in it['via']:
            vtype = v.get('type')
            if vtype not in THEME_TYPES:
                continue
            key = v.get('key') or v.get('label')
            if not key:
                continue
            estimated = bool(v.get('estimated'))
            if vtype == 'tag':
                has_theme_bucket = True
                display = key
                color = AXIS_COLORS.get(v.get('axis'), '#7c3aed')
            else:  # sector
                display = key.split(':', 1)[-1]
                color = SECTOR_COLOR

            bucket = themes.get(key)
            if bucket is None:
                bucket = {'display': display, 'type': vtype, 'color': color,
                          'estimated': estimated, 'members': [], 'member_ids': set(),
                          'score': 0.0}
                themes[key] = bucket
            # 1件でも非推定経由があればバケットは確定扱い
            bucket['estimated'] = bucket['estimated'] and estimated
            bucket['score'] += it['score']
            if d.id in bucket['member_ids']:
                continue
            bucket['member_ids'].add(d.id)
            rel_pct = max(6, round(it['score'] / max_score * 100))  # 最低6%でバーを可視化
            rel_level = 'high' if rel_pct >= 60 else ('mid' if rel_pct >= 30 else 'low')
            if estimated and rel_level == 'high':
                rel_level = 'mid'  # 推定は「強」にしない
            bucket['members'].append({
                'diary': d,
                'lead': lead,
                'latest': latest,
                'outcome': outcome,
                'direction': v.get('correlation'),
                'estimated': estimated,
                'score': it['score'],
                'rel_pct': rel_pct,
                'rel_level': rel_level,
            })

    # バケットを整列し、規模が増えても膨らまないよう「主要テーマ」と「その他」に分割する。
    # ティア: 0=明示タグ / 1=推定タグ / 2=同業種。明示を常に上、推定・業種は折り畳む。
    MAX_PRIMARY_THEMES = 5
    buckets = list(themes.values())
    for b in buckets:
        b['members'].sort(key=lambda m: -m['score'])
        b['tier'] = 2 if b['type'] == 'sector' else (1 if b['estimated'] else 0)
        # テーマの強さ＝そのテーマ内で最も関連が強い銘柄の関連度（合算でなくピーク基準）
        b['top_score'] = b['members'][0]['score'] if b['members'] else 0.0
        b['rel_pct'] = b['members'][0]['rel_pct'] if b['members'] else 0
        b.pop('member_ids', None)
    buckets.sort(key=lambda b: (b['tier'], -b['top_score']))

    primary = [b for b in buckets if b['tier'] == 0][:MAX_PRIMARY_THEMES]
    if not primary:  # 規律ゼロ（明示タグ皆無）でも何か出す
        primary = buckets[:MAX_PRIMARY_THEMES]
    primary_ids = {id(b) for b in primary}
    more = [b for b in buckets if id(b) not in primary_ids]

    is_empty = len(buckets) == 0
    result = {
        'primary': primary,
        'more': more,
        'total': len(buckets),
        'is_empty': is_empty,
        'sector_only': (not has_theme_bucket) and len(buckets) > 0,
    }

    # 空状態のオンボーディング用文脈（空のときだけ算出。非空なら追加クエリ無し）。
    if is_empty:
        THEME_LIKE_AXES = {'theme', 'macro', 'business_model', 'capital_policy', 'risk'}
        LABEL_AXES = {'custom', 'event'}  # 関連付けから除外される軸＝テーマ昇格の案内対象
        focal_theme_tags: List[str] = []
        focal_label_tags: List[dict] = []
        # prefetch 済みの focal.tags を Python で1回走査して仕分け（.filter() は使わない）。
        for t in focal.tags.all():
            axis = getattr(t, 'axis', 'theme')
            if axis in THEME_LIKE_AXES:
                focal_theme_tags.append(t.name)
            elif axis in LABEL_AXES:
                focal_label_tags.append({'id': t.id, 'name': t.name})
        from tags.models import MasterTag
        suggested_themes = list(
            MasterTag.objects.filter(is_active=True, axis='theme')
            .values_list('name', flat=True)[:6]
        )
        result['focal_theme_tags'] = focal_theme_tags
        result['focal_label_tags'] = focal_label_tags
        result['suggested_themes'] = suggested_themes

    return result
