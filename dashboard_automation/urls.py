from django.urls import path
from . import views

urlpatterns = [
   path('', views.index, name='index'),
   path('start_auto_approve/', views.start_auto_approve, name='start-auto-approve'),
   path('start_dashboard_image_downloader/', views.start_dashboard_image_downloader, name='start-dashboard-image-downloader'),
   path('start_image_uploader/', views.start_image_uploader, name='start-image-uploader'),
   path('start_categories_creator/', views.start_categories_creator, name='start-categories-creator'),
   path('start_categories_extractor/', views.start_categories_extractor, name='start-categories-extractor'),
   path('task_status/', views.task_status_view, name='task-status'),
   path('terminate_task/', views.terminate_task_view, name='task-terminate'),
]