from rest_framework import permissions

from .models import UserProfile
from .roles import get_active_role


def _has_profile_role(request, *roles):
    if not request.user or not request.user.is_authenticated:
        return False

    return get_active_role(request) in roles


class IsTeacher(permissions.BasePermission):
    """
    Permission class to check if the user is a teacher.
    """

    message = "Only teachers can access this endpoint."

    def has_permission(self, request, view):
        return _has_profile_role(request, UserProfile.ROLE_TEACHER)


class IsStudent(permissions.BasePermission):
    """
    Permission class to check if the user is a student.
    """

    message = "Only students can access this endpoint."

    def has_permission(self, request, view):
        return _has_profile_role(request, UserProfile.ROLE_STUDENT)


class IsAdmin(permissions.BasePermission):
    """
    Permission class to check if the user is an admin.
    """

    message = "Only admins can access this endpoint."

    def has_permission(self, request, view):
        return _has_profile_role(request, UserProfile.ROLE_ADMIN)


class IsTeacherOrReadOnly(permissions.BasePermission):
    """
    Permission class to allow teachers to modify, others to read only.
    """

    message = "Only teachers can modify this endpoint."

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True

        return _has_profile_role(request, UserProfile.ROLE_TEACHER)


class IsAdminOrTeacher(permissions.BasePermission):
    """
    Permission class to allow admins and teachers to access an endpoint.
    """

    message = "Only admins or teachers can access this endpoint."

    def has_permission(self, request, view):
        return _has_profile_role(
            request,
            UserProfile.ROLE_ADMIN,
            UserProfile.ROLE_TEACHER,
        )
