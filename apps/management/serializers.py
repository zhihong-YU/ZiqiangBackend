from rest_framework import serializers
from django.contrib.auth.models import User
from django.utils import timezone
from django.db import transaction

from apps.user.models import UserProfile
from apps.event.models import Event, EventApplication
from apps.file.models import ManagedFile
from apps.event.serializers import EventWriteSerializer


class AdminLoginSerializer(serializers.Serializer):
    student_id = serializers.CharField()
    password = serializers.CharField(write_only=True)
    remember_me = serializers.BooleanField(required=False, default=False)


class AdminUserListSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="profile.name", read_only=True)
    student_id = serializers.CharField(source="profile.student_id", read_only=True)
    class_name = serializers.CharField(source="profile.class_name", read_only=True)
    phone = serializers.CharField(source="profile.phone", read_only=True)
    wechat_id = serializers.CharField(source="profile.wechat_id", read_only=True)
    bio = serializers.CharField(source="profile.bio", read_only=True)
    role = serializers.CharField(source="profile.role", read_only=True)
    avatar = serializers.ImageField(source="profile.avatar", read_only=True)
    avatar_url = serializers.SerializerMethodField()
    events_count = serializers.SerializerMethodField()
    applications_count = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "name",
            "student_id",
            "class_name",
            "phone",
            "wechat_id",
            "bio",
            "role",
            "avatar",
            "avatar_url",
            "is_active",
            "date_joined",
            "events_count",
            "applications_count",
        ]
        read_only_fields = fields

    def get_avatar_url(self, obj):
        if hasattr(obj, "profile") and obj.profile.avatar:
            return obj.profile.avatar.url
        return None

    def get_events_count(self, obj):
        return getattr(obj, "created_events", None).count() if hasattr(obj, "created_events") else obj.created_events.count()

    def get_applications_count(self, obj):
        return getattr(obj, "event_applications", None).count() if hasattr(obj, "event_applications") else obj.event_applications.count()


class AdminUserDetailSerializer(AdminUserListSerializer):
    pass


class AdminUserCreateSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=20)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=6)
    name = serializers.CharField(max_length=20)
    student_id = serializers.CharField(max_length=20)
    class_name = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    wechat_id = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    bio = serializers.CharField(required=False, allow_blank=True, default="")
    role = serializers.ChoiceField(
        choices=UserProfile.ROLE_CHOICES,
        required=False,
        default=UserProfile.ROLE_STUDENT,
    )

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("This username is already registered")
        return value

    def validate_student_id(self, value):
        if UserProfile.objects.filter(student_id=value).exists():
            raise serializers.ValidationError("This student_id is already registered")
        return value

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("This email is already registered")
        return value

    @transaction.atomic
    def create(self, validated_data):
        username = validated_data["username"]
        email = validated_data["email"]
        password = validated_data.pop("password")
        name = validated_data.pop("name")
        student_id = validated_data.pop("student_id")
        role = validated_data.pop("role", UserProfile.ROLE_STUDENT)

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
        )

        UserProfile.objects.create(
            user=user,
            name=name,
            student_id=student_id,
            class_name=validated_data.pop("class_name", ""),
            phone=validated_data.pop("phone", ""),
            wechat_id=validated_data.pop("wechat_id", ""),
            bio=validated_data.pop("bio", ""),
            role=role,
        )

        return user


class AdminUserUpdateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, required=False, allow_blank=True, default="", min_length=6)
    name = serializers.CharField(max_length=20)
    student_id = serializers.CharField(max_length=20)
    class_name = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    wechat_id = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    bio = serializers.CharField(required=False, allow_blank=True, default="")
    role = serializers.ChoiceField(
        choices=UserProfile.ROLE_CHOICES,
    )
    is_active = serializers.BooleanField()

    def validate_student_id(self, value):
        user = self.context["target_user"]
        if UserProfile.objects.filter(student_id=value).exclude(user=user).exists():
            raise serializers.ValidationError("This student_id is already in use")
        return value

    def validate_email(self, value):
        user = self.context["target_user"]
        if User.objects.filter(email__iexact=value).exclude(pk=user.pk).exists():
            raise serializers.ValidationError("This email is already in use")
        return value

    @transaction.atomic
    def update(self, user, validated_data):
        password = validated_data.pop("password", "")
        role = validated_data.pop("role")
        is_active = validated_data.pop("is_active")
        email = validated_data.pop("email")
        student_id = validated_data.pop("student_id")
        name = validated_data.pop("name")

        user.email = email
        user.is_active = is_active
        if password:
            user.set_password(password)
        user.save()

        profile = user.profile
        profile.name = name
        profile.student_id = student_id
        profile.class_name = validated_data.pop("class_name", profile.class_name)
        profile.phone = validated_data.pop("phone", profile.phone)
        profile.wechat_id = validated_data.pop("wechat_id", profile.wechat_id)
        profile.bio = validated_data.pop("bio", profile.bio)
        profile.role = role
        profile.save()

        return user


class AdminEventWriteSerializer(EventWriteSerializer):
    teacher_id = serializers.PrimaryKeyRelatedField(
        source="teacher",
        queryset=User.objects.all(),
    )
    attachment_id = serializers.PrimaryKeyRelatedField(
        source="attachment",
        queryset=ManagedFile.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Event
        fields = [
            "title",
            "event_type",
            "start_time",
            "end_time",
            "location",
            "description",
            "attachment_id",
            "expected_participants",
            "teacher_id",
        ]

    def validate(self, attrs):
        return super().validate(attrs)

    def validate_teacher_id(self, value):
        if not hasattr(value, 'profile') or not value.profile.has_role(UserProfile.ROLE_TEACHER, UserProfile.ROLE_ADMIN):
            raise serializers.ValidationError('项目发布者必须是教师或管理员。')
        return value


class AdminApplicationReviewSerializer(serializers.Serializer):
    review_note = serializers.CharField(required=False, allow_blank=True, default="")


class AdminFileSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ManagedFile
        fields = [
            "id",
            "original_name",
            "category",
            "content_type",
            "file_size",
            "description",
            "uploaded_by",
            "uploaded_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_uploaded_by_name(self, obj):
        if obj.uploaded_by and hasattr(obj.uploaded_by, "profile"):
            return obj.uploaded_by.profile.name
        return obj.uploaded_by.username if obj.uploaded_by else ""
