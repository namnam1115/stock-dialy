# ads/context_processors.py
from .models import AdPlacement, AdUnit
from .utils import get_user_ad_preference
from django.conf import settings

def ads_processor(request):
    """広告関連のグローバル変数をテンプレートに提供"""
    context = {}
    
    # ミドルウェアで設定されたshow_adsの値を取得
    # ミドルウェアでFalseに設定されていれば、その値を尊重する
    show_ads = getattr(request, 'show_ads', True)
    
    # パーソナライズ広告の設定（リクエストから取得、デフォルトはTrue）
    personalized_ads = getattr(request, 'personalized_ads', True)
    
    # ミドルウェアでFalseになっていない場合のみ、ユーザー設定を確認
    # （UserAdPreference はリクエスト単位でキャッシュ済み。middleware と同じ1件を再利用）
    if show_ads and hasattr(request, 'user') and request.user.is_authenticated:
        ad_preference = get_user_ad_preference(request)
        if ad_preference is not None:
            show_ads = ad_preference.should_show_ads()
            personalized_ads = ad_preference.allow_personalized_ads
    
    context['show_ads'] = show_ads
    context['personalized_ads'] = personalized_ads
    
    # 広告表示が有効な場合のみ、広告ユニットを取得
    if show_ads:
        # 利用可能な広告配置を取得
        ad_placements = {}
        active_placements = AdPlacement.objects.filter(is_active=True)
        
        for placement in active_placements:
            # 各配置の有効な広告ユニットを取得
            ad_units = AdUnit.objects.filter(
                placement=placement,
                is_active=True
            ).first()  # 各配置につき1つの広告ユニットを使用
            
            if ad_units:
                ad_placements[placement.position] = {
                    'placement': placement,
                    'ad_unit': ad_units
                }
        
        context['ad_placements'] = ad_placements
    
    return context

def static_version(request):
    return {
        'STATIC_VERSION': settings.STATIC_VERSION,
    }