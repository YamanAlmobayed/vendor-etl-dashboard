from django.db import models
from django.contrib.auth.models import AbstractUser

class CustomUser(AbstractUser):
    ROLE_CHOICES = [
        ('super_admin', 'Super Admin'),
        ('admin', 'Admin'),
        ('user', 'User'),
    ]
    
    username = models.CharField(max_length=150, unique=False)
    email = models.EmailField(unique=True, blank=False, null=False)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='user')
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    def __str__(self):
        return f"{self.email} ({self.role})"
    
    def is_super_admin(self):
        return self.role == 'super_admin'
    
    def is_admin(self):
        return self.role == 'admin'
    
    def is_admin_or_above(self):
        return self.role in ['admin', 'super_admin']