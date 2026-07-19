# ads/utils.py
"""広告設定（UserAdPreference）のリクエスト単位キャッシュ。

AdsMiddleware（_should_show_ads / _should_show_personalized_ads）と
ads_processor コンテキストプロセッサが、同一リクエスト内で同じ UserAdPreference を
それぞれ個別に取得していたため、全ページで同一クエリが 3〜4 回発行されていた。
この getter はリクエストオブジェクトに一度だけキャッシュし、以後は再利用する。
"""

_SENTINEL = object()


def get_user_ad_preference(request):
    """UserAdPreference をリクエスト単位でキャッシュして返す。未認証なら None。

    未作成の場合は作成する（従来挙動を踏襲）。作成失敗時は None を返す。
    """
    if not (hasattr(request, 'user') and request.user.is_authenticated):
        return None

    cached = getattr(request, '_ad_preference_cache', _SENTINEL)
    if cached is not _SENTINEL:
        return cached

    from .models import UserAdPreference
    try:
        pref = UserAdPreference.objects.get(user=request.user)
    except UserAdPreference.DoesNotExist:
        try:
            pref = UserAdPreference.objects.create(user=request.user)
        except Exception:
            pref = None

    request._ad_preference_cache = pref
    return pref
