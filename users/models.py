from django.db import models
from django.contrib.auth.models import AbstractUser

# Create your models here.
class CustomUser(AbstractUser):
    username = models.CharField(max_length=150, unique=False)
    email = models.EmailField(unique=True, blank=False, null=False)
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']