from rest_framework import generics, permissions
from .serializers import UserRegistrationSerializer, CustomAuthTokenSerializer
from .models import CustomUser
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.authtoken.models import Token


class UserRegistrationView(generics.CreateAPIView):
    queryset = CustomUser.objects.all()
    serializer_class = UserRegistrationSerializer
    permission_classes = [permissions.AllowAny]
    
    def create(self, request, *args, **kwargs):
        # Check if trying to create a super_admin
        if request.data.get('role') == 'super_admin':
            # Only existing super_admins can create new super_admins
            if not (request.user.is_authenticated and request.user.role == 'super_admin'):
                return Response(
                    {'error': 'Only super admins can create super admin accounts'}, 
                    status=status.HTTP_403_FORBIDDEN
                )
        
        return super().create(request, *args, **kwargs)
    
class UserDetailsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, format=None):
        serializer = UserRegistrationSerializer(request.user)
        return Response(serializer.data)
    
class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        request.user.auth_token.delete()
        return Response(status=status.HTTP_200_OK)
    
class CustomAuthToken(ObtainAuthToken):
    serializer_class = CustomAuthTokenSerializer
    
    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data,
                                           context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        
        token, created = Token.objects.get_or_create(user=user)
        return Response({
            'token': token.key,
            'user_id': user.pk,
            'email': user.email,
            'username': user.username,
            'role': user.role,
            'last_login': user.last_login  # Optional: include in response
        })