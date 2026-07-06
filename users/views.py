# users/views.py
import datetime
from collections import defaultdict

from django.contrib.auth.views import LoginView, LogoutView
from django.views.generic import TemplateView, DeleteView
from django.urls import reverse_lazy, reverse
from django.views.generic import CreateView
from django.contrib.auth import get_user_model
from .forms import CustomUserCreationForm, CustomAuthenticationForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import (
    PasswordResetView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView
)
from .forms import CustomPasswordResetForm
from django.views.generic import FormView
from django.contrib.auth.forms import AuthenticationForm
from allauth.socialaccount.models import SocialAccount
from django.views.decorators.http import require_http_methods
from django.db.models.functions import TruncDate
from django.db.models import Count
from django.utils import timezone

from django.contrib.auth import logout as auth_logout, get_user_model
from django.shortcuts import redirect
from django.contrib.auth import login

User = get_user_model()

class CustomLoginView(LoginView):
    template_name = 'users/login.html'
    redirect_authenticated_user = True
    authentication_form = CustomAuthenticationForm
    
    def get_success_url(self):
        return reverse_lazy('stockdiary:home')

class CustomLogoutView(LogoutView):
    http_method_names = ['get', 'post']
    template_name = 'users/logout.html'
    
    def dispatch(self, request, *args, **kwargs):
        # 明示的にログアウト処理を行う
        if request.user.is_authenticated:
            auth_logout(request)
        return super().dispatch(request, *args, **kwargs)
        
class SignUpView(CreateView):
    model = User
    form_class = CustomUserCreationForm
    template_name = 'users/signup.html'
    success_url = reverse_lazy('stockdiary:home')

    def form_valid(self, form):
        # 登録→ログイン画面に戻す摩擦をなくし、そのままホームへ。
        # ?signup=1 を付けて GA4 の sign_up コンバージョンを発火させる。
        super().form_valid(form)
        login(self.request, self.object,
              backend='django.contrib.auth.backends.ModelBackend')
        return redirect(f"{reverse('stockdiary:home')}?signup=1")

@require_http_methods(["POST"])
def demo_login(request):
    """ワンクリックでデモ共有アカウントにログインする。

    登録の摩擦なくサービスを体験してもらうための入口。
    対象は settings.DEMO_USERNAME の既存ユーザー（reset_demo コマンドで作成・維持）。
    安全のため POST 限定。デモ無効時・ユーザー未作成時はログイン画面へ戻す。
    """
    from django.conf import settings
    from django.contrib import messages

    if not getattr(settings, 'DEMO_ENABLED', False):
        return redirect('users:login')

    demo_username = getattr(settings, 'DEMO_USERNAME', 'demo')
    try:
        demo_user = User.objects.get(username=demo_username)
    except User.DoesNotExist:
        messages.error(request, 'デモは現在準備中です。少し時間をおいてお試しください。')
        return redirect('users:login')

    # 万一デモユーザーに強い権限が付いていたらログインさせない（事故防止）
    if demo_user.is_staff or demo_user.is_superuser or not demo_user.is_active:
        messages.error(request, 'デモは現在ご利用いただけません。')
        return redirect('users:login')

    login(request, demo_user, backend='django.contrib.auth.backends.ModelBackend')
    messages.info(
        request,
        'デモアカウントでログインしました。自由にお試しください'
        '（データは定期的にリセットされます）。',
    )
    return redirect('stockdiary:home')


class GoogleLoginView(TemplateView):
    template_name = 'users/google_login.html'


class ProfileView(LoginRequiredMixin, TemplateView):
    template_name = 'users/profile.html'

    # 活動ヒートマップの表示期間（GitHubの草に合わせて53週分＝1年強）
    HEATMAP_WEEKS = 53

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        # ユーザーの統計情報を取得
        context['diary_count'] = user.stockdiary_set.count()
        context['tag_count'] = user.tag_set.count()
        context['template_count'] = user.analysis_templates.count()

        # 最近の投資日記を取得（最新5件）
        context['recent_diaries'] = user.stockdiary_set.all().order_by('-created_at')[:5]

        # サブスクリプション情報を追加
        try:
            subscription = user.subscription
            context['subscription'] = subscription
            context['subscription_plan'] = subscription.plan
        except:
            # サブスクリプションがない場合はフリープラン情報を取得
            try:
                from subscriptions.models import SubscriptionPlan
                context['subscription_plan'] = SubscriptionPlan.objects.get(slug='free')
            except:
                context['subscription_plan'] = None

        context.update(self._build_activity_heatmap(user))

        return context

    def _build_activity_heatmap(self, user):
        """GitHub風の活動ヒートマップ用データを組み立てる。

        「活動」= その日記録された日記作成・取引・継続記録（DiaryNote）の合計件数。
        継続度が一目でわかるよう、日曜始まりの週カラムに整形して返す。
        """
        from stockdiary.models import Transaction, DiaryNote

        today = timezone.localdate()
        # Pythonのweekday()はMon=0..Sun=6。日曜始まりに変換（Sun=0..Sat=6）
        sunday_index = (today.weekday() + 1) % 7
        week_end = today + datetime.timedelta(days=6 - sunday_index)  # 今週の土曜日
        total_days = self.HEATMAP_WEEKS * 7
        start_date = week_end - datetime.timedelta(days=total_days - 1)  # 表示開始日（日曜日）

        activity_by_date = defaultdict(int)
        querysets = [
            user.stockdiary_set.filter(created_at__date__gte=start_date),
            Transaction.objects.filter(diary__user=user, created_at__date__gte=start_date),
            DiaryNote.objects.filter(diary__user=user, created_at__date__gte=start_date),
        ]
        for qs in querysets:
            counts = (
                qs.annotate(activity_date=TruncDate('created_at'))
                .values('activity_date')
                .annotate(total=Count('id'))
            )
            for row in counts:
                activity_by_date[row['activity_date']] += row['total']

        def level_for(count):
            if count <= 0:
                return 0
            if count == 1:
                return 1
            if count <= 3:
                return 2
            if count <= 6:
                return 3
            return 4

        weeks = []
        prev_month = None
        for week_index in range(self.HEATMAP_WEEKS):
            week_sunday = start_date + datetime.timedelta(days=week_index * 7)
            days = []
            for day_offset in range(7):
                day = week_sunday + datetime.timedelta(days=day_offset)
                is_future = day > today
                count = 0 if is_future else activity_by_date.get(day, 0)
                days.append({
                    'date': day,
                    'count': count,
                    'level': level_for(count),
                    'is_future': is_future,
                    'is_today': day == today,
                })
            month_label = None
            if week_sunday.month != prev_month:
                month_label = week_sunday.month
                prev_month = week_sunday.month
            weeks.append({'days': days, 'month_label': month_label})

        # 継続日数（ストリーク）の算出
        current_streak = 0
        cursor = today if activity_by_date.get(today, 0) > 0 else today - datetime.timedelta(days=1)
        while activity_by_date.get(cursor, 0) > 0:
            current_streak += 1
            cursor -= datetime.timedelta(days=1)

        longest_streak = 0
        running = 0
        for day_index in range(total_days):
            day = start_date + datetime.timedelta(days=day_index)
            if day > today:
                break
            if activity_by_date.get(day, 0) > 0:
                running += 1
                longest_streak = max(longest_streak, running)
            else:
                running = 0

        active_days = sum(1 for d, c in activity_by_date.items() if start_date <= d <= today and c > 0)

        return {
            'heatmap_weeks': weeks,
            'heatmap_weekday_labels': ['', '月', '', '水', '', '金', ''],
            'current_streak': current_streak,
            'longest_streak': longest_streak,
            'active_days': active_days,
        }

class AccountDeleteConfirmView(LoginRequiredMixin, TemplateView):
    """アカウント削除の確認画面を表示するビュー"""
    template_name = 'users/account_delete_confirm.html'

class AccountDeleteView(LoginRequiredMixin, DeleteView):
    """アカウントを削除するビュー"""
    model = get_user_model()
    success_url = reverse_lazy('users:login')
    template_name = 'users/account_deleted.html'
    
    def get_object(self, queryset=None):
        # 現在ログインしているユーザーを対象にする
        return self.request.user
    
    def delete(self, request, *args, **kwargs):
        # ユーザーの投稿・コメント等を削除するカスタム処理を行う場合はここに記述
        user = self.get_object()
        messages.success(request, 'アカウントを削除しました。ご利用ありがとうございました。')
        
        # ログアウト処理を先に行う
        logout(request)
        
        # ユーザーを削除
        user.delete()
        
        return redirect(self.success_url)

# users/views.py に以下を追加（冒頭のインポート部分）
from django.contrib.auth.views import PasswordChangeView
from django.views.generic.edit import UpdateView
from django.contrib import messages
from django.contrib.auth import logout  # 既存のImportがなければ追加
from .forms import CustomPasswordChangeForm, CustomUserChangeForm

# 既存のビュークラスと一緒に以下を追加

class CustomPasswordChangeView(LoginRequiredMixin, PasswordChangeView):
    """パスワード変更ビュー"""
    form_class = CustomPasswordChangeForm
    template_name = 'users/password_change.html'
    success_url = reverse_lazy('users:profile')
    
    def form_valid(self, form):
        # フォームが有効な場合の処理
        response = super().form_valid(form)
        messages.success(self.request, 'パスワードが正常に変更されました。')
        return response

class ProfileUpdateView(LoginRequiredMixin, UpdateView):
    """プロフィール更新ビュー"""
    model = User
    form_class = CustomUserChangeForm
    template_name = 'users/profile_update.html'
    success_url = reverse_lazy('users:profile')
    
    def get_object(self, queryset=None):
        return self.request.user
    
    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'プロフィール情報が更新されました。')
        return response        


class CustomPasswordResetView(PasswordResetView):
    template_name = 'users/password_reset.html'
    email_template_name = 'users/password_reset_email_text.txt'  # プレーンテキスト版
    html_email_template_name = 'users/password_reset_email.html'  # HTML版
    subject_template_name = 'users/password_reset_subject.txt'
    form_class = CustomPasswordResetForm
    success_url = reverse_lazy('users:password_reset_done')

    def dispatch(self, request, *args, **kwargs):
        # ユーザーのメールアドレスを取得（フォーム送信時）
        if request.method == 'POST':
            email = request.POST.get('email')
            if email:
                # ユーザーを検索
                try:
                    user = User.objects.get(email=email)
                    # Google認証ユーザーかどうかを確認
                    if user.socialaccount_set.filter(provider='google').exists():
                        messages.info(request, 'このアカウントはGoogle認証で登録されています。Googleアカウントのパスワードリセットを行ってください。')
                        return redirect('users:password_reset')
                except User.DoesNotExist:
                    pass
        return super().dispatch(request, *args, **kwargs)
        
class CustomPasswordResetDoneView(PasswordResetDoneView):
    """パスワードリセットメール送信完了ビュー"""
    template_name = 'users/password_reset_done.html'

class CustomPasswordResetConfirmView(PasswordResetConfirmView):
    """パスワードリセット確認ビュー"""
    template_name = 'users/password_reset_confirm.html'
    success_url = reverse_lazy('users:password_reset_complete')

class CustomPasswordResetCompleteView(PasswordResetCompleteView):
    """パスワードリセット完了ビュー"""
    template_name = 'users/password_reset_complete.html'        


class SocialAccountConnectedView(LoginRequiredMixin, TemplateView):
    """ソーシャルアカウント接続完了ビュー"""
    template_name = 'socialaccount/signup_success.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        # ユーザーのソーシャルアカウント情報を取得
        try:
            social_account = SocialAccount.objects.get(user=user, provider='google')
            context['social_account'] = social_account
            context['social_data'] = social_account.extra_data
        except SocialAccount.DoesNotExist:
            context['social_account'] = None
            
        return context


class ConnectExistingAccountView(FormView):
    """既存アカウントとGoogleアカウントを連携するビュー"""
    template_name = 'users/connect_existing_account.html'
    form_class = AuthenticationForm
    success_url = reverse_lazy('stockdiary:home')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['email'] = self.request.session.get('connect_email', '')
        return context
    
    def form_valid(self, form):
        # ユーザーをログイン
        login(self.request, form.get_user())
        user = form.get_user()
        
        # セッションからGoogleアカウントのデータを取得
        email = self.request.session.get('connect_email')
        
        if email:
            # 既存アカウントとGoogleアカウントを関連付ける
            try:
                social_account = SocialAccount.objects.get(email=email, provider='google')
                social_account.user = user
                social_account.save()
                messages.success(self.request, 'Googleアカウントと既存アカウントが連携されました')
            except SocialAccount.DoesNotExist:
                messages.error(self.request, 'Google認証情報が見つかりませんでした')
            
            # セッションをクリア
            if 'connect_email' in self.request.session:
                del self.request.session['connect_email']
        
        return super().form_valid(form)            

class EmailDuplicateNotificationView(TemplateView):
    """メールアドレスが重複している場合の通知ビュー"""
    template_name = 'users/email_duplicate.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['email'] = self.request.GET.get('email', '')
        return context
