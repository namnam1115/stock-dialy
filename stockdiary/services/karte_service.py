# stockdiary/services/karte_service.py
"""投資家カルテ（自己理解）の集計サービス。

成績評価ではなく「自分はどういう投資家か」を言語化して返すための集計。
Verdict（検証）を母数に、意思決定の質×結果の2×2分布、テーマ別の的中傾向、
繰り返す見落とし、再現したい学び（投資哲学）を組み立てる。

損益計算（AggregateService）には依存せず、意思決定の質の軸で集計する。
"""
from collections import defaultdict

from ..models import Verdict
from . import metrics

# 2×2の象限メタ（並び順・ラベル・軸は services.metrics が唯一の正本）
QUADRANTS = metrics.QUADRANTS


def build_investor_karte(user):
    """ユーザーの全 Verdict から自己理解レポートを構築する。"""
    verdicts = list(
        Verdict.objects
        .filter(thesis__diary__user=user, thesis__diary__is_excluded=False)
        .select_related('thesis', 'thesis__diary')
        .prefetch_related('thesis__basis_tags')
        .order_by('-created_at')
    )
    total = len(verdicts)

    quad_counts = {key: 0 for key, _, _ in QUADRANTS}
    hyp_hit = 0          # 仮説が当たり（的中・部分的中）
    total_wins = 0       # 損益が利益
    lucky_wins = 0       # 利益だが仮説は外れ（偶然の勝ち）
    learnings = []
    missed = []
    tag_stats = {}       # tag_id -> {name, total, hit}

    for v in verdicts:
        quad_counts[v.quadrant] += 1
        if v.hyp_ok:
            hyp_hit += 1
        if v.pnl_ok:
            total_wins += 1
            if not v.hyp_ok:
                lucky_wins += 1
        if v.learning:
            learnings.append({
                'text': v.learning,
                'diary': v.thesis.diary,
                'repeatable': v.is_repeatable,
                'date': v.created_at,
                'quadrant': v.quadrant,
                'quadrant_label': v.quadrant_label,
            })
        if v.missed_factor:
            missed.append({
                'text': v.missed_factor,
                'diary': v.thesis.diary,
                'date': v.created_at,
            })
        for tag in v.thesis.basis_tags.all():
            st = tag_stats.setdefault(tag.id, {'name': tag.name, 'total': 0, 'hit': 0, 'q_sum': 0})
            st['total'] += 1
            st['q_sum'] += (v.decision_quality or 0)
            if v.hyp_ok:
                st['hit'] += 1

    quadrants = [
        {'key': key, 'label': label, 'axis': axis, 'count': quad_counts[key]}
        for key, label, axis in QUADRANTS
    ]

    hit_rate = round(hyp_hit / total * 100) if total else None
    # 意思決定の質と損益の乖離: 勝ちのうち「偶然（仮説外れ）」が占める割合
    lucky_share = round(lucky_wins / total_wins * 100) if total_wins else None

    # テーマ別の的中傾向（最小検証件数以上のタグのみ）。得意・苦手に分ける
    theme_rows = []
    for st in tag_stats.values():
        if st['total'] >= metrics.THEME_MIN_VERDICTS:
            rate = round(st['hit'] / st['total'] * 100)
            stars = round(st['q_sum'] / st['total'], 1)
            # バー幅は判断の質（★/5）。得意・苦手が量だけでなく質で見えるように
            bar_pct = max(6, round(stars / 5 * 100))
            theme_rows.append({'name': st['name'], 'total': st['total'], 'hit': st['hit'],
                               'rate': rate, 'stars': stars, 'bar_pct': bar_pct})
    theme_rows.sort(key=lambda x: (x['rate'], x['total']), reverse=True)
    strong_themes = [t for t in theme_rows if t['rate'] >= metrics.HIT_RATE_STRONG][:5]
    weak_themes = [t for t in theme_rows if t['rate'] < metrics.HIT_RATE_WEAK][:5]

    # 見落とし（最近5件）。文字列としての完全一致でカウントするのではなく、
    # 「表記が近い過去の見落としがあるか」を控えめな目印として添えるだけにする
    # （自然言語の自由記述を機械が正確に数えるのは無理があるため、断定を避け
    # 気づきの目印に留める。判断は本人に委ねる）。どの銘柄・いつの見落としかが
    # 分からないと気づきに使えないため、each entry に diary/date を持たせ、
    # 「似ている」と判定した過去の見落とし自体も similar に含めて実際に見られるようにする
    recent_misses = [
        {
            'text': m['text'],
            'diary': m['diary'],
            'date': m['date'],
            'similar': [
                other for j, other in enumerate(missed)
                if j != i and metrics.is_similar_miss(m['text'], other['text'])
            ],
        }
        for i, m in enumerate(missed[:5])
    ]

    # 投資哲学＝再現したい学び（なければ全学びの新しい順）
    philosophy = [l for l in learnings if l['repeatable']][:8]
    if not philosophy:
        philosophy = learnings[:8]

    # 自己診断（言葉で返す一文）。集計から機械的に組み立てる
    diagnosis = _build_diagnosis(total, hit_rate, lucky_share, quad_counts,
                                 strong_themes, weak_themes)

    # 成長の軌跡（月別集計）
    growth = _build_growth_trajectory(verdicts)

    return {
        'total': total,
        'has_content': total > 0,
        'diagnosis': diagnosis,
        'quadrants': quadrants,
        'hit_rate': hit_rate,
        'total_wins': total_wins,
        'lucky_wins': lucky_wins,
        'lucky_share': lucky_share,
        'strong_themes': strong_themes,
        'weak_themes': weak_themes,
        'recent_misses': recent_misses,
        'philosophy': philosophy,
        'growth': growth,
    }


def _build_diagnosis(total, hit_rate, lucky_share, quad_counts, strong_themes, weak_themes):
    """検証の集計から、自己診断の短い2文を機械的に組み立てる（言葉で返す）。"""
    if not total:
        return []
    lines = []

    parts = []
    if hit_rate is not None:
        if hit_rate >= metrics.HIT_RATE_STRONG:
            parts.append('あなたは仮説をよく当てる')
        elif hit_rate <= metrics.HIT_RATE_WEAK:
            parts.append('仮説はまだ外しがちだが')
        else:
            parts.append('仮説の的中は五分')
    if lucky_share is not None and lucky_share >= metrics.LUCKY_WIN_SHARE_ALERT:
        parts.append('勝ちには「偶然」が混じる')
    elif quad_counts.get('unlucky', 0) >= quad_counts.get('skill', 0) and quad_counts.get('unlucky', 0) > 0:
        parts.append('正しくても報われないことが多い')
    elif quad_counts.get('discipline', 0) >= quad_counts.get('skill', 0) and quad_counts.get('discipline', 0) > 0:
        parts.append('読みが外れたときは規律を守れている')
    if parts:
        lines.append('、'.join(parts) + '。')

    if strong_themes and weak_themes:
        lines.append(f'得意は「{strong_themes[0]["name"]}」、苦手は「{weak_themes[0]["name"]}」。')
    elif strong_themes:
        lines.append(f'得意は「{strong_themes[0]["name"]}」。')
    elif weak_themes:
        lines.append(f'苦手は「{weak_themes[0]["name"]}」。')

    return lines


def _build_growth_trajectory(verdicts):
    """月別の判断の質スコアと仮説的中率を集計して成長グラフ用データを返す。

    Chart.js に渡せる形式（labels, quality, hit_rate のリスト）で返す。
    2ヶ月未満のデータでは has_chart=False にして graceful degradation する。
    """
    monthly = defaultdict(lambda: {
        'quality_sum': 0, 'count': 0, 'hits': 0, 'learnings': 0
    })

    for v in verdicts:
        # created_at は UTC で保存されているため localtime に変換して集計
        from django.utils import timezone as tz
        local_dt = tz.localtime(v.created_at)
        key = local_dt.strftime('%Y-%m')
        monthly[key]['quality_sum'] += v.decision_quality or 0
        monthly[key]['count'] += 1
        if v.hyp_ok:
            monthly[key]['hits'] += 1
        if v.learning:
            monthly[key]['learnings'] += 1

    sorted_keys = sorted(monthly.keys())

    # ラベル: 最初の月は「YYYY年M月」、以降は「M月」で省略（横幅を節約）
    labels = []
    for i, k in enumerate(sorted_keys):
        y, m = int(k[:4]), int(k[5:])
        if i == 0:
            labels.append(f'{y}年{m}月')
        else:
            labels.append(f'{m}月')

    quality = [
        round(monthly[k]['quality_sum'] / monthly[k]['count'], 1)
        for k in sorted_keys
    ]
    hit_rates = [
        round(monthly[k]['hits'] / monthly[k]['count'] * 100)
        for k in sorted_keys
    ]
    total_learnings = sum(monthly[k]['learnings'] for k in sorted_keys)

    return {
        'labels': labels,
        'quality': quality,
        'hit_rates': hit_rates,
        'total_learnings': total_learnings,
        'has_chart': len(sorted_keys) >= 2,
        'latest_quality': quality[-1] if quality else None,
        'latest_hit_rate': hit_rates[-1] if hit_rates else None,
    }
