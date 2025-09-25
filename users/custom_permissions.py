# permissions.py
from rest_framework.permissions import BasePermission

class IsSuperAdmin(BasePermission):
    """
    Custom permission to only allow super admin users.
    """
    def has_permission(self, request, view):
        return (request.user.is_authenticated and 
                hasattr(request.user, 'role') and 
                request.user.role == 'super_admin')

class IsAdmin(BasePermission):
    """
    Custom permission to only allow admin users.
    """
    def has_permission(self, request, view):
        return (request.user.is_authenticated and 
                hasattr(request.user, 'role') and 
                request.user.role == 'admin')

class IsAdminOrAbove(BasePermission):
    """
    Custom permission to allow admin or super admin users.
    """
    def has_permission(self, request, view):
        return (request.user.is_authenticated and 
                hasattr(request.user, 'role') and 
                request.user.role in ['admin', 'super_admin'])

class IsRegularUser(BasePermission):
    """
    Custom permission to only allow regular users.
    """
    def has_permission(self, request, view):
        return (request.user.is_authenticated and 
                hasattr(request.user, 'role') and 
                request.user.role == 'user')

class HasRequiredRole(BasePermission):
    """
    Generic permission to check if user has required role.
    Set required_roles in your view class.
    """
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        
        required_roles = getattr(view, 'required_roles', [])
        
        if not required_roles:
            return True  # No specific roles required
        
        user_role = getattr(request.user, 'role', None)
        return user_role in required_roles