from .models import UserProfile


def available_roles(profile):
    # This grant is provisioned on the server, never by registration/profile input.
    if profile.role == UserProfile.ROLE_ADMIN and profile.can_switch_roles:
        return [UserProfile.ROLE_STUDENT, UserProfile.ROLE_TEACHER, UserProfile.ROLE_ADMIN]
    return [profile.role]


def get_active_role(request):
    if not request.user or not request.user.is_authenticated:
        return None
    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        return None
    selected = request.session.get('active_role', profile.role)
    return selected if selected in available_roles(profile) else profile.role
