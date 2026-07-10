# kabu-log 再設計：投資家の成長OS

> North Star: **10年後の自分が、今日の自分の投資判断を正確に理解できる。**

kabu-log は「投資判断のカルテを蓄積し、未来の自分が過去の自分を診察するための成長OS」。
記録するのは銘柄ではなく**意思決定**。評価するのは損益ではなく**意思決定の質**。
蓄積されるのは履歴ではなく**再現可能な学び**。

参考：Day One / Readwise / Bear / Obsidian / Notion。
反面教師：証券会社・マネフォ・TradingView（数値分析ツールにしない）。

---

## 中核アーキテクチャ（大規模リプレイスをしない解）

理想構造は「学び → 記録 → 銘柄」。だが保存は既存の銘柄起点(StockDiary)を壊さない。
→ **保存は銘柄起点のまま、上に"学びレイヤー"を一枚乗せ、ホーム/ライブラリの主役を差し替える。**

```
[ 想起・検索・成長の主役 ]   ← 学びレイヤー（Thesis / Verdict / Learning索引）
        │ points to
[ 記録の実体 ]              ← 既存: StockDiary, DiaryNote(retrospective), Tag(axis), reason
        │
[ 損益の計算 ]              ← 既存: AggregateService（不変）
```

### 検証ループ（成長OSの心臓）

```
予想(Thesis) → 結果(損益) → 検証(Verdict) → 学び(Learning)
```

**意思決定の質 ≠ 損益。** Verdict は仮説の当否(hypothesis_result)と損益(pnl_result)を
別フィールドで持ち、その2×2を可視化する：

|              | 損益 +              | 損益 −                  |
|--------------|---------------------|-------------------------|
| 仮説 当たり  | skill 再現せよ       | unlucky 正しいが報われず |
| 仮説 外れ    | lucky 偶然（危険）   | discipline 想定通りの負け |

「仮説正しい×損失」「仮説外れ×利益」を一級市民にすることが本質。

### データモデル（新規は最小2つ）

- `Thesis`（1:1 StockDiary）: claim / basis_tags / horizon / worst_case /
  review_due_date / status(open→verified→abandoned)。`is_due` がホーム想起の駆動源。
- `Verdict`（1:1 Thesis）: hypothesis_result × pnl_result / decision_quality(1–5) /
  missed_factor / is_repeatable / learning。`quadrant` で2×2を返す。
- **中間検証（2026-07）**: `pnl_result=holding`（保有中）の答え合わせは Thesis を
  verified に**閉じない**。status=open のまま `review_due_date` を先送りし、確定損益
  （profit/loss/flat）の答え合わせで初めて閉じる。保有継続中こそ想起が必要であり、
  閉じると `is_due` の母集団から外れて二度と想起されないため。UIバッジは「見張り中」。
- 「学び」は当面 `Verdict.learning` ＋ 既存 high-importance/retrospective ノートの集約で表現
  （モデルを増やさない）。

---

## 4画面の役割再定義

| 画面 | 役割 | 動詞 | 主オブジェクト |
|---|---|---|---|
| ホーム | 想起 | 思い出す | 検証期日・1年前・学び（損益は出さない） |
| ライブラリ | 整理・再利用 | 探す | 学び／テーマ／仮説（時系列は補助） |
| 詳細 | 学習（カルテ） | 診察する | 本文→仮説→結果→検証→学び |
| 関連グラフ | 探索 | つなぐ | テーマ／学びのネットワーク |

詳細の表示順は **本文 ＞ 仮説 ＞ 結果 ＞ 検証 ＞ 学び ＞ 関連 ＞ 銘柄情報 ＞ 損益**。

---

## 段階的リリース計画

| Phase | 内容 | 状態 |
|---|---|---|
| 8a 検証ループ(核) | Thesis/Verdict＋詳細カルテ表示＋検証フロー(HTMX)＋2×2 | ✅ 実装済 |
| 8b 想起ホーム | RecallService拡張（検証期日が来た仮説）＋home「答え合わせを待つ仮説」 | ✅ 実装済 |
| 8c ライブラリ | 学び/テーマ/仮説軸の知識アーカイブ（/library/） | ✅ 実装済 |
| 8d 投資家カルテ | 2×2分布・乖離・得意/苦手・繰り返す失敗・哲学（/karte/） | ✅ 実装済 |
| 8e 継続装置 | 検証期日Push（NotificationService/django-q）・月次レビュー・学び累積 | ✅ 実装済（`tasks.py` の `setup_notification_schedule`＝検証期日Push日次・`setup_monthly_review_schedule`＝月次を `apps.py` `ready()` で django-q に登録。通知本体は `notification_service.process_thesis_due_notifications` 他）。※2026-07 改善ループ R3 で実コード確認・doc同期 |

---

## 継続利用の本質

損益は変動するので一喜一憂＝離脱を生む。**判断の質と学びの蓄積は単調増加**するので、
開くほど価値が増す＝10年使える。継続の核は「損益のリマインド」ではなく
**「検証期日＝答え合わせ」という能動トリガー**（Readwise の spaced repetition の投資版）。

---

## 技術メモ

- Django + Templates + Bootstrap + HTMX。新規アプリは作らず `stockdiary` 内に最小増設。
- デザインは `static/css/9-reading/`（reading-tokens / reading-journal / reading-karte）。
  墨×紙・セリフ本文・余白＞罫線＞影。2×2のみ4象限の淡色を許可。
- `AggregateService`（FIFO損益）には一切触れない＝意思決定の質と損益を疎結合に保つ。
