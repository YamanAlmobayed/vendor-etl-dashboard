from django.urls import path
from . import views

urlpatterns = [
    path('api/process-images/', views.process_images, name='process_images'),
    path('api/process-images-comfyui/', views.process_images_comfyui, name='process_images_comfyui'),
    #path('api/download/<str:task_id>/', views.download_result, name='download_result'),
]