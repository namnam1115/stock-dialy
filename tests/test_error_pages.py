"""カスタムエラーページ（404/500/403/403_csrf）の回帰テスト。

なぜ: 従来 handler も templates/404.html 等も無く、本番では Django 既定の無地エラーページが
出ていた（ブランド無し・戻り導線無し）。ブランド統一の branded ページを追加したため、
(1) 404/403 が正しく自作テンプレを使うこと、(2) 500/403_csrf が context プロセッサや DB に
依存せず（＝障害時でも）自己完結で描画できることを固定する。
"""
from django.template import Context, Template
from django.test import Client, RequestFactory, override_settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.views.defaults import permission_denied, server_error


@override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver', '*'])
def test_404_uses_branded_template():
    """DEBUG=False で存在しない URL は自作 404（ブランド＋戻り導線）を返す。"""
    resp = Client().get('/definitely-not-a-real-page-xyz/')
    assert resp.status_code == 404
    html = resp.content.decode('utf-8', 'ignore')
    assert 'ページが見つかりません' in html
    assert '日記一覧へ' in html  # 戻り導線


@override_settings(DEBUG=False, ALLOWED_HOSTS=['*'])
def test_403_uses_branded_template():
    """PermissionDenied は自作 403（ブランド）を返す。"""
    req = RequestFactory().get('/x/')
    req.user = AnonymousUser()
    resp = permission_denied(req, PermissionDenied())
    assert resp.status_code == 403
    assert 'アクセスできません' in resp.content.decode('utf-8', 'ignore')


def test_500_template_is_self_contained():
    """500 は handler500 の空コンテキスト（context プロセッサ無効・DB 不可）でも描画できる。
    base.html を継承せず STATIC_VERSION 等に依存しないことを固定する。"""
    out = Template(open('templates/500.html').read()).render(Context({}))
    assert 'サーバーエラー' in out
    assert 'STATIC_VERSION' not in out  # base 継承・context 依存が混入していない


def test_500_handler_renders():
    req = RequestFactory().get('/y/')
    req.user = AnonymousUser()
    resp = server_error(req)
    assert resp.status_code == 500
    assert 'サーバーエラー' in resp.content.decode('utf-8', 'ignore')


def test_403_csrf_template_is_self_contained():
    """CSRF 失敗ページ（セッション切れ）も自己完結で描画できる。"""
    out = Template(open('templates/403_csrf.html').read()).render(Context({}))
    assert 'セッションが切れました' in out
    assert '再読み込み' in out
    assert 'STATIC_VERSION' not in out
