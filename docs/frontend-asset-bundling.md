# フロント資産のバンドル（django-compressor）— ConoHa VPS 前提

1ページあたり CSS 37本・JS 29本を読み込んでおり、これが表示コストの主因。
`django-compressor` でローカルCSSを1本に結合・圧縮する基盤を導入した（`base.html` の
`{% compress css %}` ブロック）。**既定は無効（opt-in）**で、有効化するまで挙動は従来と完全に同一。

前提: 本番は **ConoHa VPS**（`USE_S3=False`・静的ファイルはローカルの `STATIC_ROOT`＝
`staticfiles/` を nginx が `/static/` として配信）。S3 は使わない。

---

## 設定（`config/settings.py`）

| 変数 | 既定 | 意味 |
|------|------|------|
| `COMPRESS_ENABLED` | `False`（env `COMPRESS_ENABLED`） | バンドルの有効化。OFF のとき `{% compress %}` は元の `<link>` をそのまま出力（従来どおり） |
| `COMPRESS_OFFLINE` | `False`（env `COMPRESS_OFFLINE`） | True=事前生成（推奨）／False=初回リクエストで生成（オンライン） |

- `STATICFILES_FINDERS` に `compressor.finders.CompressorFinder` を追加済み。
- CSSフィルタ: `CssAbsoluteFilter`（結合時に `url()` を絶対化して参照崩れを防ぐ）＋ `rCSSMinFilter`。
- `COMPRESS_OFFLINE_CONTEXT` に `STATIC_VERSION` を渡している（`?v=` を含むブロックのオフライン
  マニフェストキーを実リクエストと一致させるため。未指定だと `OfflineGenerationError`）。

---

## 有効化手順（ConoHa VPS・推奨＝オフライン）

STATIC_ROOT を実行時に書き換えず済み（読み取り専用運用・権限問題回避）、初回リクエストの
圧縮遅延も無いため VPS ではオフラインを推奨。

1. VPS の `.env` に追記:
   ```
   COMPRESS_ENABLED=True
   COMPRESS_OFFLINE=True
   ```
2. デプロイ（`.github/workflows/django-tests.yml` の deploy ジョブ）は既に
   `collectstatic` の後に `python manage.py compress --force` を実行する（無効時は
   `|| true` で無害化）。手動デプロイ時も collectstatic 後に同コマンドを実行する。
3. gunicorn 再起動（ワークフローが実施）。
4. nginx は `/static/` を `STATIC_ROOT` から配信していれば追加設定不要
   （結合ファイルは `/static/CACHE/css/output.<hash>.css` に出力される）。

> CSS を追加・変更したら再デプロイで `manage.py compress` が走り、結合ファイルが再生成される。
> 新しい CSS は `base.html` の `{% compress css %}` ブロックの内側に追加すれば自動で対象になる。

### 簡易運用（オンライン）
`COMPRESS_OFFLINE=False`（＝`COMPRESS_ENABLED=True` のみ）にすると、初回リクエストで
結合ファイルを生成しキャッシュする。デプロイに `compress` 手順は不要だが、gunicorn の
実行ユーザーが `staticfiles/`（`STATIC_ROOT/CACHE`）に書き込める必要がある。

---

## ロールバック
`.env` から `COMPRESS_ENABLED` を外す（または `False`）→ 従来どおり個別 `<link>` 配信に戻る。
コード変更・再デプロイは不要。

## 検証済みの挙動（サンドボックス実測）
- 既定 OFF: `<link>` は従来どおり全出力（回帰なし）。
- `COMPRESS_ENABLED=True`（online/offline とも）: ローカルCSS 22本 → 1本
  （`output.<hash>.css` 約265KB）。先頭〜末尾まで全内容・順序を保持。`url()` は data-URI のみで
  壊れる相対参照なし。

## JS のバンドル（連続runのみ）
`base.html` 末尾は 外部 `<script src>` と インライン `<script>` が交互に並ぶため、全体の
defer/bundle は実行順を壊す。そこで **インラインを挟まない外部scriptの連続run だけ** を
`{% compress js %}` でまとめた（`compress` は defer を付けず、結合ファイルを同じ位置で
同期実行するため実行順は不変）。現状のバンドル対象:
- speed-dial / bottom-sheet / autocomplete / hashtag-autocomplete / image-compression（5本→1本）
- toast / push-notifications / notification-ui（3本→1本）

新しいスクリプトを足すときは、**インラインを挟まない連続run なら該当 `{% compress js %}`
ブロックの内側に**、依存するインラインが間にあるものはブロック外に置く。

> 残り（`{% if %}` を跨ぐもの・インラインと密結合なもの）は未バンドル。必要なら
> インライン `<script>` を外部ファイルへ切り出してから run を広げる。
