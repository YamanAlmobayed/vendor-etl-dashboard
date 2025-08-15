from django.urls import path
from . import views

urlpatterns = [
   path('', views.index, name='index'),
   path('start_auto_approve/', views.start_auto_approve, name='start-auto-approve'),
   path('sample_task/', views.start_task_view, name='sample-task'),
]