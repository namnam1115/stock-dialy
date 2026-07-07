# アプリ紹介画像（プロモ）＋ マスコット「ログモ」

カブログの SNS／ストア向けアプリ紹介画像。参考にした「auth」風レイアウトを踏襲しつつ、
文言はランディングページ（`templates/landing_page.html`）から採用し、配色はカブログの
現行パレット（`static/css/1-foundations/variables.css`：`#00668c`／`#71c4ef`／ペーパー基調）
に合わせている。

## 成果物

| ファイル | 内容 | サイズ |
|----------|------|--------|
| `01-cover.png`  | 表紙／全体紹介（投資家の成長OS・ホーム画面・主要4機能） | 2400×3000 |
| `02-loop.png`   | 検証ループ（予想→結果→検証→学び の4ステップ） | 2400×3000 |
| `03-recall.png` | 想起／投資家カルテ（ホーム＋カルテの実画面2枚） | 2400×3000 |

## マスコット「ログモ」

`static/images/logumo.svg`（＋ `logumo-wave.svg` / `logumo-point.svg` / `logumo-lookback.svg`）。
設定：記録の妖精。ごはん＝投資記録／好き＝振り返り／嫌い＝感情だけで売買すること。
参考のお化け型シルエットを SVG（放射グラデ＋ソフトシャドウ）でクレイ風に再現したもの。
LP・favicon・SNS など他用途にも流用可。

## 再生成の手順

作画は `src/` の HTML/CSS を **Chromium（Playwright）で PNG 化**している。

```bash
# 1) 依存（サンドボックス想定。Chromium はプリインストールを使用）
python3.11 -m venv venv && ./venv/bin/pip install playwright pillow

# 2) 日本語フォント（任意・推奨）
#    src/fonts/nsjp-local.css が無い場合は自動で IPAGothic にフォールバックする。
#    Noto Sans JP を埋め込みたい場合は Google Fonts の woff2 を src/fonts/woff2/ に置き、
#    それらへの相対パスで src/fonts/nsjp-local.css を生成する（@import で読み込まれる）。

# 3) レンダリング（1200×1500 を deviceScaleFactor=2 で 2400×3000 出力）
cd src
../../../../venv/bin/python render.py image1.html ../01-cover.png 1200 1500
../../../../venv/bin/python render.py image2.html ../02-loop.png 1200 1500
../../../../venv/bin/python render.py image3.html ../03-recall.png 1200 1500
```

## 文言の出典

すべて `templates/landing_page.html` の実コピーに準拠（例：「いくら儲けたか」ではなく、
「なぜ、そう判断したか」。／ 予想→結果→検証→学び／ ホームを開くと、損益ではなく、
過去の自分が現れる。）。数値・銘柄名はデモ画面のスクリーンショット由来。

## 使用アセット
- 実画面: `static/images/lp/{recall,karte}.webp`（LP と共用の実スクリーンショット）
- ロゴ: `static/images/icon-192.svg`
