from rest_framework import serializers
from django.contrib.auth.models import User
from django.utils import timezone
from django.contrib.auth import update_session_auth_hash
import datetime
from django.db import IntegrityError, transaction

from .models import UserProfile, EmailVerificationCode
from .roles import get_active_role


def lock_verification_code(record):
    # Lock every code for the mailbox so different issued codes cannot race either.
    records = list(EmailVerificationCode.objects.select_for_update().filter(
        email__iexact=record.email,
    ).order_by('pk'))
    locked = next((item for item in records if item.pk == record.pk), None)
    if locked is None or locked.is_used:
        raise serializers.ValidationError('Invalid or already used verification code')
    if timezone.now() - locked.created_at > datetime.timedelta(minutes=5):
        raise serializers.ValidationError('Verification code has expired')
    return locked

class UserProfileSerializer(serializers.ModelSerializer):
    """User profile serializer: student_id and role cannot be modified"""
    email = serializers.EmailField(source='user.email', read_only=True)
    avatar_url = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()

    class Meta:
        model = UserProfile
        fields = ['id', 'name', 'student_id', 'class_name', 'phone', 'wechat_id', 'bio', 'role', 'email', 'avatar', 'avatar_url']
        read_only_fields = ['id', 'student_id', 'role', 'email', 'avatar', 'avatar_url']

    def get_avatar_url(self, obj):
        if obj.avatar:
            return obj.avatar.url
        return None

    def get_role(self, obj):
        request = self.context.get('request')
        return get_active_role(request) if request and request.user.pk == obj.user_id else obj.role


class SendRegisterCodeSerializer(serializers.Serializer):
    """Send verification code for registration"""
    email = serializers.EmailField()

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('This email is already registered')
        return value


class RegisterSerializer(serializers.Serializer):
    """User registration serializer"""
    student_id = serializers.CharField(max_length=20)
    name = serializers.CharField(max_length=20)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=6)
    password_confirm = serializers.CharField(write_only=True, min_length=6)
    code = serializers.CharField(max_length=6)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    wechat_id = serializers.CharField(max_length=50, required=False, allow_blank=True)
    class_name = serializers.CharField(max_length=50, required=False, allow_blank=True)
    # Admin role should be provisioned by backend/admin, not self-registered.
    role = serializers.ChoiceField(
        choices=[UserProfile.ROLE_STUDENT, UserProfile.ROLE_TEACHER],
        required=False,
        default=UserProfile.ROLE_STUDENT,
    )

    def validate(self, attrs):
        # Check if student_id and email are already registered
        if User.objects.filter(username=attrs['student_id']).exists():
            raise serializers.ValidationError('This student_id is already registered')
        if User.objects.filter(email__iexact=attrs['email']).exists():
            raise serializers.ValidationError('This email is already registered')

        # Check if passwords match
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError('Passwords do not match')

        # Validate email verification code (expires after 5 minutes)
        try:
            record = EmailVerificationCode.objects.filter(
                email__iexact=attrs['email'],
                code=attrs['code'],
                purpose='register',
                is_used=False,
            ).latest('created_at')
        except EmailVerificationCode.DoesNotExist:
            raise serializers.ValidationError('Invalid or non-existent verification code')

        if timezone.now() - record.created_at > datetime.timedelta(minutes=5):
            raise serializers.ValidationError('Verification code has expired')

        attrs['code_record'] = record
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        code_record = lock_verification_code(validated_data.pop('code_record'))
        password = validated_data.pop('password')
        validated_data.pop('password_confirm')
        email = validated_data.pop('email')
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError('This email is already registered')
        student_id = validated_data.pop('student_id')
        name = validated_data.pop('name')
        role = validated_data.pop('role', UserProfile.ROLE_STUDENT)

        # 创建用户
        try:
            with transaction.atomic():
                user = User.objects.create_user(username=student_id, email=email, password=password)
                UserProfile.objects.create(
                    user=user,
                    name=name,
                    student_id=student_id,
                    class_name=validated_data.pop('class_name', ''),
                    phone=validated_data.pop('phone', ''),
                    wechat_id=validated_data.pop('wechat_id', ''),
                    role=role,
                )
        except IntegrityError:
            raise serializers.ValidationError('This student_id is already registered') from None

        # 标记验证码已使用
        code_record.is_used = True
        code_record.save()

        return user


class LoginSerializer(serializers.Serializer):
    """Login serializer: student_id + password"""
    student_id = serializers.CharField()
    password = serializers.CharField(write_only=True)
    remember_me = serializers.BooleanField(required=False, default=False)
    role = serializers.ChoiceField(choices=UserProfile.ROLE_CHOICES, required=False)


class ChangePasswordSerializer(serializers.Serializer):
    """Change password serializer"""
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=6)
    new_password_confirm = serializers.CharField(write_only=True, min_length=6)

    def validate(self, attrs):
        user = self.context['request'].user
        if not user.check_password(attrs['old_password']):
            raise serializers.ValidationError('Old password is incorrect')
        if attrs['new_password'] != attrs['new_password_confirm']:
            raise serializers.ValidationError('New passwords do not match')
        return attrs

    def save(self, **kwargs):
        user = self.context['request'].user
        user.set_password(self.validated_data['new_password'])
        user.save()
        update_session_auth_hash(self.context['request'], user)
        return user


class SendChangeEmailCodeSerializer(serializers.Serializer):
    """Send verification code for email change"""
    email = serializers.EmailField()

    def validate_email(self, value):
        user = self.context['request'].user
        # New email cannot be used by others
        if User.objects.filter(email__iexact=value).exclude(pk=user.pk).exists():
            raise serializers.ValidationError('This email is already in use')
        return value


class ChangeEmailSerializer(serializers.Serializer):
    """Change email serializer"""
    email = serializers.EmailField()
    code = serializers.CharField(max_length=6)

    def validate(self, attrs):
        user = self.context['request'].user
        email = attrs['email']
        code = attrs['code']

        # Email cannot be used by others
        if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
            raise serializers.ValidationError('This email is already in use')

        # Validate email verification code
        try:
            record = EmailVerificationCode.objects.filter(
                email__iexact=email,
                code=code,
                purpose='change_email',
                is_used=False,
            ).latest('created_at')
        except EmailVerificationCode.DoesNotExist:
            raise serializers.ValidationError('Invalid or non-existent verification code')

        if timezone.now() - record.created_at > datetime.timedelta(minutes=5):
            raise serializers.ValidationError('Verification code has expired')

        attrs['code_record'] = record
        return attrs

    @transaction.atomic
    def save(self, **kwargs):
        user = self.context['request'].user
        code_record = lock_verification_code(self.validated_data.pop('code_record'))
        email = self.validated_data['email']
        if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
            raise serializers.ValidationError('This email is already in use')

        user.email = email
        user.save()

        code_record.is_used = True
        code_record.save()

        return user


class AvatarUploadSerializer(serializers.Serializer):
    avatar = serializers.ImageField()

    def validate_avatar(self, value):
        if value.size > 5 * 1024 * 1024:
            raise serializers.ValidationError('头像不能超过 5MB。')
        if value.image.format not in {'JPEG', 'PNG', 'WEBP'}:
            raise serializers.ValidationError('头像仅支持 JPEG、PNG 或 WebP。')
        return value
