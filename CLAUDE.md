# CLAUDE.md

Claude Code（claude.ai/code）がこのリポジトリで作業する際のガイダンス。

---

## プロジェクト概要

**Stock-Dialy（カブログ）** は Django 製の株式投資日記 Web アプリ。
ユーザーは銘柄ごとに「なぜ投資したか」「取引」「振り返り」を記録する。
FIFO 損益集計・AI分析・EDINET（日本の開示 API）連携・Web Push 通知を含む。

プロダクトの背景・機能開発の判断軸 → `docs/product_context.md` を参照。
ドメイン概念（エンティティの意味・関係・混同禁止の概念対）の正本 → `docs/ontology.md` を参照。

---

## コマンド

### 開発環境セットアップ
```bash
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # 認証情報を記入
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

### テスト
```bash
# 全テスト
pytest

# 特定ファイル
pytest tests/test_models_updated.py

# 特定テスト
pytest tests/test_models_updated.py::TestClassName::test_method_name

# マーカー指定
pytest -m unit
pytest -m integration
pytest -m django_db
```

設定: `config.test_settings` / `--nomigrations` / `--reuse-db` / カバレッジ対象: `stockdiary`, `analysis_template`, `tags`

### データベース
```bash
python manage.py makemigrations
python manage.py migrate
python manage.py sqlmigrate <app> <migration>
```

### 本番
```bash
gunicorn config.wsgi:application --bind 0.0.0.0:8000
python manage.py collectstatic
```

本番環境の設定例は `etc/systemd/` と `etc/cron.d/` を参照。

---

## アーキテクチャ

### アプリ構成

| アプリ | 役割 |
|--------|------|
| `stockdiary` | コア: 日記・取引・ノート・損益集計 |
| `earnings_analysis` | 企業財務分析（Gemini AI + EDINET）URL prefix: `/copomo/`。スタンドアロンUI（CopoMo）は**おまけツール**であり本ツールの機能開発対象外。ただし EDINET 連携のデータ層（`DisclosureSync` 等）はカブログの想起機能の基盤として本体扱い（→ `docs/improvement_plan.md` 論点1） |
| `analysis_template` | 再利用可能な分析フレームワーク |
| `company_master` | 銘柄・企業マスタ |
| `tags` | 日記へのタグ付け |
| `users` | カスタム `CustomUser`（メール認証・Google OAuth） |
| `margin_tracking` | 信用取引の独立追跡 URL namespace: `margin_tracking` |
| `security` | レート制限・IP フィルタリングミドルウェア |
| `ads`, `contact`, `maintenance` | 運用系 |
| `utils`, `common` | 共有 Mixin・ヘルパー |

### コアデータフロー

`StockDiary` が中心モデル。ユーザー×銘柄で 1 レコード。
保有数・損益などの集計フィールドを `StockDiary` 自身が保持し、
`Transaction` の作成・更新・削除のたびに `AggregateService` が再計算する。

- **FIFO 損益計算**: `stockdiary/services/aggregate_service.py`
- 現物取引と信用取引は別フィールドで追跡（`cash_only_*`）
- `DiaryNote` が保有中の時系列更新を記録
- `linked_diaries` は非対称 M2M（A→B ≠ B→A）
- `StockDiary` には EDINET から定期更新される `latest_disclosure_date` / `latest_disclosure_doc_type_name` フィールドがある

### ⚠️ AggregateService — 集計の不変条件

`StockDiary` の集計値（保有数・損益等）は、取引の変更後に必ず再計算される必要がある。
この不変条件は **構造で守られている**（規約として手動で覚える必要はない）。

**単体操作 → 自動。** `Transaction.save()` / `delete()` が `diary.update_aggregates()`
を内部で呼ぶ（`models.py`）。`views.py` のように1件ずつ操作するコードは、手動で
`recalculate` を呼んではならない（二重再集計になる）。

**一括操作 → `deferred()` で囲む。** `bulk_create` / `bulk_update` /
`QuerySet.update` / `QuerySet.delete` は `save()` を経由せず自動再集計が走らない。
これらは必ず `AggregateService.deferred(diary)` ブロック内で行う。ブロックを抜けると
**必ず1回だけ** 再集計が走る（呼び忘れても集計がずれない）。

```python
from stockdiary.services.aggregate_service import AggregateService

# 一括操作はこの形に統一する（手動 recalculate は不要）
with AggregateService.deferred(diary):
    Transaction.objects.bulk_create([...])
# ここで diary の集計は確定済み（recalculate() 内で diary.save() まで実行される）
```

直接 `AggregateService.recalculate(diary)` を呼ぶのは、取引以外の理由で集計を
作り直す管理コマンド・admin アクションなど、上記2経路に当てはまらない場合のみ。

### サービス層

**`stockdiary/services/`**

| サービス | 役割 |
|----------|------|
| `AggregateService` | 全取引から損益・保有数をゼロから再計算 |
| `metrics`（`metrics.py`） | 意思決定の質の指標の**意味定義の正本**（セマンティックレイヤー）。的中/勝ちの判定・2×2象限タクソノミ・得意/苦手の閾値を1箇所に集約。`Verdict` モデルと `karte_service` が参照する。純粋関数・定数のみ（モデル非依存）。指標を足す/変える際はここを正とする |
| `GeminiStockAnalysis` | Google Gemini AI による銘柄分析 |
| `ImageService` | django-q で非同期 WebP 圧縮（800×600） |
| `NotificationService` | Web Push（VAPID）・アプリ内・メール通知 |

**`earnings_analysis/services/`**

| サービス | 役割 |
|----------|------|
| `EDINETAPIService` | 日本の EDINET 開示書類取得 |
| `AIExpertAnalyzer` | マルチターン AI 分析エンジン |
| `GeminiService` / `GeminiInsights` | Gemini API 呼び出しラッパー |
| `XBRLExtractor` / `XBRLAnalysisService` | XBRL 形式の財務データ抽出 |
| `SentimentAnalyzer` / `LangExtractSentiment` | NLP 感情分析 |
| `PDFProcessor` | PDF テキスト変換 |
| `FinancialAnalyzer` | 財務指標計算 |
| `ComprehensiveAnalyzer` | 総合分析 |
| `BreakoutDetector` | ブレイクアウト検出 |
| `BatchService` | 一括処理 |
| `DisclosureSync` | EDINET 開示情報の同期 |
| `DocumentService` | 書類管理 |

**`common/services/`**（全アプリ共通）

| サービス | 役割 |
|----------|------|
| `YahooFinanceService` | yfinance を用いた株価・財務データ取得。PER・PBR・ROE・配当利回り・時価総額を計算。静的メソッド方式。 |

### 主要な技術的決定

- **django-q**（ORM ブローカー）: Celery/Redis 不要
- **HTMX**: React/Vue なしの動的 UI
- **StockDiary が計算済みフィールドを保持**: オンザフライ計算ではなく取引変更時に再計算
- **カスタムミドルウェア**: IP フィルタ・レート制限・CSP
- 管理画面は `/admin_xyz/`（難読化済み）
- **PWA 対応**: Service Worker（`/sw.js`）・オフラインページ（`/offline/`）

### 外部連携

| サービス | 用途 | 環境変数 |
|----------|------|----------|
| Google Gemini API | AI 銘柄・財務分析 | `GEMINI_API_KEY` |
| EDINET API | 日本の開示書類取得 | `EDINET_API_KEY` |
| yfinance | 株価履歴・財務データ・ニュース | — |
| Google OAuth | 認証（allauth 経由） | `GOOGLE_CLIENT_ID` |
| Web Push（VAPID） | プッシュ通知 | `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_ADMIN_EMAIL` |
| Gmail SMTP | メール送信 | `EMAIL_HOST_PASSWORD` |
| **分析API（自前）** | Claude Code など外部ツール向け読み取り専用API | `ANALYSIS_API_KEY` |

---

## 実際のコードパターン

### ディレクトリ構成の実態

アプリによって構成が異なる。**新規ファイルを追加する前に当該アプリの既存パターンを確認すること。**

```
stockdiary/               ← 単一ファイル方式
  views.py                # メインビュー（日記CRUD・一覧・詳細・取引・ダッシュボード・EDINET・成長OS等）
  views_comparison.py     # 銘柄比較・InvestmentHub
  views_mobile_ux.py      # クイック記録（モバイル向け）
  views_timeline.py       # 全銘柄横断タイムライン
  views_migration.py      # 日記データ移行（インポート/エクスポート）
  views_trade_import.py   # 証券CSV取込（楽天・SBI）— views.py から責務分割
  views_growth.py         # 成長OS（仮説・検証・カルテ・ライブラリ・年次レビュー）— views.py から責務分割
  views_panels.py         # 詳細ページの遅延ロードHTMXパネル（backlinks・EDINET）— views.py から責務分割
  views_dashboard.py      # ダッシュボード・パフォーマンスグラフ・タグ別成績— views.py から責務分割
  views_transactions.py   # 取引・株式分割の CRUD— views.py から責務分割
  api.py                  # 株式情報 API（yfinance）
  api_views.py            # 通知・関連日記・グラフ API
  models.py               # 全モデル（単一ファイル）
  forms.py                # 全フォーム（単一ファイル）
  utils.py                # ヘルパー関数
  admin.py                # Django 管理画面カスタマイズ
  services/               # サービス層（ファイル分割済み）
  templates/stockdiary/
    *.html                # ページテンプレート（home, detail, diary_form, diary_summary, etc.）
    partials/             # HTMX 応答用部分テンプレート
    components/           # 再利用 UI コンポーネント
      _*.html             # アトミックコンポーネント（_ プレフィックス）

earnings_analysis/        ← サブディレクトリ方式（URL namespace: copomo）
  views/                  # ビュー分割
    ui.py                 # メイン UI
    download.py           # ダウンロード
    financial.py          # 財務 API
    financial_ui.py       # 財務 UI
    search.py             # 検索
    sentiment.py          # 感情分析 API
    sentiment_ui.py       # 感情分析 UI
  models/                 # モデル分割
    batch.py, company.py, document.py,
    financial.py, sentiment.py
  services/               # サービス分割（上記サービス層を参照）

common/
  services/
    yahoo_finance_service.py  # 全アプリ共通の yfinance ラッパー
```

### View パターン

```python
# CBV — Mixin 順: 機能Mixin → LoginRequiredMixin → 基底View
class StockDiaryDetailView(ObjectNotFoundRedirectMixin, LoginRequiredMixin, DetailView):
    model = StockDiary
    redirect_url = 'stockdiary:home'

# FBV — シンプルな API / HTMX 向け
@login_required
@require_POST
def add_transaction(request, diary_id):
    ...
```

**利用可能な Mixin:**

| Mixin | インポート元 | 用途 |
|-------|-------------|------|
| `LoginRequiredMixin` | `django.contrib.auth.mixins` | ログイン必須 |
| `ObjectNotFoundRedirectMixin` | `utils.mixins` | 404 時にリダイレクト |

### HTMX パターン

```python
# View でのリクエスト判定
is_htmx = (
    request.headers.get('HX-Request') == 'true'
    or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
)

if is_htmx:
    return render(request, 'stockdiary/partials/diary_list.html', context)
return render(request, 'stockdiary/home.html', context)
```

```django
{# テンプレート内での対象要素判定 #}
{% if request.htmx.target == "diary-container" %}
  ...
{% endif %}

{# 複数箇所の同時更新 #}
<div hx-swap-oob="true" id="search-result-info">...</div>
```

### URL パターン

```python
app_name = 'stockdiary'  # 各アプリで namespace 設定

urlpatterns = [
    # API エンドポイントは api/ プレフィックス（JSON 返却）
    path('api/stock/info/<str:stock_code>/', api.get_stock_info, name='api_stock_info'),

    # リソース操作は <pk>/ または <diary_id>/
    path('<int:pk>/update/', views.StockDiaryUpdateView.as_view(), name='update'),
    path('<int:diary_id>/transaction/add/', views.add_transaction, name='add_transaction'),
]
```

JSON を返す View → `api.py` / `api_views.py` に集約する。

**ルート URL 構成（`config/urls.py`）:**

| パス | アプリ | namespace |
|------|--------|-----------|
| `/stockdiary/` | `stockdiary` | `stockdiary` |
| `/copomo/` | `earnings_analysis` | `copomo` |
| `/tags/` | `tags` | — |
| `/analysis_template/` | `analysis_template` | — |
| `/company_master/` | `company_master` | — |
| `/margin/` | `margin_tracking` | `margin_tracking` |
| `/users/` | `users` | — |
| `/accounts/` | allauth | — |
| `/admin_xyz/` | Django admin | — |
| `/api/notifications/…` | `stockdiary.api_views` | — |
| `/api/push/…` | `stockdiary.api_views` | — |

### テンプレート命名規則

| 配置 | 用途 |
|------|------|
| `app/templates/app/*.html` | ページテンプレート（フルページ） |
| `app/templates/app/partials/*.html` | HTMX 応答用の部分テンプレート |
| `app/templates/app/components/*.html` | 再利用 UI コンポーネント |
| `app/templates/app/components/_*.html` | アトミックコンポーネント（`_` プレフィックス） |
| `templates/base.html` | サイト全体のマスターレイアウト |
| `templates/landing_page.html` | 公開ランディングページ |

**stockdiary の主要テンプレート:**

| ファイル | 用途 |
|----------|------|
| `home.html` | ダッシュボード |
| `detail.html` | 日記詳細（タブ: 取引・ノート・分析・詳細） |
| `diary_form.html` | 作成・編集 |
| `diary_summary.html` | 銘柄サマリー（銘柄一覧） |
| `timeline.html` | 全銘柄横断タイムライン |
| `diary_graph.html` | パフォーマンスチャート |
| `comparison.html` | 銘柄比較 |
| `investment_hub.html` | InvestmentHub ページ |
| `trading_dashboard.html` | トレーディングダッシュボード |
| `trade_upload.html` | 取引 CSV インポート |
| `notification_list.html` | 通知一覧 |
| `explore.html` | 銘柄探索 |

### Settings 構成

| ファイル | 用途 |
|----------|------|
| `config/settings.py` | 本番設定（`DEBUG` フラグで環境分岐） |
| `config/settings_local.py` | ローカル上書き用 |
| `config/test_settings.py` | テスト用（SQLite） |

---

## テスト基盤

テストは `/tests/` 配下。`pytest-django` + `config.test_settings` を使用。
マイグレーション不要（`--nomigrations`）、DB 再利用（`--reuse-db`）。

**共通フィクスチャ（`tests/conftest.py`）:**

| フィクスチャ | 内容 |
|-------------|------|
| `user` | テスト用ユーザー（testuser） |
| `another_user` | 別のテスト用ユーザー |
| `client` | Django テストクライアント |
| `authenticated_client` | ログイン済みクライアント |
| `sample_diary` | トヨタ自動車の基本日記 |
| `sample_diary_with_transaction` | 買い取引付き日記 |
| `sample_sold_diary` | 買い→売り完結済み日記 |
| `sample_memo_diary` | 取引なし・メモのみ日記 |
| `complex_diary_with_multiple_transactions` | 5 件の複数取引日記 |
| `diary_with_notes` | 継続記録（DiaryNote）付き日記 |
| `diary_with_stock_split` | 株式分割（StockSplit）含む日記 |
| `sample_tags` | 3 件のタグ（長期投資・配当狙い・成長株） |

カバレッジ対象: `stockdiary` / `analysis_template` / `tags`

---

## CI/CD

`.github/workflows/django-tests.yml` で push / PR 時に `pytest` が自動実行される。

セキュリティ検知（従量課金なし・公開リポジトリの無料枠＋定額）も常設：
`codeql.yml`（セマンティックSAST）・`security-scan.yml`（Bandit / pip-audit）・
`dependabot.yml`（依存CVE）。文脈依存の検知は Claude Code `/security-review`（定額）で補う。
詳細とリポジトリ側の有効化手順は `docs/security_scanning.md`。

---

## 開発規約

### 基本方針
- **既存ファイルを優先して編集する** — 新規ファイルは原則作らない
- 既存ファイルを使わなくなる場合は、**古いファイルの削除を明示的に指示する**
- 変更の意図を明確にする（機能拡張・改善・バグ修正など）

### 新規ファイルを作る場合
- 既存アプリの構成パターンに従う（`views.py` 方式か `views/` 方式かを確認）
- `views.py` が肥大化する場合は `views_<機能名>.py` に分割（既存パターンに倣う）
- ファイル名は責務が明確になるよう命名する（例: `request_create.py`）
- 再利用可能なロジックは `services/` や `utils/` に切り出す

### バグ修正時のルール
- **バグを修正したら必ずテストを追加する** — 再発防止のため、修正した挙動を`tests/`配下に回帰テストとして記述する
- テストは `tests/test_views_updated.py` などの既存ファイルに追記するのが基本。新規ファイルが必要な場合は `tests/test_<機能名>.py` の形式で作成する
- テストには「なぜこのバグが起きたか」をdocstringに残す（将来の読者が意図を理解できるよう）
- HTMX エンドポイントのテストは `HTTP_HX_REQUEST: 'true'` ヘッダーを付与して呼ぶ

### 禁止事項
- ❌ 同じ用途のファイルを複数作成して並行利用する（保守性低下）
- ❌ 理由なく既存の構造を変更する

---

## Claude Code 分析連携

### 目的
カブログの実データ（日記・取引・損益）＋最新ニュースを Claude Code から取得し、
個別銘柄の投資分析を行う。**従量課金ゼロ**（ニュースは yfinance 無料、分析は Claude Code 定額）。

### アーキテクチャ
```
Claude Code
  └─ WebFetch / curl
       └─ GET /api/analysis/diary/<symbol>/  ← Bearer 認証
            ├─ StockDiary（日記・取引・継続記録）
            └─ yfinance.news（最新ニュース・無料）
```

### 実装ファイル

| ファイル | 役割 |
|----------|------|
| `stockdiary/api_analysis.py` | 分析API エンドポイント本体（Bearer認証・読み取り専用） |
| `stockdiary/management/commands/generate_analysis_key.py` | APIキー生成コマンド |
| `config/settings.py` | `ANALYSIS_API_KEY = os.environ.get('ANALYSIS_API_KEY', '')` |
| `config/urls.py` | `/api/analysis/` 以下の URL 登録 |

### エンドポイント一覧

| パス | 返却内容 |
|------|----------|
| `GET /api/analysis/holdings/` | 保有中全銘柄（銘柄コード・名前・数量・平均取得単価・実現損益） |
| `GET /api/analysis/diaries/` | **記録銘柄の一覧（スクリーニング用・保有/売却/メモ横断）**。`?tags=半導体,AI`（OR）・`?sector=`・`?status=holding\|sold\|memo\|all` で絞り込み。各銘柄に最新の信用倍率を付与（バリュエーションは呼び出し側で補完）。返却は `ANALYSIS_API_USER` 固定 |
| `GET /api/analysis/positions/` | **現在保有中の全ポジションを判断材料付きで返す（利確/損切り/継続/買い増しのスクリーニング用）**。各ポジションに現在値・含み損益（率）・時価・信用倍率・**仮説の有無（`thesis_count`/`open_thesis_count`）**を付与。`?valuation=1` で PER/PBR/ROE/配当利回りも付与（財務諸表を引くため重い・既定OFF）・`?price=0` で調整。返却は `ANALYSIS_API_USER` 固定。ポートフォリオ合計（時価・含み損益）も返す |
| `GET /api/analysis/diary/<symbol>/` | 指定銘柄の日記全体＋取引履歴＋継続記録＋**最新ニュース**。**現在値・含み損益（率）・時価・バリュエーション（PER/PBR/ROE/配当利回り）・投資仮説（`theses`）も返す**（`?price=0`・`?valuation=0` で省略可）。⚠️ **買った理由（エントリー仮説）は `theses`（Thesis の claim/basis/worst_case/status＋Verdict）にある。`investment_reason`（reason）は『企業説明テンプレート』で書かれる企業の俯瞰説明で、買い判断が入っているとは限らない**ため、継続/損切り判断では theses を主ソースにする |
| `GET /api/analysis/portfolio/` | 業種分布・損益合計などポートフォリオサマリー |
| `POST /api/analysis/diary/<symbol>/notes/` | **継続記録（DiaryNote）を追加**（書き込み）。書き込み先ユーザーは `ANALYSIS_API_USER` で固定 |
| `DELETE /api/analysis/diary/<symbol>/notes/<note_id>/` | **継続記録を1件削除**（書き込み）。削除後にタグを再同期する |
| `PATCH /api/analysis/diary/<symbol>/reason/` | **日記本体の投資理由（reason）を更新**（書き込み）。旧内容は `ReasonVersion` に自動退避 |

クエリパラメータ:
- `diary/<symbol>/?news=0` → ニュース取得をスキップ（高速）
- `diary/<symbol>/?margin=0` → 信用残（信用倍率）取得をスキップ
- 返却データは `ANALYSIS_API_USER` に固定（他ユーザーの日記は返さない）

`diary_detail` は `margin` フィールドを返す（`margin_tracking.MarginData` 連携）。
JPX週次の信用取引残高から、最新の `margin_ratio`（=買い残/売り残）と直近トレンド
（`history`・古い順）を含む。信用倍率は需給の補助指標として分析に使う
（1倍未満＝売り長で取組良好、高倍率・買い残増＝将来の戻り売り圧力＝上値の重し）。
データが無い銘柄（外国株・未取得）は `margin: null`。回帰テスト:
`tests/test_api_analysis_margin.py`。

📝 **書き込み本文は markdown をそのまま使ってよい**（見出し `##`・表 `|---|`・
アポストロフィ可）。日記本体（PATCH）も継続記録（POST）も同じ。

補足: security ミドルウェア（`security/middleware.py` の `_is_suspicious_request`）は
`/api/` 配下の POST JSON ボディを SQLi/XSS ヒューリスティックで走査し、
`'`・`--`・`#`・`<...>` を不正とみなす。これが markdown を誤検知して 403
`Suspicious Request Detected` を返していたため、**`/api/analysis/` だけ本文走査の
対象外**にした（Bearer 認証・ORM 保存・テンプレート自動エスケープで SQLi/XSS は別途不成立）。
回帰テスト: `tests/test_security_middleware.py`。
※ この除外はサーバーへ反映（デプロイ＋再起動）して初めて有効。

### 認証方式
```
Authorization: Bearer <ANALYSIS_API_KEY>
```
キーが `.env` 未設定の場合は 503 を返す（誤って公開 API になることを防ぐ）。

### サーバー側セットアップ（初回のみ）
```bash
# 1. キー生成（指示に従って .env に追記）
python manage.py generate_analysis_key

# 2. サーバー再起動
sudo systemctl reload gunicorn  # または systemctl restart
```

### Claude Code からの使い方
```bash
# 保有銘柄一覧を確認
curl -H "Authorization: Bearer <KEY>" https://<サーバー>/api/analysis/holdings/

# 7203（トヨタ）を分析
curl -H "Authorization: Bearer <KEY>" https://<サーバー>/api/analysis/diary/7203/
```

Claude Code セッション内でのプロンプト例:
> 「7203の日記データと最新ニュースを /api/analysis/diary/7203/ から取得して、
>  投資継続判断の観点で分析して」

Claude が `diary_detail` エンドポイントを叩き、
`investment_reason`（投資理由）・`transactions`（取引履歴）・`notes`（継続記録）・
`latest_news`（最新ニュース）を一括取得して分析する。

### `diary_detail` レスポンス構造（主要フィールド）
```json
{
  "symbol": "7203",
  "name": "トヨタ自動車",
  "status": "保有中",
  "investment_reason": "投資理由のテキスト...",
  "current_quantity": 100.0,
  "avg_cost": 2000.0,
  "realized_profit": 50000.0,
  "transactions": [
    { "date": "2023-04-01", "type": "buy", "price": 2000.0, "quantity": 100.0 }
  ],
  "notes": [
    { "date": "2023-06-01", "type": "analysis", "topic": "決算", "content": "..." }
  ],
  "latest_news": [
    { "title": "...", "publisher": "...", "published_at": "2024-01-15 12:00 UTC", "url": "..." }
  ]
}
```

### 記録時のテンプレート運用（必須）

分析APIで日記・継続記録を**書き込む**ときは、必ず `docs/analysis_templates/` の
テンプレートに沿って記録する。役割設定・出力制限・タグ運用は `_手順.md` が正本。

| 記録の種類 | 書き込み先 | エンドポイント | 使うテンプレート |
|------------|------------|----------------|------------------|
| **企業分析**（ビジネスモデル・稼ぐ力・競合の俯瞰） | 日記本体（`reason`） | `PATCH .../diary/<symbol>/reason/` | `企業説明テンプレート.md` |
| **決算分析**（決算の質×継続性×アクション判定） | 継続記録（DiaryNote） | `POST .../diary/<symbol>/notes/` | `決算業績分析テンプレート.md` |
| **ポジション判定**（保有中を利確/損切り/継続/買い増しで判定） | 継続記録（DiaryNote） | `POST .../diary/<symbol>/notes/` | `保有ポジション判定テンプレート.md` |

- **決算分析を継続記録に書くときは `topic="決算分析"` を必ず指定する。**
- **ポジション判定を継続記録に書くときは `topic="ポジション判定"` を必ず指定する。**
  判定材料は `positions/`（一覧スクリーニング）→ `diary/<symbol>/`（個別の現在値・含み損益・
  投資理由・ノート・ニュース）で取得する。新しい書き込みAPIは不要（既存の `add_note` で記録）。
- タグは **`docs/analysis_templates/tag_master.md` から最大4つ**だけ選ぶ
  （子タグ優先・企業名/コードは不可・マクロは方向矢印付き・マスタにないタグは作らない）。
  本文中の `@タグ` は書き込み後に `diary.tags` へ**自動同期**される（UIと同じ
  `views._sync_hashtag_tags` を `update_reason`/`add_note` が呼ぶ。追加・解除・
  方向(↑/↓/→)・df 再計算まで実施。reason とノートの和集合で同期される）。
  → 別途タグ更新APIを叩く必要はない。レスポンスの `tags` に同期後の一覧が返る。
  回帰テスト: `tests/test_api_analysis_tags.py`。
- 出力制限（`_手順.md`）: テンプレートの指示文コメントは記録に含めない／
  3,000字以内（改行含む）／`<h1>` は使わない。
- 該当テンプレートが未取り込みの分析種別（仮説・ピア比較等。`docs/analysis_templates/README.md`
  の「⛔ 本文未取り込み」を参照）は、本文が揃うまで記録用途には使わない。

### 注意事項
- **読み取り系（`holdings`/`diary_detail`/`positions`/`diaries`/`portfolio`）は
  `ANALYSIS_API_USER` のデータに固定**され、他ユーザーの日記は一切返さない
  （`_get_scope_user`）。`ANALYSIS_API_USER` 未設定なら 503 で fail-closed。
  `?user=` に設定ユーザー以外を指定すると 403。本番DBは複数ユーザーを含むため、
  この固定を外して全ユーザーを返す実装に戻してはならない。回帰テスト:
  `tests/test_api_analysis_scope.py`。
- `ANALYSIS_API_KEY` はセッション認証とは完全に独立。ローテーションは
  `.env` の書き換え＋サーバー再起動のみで完結する。
- ニュースは yfinance 経由のため日本株は `.T` サフィックスを自動付与。
  外国株（`AAPL` 等）はそのままで取得できる。
