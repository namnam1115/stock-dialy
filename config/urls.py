# config/urls.py
"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
import os
from django.http import HttpResponse
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from . import views
from stockdiary import api_views, api_analysis
from django.contrib.staticfiles.storage import staticfiles_storage
from django.views.generic.base import RedirectView
from django.views.generic import TemplateView

def serve_service_worker(request):
    """Service Workerをルートスコープで配信"""
    sw_path = os.path.join(settings.BASE_DIR, 'static', 'sw.js')
    
    try:
        with open(sw_path, 'r', encoding='utf-8') as f:
            js_content = f.read()
        
        response = HttpResponse(js_content, content_type='application/javascript; charset=utf-8')
        response['Service-Worker-Allowed'] = '/'
        response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        
        # 🆕 Safari対応: X-Content-Type-Optionsを追加
        response['X-Content-Type-Options'] = 'nosniff'
        
        return response
    except FileNotFoundError:
        return HttpResponse('Service Worker not found', status=404, content_type='text/plain')



urlpatterns = [
    
    path('sw.js', serve_service_worker, name='service-worker'),
    
    # ランディングページ
    path('', views.landing_page, name='landing_page'),
    
    # 管理画面
    path("admin_xyz/", admin.site.urls),
    
    # ユーザー認証関連
    path("users/", include("users.urls")),
    path('accounts/', include('allauth.urls')),  # allauthのURLを追加
    
    # メインアプリケーション
    path('stockdiary/', include('stockdiary.urls')),  # stockdiary アプリのURL
    path('tags/', include('tags.urls')),
    path('diary-templates/', include('diary_templates.urls')),
    path('analysis_template/', include('analysis_template.urls')),
    path('company_master/', include('company_master.urls')),
    
    #コーポマインドリーダー（新機能）
    path('copomo/', include('earnings_analysis.urls', namespace='copomo')),
    
    # サポート・サービス系
    path('subscriptions/', include('subscriptions.urls')),
    path('ads/', include('ads.urls')),
    path('security/', include('security.urls')),
    path('contact/', include('contact.urls')),
    
    # 信用倍率
    path('margin/', include('margin_tracking.urls', namespace='margin_tracking')),

    # 分析API（Claude Code / 外部ツール向け・Bearer認証）
    # 読み取り
    path('api/analysis/holdings/', api_analysis.holdings, name='api_analysis_holdings'),
    path('api/analysis/diaries/', api_analysis.list_diaries, name='api_analysis_list_diaries'),
    path('api/analysis/positions/', api_analysis.positions, name='api_analysis_positions'),
    path('api/analysis/diary/<str:symbol>/', api_analysis.diary_detail, name='api_analysis_diary'),
    path('api/analysis/portfolio/', api_analysis.portfolio_summary, name='api_analysis_portfolio'),
    # 書き込み（ANALYSIS_API_USER で対象ユーザーを固定）
    path('api/analysis/diary/<str:symbol>/notes/', api_analysis.add_note, name='api_analysis_add_note'),
    path('api/analysis/diary/<str:symbol>/notes/<int:note_id>/', api_analysis.delete_note, name='api_analysis_delete_note'),
    path('api/analysis/diary/<str:symbol>/reason/', api_analysis.update_reason, name='api_analysis_update_reason'),

    # APIエンドポイント（将来的な拡張用）
    # path('api/v1/earnings/', include('earnings_analysis.urls')),  # API専用
    path('offline/', TemplateView.as_view(template_name='offline.html'), name='offline'),
    
    # 通知履歴API
    path('api/notifications/logs/', api_views.get_notification_logs, name='api_notification_logs'),
    path('api/notifications/<int:log_id>/read/', api_views.mark_notification_read, name='api_mark_notification_read'),
    path('api/notifications/<int:log_id>/click/', api_views.mark_notification_read, name='api_mark_notification_clicked'),
    path('api/notifications/mark-all-read/', api_views.mark_all_read, name='api_mark_all_read'),
    
    # プッシュ通知API（共通）
    path('api/push/vapid-key/', api_views.get_vapid_public_key, name='api_vapid_key'),
    path('api/push/subscribe/', api_views.subscribe_push, name='api_subscribe_push'),
    path('api/push/unsubscribe/', api_views.unsubscribe_push, name='api_unsubscribe_push'),
    
]

# 静的ファイル関連
urlpatterns += [
    path('ads.txt', RedirectView.as_view(url=staticfiles_storage.url('ads.txt'))),
]

# 開発環境でのスタティックファイル配信
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# カスタムエラーページ（本番環境用）
#if not settings.DEBUG:
    # 404, 500エラーページのハンドラー
    #handler404 = 'config.views.custom_404'
    #handler500 = 'config.views.custom_500'