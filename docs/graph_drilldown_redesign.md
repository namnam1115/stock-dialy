# 関連グラフ・ドリルダウン探索の再設計（TG-DD）

**状態：v1 実装済み（2026-07-05・§8 決定に沿って実装／pytest 838 green・実HTTP経路検証 PASS）。
残：ブラウザ実機スクショ（run-app スキルが web サンドボックス専用のためローカルMacで未自動化）。**
起票：改善ループ R9（`docs/improvement_loop.md` §3 TG-DD）。信号源③（実運用の摩擦）。

### 実装物（v1）
- View：`stockdiary/views_dashboard.py::ExploreGraphView`（`/stockdiary/explore/graph/`）
- API：`stockdiary/api_views.py::graph_neighbors`（`/stockdiary/api/graph/neighbors/`）
- 近傍導出（純粋関数・テスト可）：`stockdiary/utils.py::get_graph_neighbors`
- Template：`stockdiary/templates/stockdiary/explore_graph.html`
- JS：`static/js/explore-graph.js`（d3・独立。可視ノードは `expanded` 集合から再計算＝再タップで子孫も畳む）
- Tests：`tests/test_relation_graph.py::TestGraphNeighbors`・`tests/test_api_views.py::TestGraphNeighborsAPI`

---

## 1. 背景・課題（なぜ作り替えるか）

現行の関連グラフ（`diary_graph` / `diary-graph.js`）は **全ノードを最初に一度に描く**。
実運用でユーザーが挙げた摩擦：

1. **全件表示に意味がほぼない** — 開いた瞬間に全部出るので、見たい要素が埋もれる。
2. **ノード過多で視認性が低い** — 銘柄・タグが多いほど密集して探せない。
3. 実際にやりたいのは **1つの要素を起点にした深掘り（ドリルダウン）**。

R6 で G1（検索フォーカス）・G4（タグ控えめ）を入れて緩和したが、**「全部見せてから絞る」
という前提自体**が課題の根。本再設計は前提を反転し、**「空から始めて、選んだ所だけ広げる」**
progressive disclosure にする。

### 重要な設計上の反転（R7→R9）
R7 では「タグ親子は接続を紛らわしくする」としてフラットグラフから親子エッジを撤去した。
しかしそれは **"一度に全部線を引く"** から紛らわしかっただけで、**"畳んで、タップで開く"**
なら階層はむしろ探索の背骨になる。よって本再設計は**宣言済み親子タグ（`Tag.parent`）を
ドリルダウンの階層ソースとして積極利用**する（親子編集UIは温存＝TP3 撤回）。

---

## 2. 探索モデル（相互作用）

```
[要素を検索 / 選ぶ]        ← 初期状態はこれだけ（キャンバスは空）
      │  「半導体」を選択
      ▼
   ● 半導体            （選んだ要素だけが中央に出て、1段だけ展開）
   ├─ ○ AI半導体        子タグ（Tag.parent = 半導体）
   ├─ ○ 半導体装置      子タグ
   ├─ ▪ 6758 ソニー     半導体タグの付いた銘柄
   └─ ▪ 8035 東エレ
           │  8035 をタップ
           ▼
      8035 の隣接（＝この銘柄に付いた別の要素）を1段展開
      ├─ ○ AI
      └─ ○ 製造装置 …
```

- **ノードの種類**：`tag`（要素）／`stock`（銘柄＝ユーザーの同一銘柄の日記群を1ノードに集約）。
- **隣接の定義**：
  - `tag` ノードの隣接 ＝ **子タグ**（`Tag.parent = self`）＋ **親タグ**（`self.parent`）＋
    **そのタグが付いた銘柄**（distinct symbol）
  - `stock` ノードの隣接 ＝ **その銘柄に付いた全タグ**
- **操作**：ノードをタップ＝隣接を1段展開。もう一度タップ＝畳む（子孫も畳む）。
  展開していないノードは出さない。→ 画面には「今たどっている文脈」だけが残る。
- **v1 の「要素」= タグに限定**（業種＝sector ハブは対象外。将来拡張）。

---

## 3. データの形

ノード識別子：
- タグ：`tag:<pk>`
- 銘柄：`stock:<symbol>`（ユーザー内で symbol 単位に集約）

### 展開挙動：オンデマンド近傍API（提案）
全件を先に読まず、**タップ時にそのノードの近傍だけ**を返すAPIを新設する
（「全部読んで隠す」より、過密の根＝データ量に強い）。

```
GET /stockdiary/api/graph/neighbors/?node=tag:123
→ {
    "node": {"id":"tag:123","type":"tag","label":"半導体","axis":"theme"},
    "neighbors": [
      {"id":"tag:130","type":"tag","label":"AI半導体","rel":"child"},
      {"id":"tag:99","type":"tag","label":"（親があれば）","rel":"parent"},
      {"id":"stock:6758","type":"stock","label":"ソニーG","rel":"tagged"},
      ...
    ]
  }

GET /stockdiary/api/graph/neighbors/?node=stock:6758
→ そのユーザーの 6758 日記群に付いた全タグを neighbors(type=tag, rel="tag") で返す
```

- 返却は `ANALYSIS_API_USER` ではなく **ログインユーザー**のデータ（通常のセッション認証。
  分析APIとは別系統）。
- ノイズ抑制：`diary_count` が極端に多いタグ（現行 `_TAG_NOISE_MAX` 相当）や
  `axis in (event, custom)` は隣接から除外 or 弱表示（現行グラフの方針を踏襲）。
- **代替案**：ユーザーのデータは有界なので「起動時に隣接辞書を一括ロード→クライアントで
  展開/畳み」でもよい（round-trip 無し・実装単純）。**どちらにするかは §8 の要確認**。

### 空状態の入口
- **検索ボックス**：ユーザーの**タグ（要素）と銘柄の両方**を埋め込み（`explore_items`・json_script）、
  クライアント側で部分一致検索。タグは名前、銘柄は**名前＋コード（symbol）**で引ける。
- 選んだ要素/銘柄を中央に置き 1 段展開してドリルダウン開始。
  - タグ起点 → 子タグ・親タグ・銘柄へ。銘柄起点 → その銘柄のタグへ（近傍APIは元から両対応）。
- 「よく使う要素リスト」は v1 では出さない（検索のみ）。

> **拡張（2026-07-05・R10後）**：当初「起点＝タグのみ」だったが、ユーザー提案で**銘柄からも開始可能**に。
> 近傍APIは元から `stock:` ノードを処理できたため、入口（`ExploreGraphView` の埋め込み＋JS検索）に
> 銘柄を足すだけで実現。回帰テスト `tests/test_api_views.py::TestExploreGraphPage`。

---

## 4. 実装構成（新ビュー・既存 diary_graph は不変）

| 種類 | 追加/変更 | 内容 |
|------|-----------|------|
| URL | 追加 | `stockdiary/urls.py`：`explore/graph/`（ページ）・`api/graph/neighbors/`（近傍） |
| View | 追加 | `views_dashboard.py` に `ExploreGraphView`（テンプレ描画のみ・軽い） |
| API | 追加 | `api_views.py` に `graph_neighbors`（近傍JSON）。近傍導出は `utils.py` にヘルパー関数化 |
| Template | 新規 | `stockdiary/templates/stockdiary/explore_graph.html`（最小コントロール：検索・リセット・戻る） |
| JS | 新規 | `static/js/explore-graph.js`（d3・小さく新規。既存 `diary-graph.js` は流用せず独立） |

- 既存 `diary_graph`／`get_tag_graph_data`／`diary-graph.js` は**触らない**（共存）。
- CLAUDE.md 準拠：Python は既存ファイル（`views_dashboard.py`・`api_views.py`・`utils.py`）に
  追記。テンプレ/JS は新規UI資産として新設（責務が明確なので可）。
- 近傍導出は `utils.py` に純粋関数（`get_tag_neighbors(user, node)` 等）で置き、テスト可能にする。

### TP1（フラットグラフの親子エッジ撤去）との関係
TP1 は **diary_graph（全部見せる版）** の話。本 explore は**親子を"展開時の隣接"として使う**別レイヤ
なので矛盾しない。diary_graph はフラットのまま、explore はドリルダウン。

---

## 5. スコープ

**v1（この再設計）**
- タグ要素のドリルダウン（子/親タグ＋銘柄、銘柄→タグ）。
- 空状態の入口（検索＋起点候補）。タップ展開/畳み。最小コントロール。

**v1 非対象（将来）**
- 業種（sector）要素、@メンション・手動リンク等の他エッジ種、方向（追い風/向かい風）色分け、
  複数起点の同時探索、保存/共有。

---

## 6. 前提データ

半導体→AI半導体 のような階層は**宣言済み親子タグ**が必要。編集UI（`TagForm` の parent）は
温存済みなので、ユーザーがタグ管理で親子を設定すれば反映される。**親子が無い要素は銘柄だけ
展開**され、探索は成立する（階層はあれば深く、無くてもフラットに辿れる）。

---

## 7. 検証計画
- 近傍導出ヘルパーの単体テスト（`tests/test_relation_graph.py` 近傍に追記）：
  タグ→子/親/銘柄、銘柄→タグ、ノイズ除外。
- `graph_neighbors` API のビューテスト（`tests/test_api_views.py`）：ログインユーザー固定・JSON形。
- `run-app`（`create_realistic_test_data` シード）で実機ドリルダウンをスクショ確認。

---

## 8. 決定事項（レビュー済み・2026-07-05）

1. **展開方式**＝**オンデマンド近傍API**（`/stockdiary/api/graph/neighbors/`）。全件は読まない。
2. **入口**＝**検索ボックスのみ**。ユーザーの自タグを埋め込み（json_script）→クライアント側で
   部分一致検索。選択でそのタグを起点に1段展開。「よく使う要素リスト」は v1 では出さない。
3. **畳み挙動**＝**再タップで畳む（子孫も再帰的に畳む）**。
4. **銘柄ノードのクリック**＝**展開＋日記詳細への遷移導線**。近傍APIの stock ノードに
   `detail_url`（代表 diary の詳細）を含め、サイドパネル or ノードの導線から開ける。
5. **ノイズ除外**＝**explore でも同じ**。タグは `axis in (event, custom)` を除外、
   `df`（出現銘柄数）> `RELATED_NOISE_MAX(=25)` を除外（既存グラフの方針を踏襲）。
   ※「1銘柄のみ＝孤立」の <2 除外は全件版の混雑対策なので、ドリルダウンでは適用しない
   （選んで辿る文脈では 1 銘柄の要素も見せる価値がある）。
```
