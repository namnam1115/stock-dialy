# common/context_processors.py
"""全アプリ共通のコンテキストプロセッサ。"""

# 主要ナビゲーションの単一ソース（CH3）。
# PCヘッダー（nav-item）とモバイルメニュー（menu-item primary）は base.html 内の
# 別々の手書きリストだったため、ナビ変更のたびに2箇所同時編集が必要で、
# 片側だけ直る drift が構造的に起きていた（NV1 実装時に実測）。
# 項目の追加・削除・並び替えはこのリストだけを変更する。
# ※「設定・その他」セクションは頻度が低く形式も異なるため対象外（base.html 直書きのまま）。
MAIN_NAV = [
    {'label': '日記一覧',   'url_name': 'stockdiary:home',          'icon': 'bi-journal-text'},
    {'label': 'タイムライン', 'url_name': 'stockdiary:timeline',      'icon': 'bi-clock-history'},
    {'label': '要素で探索',  'url_name': 'stockdiary:explore_graph', 'icon': 'bi-diagram-3-fill'},
    {'label': 'ダッシュボード', 'url_name': 'stockdiary:dashboard',   'icon': 'bi-graph-up'},
    {'label': '新規作成',   'url_name': 'stockdiary:create',        'icon': 'bi-plus-circle'},
]


def main_nav(request):
    """主要ナビ項目をテンプレートへ供給する。"""
    return {'MAIN_NAV': MAIN_NAV}
