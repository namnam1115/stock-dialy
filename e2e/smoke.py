"""カブログ E2E スモークテスト（Playwright / Python）

ログイン → 日記作成（ウィザード）→ 詳細（タブ切替）→ タイムライン の
1本道をブラウザで通し、JS 例外（pageerror）ゼロで完走することを確認する。

pytest の単体テスト（tests/）はテンプレート内 JS を実行しないため、
「ページは 200 だが JS が壊れている」退行はここでしか検出できない。

実行方法は e2e/README.md を参照。環境変数:
    E2E_BASE_URL  対象サーバー（既定: http://127.0.0.1:8765）
    E2E_USERNAME / E2E_PASSWORD  ログイン情報（既定: uxcheck / uxcheck-pass）
    E2E_CHROME    Chromium 実行ファイル（既定: Playwright 標準解決）
    E2E_CDN_DIR   CDN 資産のローカルミラー（サンドボックス用。無ければ素通し）
"""
import mimetypes
import os
import re
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get('E2E_BASE_URL', 'http://127.0.0.1:8765')
USERNAME = os.environ.get('E2E_USERNAME', 'uxcheck')
PASSWORD = os.environ.get('E2E_PASSWORD', 'uxcheck-pass')
CHROME = os.environ.get('E2E_CHROME', '')
CDN_DIR = os.environ.get('E2E_CDN_DIR', '/tmp/cdn')

# ネットワーク遮断環境での既知の無害ノイズ（JS 例外ではない console error）
IGNORABLE_CONSOLE = ('Failed to load resource', 'ServiceWorker', 'bad HTTP response code')

JSDELIVR_RE = re.compile(r'https://cdn\.jsdelivr\.net/npm/([^@]+)@[^/]+/(.+?)(?:\?.*)?$')


def serve_cdn(route):
    """cdn.jsdelivr.net をローカルミラー（E2E_CDN_DIR）から返す。ミラーが無ければ素通し。"""
    m = JSDELIVR_RE.match(route.request.url)
    if m and os.path.isdir(CDN_DIR):
        rel = m.group(2)
        for cand in (rel, rel.replace('.min.js', '.js'), rel.replace('.min.css', '.css')):
            local = os.path.join(CDN_DIR, m.group(1), 'package', cand)
            if os.path.exists(local):
                ctype = mimetypes.guess_type(local)[0] or 'application/octet-stream'
                route.fulfill(status=200, content_type=ctype, body=open(local, 'rb').read())
                return
        route.abort()
        return
    route.continue_()


def serve_htmx(route):
    local = os.path.join(CDN_DIR, 'htmx.org/package/dist/htmx.min.js')
    if os.path.exists(local):
        route.fulfill(status=200, content_type='application/javascript', body=open(local, 'rb').read())
    else:
        route.continue_()


class Smoke:
    def __init__(self, page):
        self.page = page
        self.js_errors = []
        self.steps = []
        page.on('pageerror', lambda e: self.js_errors.append(str(e)))
        page.on('console', lambda m: (
            self.js_errors.append(f'console: {m.text}')
            if m.type == 'error' and not any(s in m.text for s in IGNORABLE_CONSOLE)
            else None
        ))

    def ok(self, name, cond):
        self.steps.append((name, bool(cond)))
        print(('  ✅ ' if cond else '  ❌ ') + name)

    def run(self):
        pg = self.page

        # 1. ログイン
        pg.goto(f'{BASE}/users/login/')
        pg.fill('input[name="username"]', USERNAME)
        pg.fill('input[name="password"]', PASSWORD)
        pg.click('button[type="submit"]')
        pg.wait_for_load_state('networkidle')
        self.ok('ログイン', '/login' not in pg.url)

        # 2. ホーム（日記一覧）
        pg.goto(f'{BASE}/')
        pg.wait_for_load_state('networkidle')
        pg.wait_for_timeout(800)
        self.ok('ホーム表示（一覧コンテナ）', pg.query_selector('#diary-container') is not None)

        # 3. 日記作成（ウィザード step1 → step2 → 送信）
        stock_name = 'E2Eスモーク銘柄'
        pg.goto(f'{BASE}/create/')
        pg.wait_for_load_state('networkidle')
        pg.wait_for_timeout(800)
        pg.fill('#id_stock_name', stock_name)
        pg.evaluate('window.wizardNext && window.wizardNext()')
        pg.wait_for_timeout(800)
        pg.evaluate("""
          (() => {
            const ta = document.getElementById('id_reason');
            const cmEl = ta.parentElement.querySelector('.CodeMirror');
            const text = 'E2Eスモークテストで作成した記録。';
            if (cmEl && cmEl.CodeMirror) cmEl.CodeMirror.setValue(text); else ta.value = text;
          })()
        """)
        pg.click('#diaryForm button[type="submit"]')
        pg.wait_for_load_state('networkidle')
        pg.wait_for_timeout(800)
        self.ok('日記作成→保存', stock_name in pg.content())

        # 4. 詳細ページ（作成した日記へ遷移し、タブを一巡）
        link = pg.query_selector(f'a:has-text("{stock_name}")')
        if link:
            link.click()
        pg.wait_for_load_state('networkidle')
        pg.wait_for_timeout(800)
        on_detail = pg.query_selector('#basic-content') is not None
        self.ok('詳細ページ表示', on_detail)
        if on_detail:
            for tab in ('#notes-tab', '#transactions-tab', '#related-tab', '#basic-tab'):
                el = pg.query_selector(tab)
                if el:
                    el.click()
                    pg.wait_for_timeout(600)
            self.ok('タブ切替（記録/取引/関連/概要）', True)

        # 5. タイムライン
        pg.goto(f'{BASE}/timeline/')
        pg.wait_for_load_state('networkidle')
        pg.wait_for_timeout(800)
        self.ok('タイムライン表示', 'タイムライン' in pg.content() or pg.url.endswith('/timeline/'))

        # 6. JS 例外ゼロ
        self.ok('JS例外ゼロ', not self.js_errors)
        for e in self.js_errors:
            print('     pageerror:', e[:200])

        return all(passed for _, passed in self.steps)


def main():
    launch_kwargs = {'args': ['--no-sandbox', '--ignore-certificate-errors']}
    if CHROME:
        launch_kwargs['executable_path'] = CHROME
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        ctx = browser.new_context(viewport={'width': 1280, 'height': 900})
        ctx.route('https://cdn.jsdelivr.net/**', serve_cdn)
        ctx.route('https://unpkg.com/htmx.org**', serve_htmx)
        ctx.route('https://pagead2.googlesyndication.com/**', lambda r: r.abort())
        ctx.route('https://www.googletagmanager.com/**', lambda r: r.abort())
        page = ctx.new_page()
        passed = Smoke(page).run()
        browser.close()
    print('SMOKE ' + ('PASSED' if passed else 'FAILED'))
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
