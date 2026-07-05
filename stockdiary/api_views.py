# stockdiary/api_views.py
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods, require_GET
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated


class CsrfExemptSessionAuthentication(SessionAuthentication):
    """CSRF チェックをスキップする SessionAuthentication

    プッシュ通知 API など、JSON を受け取る同一オリジンの API エンドポイントに使用。
    IsAuthenticated によるセッション認証と SameSite=Lax Cookie で保護されるため安全。
    """
    def enforce_csrf(self, request):
        return
from rest_framework.response import Response
from .models import (
    PushSubscription, DiaryNotification,
    NotificationLog, StockDiary
)
from .utils import (
    extract_hashtags, get_all_hashtags_from_queryset, search_diaries_by_hashtag,
    get_tag_graph_data, get_sector_graph_data, get_hashtag_graph_data,
    get_mention_graph_data, extract_stock_mentions,
)
import json
import logging
import re
import traceback
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

from pywebpush import webpush, WebPushException
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)


@require_GET
def get_vapid_public_key(request):
    """VAPID公開鍵を返す（認証不要）"""
    return JsonResponse({
        'public_key': settings.WEBPUSH_SETTINGS.get('VAPID_PUBLIC_KEY', '')
    })


@api_view(['POST'])
@authentication_classes([CsrfExemptSessionAuthentication])
@permission_classes([IsAuthenticated])
def subscribe_push(request):
    """プッシュ通知サブスクリプションを登録"""
    
    try:
        data = json.loads(request.body)
        subscription_info = data.get('subscription')
        
        if not subscription_info:
            return JsonResponse({'error': '無効なサブスクリプション'}, status=400)
        
        from stockdiary.models import PushSubscription
        
        # サブスクリプションを作成または更新
        subscription, created = PushSubscription.objects.update_or_create(
            endpoint=subscription_info['endpoint'],
            defaults={
                'user': request.user,
                'p256dh': subscription_info['keys']['p256dh'],
                'auth': subscription_info['keys']['auth'],
                'device_name': data.get('device_name', ''),
                'user_agent': data.get('user_agent', ''),
                'is_active': True,
            }
        )
        
        return JsonResponse({
            'success': True,
            'message': 'プッシュ通知を有効にしました',
            'created': created,
            'subscription_id': str(subscription.id)
        })
        
    except Exception as e:
        logger.error("Push subscription error: %s", e, exc_info=True)

        return JsonResponse({'error': 'サブスクリプションの登録に失敗しました'}, status=500)
        

@api_view(['POST'])
@authentication_classes([CsrfExemptSessionAuthentication])
@permission_classes([IsAuthenticated])
def unsubscribe_push(request):
    """プッシュ通知サブスクリプションを解除"""
    
    try:
        data = json.loads(request.body)
        endpoint = data.get('endpoint')
        
        if not endpoint:
            return JsonResponse({'error': 'エンドポイントが必要です'}, status=400)
        
        from stockdiary.models import PushSubscription
        
        PushSubscription.objects.filter(
            user=request.user,
            endpoint=endpoint
        ).update(is_active=False)
        
        return JsonResponse({
            'success': True,
            'message': 'プッシュ通知を無効にしました'
        })
        
    except Exception as e:
        logger.error("Unsubscribe push error: %s", e, exc_info=True)
        return JsonResponse({'error': 'サブスクリプションの解除に失敗しました'}, status=500)


@login_required
@require_http_methods(["POST"])
def create_diary_notification(request, diary_id):
    """日記の通知設定を作成（リマインダーのみ）"""
    try:
        # 日記を取得
        diary = get_object_or_404(StockDiary, id=diary_id, user=request.user)
        
        # リクエストボディをパース
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}")
            return JsonResponse({
                'success': False,
                'error': 'Invalid JSON format'
            }, status=400)
        
        # 必須フィールドのバリデーション
        remind_at = data.get('remind_at')
        if not remind_at:
            return JsonResponse({
                'success': False,
                'error': '通知日時を入力してください'
            }, status=400)
        
        # 日時をパース
        try:
            from datetime import datetime
            remind_at_dt = datetime.fromisoformat(remind_at.replace('Z', '+00:00'))
            
            # タイムゾーンを適用
            from django.utils import timezone
            if timezone.is_naive(remind_at_dt):
                remind_at_dt = timezone.make_aware(remind_at_dt)
        except ValueError as e:
            logger.error(f"Date parsing error: {e}")
            return JsonResponse({
                'success': False,
                'error': '日時の形式が正しくありません'
            }, status=400)
        
        # 過去の日時をチェック
        now = timezone.now()
        if remind_at_dt < now:
            return JsonResponse({
                'success': False,
                'error': '過去の日時は指定できません'
            }, status=400)
        
        # メッセージを取得（オプション）
        message = data.get('message', '').strip()
        
        # 通知を作成
        notification_data = {
            'diary': diary,
            'remind_at': remind_at_dt,
            'message': message,
            'is_active': True
        }
        
        logger.info(f"Creating notification with data: {notification_data}")
        
        notification = DiaryNotification.objects.create(**notification_data)
        
        logger.info(f"✅ Notification created: {notification.id}")
        
        return JsonResponse({
            'success': True,
            'message': 'リマインダーを設定しました',
            'notification_id': str(notification.id),
            'notification': {
                'id': str(notification.id),
                'remind_at': notification.remind_at.isoformat(),
                'message': notification.message,
                'is_active': notification.is_active,
                'created_at': notification.created_at.isoformat()
            }
        })
        
    except Exception as e:
        logger.error("Unexpected error in create_diary_notification: %s", e, exc_info=True)
        return JsonResponse({
            'success': False,
            'error': '通知設定中にエラーが発生しました'
        }, status=500)


@require_http_methods(["POST"])  # ← DELETEを削除、POSTのみ
@login_required
def delete_diary_notification(request, notification_id):
    """日記の通知設定を削除"""
    try:
        notification = get_object_or_404(
            DiaryNotification,
            id=notification_id,
            diary__user=request.user
        )
        notification.delete()
        
        return JsonResponse({
            'success': True,
            'message': '通知設定を削除しました'
        })
        
    except Exception as e:
        logger.error("通知削除エラー: %s", e, exc_info=True)
        return JsonResponse({'error': '通知設定の削除に失敗しました'}, status=500)


@login_required
@require_http_methods(["GET"])
def list_diary_notifications(request, diary_id):
    """日記の通知設定一覧を取得"""
    try:
        diary = get_object_or_404(StockDiary, id=diary_id, user=request.user)
        
        notifications = DiaryNotification.objects.filter(
            diary=diary
        ).order_by('-created_at')
        
        notification_list = []
        for notification in notifications:
            notification_list.append({
                'id': str(notification.id),
                'remind_at': notification.remind_at.isoformat(),
                'message': notification.message,
                'is_active': notification.is_active,
                'last_sent': notification.last_sent.isoformat() if notification.last_sent else None,
                'created_at': notification.created_at.isoformat(),
            })
        
        return JsonResponse({
            'success': True,
            'notifications': notification_list,
            'count': len(notification_list)
        })
        
    except Exception as e:
        logger.error("List notifications error: %s", e, exc_info=True)
        return JsonResponse({
            'success': False,
            'error': '通知設定の取得に失敗しました'
        }, status=500)


@require_GET
@login_required
def get_notification_logs(request):
    """通知履歴を取得"""
    limit = int(request.GET.get('limit', 20))
    offset = int(request.GET.get('offset', 0))
    unread_only = request.GET.get('unread', 'false').lower() == 'true'
    
    logs = NotificationLog.objects.filter(user=request.user)
    
    if unread_only:
        logs = logs.filter(is_read=False)
    
    logs = logs[offset:offset + limit]
    
    data = [{
        'id': log.id,
        'title': log.title,
        'message': log.message,
        'url': log.url,
        'is_read': log.is_read,
        'sent_at': log.sent_at.isoformat(),
    } for log in logs]
    
    unread_count = NotificationLog.objects.filter(
        user=request.user,
        is_read=False
    ).count()
    
    return JsonResponse({
        'logs': data,
        'unread_count': unread_count,
        'has_more': logs.count() == limit
    })


@require_http_methods(["POST"])
@login_required
def mark_notification_read(request, log_id):
    """通知を既読にする"""
    from django.http import Http404
    try:
        log = get_object_or_404(NotificationLog, id=log_id, user=request.user)
        log.is_read = True
        log.read_at = timezone.now()
        log.save()

        return JsonResponse({'success': True})

    except Http404:
        raise
    except Exception as e:
        logger.error("Mark notification read error: %s", e, exc_info=True)
        return JsonResponse({'error': '既読処理に失敗しました'}, status=500)


@require_http_methods(["POST"])
@login_required
def mark_all_read(request):
    """すべての通知を既読にする"""
    try:
        NotificationLog.objects.filter(
            user=request.user,
            is_read=False
        ).update(is_read=True, read_at=timezone.now())
        
        return JsonResponse({'success': True})
        
    except Exception as e:
        logger.error("Mark all read error: %s", e, exc_info=True)
        return JsonResponse({'error': '既読処理に失敗しました'}, status=500)


# プッシュ通知送信ヘルパー
def send_push_notification(user, title, message, url='/', tag='notification', notification_id=None):
    """ユーザーにプッシュ通知を送信"""
    subscriptions = PushSubscription.objects.filter(
        user=user,
        is_active=True
    )

    # VAPID 鍵が未設定なら、ここで止めて明示的にログを残す（無言の未配信を防ぐ）
    vapid_private_key = settings.WEBPUSH_SETTINGS.get('VAPID_PRIVATE_KEY')
    if not vapid_private_key:
        logger.error(
            "send_push_notification: VAPID_PRIVATE_KEY が未設定のため送信できません "
            "(user=%s)", user.username
        )
        return 0

    payload = json.dumps({
        'title': title,
        'message': message,
        'url': url,
        'tag': tag,
        'notification_id': notification_id,
        'icon': '/static/images/icon-192.svg',
        'badge': '/static/images/badge-72.png',
    })

    success_count = 0
    failed_count = 0
    failed_subscriptions = []

    for subscription in subscriptions:
        try:
            webpush(
                subscription_info={
                    'endpoint': subscription.endpoint,
                    'keys': {
                        'p256dh': subscription.p256dh,
                        'auth': subscription.auth
                    }
                },
                data=payload,
                vapid_private_key=vapid_private_key,
                vapid_claims={
                    'sub': f'mailto:{settings.WEBPUSH_SETTINGS.get("VAPID_ADMIN_EMAIL")}'
                }
            )
            success_count += 1
            subscription.last_used = timezone.now()
            subscription.save()

        except WebPushException as e:
            failed_count += 1
            status_code = e.response.status_code if e.response is not None else None
            body = e.response.text if e.response is not None else ''
            if status_code in [404, 410]:
                # 端末側で購読が失効済み → 無効化
                logger.info(
                    "send_push_notification: 失効した購読を無効化 "
                    "(user=%s, status=%s, sub_id=%s)",
                    user.username, status_code, subscription.id
                )
                failed_subscriptions.append(subscription)
            else:
                # それ以外（VAPID 鍵不正/401/400 等）は原因特定のため必ずログ
                logger.error(
                    "send_push_notification: WebPush 送信失敗 "
                    "(user=%s, status=%s, sub_id=%s): %s",
                    user.username, status_code, subscription.id, body or str(e)
                )
        except Exception as e:
            # 鍵フォーマット不正など WebPushException 以外も握りつぶさずログ
            failed_count += 1
            logger.error(
                "send_push_notification: 想定外のエラー (user=%s, sub_id=%s): %s",
                user.username, subscription.id, e, exc_info=True
            )

    # 失効したサブスクリプションを無効化
    for sub in failed_subscriptions:
        sub.is_active = False
        sub.save()

    if failed_count:
        logger.warning(
            "send_push_notification: 送信結果 user=%s 成功=%d 失敗=%d",
            user.username, success_count, failed_count
        )

    return success_count

@login_required
@require_http_methods(["GET"])
def list_all_notifications(request):
    """すべての日記の通知設定一覧を取得"""
    try:
        notifications = DiaryNotification.objects.filter(
            diary__user=request.user
        ).select_related('diary').order_by('-created_at')

        notification_list = []
        for notification in notifications:
            notification_list.append({
                'id': str(notification.id),
                'diary_id': notification.diary.id,
                'diary_name': notification.diary.stock_name,
                'remind_at': notification.remind_at.isoformat(),
                'message': notification.message,
                'is_active': notification.is_active,
                'last_sent': notification.last_sent.isoformat() if notification.last_sent else None,
                'created_at': notification.created_at.isoformat(),
            })

        return JsonResponse({
            'success': True,
            'notifications': notification_list,
            'count': len(notification_list)
        })

    except Exception as e:
        logger.error("List all notifications error: %s", e, exc_info=True)
        return JsonResponse({
            'success': False,
            'error': '通知設定の取得に失敗しました'
        }, status=500)


@login_required
@require_http_methods(["GET"])
def get_hashtags(request):
    """
    ユーザーの日記から全てのハッシュタグを取得

    Query Parameters:
        - q: 検索クエリ（ハッシュタグのフィルタリング用）
        - limit: 返却する最大件数（デフォルト: 50）
    """
    try:
        query = request.GET.get('q', '').strip().lstrip('#')
        limit = int(request.GET.get('limit', 50))

        from stockdiary.tag_axis_config import get_master_axis_map
        from tags.models import Tag

        master_axis_map = get_master_axis_map()

        # 1. ダイアリーテキストから使用済みタグ（count付き）
        diaries = StockDiary.objects.filter(user=request.user)
        hashtags_from_text = get_all_hashtags_from_queryset(diaries)
        merged = {h['tag']: h for h in hashtags_from_text}

        # 2. Tag M2M（DB保存済みタグ）を補完 — axis情報はM2Mが優先
        for tag in Tag.objects.filter(user=request.user):
            if tag.name in merged:
                merged[tag.name]['axis'] = tag.axis
            else:
                merged[tag.name] = {'tag': tag.name, 'count': 0, 'axis': tag.axis}

        # 3. 標準タグ（MasterTag）を補完（まだmergedにないもの）
        for name, axis in master_axis_map.items():
            if name not in merged:
                merged[name] = {'tag': name, 'count': 0, 'axis': axis}
            elif 'axis' not in merged[name]:
                merged[name]['axis'] = axis

        # 4. 軸情報がまだないものはデフォルト
        for h in merged.values():
            if 'axis' not in h:
                h['axis'] = 'custom'

        # 5. ソート: count降順、同countなら標準タグ優先
        hashtags = sorted(
            merged.values(),
            key=lambda h: (-h.get('count', 0), 0 if h['tag'] in master_axis_map else 1),
        )

        # 6. クエリでフィルタリング
        if query:
            hashtags = [
                h for h in hashtags
                if query.lower() in h['tag'].lower()
            ]

        return JsonResponse({
            'success': True,
            'hashtags': hashtags[:limit],
            'count': len(hashtags)
        })

    except Exception as e:
        logger.error("Get hashtags error: %s", e, exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'ハッシュタグの取得に失敗しました'
        }, status=500)


# ==========================================
# 関連日記API
# ==========================================

def _diary_excerpt(diary, length=60):
    """日記の本文（reason）から簡易抜粋を生成"""
    import re
    text = (diary.reason or '').strip()
    text = re.sub(r'#{1,6}\s*', '', text)       # Markdown見出し除去
    text = re.sub(r'[*_`>\-]+', '', text)        # Markdown記号除去
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        return ''
    return text[:length] + ('…' if len(text) > length else '')


@login_required
@require_http_methods(["GET"])
def search_related_diaries(request, diary_id):
    """関連付けする日記を検索する"""
    diary = get_object_or_404(StockDiary, id=diary_id, user=request.user)
    query = request.GET.get('q', '').strip()

    if not query:
        return JsonResponse({'diaries': []})

    # 自身・既に関連付け済み・逆方向も除外
    already_linked_ids = set(diary.linked_diaries.values_list('id', flat=True))
    already_linked_ids |= set(diary.linked_from.values_list('id', flat=True))
    already_linked_ids.add(diary_id)

    results = StockDiary.objects.filter(
        user=request.user
    ).filter(
        Q(stock_name__icontains=query) | Q(stock_symbol__icontains=query) | Q(reason__icontains=query)
    ).exclude(
        id__in=already_linked_ids
    ).order_by('-updated_at')[:10]

    diaries = [
        {
            'id': d.id,
            'stock_name': d.stock_name,
            'stock_symbol': d.stock_symbol,
            'first_purchase_date': d.first_purchase_date.strftime('%Y/%m/%d') if d.first_purchase_date else None,
            'excerpt': _diary_excerpt(d),
        }
        for d in results
    ]
    return JsonResponse({'diaries': diaries})


@login_required
@require_http_methods(["GET"])
def search_my_diaries(request):
    """統一玄関(「書く」FAB)用：ログインユーザー自身の日記を検索する。

    diary_id を取らないため新規作成フローでも使える（既存への追記 or 新規作成の
    振り分け、本文の [[ 日記メンション候補）。docs/diary_recording_redesign.md 段階9b。

    クエリが空のときは最近更新した日記を返す（[[ 直後に即候補を出すため。
    @タグ補完が空クエリで全候補を返すのと挙動を揃える）。
    """
    query = request.GET.get('q', '').strip()

    qs = StockDiary.objects.filter(user=request.user)
    if query:
        qs = qs.filter(Q(stock_name__icontains=query) | Q(stock_symbol__icontains=query))
    results = list(qs.prefetch_related('notes').order_by('-updated_at')[:8])

    diaries = []
    for d in results:
        topics = list(
            d.notes.exclude(topic='').order_by('-date')
            .values_list('topic', flat=True).distinct()
        )[:8]
        diaries.append({
            'id': d.id,
            'stock_name': d.stock_name,
            'stock_symbol': d.stock_symbol,
            'topics': topics,
        })
    return JsonResponse({'diaries': diaries})


@login_required
@require_http_methods(["POST"])
def add_related_diary(request, diary_id):
    """関連日記を追加する（双方向）"""
    import json
    diary = get_object_or_404(StockDiary, id=diary_id, user=request.user)

    try:
        data = json.loads(request.body)
        related_id = int(data.get('related_id'))
    except (ValueError, TypeError, KeyError):
        return JsonResponse({'error': '無効なパラメータ'}, status=400)

    if related_id == diary_id:
        return JsonResponse({'error': '自分自身は関連付けできません'}, status=400)

    related = get_object_or_404(StockDiary, id=related_id, user=request.user)

    # 双方向で追加
    diary.linked_diaries.add(related)
    related.linked_diaries.add(diary)

    return JsonResponse({
        'success': True,
        'diary': {
            'id': related.id,
            'stock_name': related.stock_name,
            'stock_symbol': related.stock_symbol,
        }
    })


@login_required
@require_http_methods(["POST"])
def remove_related_diary(request, diary_id, related_id):
    """関連日記を解除する（双方向）"""
    diary = get_object_or_404(StockDiary, id=diary_id, user=request.user)
    related = get_object_or_404(StockDiary, id=related_id, user=request.user)

    # 双方向で削除
    diary.linked_diaries.remove(related)
    related.linked_diaries.remove(diary)

    return JsonResponse({'success': True})


@login_required
@require_http_methods(["GET"])
def diary_graph_data(request):
    """日記関連グラフのノード・エッジデータを返す。

    Query Parameters:
        status:     カンマ区切りのステータス列（holding,sold,memo / all）。デフォルト: all
        tag:        タグID（空文字 or 未指定で全て）
        edge_modes: カンマ区切りのモード列（manual,tag,sector,hashtag）
                    後方互換のため edge_mode（単数）も受け付ける

    edge_modes:
        manual   - 手動 linked_diaries リンク
        tag      - Tagをハブノードとして diary→tag エッジを生成
        sector   - 業種をハブノードとして diary→sector エッジを生成
        hashtag  - @ハッシュタグをハブノードとして diary→hashtag エッジを生成

    複数モードを同時に指定すると各モードのノード・エッジを統合して返す。
    """
    try:
        user = request.user
        tag_id = request.GET.get('tag', '').strip()

        VALID_STATUSES = {'holding', 'sold', 'memo', 'all'}
        status_param = request.GET.get('status', 'all').strip()
        statuses = {s.strip() for s in status_param.split(',') if s.strip() in VALID_STATUSES}

        # edge_modes（複数可）を解析。後方互換として edge_mode（単数）も受け付ける
        VALID_MODES = {'manual', 'tag', 'sector', 'hashtag', 'mention'}
        raw = request.GET.get('edge_modes', request.GET.get('edge_mode', 'tag')).strip()
        edge_modes = [m.strip() for m in raw.split(',') if m.strip() in VALID_MODES]
        if not edge_modes:
            edge_modes = ['tag']

        # 軸フィルター（カンマ区切り。指定がある場合はそのタグ軸のみ表示）
        VALID_AXES = {'theme', 'business_model', 'risk', 'capital_policy', 'macro', 'event'}
        axis_raw = request.GET.get('axes', '').strip()
        axis_filter = {a.strip() for a in axis_raw.split(',') if a.strip() in VALID_AXES} if axis_raw else set()

        all_user_qs = StockDiary.objects.filter(user=user, is_excluded=False)

        # --- primary: フィルター条件に合う日記 ---
        primary_qs = all_user_qs
        if statuses and 'all' not in statuses:
            status_q = Q()
            if 'holding' in statuses:
                status_q |= Q(current_quantity__gt=0)
            if 'sold' in statuses:
                status_q |= Q(transaction_count__gt=0, current_quantity=0)
            if 'memo' in statuses:
                status_q |= Q(transaction_count=0)
            primary_qs = primary_qs.filter(status_q)
        if tag_id:
            try:
                primary_qs = primary_qs.filter(tags__id=int(tag_id))
            except (ValueError, TypeError):
                pass

        primary_ids = set(primary_qs.values_list('id', flat=True))

        # 全モードで共通利用する primary 日記を一括取得
        # mention モードで reason 本文を参照するため常に含める
        primary_diaries = list(
            primary_qs.prefetch_related('tags', 'notes').only(
                'id', 'stock_name', 'stock_symbol', 'sector',
                'realized_profit', 'current_quantity', 'transaction_count',
                'reason', 'created_at',
            )
        )

        # 結果コンテナ（日記ノードは id で重複排除）
        diary_nodes_map = {}   # diary_id -> node dict
        hub_nodes_map = {}     # hub_id   -> node dict
        all_edges = []

        # ====================================================
        # manual モード: 手動リンク（diary-diary エッジ）
        # ====================================================
        if 'manual' in edge_modes:
            Through = StockDiary.linked_diaries.through
            is_filtered = (bool(statuses) and 'all' not in statuses) or bool(tag_id)
            secondary_ids = set()
            if is_filtered:
                linked_from = set(
                    Through.objects.filter(from_stockdiary_id__in=primary_ids)
                    .values_list('to_stockdiary_id', flat=True)
                )
                linked_to = set(
                    Through.objects.filter(to_stockdiary_id__in=primary_ids)
                    .values_list('from_stockdiary_id', flat=True)
                )
                secondary_ids = (linked_from | linked_to) - primary_ids

            manual_all_ids = primary_ids | secondary_ids

            # secondary 日記（フィルター外だが手動リンクで繋がっている日記）
            if secondary_ids:
                for d in all_user_qs.filter(id__in=secondary_ids).only(
                    'id', 'stock_name', 'stock_symbol', 'sector',
                    'realized_profit', 'current_quantity', 'transaction_count'
                ):
                    if d.id not in diary_nodes_map:
                        diary_nodes_map[d.id] = _build_diary_node(d, is_primary=False)

            raw_links = Through.objects.filter(
                from_stockdiary_id__in=manual_all_ids,
                to_stockdiary_id__in=manual_all_ids,
            ).values_list('from_stockdiary_id', 'to_stockdiary_id')

            manual_edge_set = set()
            for src, tgt in raw_links:
                manual_edge_set.add((min(src, tgt), max(src, tgt)))

            for s, t in manual_edge_set:
                all_edges.append({'source': s, 'target': t, 'edge_type': 'manual', 'weight': 1.0})

        # ====================================================
        # tag モード: タグハブノード（軸フィルター適用）
        # ====================================================
        if 'tag' in edge_modes:
            from stockdiary.tag_axis_config import RELATED_NOISE_MAX as _TAG_NOISE_MAX
            hub_data = get_tag_graph_data(primary_diaries)
            filtered_tag_ids = set()
            for hub in hub_data['tag_nodes']:
                # A: 孤立タグ（1銘柄のみ）を非表示
                if hub.get('diary_count', 0) < 2:
                    continue
                # B: 多すぎて意味のないタグ（ノイズ上限超え）を非表示
                if hub.get('diary_count', 0) > _TAG_NOISE_MAX:
                    continue
                # C: 個人管理ラベルはグラフに表示しない
                if hub.get('axis') == 'custom':
                    continue
                if axis_filter and hub.get('axis') not in axis_filter:
                    continue
                hub['link_count'] = hub.get('diary_count', 0)
                hub_nodes_map[hub['id']] = hub
                filtered_tag_ids.add(hub['id'])
            for e in hub_data['edges']:
                # グラフは銘柄↔タグのフラット接続のみ（タグ間の親子エッジは描かない）。
                if e.get('target') not in filtered_tag_ids:
                    continue
                all_edges.append(e)

        # ====================================================
        # sector モード: 業種ハブノード（CompanyMaster で補完）
        # ====================================================
        if 'sector' in edge_modes:
            # sector が空の日記について CompanyMaster から業種を補完
            symbols_without_sector = [
                d.stock_symbol for d in primary_diaries
                if not (d.sector or '').strip() and d.stock_symbol
            ]
            company_sector_map = {}
            if symbols_without_sector:
                from company_master.models import CompanyMaster
                for c in CompanyMaster.objects.filter(
                    code__in=symbols_without_sector
                ).values('code', 'industry_name_33', 'industry_name_17'):
                    company_sector_map[c['code']] = (
                        c['industry_name_33'] or c['industry_name_17'] or ''
                    )

            hub_data = get_sector_graph_data(primary_diaries, company_sector_map)
            for hub in hub_data['sector_nodes']:
                hub['link_count'] = hub.get('diary_count', 0)
                hub_nodes_map[hub['id']] = hub
            all_edges.extend(hub_data['edges'])

        # ====================================================
        # hashtag モード: @ハッシュタグハブノード（軸・A/B/Cフィルター適用）
        # ====================================================
        if 'hashtag' in edge_modes:
            from stockdiary.tag_axis_config import RELATED_NOISE_MAX as _HT_NOISE_MAX
            from tags.models import Tag as _TagModel
            # Tag M2M から {名前: 軸} マップを構築（軸オーバーライド用）
            user_tag_axis_map = dict(
                _TagModel.objects.filter(user=user).values_list('name', 'axis')
            )
            # @ハッシュタグは同名 Tag(M2M) に同期されるため、その Tag に設定された
            # 方向（DiaryTagDirection）を {(diary_id, タグ名): 方向} で引けるようにする。
            from .models import DiaryTagDirection as _DTD
            _tag_name_by_pk = dict(
                _TagModel.objects.filter(user=user).values_list('id', 'name')
            )
            user_tag_dir_map = {}
            for _d_id, _t_id, _dir in _DTD.objects.filter(
                diary_id__in=primary_ids
            ).values_list('diary_id', 'tag_id', 'direction'):
                _name = _tag_name_by_pk.get(_t_id)
                if _name and _dir in ('up', 'down'):
                    user_tag_dir_map[(_d_id, _name)] = _dir
            note_limit = getattr(request.user, 'diary_note_tag_limit', 3)
            hub_data = get_hashtag_graph_data(
                primary_diaries,
                note_limit=note_limit,
                user_tag_axis_map=user_tag_axis_map,
                user_tag_dir_map=user_tag_dir_map,
            )
            filtered_ht_ids = set()
            for hub in hub_data['hashtag_nodes']:
                # A: 孤立タグ（1銘柄のみ）を非表示
                if hub.get('diary_count', 0) < 2:
                    continue
                # B: 多すぎてノイズになるタグを非表示
                if hub.get('diary_count', 0) > _HT_NOISE_MAX:
                    continue
                # C: 個人管理ラベルはグラフに表示しない
                if hub.get('axis') == 'custom':
                    continue
                # D: 軸フィルター
                if axis_filter and hub.get('axis') not in axis_filter:
                    continue
                hub['link_count'] = hub.get('diary_count', 0)
                hub_nodes_map[hub['id']] = hub
                filtered_ht_ids.add(hub['id'])
            for e in hub_data['edges']:
                if e.get('target') not in filtered_ht_ids:
                    continue
                all_edges.append(e)

        # ====================================================
        # mention モード: テキスト内銘柄コード → diary-diary エッジ
        # ====================================================
        if 'mention' in edge_modes:
            # 全ユーザー日記の stock_symbol → diary_id マップを構築
            symbol_to_diary_id = {}
            for row in all_user_qs.filter(stock_symbol__gt='').values('id', 'stock_symbol'):
                if row['stock_symbol']:
                    symbol_to_diary_id[row['stock_symbol']] = row['id']

            mention_data = get_mention_graph_data(primary_diaries, symbol_to_diary_id)
            all_edges.extend(mention_data['edges'])

            # primary に含まれないメンション先日記をセカンダリノードとして追加
            mention_secondary_ids = mention_data['mentioned_diary_ids'] - set(diary_nodes_map.keys())
            if mention_secondary_ids:
                for d in all_user_qs.filter(id__in=mention_secondary_ids).only(
                    'id', 'stock_name', 'stock_symbol', 'sector',
                    'realized_profit', 'current_quantity', 'transaction_count'
                ):
                    if d.id not in diary_nodes_map:
                        diary_nodes_map[d.id] = _build_diary_node(d, is_primary=False)

        # ====================================================
        # primary 日記ノードを diary_nodes_map に追加（重複排除）
        # ====================================================
        for d in primary_diaries:
            if d.id not in diary_nodes_map:
                diary_nodes_map[d.id] = _build_diary_node(
                    d, is_primary=True, include_content=True
                )

        # link_count を全エッジから集計（diary ノードのみ）
        link_count_map = {}
        for e in all_edges:
            for key in ('source', 'target'):
                v = e[key]
                if isinstance(v, int):
                    link_count_map[v] = link_count_map.get(v, 0) + 1

        for diary_id, node in diary_nodes_map.items():
            node['link_count'] = link_count_map.get(diary_id, 0)

        # ====================================================
        # タグ/ハッシュタグ重複排除: 同名の tag と hashtag が
        # 共存する場合、hashtag を tag に統合する
        # （DiaryNote 内の未同期ハッシュタグは tag が存在しないため保持される）
        # ====================================================
        tag_name_to_tag_id = {
            node['tag_name']: node['id']
            for node in hub_nodes_map.values()
            if node.get('node_type') == 'tag' and node.get('tag_name')
        }
        ht_ids_to_remove = {
            node['id']
            for node in hub_nodes_map.values()
            if node.get('node_type') == 'hashtag'
            and node.get('tag_name') in tag_name_to_tag_id
        }
        for ht_id in ht_ids_to_remove:
            del hub_nodes_map[ht_id]
        if ht_ids_to_remove:
            for e in all_edges:
                tgt = e.get('target')
                if isinstance(tgt, str) and tgt.startswith('ht_'):
                    tag_name = tgt[3:]
                    if tag_name in tag_name_to_tag_id:
                        e['target'] = tag_name_to_tag_id[tag_name]
                        e['edge_type'] = 'tag'

        all_nodes = list(diary_nodes_map.values()) + list(hub_nodes_map.values())

        return JsonResponse({
            'nodes': all_nodes,
            'edges': all_edges,
            'meta': {
                'total_nodes': len(all_nodes),
                'total_edges': len(all_edges),
                'modes': edge_modes,
                'axes': list(axis_filter) if axis_filter else [],
            },
        })

    except Exception as e:
        logger.error("diary_graph_data error: %s", e, exc_info=True)
        return JsonResponse({'success': False, 'error': 'グラフデータの取得に失敗しました'}, status=500)


def _diary_status(diary) -> str:
    """日記オブジェクトから保有ステータス文字列を返す"""
    if diary.current_quantity > 0:
        return 'holding'
    elif diary.transaction_count > 0:
        return 'sold'
    return 'memo'


def _text_excerpt(text, limit: int = 90) -> str:
    """Markdownテキストから装飾記号を除いた冒頭抜粋を返す"""
    if not text:
        return ''
    t = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text)          # 画像
    t = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', t)          # リンク→表示文字
    t = re.sub(r'[#*`>~|]+', '', t)                          # 見出し・強調等の記号
    t = ' '.join(t.split())
    return t[:limit] + ('…' if len(t) > limit else '')


def _build_diary_node(diary, *, is_primary: bool, include_content: bool = False) -> dict:
    """グラフAPI共通の diary ノード dict を生成する。

    include_content=True の場合、サイドパネル表示用に投資理由の抜粋と
    継続記録（DiaryNote）のサマリー・鮮度情報を付与する。
    notes が prefetch 済みの QuerySet にのみ指定すること。
    """
    node = {
        'id': diary.id,
        'node_type': 'diary',
        'stock_name': diary.stock_name,
        'stock_symbol': diary.stock_symbol or '',
        'status': _diary_status(diary),
        'sector': diary.sector or '未分類',
        'realized_profit': float(diary.realized_profit),
        'link_count': 0,
        'url': f'/stockdiary/{diary.id}/',
        'is_primary': is_primary,
    }
    if include_content:
        node['reason_excerpt'] = _text_excerpt(diary.reason)
        notes = list(diary.notes.all())
        node['note_count'] = len(notes)
        # 売却済み銘柄の「振り返り未記入」可視化用
        node['has_retrospective'] = any(
            n.note_type == 'retrospective' for n in notes
        )
        if notes:
            latest = max(notes, key=lambda n: (n.date, n.id))
            node['last_note_date'] = latest.date.isoformat()
            node['last_note_type'] = latest.get_note_type_display()
            node['last_note_excerpt'] = _text_excerpt(latest.content, 80)
        created = getattr(diary, 'created_at', None)
        if created:
            node['created_date'] = created.date().isoformat()
    return node


@login_required
@require_http_methods(["GET"])
def diary_detail_graph_data(request, diary_id):
    """日記詳細画面用：特定の日記を中心とした関連グラフデータを返す。

    Query Parameters:
        edge_modes: カンマ区切りのモード列（manual,tag,sector,hashtag,mention）
                    デフォルトは全モード

    Returns:
        {
          nodes: [...],  # diary ノード + ハブノード
          edges: [...],
          meta: { total_nodes, total_edges, modes, focal_diary_id }
        }
        diary ノードには is_focal: true/false フラグを含む
    """
    try:
        focal = get_object_or_404(StockDiary, id=diary_id, user=request.user)

        VALID_MODES = {'manual', 'tag', 'sector', 'hashtag', 'mention'}
        raw = request.GET.get('edge_modes', 'manual,tag,sector,hashtag,mention').strip()
        edge_modes = [m.strip() for m in raw.split(',') if m.strip() in VALID_MODES]
        if not edge_modes:
            edge_modes = ['manual', 'tag', 'sector', 'hashtag', 'mention']

        # ── 近傍日記IDを収集 ──────────────────────────────────────────
        neighbor_ids = set()

        # 手動リンク（両方向）
        manual_linked_ids = set(focal.linked_diaries.values_list('id', flat=True))
        manual_from_ids = set(focal.linked_from.values_list('id', flat=True))
        if 'manual' in edge_modes:
            neighbor_ids |= manual_linked_ids | manual_from_ids

        # 同一銘柄コードの日記（常に含める）
        if focal.stock_symbol:
            same_symbol_ids = set(
                StockDiary.objects.filter(
                    user=request.user,
                    stock_symbol=focal.stock_symbol,
                    is_excluded=False,
                ).exclude(id=focal.id).values_list('id', flat=True)
            )
            neighbor_ids |= same_symbol_ids

        # テキスト内言及銘柄の日記
        if 'mention' in edge_modes:
            search_text = focal.reason or ''
            mentioned_codes = extract_stock_mentions(search_text)
            if mentioned_codes:
                mentioned_ids = set(
                    StockDiary.objects.filter(
                        user=request.user,
                        stock_symbol__in=mentioned_codes,
                        is_excluded=False,
                    ).exclude(id=focal.id).values_list('id', flat=True)
                )
                neighbor_ids |= mentioned_ids

        # ── 全ノード日記を取得 ──────────────────────────────────────────
        all_diary_ids = {focal.id} | neighbor_ids
        all_diaries = list(
            StockDiary.objects.filter(id__in=all_diary_ids)
            .prefetch_related('tags', 'notes')
            .only(
                'id', 'stock_name', 'stock_symbol', 'sector',
                'realized_profit', 'current_quantity', 'transaction_count',
                'reason',
            )
        )

        diary_nodes_map = {}
        hub_nodes_map = {}
        all_edges = []

        for d in all_diaries:
            node = _build_diary_node(d, is_primary=True)
            node['is_focal'] = d.id == focal.id
            diary_nodes_map[d.id] = node

        # ── エッジ構築 ──────────────────────────────────────────────────
        # manual エッジ
        if 'manual' in edge_modes:
            edge_set = set()
            for linked_id in manual_linked_ids:
                if linked_id in diary_nodes_map:
                    key = (focal.id, linked_id)
                    if key not in edge_set:
                        edge_set.add(key)
                        all_edges.append({'source': focal.id, 'target': linked_id, 'edge_type': 'manual', 'weight': 1.0})
            for linked_id in manual_from_ids:
                if linked_id in diary_nodes_map:
                    key = tuple(sorted([focal.id, linked_id]))
                    if key not in edge_set:
                        edge_set.add(key)
                        all_edges.append({'source': linked_id, 'target': focal.id, 'edge_type': 'manual', 'weight': 1.0})

        # tag ハブノード（フォーカル日記のタグのみ）
        if 'tag' in edge_modes:
            focal_list = [d for d in all_diaries if d.id == focal.id]
            hub_data = get_tag_graph_data(focal_list)
            for hub in hub_data['tag_nodes']:
                hub['link_count'] = hub.get('diary_count', 0)
                hub_nodes_map[hub['id']] = hub
            all_edges.extend(hub_data['edges'])

        # sector ハブノード（フォーカル日記の業種のみ）
        if 'sector' in edge_modes and focal.sector:
            sector_id = f'sec_{focal.sector}'
            hub_nodes_map[sector_id] = {
                'id': sector_id,
                'node_type': 'sector',
                'tag_name': focal.sector,
                'diary_count': 1,
                'link_count': 1,
            }
            all_edges.append({'source': focal.id, 'target': sector_id, 'edge_type': 'sector', 'weight': 1.0})

        # hashtag ハブノード（フォーカル日記のハッシュタグのみ）
        if 'hashtag' in edge_modes:
            focal_list = [d for d in all_diaries if d.id == focal.id]
            note_limit = getattr(request.user, 'diary_note_tag_limit', 3)
            hub_data = get_hashtag_graph_data(focal_list, note_limit=note_limit)
            for hub in hub_data['hashtag_nodes']:
                hub['link_count'] = hub.get('diary_count', 0)
                hub_nodes_map[hub['id']] = hub
            all_edges.extend(hub_data['edges'])

        # mention エッジ（フォーカル日記のテキスト内言及）
        if 'mention' in edge_modes:
            symbol_to_id = {d.stock_symbol: d.id for d in all_diaries if d.stock_symbol}
            focal_list = [d for d in all_diaries if d.id == focal.id]
            mention_data = get_mention_graph_data(focal_list, symbol_to_id)
            all_edges.extend(mention_data['edges'])

        # ── link_count 集計 ──────────────────────────────────────────────
        link_count_map = {}
        for e in all_edges:
            for key in ('source', 'target'):
                v = e[key]
                if isinstance(v, int):
                    link_count_map[v] = link_count_map.get(v, 0) + 1

        for nid, node in diary_nodes_map.items():
            node['link_count'] = link_count_map.get(nid, 0)

        # タグ/ハッシュタグ重複排除: 同名の tag と hashtag が共存する場合、hashtag を tag に統合する
        tag_name_to_tag_id = {
            node['tag_name']: node['id']
            for node in hub_nodes_map.values()
            if node.get('node_type') == 'tag' and node.get('tag_name')
        }
        ht_ids_to_remove = {
            node['id']
            for node in hub_nodes_map.values()
            if node.get('node_type') == 'hashtag'
            and node.get('tag_name') in tag_name_to_tag_id
        }
        for ht_id in ht_ids_to_remove:
            del hub_nodes_map[ht_id]
        if ht_ids_to_remove:
            for e in all_edges:
                tgt = e.get('target')
                if isinstance(tgt, str) and tgt.startswith('ht_'):
                    tag_name = tgt[3:]
                    if tag_name in tag_name_to_tag_id:
                        e['target'] = tag_name_to_tag_id[tag_name]
                        e['edge_type'] = 'tag'

        all_nodes = list(diary_nodes_map.values()) + list(hub_nodes_map.values())

        return JsonResponse({
            'nodes': all_nodes,
            'edges': all_edges,
            'meta': {
                'total_nodes': len(all_nodes),
                'total_edges': len(all_edges),
                'modes': edge_modes,
                'focal_diary_id': focal.id,
            },
        })

    except Exception as e:
        logger.error("diary_detail_graph_data error: %s", e, exc_info=True)
        return JsonResponse({'success': False, 'error': 'グラフデータの取得に失敗しました'}, status=500)


@login_required
@require_GET
def link_preview(request):
    """外部URLのOGPメタデータを取得してプレビュー情報をJSONで返す。"""
    url = request.GET.get('url', '').strip()

    # URLバリデーション（http/httpsのみ許可）
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            return JsonResponse({'error': 'invalid_url'}, status=400)
    except Exception:
        return JsonResponse({'error': 'invalid_url'}, status=400)

    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; Kabulog-LinkPreview/1.0)',
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'ja,en;q=0.9',
    }

    try:
        resp = requests.get(url, headers=headers, timeout=8, allow_redirects=True)
        resp.raise_for_status()
        content_type = resp.headers.get('Content-Type', '')
        if 'text/html' not in content_type:
            return JsonResponse({'error': 'not_html'}, status=422)
    except requests.exceptions.Timeout:
        return JsonResponse({'error': 'timeout'}, status=504)
    except Exception:
        return JsonResponse({'error': 'fetch_failed'}, status=502)

    try:
        soup = BeautifulSoup(resp.content, 'lxml')
    except Exception:
        soup = BeautifulSoup(resp.content, 'html.parser')

    def og(prop):
        tag = soup.find('meta', property=f'og:{prop}')
        if tag:
            return tag.get('content', '').strip()
        return ''

    def meta_name(name):
        tag = soup.find('meta', attrs={'name': name})
        if tag:
            return tag.get('content', '').strip()
        return ''

    title = og('title') or (soup.title.string.strip() if soup.title and soup.title.string else '') or ''
    description = og('description') or meta_name('description') or ''
    image = og('image') or ''
    site_name = og('site_name') or parsed.netloc

    # og:image が相対URLの場合は絶対URLに変換
    if image and not image.startswith(('http://', 'https://')):
        image = urljoin(url, image)

    # 文字数制限
    title = title[:200]
    description = description[:500]
    site_name = site_name[:100]

    return JsonResponse({
        'title': title,
        'description': description,
        'image': image,
        'site_name': site_name,
        'url': url,
    })

@login_required
@require_GET
def graph_neighbors(request):
    """ドリルダウン探索グラフの近傍API（TG-DD）。

    GET /stockdiary/api/graph/neighbors/?node=tag:<pk> | stock:<symbol>
    ログインユーザーのデータのみを対象に、指定ノードの隣接を1段返す。
    設計: docs/graph_drilldown_redesign.md
    """
    from .utils import get_graph_neighbors

    node_id = request.GET.get('node', '')
    result = get_graph_neighbors(request.user, node_id)
    if result is None:
        return JsonResponse({'error': 'node not found'}, status=404)
    return JsonResponse(result)
