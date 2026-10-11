from rest_framework import generics, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import APIException
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.utils.crypto import get_random_string
from django.conf import settings
import datetime
import logging
import smtplib
from django.utils import timezone
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator

from core.error_codes import AUTH_INVALID_CREDENTIALS, error_response

from .models import UserProfile, EmailVerificationCode
from .roles import available_roles
from .authentication import authenticate_login_id
from .serializers import (
    AvatarUploadSerializer,
    UserProfileSerializer,
    SendRegisterCodeSerializer,
    RegisterSerializer,
    LoginSerializer,
    ChangePasswordSerializer,
    SendChangeEmailCodeSerializer,
    ChangeEmailSerializer,
)
from .throttles import RegisterCodeRateThrottle, LoginRateThrottle


logger = logging.getLogger(__name__)
_login_throttle_warning_logged = False


def warn_login_throttle_enabled():
    global _login_throttle_warning_logged

    if _login_throttle_warning_logged or not settings.DEBUG:
        return

    logger.warning(
        "Login rate throttle is enabled in DEBUG mode. "
        "Adjust REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['login'] if frontend testing is blocked."
    )
    _login_throttle_warning_logged = True


def send_verification_code(email, purpose, subject, message_template):
    """
    Common method to send verification code emails.
    
    Args:
        email: Email address to send to
        purpose: 'register' or 'change_email'
        subject: Email subject
        message_template: Message format with {code} placeholder
    """
    code = get_random_string(length=6, allowed_chars='0123456789')
    verification = EmailVerificationCode.objects.create(
        email=email,
        code=code,
        purpose=purpose,
    )

    message = message_template.format(code=code)
    try:
        sent = send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
        )
        if sent != 1:
            raise smtplib.SMTPException('Email delivery was not accepted')
    except (smtplib.SMTPException, OSError):
        verification.delete()
        logger.error('Verification email delivery failed')
        error = APIException('验证码邮件暂时无法发送，请稍后重试。', code='email_unavailable')
        error.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        raise error from None
    return code

class SendRegisterCodeView(APIView):
    """
    Send verification code to email for registration.
    
    POST /api/user/send-register-code/
    Request: {"email": "student@example.com"}
    Response: {"detail": "Verification code sent"}
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [RegisterCodeRateThrottle]

    def post(self, request):
        serializer = SendRegisterCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        send_verification_code(
            email=email,
            purpose='register',
            subject='Registration Verification Code',
            message_template='【Ziqiang Academy】Your verification code is: {code} (valid for 5 minutes)'
        )
        return Response({'detail': 'Verification code sent'}, status=status.HTTP_200_OK)


class RegisterView(APIView):
    """
    Create new user account with email verification.
    
    POST /api/user/register/
    Request: {
      "student_id": "2023001",
      "name": "张三",
      "email": "student@example.com",
      "password": "password123",
      "password_confirm": "password123",
      "code": "123456",
      "phone": "13812345678"
    }
    Response: {"detail": "Registration successful", "username": "2023001"}
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            {'detail': 'Registration successful', 'username': user.username},
            status=status.HTTP_201_CREATED
        )


class LoginView(APIView):
    """
    User login with student_id and password.
    
    POST /api/user/login/
    Request: {
      "student_id": "2023001",
      "password": "password123",
      "remember_me": false
    }
    Response: {"detail": "Login successful"}
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        warn_login_throttle_enabled()
        SessionAuthentication().enforce_csrf(request)
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        student_id = serializer.validated_data['student_id']
        password = serializer.validated_data['password']
        remember_me = serializer.validated_data.get('remember_me', False)

        user = authenticate_login_id(request, student_id, password)
        if not user:
            return error_response(AUTH_INVALID_CREDENTIALS)

        try:
            profile = user.profile
        except UserProfile.DoesNotExist:
            return Response({'detail': '账号资料未配置，请联系管理员。'}, status=status.HTTP_403_FORBIDDEN)
        role = serializer.validated_data.get('role', profile.role)
        if role not in available_roles(profile):
            return Response({'detail': '当前账号没有所选身份的权限。'}, status=status.HTTP_403_FORBIDDEN)

        login(request, user)
        request.session['active_role'] = role

        if remember_me:
            request.session.set_expiry(60 * 60 * 24 * 14)  # 14 days
        else:
            request.session.set_expiry(0)  # Close browser to logout

        return Response(
            {
                "detail": "Login successful",
            },
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    """
    User logout endpoint.
    Clears session cookie.
    
    POST /api/user/logout/
    Response: {"detail": "Logged out"}
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response({'detail': 'Logged out'}, status=status.HTTP_200_OK)


@method_decorator(ensure_csrf_cookie, name='dispatch')
class CsrfTokenView(APIView):
    """
    Get CSRF token for frontend requests.

    GET /api/user/csrf/
    Response: {"csrfToken": "<token>"}
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        token = get_token(request)
        return Response({'csrfToken': token}, status=status.HTTP_200_OK)


class MeProfileView(generics.RetrieveUpdateAPIView):
    """
    Get/update current user profile.
    
    GET /api/user/me/profile/ - Get user profile
    PUT /api/user/me/profile/ - Overwrite profile (except student_id)
    PATCH /api/user/me/profile/ - Partial update (except student_id)
    
    Note: student_id is read-only and cannot be modified.
    """
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        profile, _ = UserProfile.objects.get_or_create(user=self.request.user)
        return profile


class ChangePasswordView(APIView):
    """
    Change user password with verification of old password.
    
    POST /api/user/change-password/
    Request: {
      "old_password": "password123",
      "new_password": "newpassword456",
      "new_password_confirm": "newpassword456"
    }
    Response: {"detail": "Password changed successfully"}
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data,
            context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({'detail': 'Password changed successfully'}, status=status.HTTP_200_OK)


class SendChangeEmailCodeView(APIView):
    """
    Send verification code to new email for email change.
    
    POST /api/user/send-change-email-code/
    Request: {"email": "newemail@example.com"}
    Response: {"detail": "Verification code sent"}
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [RegisterCodeRateThrottle]

    def post(self, request):
        serializer = SendChangeEmailCodeSerializer(
            data=request.data,
            context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        send_verification_code(
            email=email,
            purpose='change_email',
            subject='Email Change Verification Code',
            message_template='【Ziqiang Academy】Your email change verification code is: {code} (valid for 5 minutes)'
        )
        return Response({'detail': 'Verification code sent'}, status=status.HTTP_200_OK)


class ChangeEmailView(APIView):
    """
    Change user email with verification code.
    
    POST /api/user/change-email/
    Request: {
      "email": "newemail@example.com",
      "code": "123456"
    }
    Response: {"detail": "Email changed successfully", "email": "newemail@example.com"}
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ChangeEmailSerializer(
            data=request.data,
            context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            {'detail': 'Email changed successfully', 'email': user.email},
            status=status.HTTP_200_OK
        )


class AvatarUploadView(APIView):
    """
    上传/更新用户头像。

    PUT /api/user/me/avatar/
    Request: multipart/form-data, field name: avatar
    """
    permission_classes = [permissions.IsAuthenticated]

    def put(self, request):
        serializer = AvatarUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile, _ = UserProfile.objects.get_or_create(user=request.user, defaults={"student_id": request.user.username})

        if profile.avatar:
            profile.avatar.delete(save=False)

        profile.avatar = serializer.validated_data["avatar"]
        profile.save(update_fields=["avatar"])

        return Response(
            {
                "detail": "Avatar uploaded successfully",
                "avatar_url": profile.avatar.url,
            },
            status=status.HTTP_200_OK,
        )
