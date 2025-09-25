from rest_framework import serializers
from .models import CustomUser
from django.contrib.auth import authenticate
from django.contrib.auth.models import update_last_login


class UserRegistrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ('id', 'username', 'email', 'password', 'role')
        extra_kwargs = {'password': {'write_only': True}}
    
    def create(self, validated_data):
        role = validated_data.get('role', 'user')
        
        user = CustomUser.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
            role=role
        )
        
        # If role is super_admin, make them a superuser
        if role == 'super_admin':
            user.is_superuser = True
            user.is_staff = True  # Usually superusers are also staff
            user.save()
        
        return user

class CustomAuthTokenSerializer(serializers.Serializer):
    email = serializers.EmailField(write_only=True)
    password = serializers.CharField(write_only=True)
    
    def validate(self, data):
        email = data.get('email')
        password = data.get('password')
        
        if email and password:
            user = authenticate(request=self.context.get('request'), username=email, password=password)
            if not user:
                raise serializers.ValidationError('Unable to log in with provided credentials.', code='authorization')
            
            # Check if user is active
            if not user.is_active:
                raise serializers.ValidationError('User account is disabled.', code='authorization')
            
            # Update last_login timestamp
            update_last_login(None, user)
            
        else:
            raise serializers.ValidationError('Must include "email" and "password".', code='authorization')
        
        data['user'] = user
        return data

class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ('id', 'username', 'email', 'role', 'date_joined')
        read_only_fields = ('id', 'date_joined')