from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser

class CustomUserAdmin(UserAdmin):
    model = CustomUser
    
    list_display = ('email', 'username', 'role', 'is_active', 'is_superuser', 'date_joined')
    list_filter = ('role', 'is_active', 'is_superuser', 'date_joined')
    search_fields = ('email', 'username')
    ordering = ('email',)
    
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'email', 'role')}),
        ('Permissions', {'fields': ('is_active', 'is_superuser')}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'email', 'role', 'password1', 'password2'),
        }),
    )
    
    readonly_fields = ('date_joined', 'last_login')
    filter_horizontal = ()
    
    def save_model(self, request, obj, form, change):
        # Automatically set superuser status based on role
        if obj.role == 'super_admin':
            obj.is_superuser = True
            obj.is_staff = True  # Super admins should be staff too
        else:
            obj.is_superuser = False
            # You might want to keep is_staff as True for admins if they need admin access
            if obj.role != 'admin':
                obj.is_staff = False
        
        super().save_model(request, obj, form, change)

admin.site.register(CustomUser, CustomUserAdmin)

admin.site.site_header = "Control Dashboard Admin"
admin.site.site_title = "Control Dashboard Admin"
admin.site.index_title = "Welcome to Control Dashboard Admin"