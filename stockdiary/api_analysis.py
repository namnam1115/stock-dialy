# stockdiary/api_analysis.py
"""
Claude Code などの外部ツール向け分析API（読み取り＋書き込み）。

認証: Authorization: Bearer <ANALYSIS_API_KEY> ヘッダー。
書き込み先ユーザー: 環境変数 ANALYSIS_API_USER で固定（サーバー側で決まる）。

セットアップ:
    python manage.py generate_analysis_key
    # .env に ANALYSIS_API_KEY と ANALYSIS_API_USER を追記
    # gunicorn reload

従量課金なし: ニュースは yfinance.news（無料）を使用。
"""
import json
import logging
from datetime import date, datetime, timezone as dt_timezone
from functools import wraps

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from .models import DiaryNote, ReasonVersion, StockDiary, Thesis
from .utils import is_japanese_stock

logger = logging.getLogger(__name__)
User = get_user_model()

# ------------------------------------------------------------------ #
#  共通ヘルパー
# ------------------------------------------------------------------ #

def _require_analysis_key(view_func):
    """ANALYSIS_API_KEY による Bearer 認証デコレータ"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        expected = getattr(settings, 'ANALYSIS_API_KEY', None)
        if not expected:
            return JsonResponse(
                {'error': 'ANALYSIS_API_KEY が未設定です。manage.py generate_analysis_key を実行してください'},
                status=503,
            )
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer ') or auth[7:] != expected:
            return JsonResponse({'error': '認証失敗'}, status=401)
        return view_func(request, *args, **kwargs)
    return wrapper


def _get_api_user():
    """
    書き込み操作の対象ユーザーを返す。
    ANALYSIS_API_USER 環境変数で固定（呼び出し元からは変更不可）。
    """
    username = getattr(settings, 'ANALYSIS_API_USER', '').strip()
    if not username:
        return None, JsonResponse(
            {'error': 'ANALYSIS_API_USER が未設定です。.env に追記してサーバーを再起動してください'},
            status=503,
        )
    try:
        return User.objects.get(username=username), None
    except User.DoesNotExist:
        return None, JsonResponse(
            {'error': f'ユーザー "{username}" が存在しません。ANALYSIS_API_USER を確認してください'},
            status=503,
        )


def _sync_diary_tags(diary, user) -> list[str]:
    """本文（reason＋全ノート）の @タグを diary.tags へ同期し、結果のタグ名を返す。

    UI の保存フローと同じ正本ロジック（views._sync_hashtag_tags）を使い、
    タグの追加・解除・方向(↑/↓/→)・df 再計算まで行う。分析API経由の書き込みでも
    本文中の `@タグ` がタグ欄へ反映されるように、reason/note の保存後に呼ぶ。
    """
    from .views import _sync_hashtag_tags  # 循環インポート回避のため遅延 import
    _sync_hashtag_tags(diary, user)
    return list(diary.tags.values_list('name', flat=True))


def _fetch_margin_data(symbol: str, weeks: int = 8) -> dict | None:
    """信用取引残高（JPX週次）の最新値＋直近トレンドを返す。

    margin_tracking.MarginData（銘柄コード単位・週次）から取得する。
    信用倍率 = 買い残 / 売り残（1未満は売り長＝取組良好の目安、過大は上値の重し）。
    データが無い銘柄（外国株・未取得）は None を返す。
    """
    try:
        from margin_tracking.models import MarginData
    except Exception:
        return None

    rows = list(
        MarginData.objects
        .filter(stock_code=symbol)
        .order_by('-record_date')[:weeks]
    )
    if not rows:
        return None

    def _row(m):
        return {
            'date': m.record_date.isoformat(),
            'long_balance': m.long_balance,
            'short_balance': m.short_balance,
            'margin_ratio': float(m.margin_ratio) if m.margin_ratio is not None else None,
        }

    latest = rows[0]
    history = [_row(m) for m in reversed(rows)]  # 古い→新しい
    return {
        'latest': _row(latest),
        'history': history,  # 直近 weeks 週（古い順）
        'note': '信用倍率 = 買い残 / 売り残。1倍未満は売り長（取組良好）、'
                '高倍率・買い残増は将来の戻り売り圧力（上値の重し）の目安。',
    }


def _fetch_current_price(symbol: str) -> float | None:
    """現在株価を取得（Yahoo Finance Chart API・無料・軽量）。

    api.get_stock_price と同じ v8/chart エンドポイントを使う（1リクエスト）。
    取得失敗・外国株の取り違え等は None を返す（含み損益は算出しない）。
    """
    try:
        import requests
        ticker_symbol = symbol if not is_japanese_stock(symbol) else f"{symbol}.T"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_symbol}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        resp = requests.get(url, headers=headers, timeout=5)
        data = resp.json()
        result = (data.get('chart') or {}).get('result') or []
        if not result:
            return None
        price = (result[0].get('meta') or {}).get('regularMarketPrice')
        return float(price) if price else None
    except Exception as e:
        logger.warning("current price fetch failed for %s: %s", symbol, e)
        return None


def _fetch_valuation(symbol: str) -> dict | None:
    """バリュエーション指標（PER/PBR/ROE/配当利回り/時価総額）を返す。

    common.services.YahooFinanceService（財務諸表ベース＋info フォールバック）を使う。
    価格取得より重い（財務諸表を引く）ため、呼び出し側で opt-in する想定。
    取得できなければ None。
    """
    try:
        from common.services.yahoo_finance_service import YahooFinanceService
        data = YahooFinanceService.fetch_company_data(symbol)
        if not data:
            return None
        keys = ('per', 'pbr', 'roe', 'dividend_yield', 'market_cap')
        val = {k: (float(data[k]) if k in data and data[k] is not None else None) for k in keys}
        if all(v is None for v in val.values()):
            return None
        val['note'] = ('PER/PBR は割高・割安の目安、ROE は稼ぐ力、配当利回り[%]、'
                       '時価総額[億円]。買い増し判断のバリュエーション補助指標。')
        return val
    except Exception as e:
        logger.warning("valuation fetch failed for %s: %s", symbol, e)
        return None


def _position_metrics(diary, current_price: float | None) -> dict:
    """保有ポジションの含み損益・時価を算出する。

    含み損益 = (現在値 − 平均取得単価) × 保有数量。
    保有なし・平均取得単価なし・現在値なしのときは金額系を None にする。
    """
    qty = float(diary.current_quantity or 0)
    avg = float(diary.average_purchase_price) if diary.average_purchase_price else None

    metrics = {
        'current_price': current_price,
        'market_value': None,
        'cost_basis': (avg * qty) if (avg and qty) else None,
        'unrealized_profit': None,
        'unrealized_profit_rate': None,
    }
    if current_price and qty:
        metrics['market_value'] = current_price * qty
    if current_price and avg and qty:
        metrics['unrealized_profit'] = (current_price - avg) * qty
        if avg:
            metrics['unrealized_profit_rate'] = (current_price / avg - 1) * 100
    return metrics


def _serialize_theses(diary) -> list[dict]:
    """投資仮説（Thesis）＝「なぜ買ったか」の答え合わせ可能な主張を返す。

    重要: 日記本体の reason（投資理由）は『企業説明テンプレート』で書かれる
    “企業の俯瞰説明”であり、エントリー時の買い判断（仮説）が入っているとは限らない。
    買った理由・崩れる条件（worst_case）・検証状況は Thesis 側にある。ポジション判定
    （継続/損切り/買い増し）は「テーマ＝仮説が今も生きているか」で決まるため、
    reason ではなくこの theses を主ソースにする。検証済みなら Verdict も添える。
    """
    theses = []
    for t in diary.theses.all():
        verdict = None
        v = getattr(t, 'verdict', None)
        if v is not None:
            verdict = {
                'hypothesis_result': v.get_hypothesis_result_display(),
                'pnl_result': v.get_pnl_result_display(),
                'decision_quality': v.decision_quality,
                'quadrant': v.quadrant_label,
                'learning': v.learning,
            }
        theses.append({
            'id': t.id,
            'claim': t.claim,                 # 主張（＝この投資で賭けていること）
            'basis': t.basis,                 # 根拠
            'worst_case': t.worst_case,       # これが起きたら仮説は崩れる（＝損切り/撤退の起点）
            'horizon': t.get_horizon_display(),
            'status': t.get_status_display(),  # 未検証 / 検証済み / 取り下げ
            'review_due_date': t.review_due_date.isoformat() if t.review_due_date else None,
            'is_due': t.is_due,               # 検証期日が到来した未検証の仮説か
            'verdict': verdict,
        })
    return theses


def _fetch_yfinance_news(stock_symbol: str, limit: int = 10) -> list[dict]:
    """yfinance でニュースを取得（無料）"""
    try:
        import yfinance as yf
        ticker_symbol = f"{stock_symbol}.T" if is_japanese_stock(stock_symbol) else stock_symbol
        ticker = yf.Ticker(ticker_symbol)
        raw_news = ticker.news or []
        results = []
        for item in raw_news[:limit]:
            content = item.get('content') or item
            title = content.get('title') or item.get('title', '')
            link = (
                (content.get('canonicalUrl') or {}).get('url')
                or content.get('clickThroughUrl', {}).get('url')
                or item.get('link', '')
            )
            publisher = (
                (content.get('provider') or {}).get('displayName')
                or item.get('publisher', '')
            )
            pub_ts = item.get('providerPublishTime') or None
            if pub_ts:
                try:
                    pub_date = datetime.fromtimestamp(pub_ts, tz=dt_timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
                except Exception:
                    pub_date = str(pub_ts)
            else:
                pub_date = content.get('pubDate', '')
            results.append({
                'title': title,
                'publisher': publisher,
                'published_at': pub_date,
                'url': link,
            })
        return results
    except Exception as e:
        logger.warning("yfinance news fetch failed for %s: %s", stock_symbol, e)
        return []


# ------------------------------------------------------------------ #
#  読み取りエンドポイント
# ------------------------------------------------------------------ #

@require_GET
@_require_analysis_key
def holdings(request):
    """
    現在保有中の銘柄一覧。

    GET /api/analysis/holdings/
    Authorization: Bearer <key>
    """
    diaries = (
        StockDiary.objects
        .filter(user__isnull=False, current_quantity__gt=0)
        .order_by('-first_purchase_date')
        .values(
            'id', 'stock_symbol', 'stock_name', 'sector',
            'current_quantity', 'average_purchase_price',
            'realized_profit', 'first_purchase_date',
            'user__username',
        )
    )
    rows = list(diaries)
    return JsonResponse({
        'count': len(rows),
        'holdings': [
            {
                'id': d['id'],
                'symbol': d['stock_symbol'],
                'name': d['stock_name'],
                'sector': d['sector'],
                'quantity': float(d['current_quantity']),
                'avg_cost': float(d['average_purchase_price']) if d['average_purchase_price'] else None,
                'realized_profit': float(d['realized_profit']),
                'since': d['first_purchase_date'].isoformat() if d['first_purchase_date'] else None,
                'user': d['user__username'],
            }
            for d in rows
        ],
    })


@require_GET
@_require_analysis_key
def diary_detail(request, symbol: str):
    """
    指定銘柄の日記全データ + 最新ニュース（yfinance 無料）。

    GET /api/analysis/diary/<symbol>/
    Authorization: Bearer <key>

    ?user=<username>  複数ユーザー環境で絞り込む場合に使用
    ?news=0           ニュース取得をスキップ（高速化）
    """
    symbol = symbol.upper().strip()
    qs = StockDiary.objects.filter(stock_symbol__iexact=symbol)

    username = request.GET.get('user', '').strip()
    if username:
        qs = qs.filter(user__username=username)

    diary = qs.prefetch_related(
        'transactions', 'notes', 'tags', 'theses__verdict'
    ).first()
    if not diary:
        return JsonResponse({'error': f'{symbol} の日記が見つかりません'}, status=404)

    transactions = [
        {
            'date': t.transaction_date.isoformat(),
            'type': t.transaction_type,
            'price': float(t.price),
            'quantity': float(t.quantity),
            'amount': float(t.amount),
            'is_margin': t.is_margin,
            'memo': t.memo,
        }
        for t in diary.transactions.order_by('transaction_date')
    ]

    notes = [
        {
            'id': n.id,
            'date': n.date.isoformat(),
            'type': n.note_type,
            'topic': n.topic,
            'content': n.content,
        }
        for n in diary.notes.order_by('date')
    ]

    tags = list(diary.tags.values_list('name', flat=True))

    if diary.current_quantity > 0:
        status = '保有中'
    elif diary.transaction_count > 0:
        status = '売却済み'
    else:
        status = 'メモ'

    fetch_news = request.GET.get('news', '1') != '0'
    news = _fetch_yfinance_news(symbol) if fetch_news else []

    fetch_margin = request.GET.get('margin', '1') != '0'
    margin = _fetch_margin_data(symbol) if fetch_margin else None

    # 現在値・含み損益（保有ポジションの手仕舞い/継続/買い増し判断に使う）
    fetch_price = request.GET.get('price', '1') != '0'
    current_price = _fetch_current_price(symbol) if fetch_price else None
    position = _position_metrics(diary, current_price)

    # バリュエーション（PER/PBR等）は財務諸表を引くため重い。?valuation=0 で省略可
    fetch_valuation = request.GET.get('valuation', '1') != '0'
    valuation = _fetch_valuation(symbol) if fetch_valuation else None

    return JsonResponse({
        'symbol': symbol,
        'name': diary.stock_name,
        'status': status,
        'sector': diary.sector,
        'tags': tags,
        # reason は『企業説明』（企業の俯瞰）であり、買った理由とは限らない点に注意。
        # エントリー仮説・崩れる条件は theses を参照する。
        'investment_reason': diary.reason or '',
        'theses': _serialize_theses(diary),
        'first_purchase_date': diary.first_purchase_date.isoformat() if diary.first_purchase_date else None,
        'current_quantity': float(diary.current_quantity),
        'avg_cost': float(diary.average_purchase_price) if diary.average_purchase_price else None,
        'realized_profit': float(diary.realized_profit),
        'current_price': position['current_price'],
        'market_value': position['market_value'],
        'cost_basis': position['cost_basis'],
        'unrealized_profit': position['unrealized_profit'],
        'unrealized_profit_rate': position['unrealized_profit_rate'],
        'valuation': valuation,
        'transaction_count': diary.transaction_count,
        'transactions': transactions,
        'notes': notes,
        'latest_news': news,
        'margin': margin,
        'fetched_at': datetime.now(tz=dt_timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
    })


@require_GET
@_require_analysis_key
def portfolio_summary(request):
    """
    ポートフォリオ全体サマリー（業種分布・損益統計）。

    GET /api/analysis/portfolio/
    Authorization: Bearer <key>
    """
    from django.db.models import Count, Q, Sum

    qs = StockDiary.objects.filter(user__isnull=False)

    agg = qs.aggregate(
        total_diaries=Count('id'),
        holding_count=Count('id', filter=Q(current_quantity__gt=0)),
        sold_count=Count('id', filter=Q(transaction_count__gt=0, current_quantity=0)),
        total_realized_profit=Sum('realized_profit'),
    )

    sector_dist = (
        qs.filter(current_quantity__gt=0)
        .values('sector')
        .annotate(count=Count('id'), realized=Sum('realized_profit'))
        .order_by('-count')
    )

    return JsonResponse({
        'total_diaries': agg['total_diaries'],
        'holding_count': agg['holding_count'],
        'sold_count': agg['sold_count'],
        'total_realized_profit': float(agg['total_realized_profit'] or 0),
        'sector_distribution': [
            {
                'sector': s['sector'] or '未分類',
                'holding_count': s['count'],
                'realized_profit': float(s['realized'] or 0),
            }
            for s in sector_dist
        ],
    })


def _diary_status(diary) -> str:
    if diary.current_quantity > 0:
        return '保有中'
    if diary.transaction_count > 0:
        return '売却済み'
    return 'メモ'


@require_GET
@_require_analysis_key
def list_diaries(request):
    """
    記録銘柄の一覧（スクリーニング用・保有/売却/メモを横断）。

    GET /api/analysis/diaries/
    Authorization: Bearer <key>

    クエリ:
      ?tags=半導体,AI   いずれかのタグを持つ日記に絞る（OR）
      ?sector=電気       業種の部分一致で絞る
      ?status=holding|sold|memo|all（既定 all）
      ?user=<username>   複数ユーザー環境での絞り込み

    各銘柄に最新の信用倍率（margin_ratio）を付与する（バリュエーションは
    呼び出し側で yfinance 等から補完する想定＝サーバ側で外部APIは叩かない）。
    """
    qs = StockDiary.objects.filter(user__isnull=False).prefetch_related('tags')

    username = request.GET.get('user', '').strip()
    if username:
        qs = qs.filter(user__username=username)

    status = request.GET.get('status', 'all').strip()
    if status == 'holding':
        qs = qs.filter(current_quantity__gt=0)
    elif status == 'sold':
        qs = qs.filter(current_quantity=0, transaction_count__gt=0)
    elif status == 'memo':
        qs = qs.filter(transaction_count=0)

    sector = request.GET.get('sector', '').strip()
    if sector:
        qs = qs.filter(sector__icontains=sector)

    tags_param = request.GET.get('tags', '').strip()
    want_tags = [t.strip().lstrip('@') for t in tags_param.split(',') if t.strip()]
    if want_tags:
        qs = qs.filter(tags__name__in=want_tags).distinct()

    # 最新週の信用倍率をまとめて引く（JPX週次は全銘柄同一 record_date のため1クエリ）
    margin_map = {}
    try:
        from django.db.models import Max
        from margin_tracking.models import MarginData
        latest_date = MarginData.objects.aggregate(d=Max('record_date'))['d']
        if latest_date:
            margin_map = {
                m.stock_code: float(m.margin_ratio) if m.margin_ratio is not None else None
                for m in MarginData.objects.filter(record_date=latest_date)
            }
    except Exception:
        margin_map = {}

    diaries = []
    for d in qs.order_by('stock_symbol'):
        diaries.append({
            'symbol': d.stock_symbol,
            'name': d.stock_name,
            'status': _diary_status(d),
            'sector': d.sector,
            'tags': list(d.tags.values_list('name', flat=True)),
            'current_quantity': float(d.current_quantity),
            'realized_profit': float(d.realized_profit),
            'latest_disclosure_date': (
                d.latest_disclosure_date.isoformat() if d.latest_disclosure_date else None
            ),
            'margin_ratio': margin_map.get(d.stock_symbol),
        })

    return JsonResponse({'count': len(diaries), 'diaries': diaries})


@require_GET
@_require_analysis_key
def positions(request):
    """
    現在保有中の全ポジションを、判断材料付きで返す（利確/損切り/継続/買い増し用）。

    GET /api/analysis/positions/
    Authorization: Bearer <key>

    各ポジションに現在値・含み損益（率）・時価を付与し、需給（信用倍率）と
    直近開示日も添える。個別の深掘り（投資理由・ノート・ニュース）は
    diary/<symbol>/ を叩く。この一覧はどのポジションを見直すかのスクリーニング用。

    クエリ:
      ?valuation=1       PER/PBR/ROE/配当利回りを付与（財務諸表を引くため重い。既定 0）
      ?price=0           現在値取得をスキップ（含み損益は算出されない）
      ?user=<username>   複数ユーザー環境での絞り込み

    ポートフォリオ合計（時価・含み損益・取得原価）も併せて返す。
    """
    from django.db.models import Count, Q

    qs = (
        StockDiary.objects
        .filter(user__isnull=False, current_quantity__gt=0)
        .prefetch_related('tags')
        .annotate(
            thesis_total=Count('theses', distinct=True),
            open_thesis_count=Count(
                'theses', filter=Q(theses__status='open'), distinct=True
            ),
        )
        .order_by('stock_symbol')
    )

    username = request.GET.get('user', '').strip()
    if username:
        qs = qs.filter(user__username=username)

    fetch_price = request.GET.get('price', '1') != '0'
    fetch_valuation = request.GET.get('valuation', '0') == '1'

    # 最新週の信用倍率をまとめて引く（list_diaries と同じく1クエリ）
    margin_map = {}
    try:
        from django.db.models import Max
        from margin_tracking.models import MarginData
        latest_date = MarginData.objects.aggregate(d=Max('record_date'))['d']
        if latest_date:
            margin_map = {
                m.stock_code: float(m.margin_ratio) if m.margin_ratio is not None else None
                for m in MarginData.objects.filter(record_date=latest_date)
            }
    except Exception:
        margin_map = {}

    rows = []
    total_market_value = 0.0
    total_cost_basis = 0.0
    total_unrealized = 0.0
    for d in qs:
        current_price = _fetch_current_price(d.stock_symbol) if fetch_price else None
        pos = _position_metrics(d, current_price)
        valuation = _fetch_valuation(d.stock_symbol) if fetch_valuation else None

        if pos['market_value'] is not None:
            total_market_value += pos['market_value']
        if pos['cost_basis'] is not None:
            total_cost_basis += pos['cost_basis']
        if pos['unrealized_profit'] is not None:
            total_unrealized += pos['unrealized_profit']

        rows.append({
            'symbol': d.stock_symbol,
            'name': d.stock_name,
            'sector': d.sector,
            'tags': list(d.tags.values_list('name', flat=True)),
            'quantity': float(d.current_quantity),
            'avg_cost': float(d.average_purchase_price) if d.average_purchase_price else None,
            'realized_profit': float(d.realized_profit),
            'current_price': pos['current_price'],
            'market_value': pos['market_value'],
            'cost_basis': pos['cost_basis'],
            'unrealized_profit': pos['unrealized_profit'],
            'unrealized_profit_rate': pos['unrealized_profit_rate'],
            'valuation': valuation,
            'margin_ratio': margin_map.get(d.stock_symbol),
            # 買った理由（仮説）が記録されているか。0 なら判定前に diary/<symbol>/ で
            # 仮説の有無を確認し、無ければ「エントリー仮説未記録」として扱う。
            'thesis_count': d.thesis_total,
            'open_thesis_count': d.open_thesis_count,
            'latest_disclosure_date': (
                d.latest_disclosure_date.isoformat() if d.latest_disclosure_date else None
            ),
        })

    total_unrealized_rate = (
        (total_unrealized / total_cost_basis * 100) if total_cost_basis else None
    )

    return JsonResponse({
        'count': len(rows),
        'portfolio': {
            'total_market_value': total_market_value,
            'total_cost_basis': total_cost_basis,
            'total_unrealized_profit': total_unrealized,
            'total_unrealized_profit_rate': total_unrealized_rate,
        },
        'positions': rows,
        'fetched_at': datetime.now(tz=dt_timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
    })


# ------------------------------------------------------------------ #
#  書き込みエンドポイント
# ------------------------------------------------------------------ #

_VALID_NOTE_TYPES = {c[0] for c in DiaryNote.TYPE_CHOICES}


@csrf_exempt
@require_http_methods(['POST'])
@_require_analysis_key
def add_note(request, symbol: str):
    """
    継続記録（DiaryNote）を追加する。

    POST /api/analysis/diary/<symbol>/notes/
    Authorization: Bearer <key>
    Content-Type: application/json

    {
      "content":   "分析内容...",          // 必須
      "note_type": "analysis",             // 省略可（デフォルト: analysis）
                                           //   analysis / news / earnings /
                                           //   insight / risk / retrospective / other
      "topic":     "決算後の見直し",       // 省略可（retrospective 以外は任意）
      "date":      "2024-01-15"            // 省略可（デフォルト: 今日）
    }

    書き込み先ユーザーは ANALYSIS_API_USER 環境変数で固定（呼び出し元からは変更不可）。
    """
    user, err = _get_api_user()
    if err:
        return err

    symbol = symbol.upper().strip()
    diary = StockDiary.objects.filter(
        stock_symbol__iexact=symbol, user=user
    ).first()
    if not diary:
        return JsonResponse(
            {'error': f'{symbol} の日記が見つかりません（ユーザー: {user.username}）'},
            status=404,
        )

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'リクエストボディが不正な JSON です'}, status=400)

    content = (body.get('content') or '').strip()
    if not content:
        return JsonResponse({'error': 'content は必須です'}, status=400)
    if len(content) > 5000:
        return JsonResponse({'error': 'content は 5000 文字以内にしてください'}, status=400)

    note_type = (body.get('note_type') or 'analysis').strip()
    if note_type not in _VALID_NOTE_TYPES:
        return JsonResponse(
            {'error': f'note_type が不正です。使用可能: {sorted(_VALID_NOTE_TYPES)}'},
            status=400,
        )

    topic = (body.get('topic') or '').strip()

    raw_date = body.get('date')
    if raw_date:
        try:
            note_date = date.fromisoformat(raw_date)
        except ValueError:
            return JsonResponse({'error': 'date は YYYY-MM-DD 形式で指定してください'}, status=400)
    else:
        note_date = date.today()

    note = DiaryNote(
        diary=diary,
        content=content,
        note_type=note_type,
        topic=topic,
        date=note_date,
    )
    try:
        note.full_clean()
        note.save()
    except ValidationError as e:
        return JsonResponse({'error': str(e)}, status=400)

    synced_tags = _sync_diary_tags(diary, user)

    return JsonResponse({
        'success': True,
        'note_id': note.id,
        'symbol': symbol,
        'diary_name': diary.stock_name,
        'note_type': note.note_type,
        'topic': note.topic,
        'date': note.date.isoformat(),
        'content_length': len(note.content),
        'tags': synced_tags,
    }, status=201)


@csrf_exempt
@require_http_methods(['DELETE'])
@_require_analysis_key
def delete_note(request, symbol: str, note_id: int):
    """
    継続記録（DiaryNote）を1件削除する。

    DELETE /api/analysis/diary/<symbol>/notes/<note_id>/
    Authorization: Bearer <key>

    削除後は reason＋残りノートの和集合でタグを再同期する
    （削除したノートにしか無かった @タグはタグ欄からも解除される）。
    書き込み先ユーザーは ANALYSIS_API_USER 環境変数で固定。
    """
    user, err = _get_api_user()
    if err:
        return err

    symbol = symbol.upper().strip()
    diary = StockDiary.objects.filter(
        stock_symbol__iexact=symbol, user=user
    ).first()
    if not diary:
        return JsonResponse(
            {'error': f'{symbol} の日記が見つかりません（ユーザー: {user.username}）'},
            status=404,
        )

    note = diary.notes.filter(id=note_id).first()
    if not note:
        return JsonResponse(
            {'error': f'ノート(id={note_id}) が {symbol} の日記に見つかりません'},
            status=404,
        )

    note.delete()
    synced_tags = _sync_diary_tags(diary, user)

    return JsonResponse({
        'success': True,
        'symbol': symbol,
        'diary_name': diary.stock_name,
        'deleted_note_id': note_id,
        'tags': synced_tags,
    })


@csrf_exempt
@require_http_methods(['PATCH'])
@_require_analysis_key
def update_reason(request, symbol: str):
    """
    投資理由（reason）を更新する。上書き前の内容は ReasonVersion に自動退避される。

    PATCH /api/analysis/diary/<symbol>/
    Authorization: Bearer <key>
    Content-Type: application/json

    {
      "reason": "更新後の投資理由テキスト..."  // 必須
    }

    書き込み先ユーザーは ANALYSIS_API_USER 環境変数で固定（呼び出し元からは変更不可）。
    """
    user, err = _get_api_user()
    if err:
        return err

    symbol = symbol.upper().strip()
    diary = StockDiary.objects.filter(
        stock_symbol__iexact=symbol, user=user
    ).first()
    if not diary:
        return JsonResponse(
            {'error': f'{symbol} の日記が見つかりません（ユーザー: {user.username}）'},
            status=404,
        )

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'リクエストボディが不正な JSON です'}, status=400)

    new_reason = (body.get('reason') or '').strip()
    if not new_reason:
        return JsonResponse({'error': 'reason は必須です'}, status=400)

    old_reason = diary.reason or ''
    diary.reason = new_reason

    try:
        diary.full_clean(exclude=['user', 'stock_name'])
    except ValidationError as e:
        return JsonResponse({'error': str(e)}, status=400)

    diary.save(update_fields=['reason', 'updated_at'])
    snapshot = ReasonVersion.snapshot_on_change(diary, old_reason)

    synced_tags = _sync_diary_tags(diary, user)

    return JsonResponse({
        'success': True,
        'symbol': symbol,
        'diary_name': diary.stock_name,
        'reason_updated': True,
        'snapshot_created': snapshot is not None,
        'reason_length': len(new_reason),
        'tags': synced_tags,
    })


_VALID_HORIZONS = {c[0] for c in Thesis.HORIZON_CHOICES}


@csrf_exempt
@require_http_methods(['POST'])
@_require_analysis_key
def add_thesis(request, symbol: str):
    """
    投資仮説（Thesis）を1件作成する＝「買った理由」と「崩れる条件(worst_case)」を構造化する。

    POST /api/analysis/diary/<symbol>/thesis/
    Authorization: Bearer <key>
    Content-Type: application/json

    {
      "claim":       "この投資で賭けている命題",      // 必須（最大500字）
      "worst_case":  "これが起きたら仮説は崩れる（＝損切り/縮小条件）",  // 任意（最大300字）
      "basis":       "なぜ成り立つと考えるか",         // 任意（最大1000字）
      "horizon":     "6m",                          // 任意（next_earnings/3m/6m/1y/long。既定 6m）
      "review_due_date": "2026-08-26"               // 任意。省略時は horizon から自動補完（UIと同じ導出）
    }

    review_due_date がホーム想起（答え合わせ待ちの仮説）を駆動する。省略時は
    views_growth._default_review_due_date（UIと同一ロジック）で補完する。
    書き込み先ユーザーは ANALYSIS_API_USER 環境変数で固定。
    ※ Thesis.claim/worst_case は @タグ同期の対象外（diary.tags は reason＋notes の和集合）。
    """
    user, err = _get_api_user()
    if err:
        return err

    symbol = symbol.upper().strip()
    diary = StockDiary.objects.filter(
        stock_symbol__iexact=symbol, user=user
    ).first()
    if not diary:
        return JsonResponse(
            {'error': f'{symbol} の日記が見つかりません（ユーザー: {user.username}）'},
            status=404,
        )

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'リクエストボディが不正な JSON です'}, status=400)

    claim = (body.get('claim') or '').strip()
    if not claim:
        return JsonResponse({'error': 'claim（主張）は必須です'}, status=400)

    horizon = (body.get('horizon') or '6m').strip()
    if horizon not in _VALID_HORIZONS:
        return JsonResponse(
            {'error': f'horizon が不正です。使用可能: {sorted(_VALID_HORIZONS)}'},
            status=400,
        )

    review_due_date = None
    raw_due = body.get('review_due_date')
    if raw_due:
        try:
            review_due_date = date.fromisoformat(raw_due)
        except ValueError:
            return JsonResponse(
                {'error': 'review_due_date は YYYY-MM-DD 形式で指定してください'}, status=400
            )

    thesis = Thesis(
        diary=diary,
        claim=claim,
        basis=(body.get('basis') or '').strip(),
        worst_case=(body.get('worst_case') or '').strip(),
        horizon=horizon,
        review_due_date=review_due_date,
    )
    try:
        thesis.full_clean(exclude=['review_due_date'])
    except ValidationError as e:
        return JsonResponse({'error': str(e)}, status=400)

    if not thesis.review_due_date:
        # UI と同じ導出（初回購入日 or 今日を基準に horizon 日数を加算）
        from .views_growth import _default_review_due_date
        thesis.review_due_date = _default_review_due_date(diary, thesis.horizon)

    thesis.save()

    return JsonResponse({
        'success': True,
        'symbol': symbol,
        'diary_name': diary.stock_name,
        'thesis_id': thesis.id,
        'claim': thesis.claim,
        'worst_case': thesis.worst_case,
        'horizon': thesis.get_horizon_display(),
        'status': thesis.get_status_display(),
        'review_due_date': thesis.review_due_date.isoformat() if thesis.review_due_date else None,
        'is_due': thesis.is_due,
    }, status=201)
