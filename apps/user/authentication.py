from django.contrib.auth import authenticate

from .models import UserProfile


def authenticate_login_id(request, login_id, password):
    # Academic IDs can be edited independently of the internal username.
    profile = UserProfile.objects.select_related('user').filter(student_id=login_id).first()
    username = profile.user.username if profile else login_id
    return authenticate(request, username=username, password=password)
