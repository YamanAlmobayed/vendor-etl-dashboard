from django.urls import path
from . import views

urlpatterns = [
   path('api-token-auth/', views.CustomAuthToken.as_view(), name='api_token_auth'),
   path('register/', views.UserRegistrationView.as_view(), name='user_registration'),
   path('logout/', views.LogoutView.as_view(), name='user_logout'),
   path('details/', views.UserDetailsView.as_view(), name='user_details'),
]