"""テンプレートの「繰り返し事故」を構造的に止める lint テスト。

個別ページのテストで文字列漏れを都度検知する方式（「複数行 {# #} 事故の回帰防止」
アサーションが複数ファイルに散在）では、新しいテンプレートで同じ事故が再発する。
ここでは全テンプレートを機械走査し、事故クラスそのものを禁止する。

検知する事故クラス:

1. 複数行 {# #} コメント
   Django の {# #} は単一行専用。複数行に跨ぐとコメントがそのまま本文HTMLへ
   漏れる。過去に detail.html のタブ改修・仮説シート・時系列タブ撤去で発生し、
   2026-07 のナビ整理でも2回発生した（本テスト導入の直接の契機）。
   複数行コメントは {% comment %}...{% endcomment %} を使うこと。

2. base.html と個別ページの同一JSの二重読み込み
   クラシックスクリプトの二重実行はトップレベル const の再宣言で SyntaxError
   になり、2回目のロードが丸ごと無効化される（hashtag-autocomplete.js で
   「Identifier 'HASHTAG_AXIS_META' has already been declared」が全ページ
   常時発生していた実績）。base.html が読むJSはページ側で再読み込みしない。
"""
import glob
import re
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent

# 走査対象: 自作テンプレートのみ（venv 等は除外）
TEMPLATE_GLOBS = [
    'templates/**/*.html',
    '*/templates/**/*.html',
]


def _project_templates():
    seen = set()
    for pattern in TEMPLATE_GLOBS:
        for path in glob.glob(str(BASE_DIR / pattern), recursive=True):
            if '/venv/' in path or '/node_modules/' in path:
                continue
            if path not in seen:
                seen.add(path)
                yield Path(path)


def test_no_multiline_template_comments():
    """{# が同じ行の #} で閉じないテンプレートを禁止する（本文への漏れ防止）。"""
    violations = []
    for path in _project_templates():
        for lineno, line in enumerate(
            path.read_text(encoding='utf-8').splitlines(), 1
        ):
            if '{#' in line and '#}' not in line.split('{#', 1)[1]:
                violations.append(f'{path.relative_to(BASE_DIR)}:{lineno}')
    assert not violations, (
        '複数行 {# #} コメントはHTML本文へ漏れます。'
        '{% comment %}...{% endcomment %} を使ってください:\n  '
        + '\n  '.join(violations)
    )


def test_no_duplicate_static_js_with_base():
    """base.html が読み込む静的JSを、個別ページが重ねて読み込むのを禁止する。"""
    js_re = re.compile(r"static ['\"](js/[^'\"]+\.js)['\"]")
    base_js = set(js_re.findall((BASE_DIR / 'templates/base.html').read_text(encoding='utf-8')))
    violations = []
    for path in _project_templates():
        if path.name == 'base.html':
            continue
        dup = set(js_re.findall(path.read_text(encoding='utf-8'))) & base_js
        for js in sorted(dup):
            violations.append(f'{path.relative_to(BASE_DIR)} → {js}')
    assert not violations, (
        'base.html と重複するJS読み込みがあります（二重実行は const 再宣言で '
        'SyntaxError になります）:\n  ' + '\n  '.join(violations)
    )


def test_static_version_matches_sw_version():
    """STATIC_VERSION（settings.py）と Service Worker の VERSION（sw.js）の一致を強制する。

    この2つは「静的アセット更新時に人手で同時にバンプする」運用で、実際に
    片方ずつ手動同期する場面が繰り返し発生していた（CH2）。片方の更新漏れは
    「デプロイしたのに古いキャッシュが配信され続ける」事故として現れ、原因
    特定が難しい。ここで一致を機械的に強制し、更新漏れをCIで即検知する。
    """
    from django.conf import settings

    sw = (BASE_DIR / 'static/sw.js').read_text(encoding='utf-8')
    m = re.search(r"const VERSION = '([^']+)'", sw)
    assert m, 'static/sw.js の VERSION 定義が見つかりません'
    assert m.group(1) == settings.STATIC_VERSION, (
        f'sw.js の VERSION ({m.group(1)}) と settings.STATIC_VERSION '
        f'({settings.STATIC_VERSION}) が不一致です。両方を同じ値に更新してください'
    )


def test_hardcoded_stockdiary_urls_in_js_do_not_increase():
    """static/js 内の '/stockdiary/' ハードコードURLの増加を禁止する（CH4・ラチェット）。

    URLはテンプレート側で {% url %} により解決し、window.*_CONFIG.urls で
    JSへ渡す（diary-detail-page.js で全12箇所を config 化した確立パターン）。
    直書きは URL 変更・プレフィックス変更（test_urls はルート直下）で静かに壊れる。
    実際、直書きされていた EDINET パネル等の fetch はテスト環境では 404 だった。

    既存負債（下記 BASELINE）は一括修正せず凍結し、**増加のみを失敗にする**。
    直書きを config 化したら該当ファイルの件数を減らす（0 になったら行ごと削除）。
    """
    # 恒久除外: _sw.js=旧SW（未配信）/ autocomplete.js=上書き可能な既定値 /
    #           diary-detail-graph.js / hashtag-autocomplete.js=docコメント内のみ
    allowlist = {'_sw.js', 'autocomplete.js', 'diary-detail-graph.js', 'hashtag-autocomplete.js'}
    # 凍結された既存負債（2026-07 時点）。増やしてはならない。
    baseline = {
        'diary-detail.js': 5,
        'diary-form.js': 4,
        'diary-tabs.js': 1,
        'diary-theme.js': 1,
        'home-page.js': 1,
        'margin-analysis.js': 2,
        'notifications.js': 1,
        'stock_diary_modal.js': 2,
    }
    counts = {}
    for path in sorted((BASE_DIR / 'static/js').glob('*.js')):
        if path.name in allowlist:
            continue
        n = sum(
            ("'/stockdiary/" in line or '"/stockdiary/' in line or '`/stockdiary/' in line)
            for line in path.read_text(encoding='utf-8').splitlines()
        )
        if n:
            counts[path.name] = n
    over = {
        name: (n, baseline.get(name, 0))
        for name, n in counts.items() if n > baseline.get(name, 0)
    }
    assert not over, (
        "'/stockdiary/' の直書きが増えています。window.*_CONFIG.urls（テンプレートで "
        '{% url %} 解決）を使ってください: '
        + ', '.join(f'{k}: {v[0]}件 (許容 {v[1]})' for k, v in over.items())
    )
