# E2E スモークテスト

`pytest`（tests/）はテンプレート内 JS を実行しないため、「ページは 200 だが JS が
壊れている」タイプの退行を検出できない。このディレクトリの Playwright スモークが
その穴を埋める。**ログイン → 日記作成 → 詳細（タブ切替）→ タイムライン** の
1本道を実ブラウザで通し、JS 例外（pageerror）ゼロで完走することを確認する。

CI には組み込まない（外部ブラウザ依存のため）。UI に触る変更をしたら手動で流す。

## 実行手順（ローカル / サンドボックス共通）

```bash
# 1. 依存（初回のみ）
pip install playwright
# ローカルPCなら: playwright install chromium
# Claude Code サンドボックスはプリインストールの Chromium を使う（下記 E2E_CHROME）

# 2. テスト用サーバーを起動（SQLite・シード済み）
#    セットアップ詳細は .claude/skills/run-app/SKILL.md を参照
DJANGO_TESTING=1 DJANGO_SETTINGS_MODULE=config.test_settings \
  python manage.py migrate --run-syncdb
DJANGO_TESTING=1 DJANGO_SETTINGS_MODULE=config.test_settings \
  python manage.py shell < .claude/skills/run-app/seed_demo.py
DJANGO_TESTING=1 DJANGO_SETTINGS_MODULE=config.test_settings \
  python manage.py runserver 127.0.0.1:8765 --noreload &

# 3. スモーク実行
python e2e/smoke.py

# サンドボックス（CDN 遮断環境）の場合:
#   .claude/skills/run-app/SKILL.md §4 の手順で /tmp/cdn を作成した上で
E2E_CHROME=/opt/pw-browsers/chromium-1194/chrome-linux/chrome python e2e/smoke.py
```

成功時は各ステップに ✅ が並び `SMOKE PASSED`（exit 0）、失敗時は ❌ と
`SMOKE FAILED`（exit 1）で終わる。

## 環境変数

| 変数 | 既定値 | 用途 |
|------|--------|------|
| `E2E_BASE_URL` | `http://127.0.0.1:8765` | 対象サーバー |
| `E2E_USERNAME` / `E2E_PASSWORD` | `uxcheck` / `uxcheck-pass` | ログイン情報（seed_demo.py が作成） |
| `E2E_CHROME` | （Playwright 標準解決） | Chromium 実行ファイルパス |
| `E2E_CDN_DIR` | `/tmp/cdn` | CDN 資産ローカルミラー。無ければ実 CDN へ素通し |

## 注意

- 対象サーバーの DB に **書き込む**（「E2Eスモーク銘柄」という日記を作成する）。
  本番 URL に向けて実行しないこと。
- `tests/` の pytest とは独立（pytest.ini の testpaths に含まれないため
  `pytest` 実行時に collect されない）。
