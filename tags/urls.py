# tags/urls.py
from django.urls import path
from . import views
from . import api


app_name = 'tags'

urlpatterns = [
    path('', views.TagListView.as_view(), name='list'),
    path('create/', views.TagCreateView.as_view(), name='create'),
    path('<int:pk>/', views.TagDetailView.as_view(), name='detail'),
    path('<int:pk>/update/', views.TagUpdateView.as_view(), name='update'),
    path('<int:pk>/delete/', views.TagDeleteView.as_view(), name='delete'),
    path('<int:pk>/book/', views.TagBookView.as_view(), name='book'),  # 本モード
    path('<int:pk>/direction/', views.set_tag_direction, name='set_direction'),  # 方向トグル(HTMX)
    path('bulk-delete-unused/', views.bulk_delete_unused_tags, name='bulk_delete_unused'),
    path('<int:pk>/assign-children/', views.bulk_assign_children, name='bulk_assign_children'),

    path('api/list/', api.list_tags, name='api_list'),
]