"""意思決定の質に関する指標の「意味」を定義する単一の正本（セマンティックレイヤー）。

成長OS（docs/growth_os_redesign.md）の検証ループは「意思決定の質 ≠ 損益」を前提に、
仮説の当否（hypothesis_result）と損益（pnl_result）を別々に評価する。その
**「何をもって的中とするか」「何をもって勝ちとするか」「どの象限に分類するか」**
といった指標の定義は、従来 Verdict モデルと karte_service に分散し、象限ラベルは
2箇所に重複、閾値は説明のない数値リテラルとして散在していた。

このモジュールはそれらを1箇所に集約し、AI・人間のどちらが触っても定義が
ブレない（取り違えない）ようにする。**モデルや DB には依存しない純粋関数・定数のみ**
で構成し、models / services のどちらからも安全に参照できる。

注意: ここで定義する「勝ち」は *意思決定の質* の系統（Verdict ベース）であり、
tags の *実現損益* の勝率（AggregateService の realized_profit > 0）とは
**別の指標**である。両者を同じ語で語らないこと。
"""
import difflib

# ---------------------------------------------------------------------------
# 仮説の当否（hypothesis_result）と損益（pnl_result）の意味づけ
# 文字列値そのものは Verdict.HYP_* / PNL_* と一致させる（データ層は model 側が正、
# 「どの値を的中/勝ちと見なすか」という意味づけは本モジュールが正）。
# ---------------------------------------------------------------------------

#: 仮説が「当たった」と見なす hypothesis_result の集合（的中・部分的中）
HYPOTHESIS_HIT_RESULTS = frozenset({'hit', 'partial'})

#: 損益が「勝ち」と見なす pnl_result（利益のみ。flat/holding は勝ちに含めない）
PNL_WIN_RESULT = 'profit'


def is_hypothesis_hit(hypothesis_result):
    """仮説が当たり（的中・部分的中）か。"""
    return hypothesis_result in HYPOTHESIS_HIT_RESULTS


def is_pnl_win(pnl_result):
    """損益が勝ち（利益）か。"""
    return pnl_result == PNL_WIN_RESULT


# ---------------------------------------------------------------------------
# 意思決定の質 × 結果 の 2×2 象限タクソノミ（表示順・ラベル・軸の唯一の定義）
# ---------------------------------------------------------------------------

#: (key, label, axis) を表示順で持つ。key が分類の安定識別子。
QUADRANTS = (
    ('skill',      '再現すべき勝ち',     '仮説◯ × 利益'),
    ('unlucky',    '正しいが報われず',   '仮説◯ × 損失'),
    ('lucky',      '偶然の勝ち（要注意）', '仮説× × 利益'),
    ('discipline', '想定通りの負け',     '仮説× × 損失'),
)

#: key -> label の辞書（QUADRANTS から導出。重複定義を作らない）
QUADRANT_LABELS = {key: label for key, label, _axis in QUADRANTS}


def quadrant_of(hyp_ok, pnl_ok):
    """仮説の当否(bool) × 損益の勝ち(bool) から象限 key を返す。"""
    if hyp_ok and pnl_ok:
        return 'skill'        # 仮説◯×利益: 再現せよ
    if hyp_ok and not pnl_ok:
        return 'unlucky'      # 仮説◯×損失: 運/握力（学び: 継続の是非）
    if not hyp_ok and pnl_ok:
        return 'lucky'        # 仮説×××利益: 偶然（危険）
    return 'discipline'       # 仮説×××損失: 想定通りの失敗（学び: 撤退の妥当性）


# ---------------------------------------------------------------------------
# 集計の閾値（karte_service が参照する判定基準。意味を名前で固定する）
# ---------------------------------------------------------------------------

#: 的中率がこの値以上なら「得意（仮説をよく当てる）」とみなす [%]
HIT_RATE_STRONG = 60

#: 的中率がこの値未満（診断では以下）なら「苦手（外しがち）」とみなす [%]
HIT_RATE_WEAK = 40

#: 勝ちのうち「偶然（仮説外れ）」がこの割合以上なら警告する [%]
LUCKY_WIN_SHARE_ALERT = 40

#: テーマ別の的中傾向を集計対象にする最小検証件数
THEME_MIN_VERDICTS = 2

#: 見落とし（missed_factor）の類似判定のしきい値 [0.0-1.0]。
#: difflib.SequenceMatcher.ratio() による表記の近さのみを見る（意味の近さではない）。
#: 「何回目」という断定はせず、「似た内容がある」という控えめな気づきの目印に留める。
MISS_SIMILARITY_THRESHOLD = 0.6


def is_similar_miss(a, b):
    """2つの見落とし（missed_factor）の表記が近いか（意味の類似ではない）。"""
    return difflib.SequenceMatcher(None, a, b).ratio() >= MISS_SIMILARITY_THRESHOLD
