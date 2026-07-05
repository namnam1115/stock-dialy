# stockdiary/urls.py（完全版）
from django.urls import path
from . import views
from . import api
from . import api_views  # 🔧 追加
from . import views_mobile_ux  # 🆕 モバイルUX用ビュー
from . import views_comparison  # 銘柄比較機能
from . import views_timeline  # 全銘柄横断タイムライン
from . import views_migration  # 日記データ移行（インポート/エクスポート）
from . import views_trade_import  # 証券CSV取込（楽天・SBI）
from . import views_growth  # 成長OS（仮説・検証・カルテ・ライブラリ・年次レビュー）
from . import views_panels  # 詳細ページの遅延ロードHTMXパネル（backlinks・EDINET）
from . import views_dashboard  # ダッシュボード・パフォーマンスグラフ・タグ別成績
from . import views_earnings  # 決算カレンダー（決算予定の表示）
from . import views_transactions  # 取引・株式分割の CRUD
from django.contrib.auth.decorators import login_required
from django.views.generic.base import RedirectView

app_name = 'stockdiary'

urlpatterns = [
    # ==========================================
    # 日記の基本CRUD
    # ==========================================
    path('', views.StockDiaryListView.as_view(), name='home'),
    path('timeline/', views_timeline.TimelineView.as_view(), name='timeline'),
    path('create/', views.StockDiaryCreateView.as_view(), name='create'),
    path('<int:pk>/', views.StockDiaryDetailView.as_view(), name='detail'),
    path('<int:pk>/update/', views.StockDiaryUpdateView.as_view(), name='update'),
    path('<int:pk>/delete/', views.StockDiaryDeleteView.as_view(), name='delete'),
    path('<int:diary_id>/toggle-exclude/', views.toggle_exclude_diary, name='toggle_exclude'),

    # ==========================================
    # 🆕 クイック記録（モバイルUX）
    # ==========================================
    path('quick/create/', views_mobile_ux.quick_create_diary, name='quick_create'),
    path('quick/<int:diary_id>/note/', views_mobile_ux.quick_add_note, name='quick_add_note'),
    path('quick/<int:diary_id>/transaction/', views_mobile_ux.quick_add_transaction, name='quick_add_transaction'),

    # ==========================================
    # 取引管理
    # ==========================================
    path('<int:diary_id>/transaction/add/', views_transactions.add_transaction, name='add_transaction'),
    path('transaction/<int:transaction_id>/', views_transactions.get_transaction, name='get_transaction'),
    path('transaction/<int:transaction_id>/update/', views_transactions.update_transaction, name='update_transaction'),
    path('transaction/<int:transaction_id>/delete/', views_transactions.delete_transaction, name='delete_transaction'),

    # ==========================================
    # 株式分割管理
    # ==========================================
    path('<int:diary_id>/stock-split/add/', views_transactions.add_stock_split, name='add_stock_split'),
    path('stock-split/<int:split_id>/apply/', views_transactions.apply_stock_split, name='apply_stock_split'),
    path('stock-split/<int:split_id>/delete/', views_transactions.delete_stock_split, name='delete_stock_split'),

    # ==========================================
    # 継続記録関連
    # ==========================================
    path('<int:pk>/note/', views.AddDiaryNoteView.as_view(), name='add_note'),
    path('<int:diary_pk>/note/<int:pk>/edit/', views.UpdateDiaryNoteView.as_view(), name='edit_note'),
    path('<int:diary_pk>/note/<int:pk>/delete/', views.DeleteDiaryNoteView.as_view(), name='delete_note'),

    # ==========================================
    # 🆕 通知API（追加）
    # ==========================================
    path('api/diary/<int:diary_id>/notifications/create/', 
         api_views.create_diary_notification, 
         name='api_create_diary_notification'),
    
    path('api/diary/<int:diary_id>/notifications/', 
         api_views.list_diary_notifications, 
         name='api_list_diary_notifications'),
    path('api/notifications/<uuid:notification_id>/delete/', 
         api_views.delete_diary_notification, 
         name='api_delete_diary_notification'),

    path('notifications/', views.NotificationListView.as_view(), name='notification_list'),
    path('api/notifications/all/', api_views.list_all_notifications, name='api_list_all_notifications'),
    
    # ==========================================
    # 株式情報API
    # ==========================================
    path('api/stock/info/<str:stock_code>/', api.get_stock_info, name='api_stock_info'),
    path('api/stock/price/<str:stock_code>/', api.get_stock_price, name='api_stock_price'),
    path('api/stock/metrics/<str:stock_code>/', api.get_stock_metrics, name='api_stock_metrics'),
    path('api/stock/historical/<str:stock_code>/', api.get_stock_historical, name='api_stock_historical'),
    path('api/stock/search/', api.search_stock, name='search_stock'),
    path('api/diary/check-duplicate/', api.check_diary_duplicate, name='api_check_duplicate'),
    path('api/diary/lead-preview/', api.lead_preview, name='api_lead_preview'),
    path('api/industry/list/',   api.get_industry_list,   name='api_industry_list'),
    path('api/industry/stocks/', api.get_industry_stocks, name='api_industry_stocks'),
path('api/create/', api.api_create_diary, name='api_create'),
    path('api/tab-content/<int:diary_id>/<str:tab_type>/', views.DiaryTabContentView.as_view(), name='api_tab_content'),

    # ==========================================
    # その他のエンドポイント
    # ==========================================
    path('diary-list/', login_required(views.diary_list), name='diary_list'),
    path('tab-content/<int:diary_id>/<str:tab_type>/', login_required(views.tab_content), name='tab_content'),

    # モバイル操作性向上
    path('search-suggestion/', login_required(views.search_suggestion), name='search_suggestion'),
    
    # 画像配信（セキュア）
    path('image/<int:diary_id>/<str:image_type>/', views.ServeImageView.as_view(), name='serve_image'),
    path('image/<int:diary_id>/<str:image_type>/<int:note_id>/', views.ServeImageView.as_view(), name='serve_image'),
    
    # ハッシュタグAPI
    path('api/hashtags/', api_views.get_hashtags, name='api_hashtags'),

    # リンクプレビューAPI（OGPメタデータ取得）
    path('api/link-preview/', api_views.link_preview, name='api_link_preview'),

    # ==========================================
    # 関連日記API
    # ==========================================
    path('api/diary/<int:diary_id>/related/search/', api_views.search_related_diaries, name='api_related_search'),
    path('api/diaries/mine/search/', api_views.search_my_diaries, name='api_search_my_diaries'),
    path('api/diary/<int:diary_id>/related/add/', api_views.add_related_diary, name='api_related_add'),
    path('api/diary/<int:diary_id>/related/<int:related_id>/remove/', api_views.remove_related_diary, name='api_related_remove'),
    path('api/diary/<int:diary_id>/graph/', api_views.diary_detail_graph_data, name='api_diary_detail_graph'),

    # 銘柄一覧（diary_summary に統合。旧 URL はリダイレクトで維持）
    path('stocks/', RedirectView.as_view(pattern_name='stockdiary:diary_summary'), name='stock_list'),
    path('diary-summary/', views.DiarySummaryView.as_view(), name='diary_summary'),
    path('api/stock-diaries/<str:symbol>/', views.api_stock_diaries, name='api_stock_diaries'),

    # 取引履歴アップロード
    path('trade-upload/', views_trade_import.TradeUploadView.as_view(), name='trade_upload'),
    path('trade-upload/process/', views_trade_import.process_trade_upload, name='process_trade_upload'),

    # 日記データ移行（インポート/エクスポート）
    path('migration/export/', views_migration.MigrationExportView.as_view(), name='migration_export'),
    path('migration/export/download/', views_migration.migration_export_download, name='migration_export_download'),
    path('migration/export/selective/', views_migration.migration_export_selective, name='migration_export_selective'),
    path('migration/import/', views_migration.MigrationImportView.as_view(), name='migration_import'),
    path('migration/import/process/', views_migration.migration_import_process, name='migration_import_process'),


    path('dashboard/', views_dashboard.TradingDashboardView.as_view(), name='dashboard'),
    path('earnings-calendar/', views_earnings.earnings_calendar, name='earnings_calendar'),
    path('review/', views_growth.AnnualReviewView.as_view(), name='annual_review'),
    path('karte/', views_growth.InvestorKarteView.as_view(), name='investor_karte'),
    path('library/', views_growth.LibraryView.as_view(), name='library'),
    # Phase 8a: 検証ループ（仮説・検証）
    path('<int:diary_id>/karte/', views_growth.karte_block, name='karte_block'),
    path('<int:diary_id>/thesis/new/', views_growth.thesis_edit, name='thesis_create'),
    path('<int:diary_id>/thesis/<int:thesis_id>/edit/', views_growth.thesis_edit, name='thesis_edit'),
    path('<int:diary_id>/thesis/<int:thesis_id>/verify/', views_growth.thesis_verify, name='thesis_verify'),
    path('compare/', views_comparison.StockComparisonView.as_view(), name='stock_comparison'),
    path('investment-hub/', views_comparison.InvestmentHubView.as_view(), name='investment_hub'),
    path('api/gemini-stock-analysis/', views_comparison.api_gemini_stock_analysis, name='api_gemini_stock_analysis'),

    # ==========================================
    # 日記関連グラフ
    # ==========================================
    path('graph/', views_dashboard.DiaryGraphView.as_view(), name='diary_graph'),
    path('api/diary-graph/data/', api_views.diary_graph_data, name='api_diary_graph_data'),
    # ドリルダウン探索グラフ（TG-DD・空から要素選択→近傍を1段ずつ展開）
    path('explore/graph/', views_dashboard.ExploreGraphView.as_view(), name='explore_graph'),
    path('api/graph/neighbors/', api_views.graph_neighbors, name='api_graph_neighbors'),

    # ==========================================
    # EDINET連携（開示書類パネル）
    # ==========================================
    # バックリンク（関連タブの遅延ロード）
    path('<int:diary_id>/backlinks-panel/', views_panels.backlinks_panel, name='backlinks_panel'),

    path('<int:diary_id>/edinet-panel/', views_panels.edinet_panel, name='edinet_panel'),
    path('<int:diary_id>/edinet-note-prefill/', views_panels.edinet_note_prefill, name='edinet_note_prefill'),
    path('<int:diary_id>/edinet-xbrl-analyze/', views_panels.edinet_xbrl_analyze, name='edinet_xbrl_analyze'),
]