from django.urls import path
from . import views

urlpatterns = [
   path('start_categories_scraper/', views.start_categories_scraper, name='start-categories-scraper'),
   path('start_items_downloader/', views.start_items_downloader, name='start-items-downloader'),
]