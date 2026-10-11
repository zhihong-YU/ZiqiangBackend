from rest_framework import serializers
from apps.user.roles import get_active_role

from apps.file.models import ManagedFile
from apps.file.serializers import ManagedFileSerializer

from .models import Event, EventApplication


class EventSerializer(serializers.ModelSerializer):
    teacher_name = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()
    can_publish = serializers.SerializerMethodField()
    can_close = serializers.SerializerMethodField()
    pending_applications_count = serializers.SerializerMethodField()
    attachment = ManagedFileSerializer(read_only=True)

    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "event_type",
            "start_time",
            "end_time",
            "location",
            "description",
            "attachment",
            "expected_participants",
            "current_participants",
            "status",
            "teacher",
            "teacher_name",
            "can_delete",
            "can_publish",
            "can_close",
            "pending_applications_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "current_participants",
            "status",
            "teacher",
            "teacher_name",
            "can_delete",
            "can_publish",
            "can_close",
            "pending_applications_count",
            "created_at",
            "updated_at",
        ]

    def get_teacher_name(self, obj):
        if hasattr(obj.teacher, "profile"):
            return obj.teacher.profile.name
        return obj.teacher.username

    def get_status(self, obj):
        return obj.get_runtime_status()

    def get_can_delete(self, obj):
        return obj.can_delete()

    def get_can_publish(self, obj):
        return obj.can_publish()

    def get_can_close(self, obj):
        return obj.can_close()

    def get_pending_applications_count(self, obj):
        if hasattr(obj, "pending_applications_count"):
            return obj.pending_applications_count
        return obj.applications.filter(status=EventApplication.STATUS_PENDING).count()


class EventWriteSerializer(serializers.ModelSerializer):
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
        ]

    def validate_attachment_id(self, value):
        if value is None:
            return value
        user = self.context['request'].user
        if value.category != ManagedFile.CATEGORY_EVENT_ATTACHMENT:
            raise serializers.ValidationError('请选择项目附件分类的文件。')
        if value.uploaded_by_id != user.id and get_active_role(self.context['request']) != 'admin':
            raise serializers.ValidationError('只能绑定自己上传的附件。')
        return value

    def validate(self, attrs):
        start_time = attrs.get("start_time", getattr(self.instance, "start_time", None))
        end_time = attrs.get("end_time", getattr(self.instance, "end_time", None))

        if start_time and end_time and start_time >= end_time:
            raise serializers.ValidationError("End time must be later than start time.")
        capacity = attrs.get('expected_participants', getattr(self.instance, 'expected_participants', 0))
        if capacity < max(1, getattr(self.instance, 'current_participants', 0)):
            raise serializers.ValidationError('期望人数不能小于 1 或已通过人数。')

        return attrs


class EventApplicationSerializer(serializers.ModelSerializer):
    event_title = serializers.CharField(source="event.title", read_only=True)
    student_name = serializers.SerializerMethodField()
    student_id = serializers.SerializerMethodField()
    student_email = serializers.EmailField(source="student.email", read_only=True)
    resume = ManagedFileSerializer(read_only=True)

    class Meta:
        model = EventApplication
        fields = [
            "id",
            "event",
            "event_title",
            "student",
            "student_name",
            "student_id",
            "student_email",
            "statement",
            "resume",
            "status",
            "review_note",
            "reviewed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "event",
            "event_title",
            "student",
            "student_name",
            "student_id",
            "student_email",
            "status",
            "review_note",
            "reviewed_at",
            "created_at",
            "updated_at",
        ]

    def get_student_name(self, obj):
        if hasattr(obj.student, "profile"):
            return obj.student.profile.name
        return obj.student.username

    def get_student_id(self, obj):
        if hasattr(obj.student, "profile"):
            return obj.student.profile.student_id
        return obj.student.username


class EventApplicationCreateSerializer(serializers.ModelSerializer):
    resume_id = serializers.PrimaryKeyRelatedField(
        source="resume",
        queryset=ManagedFile.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = EventApplication
        fields = ["statement", "resume_id"]

    def validate_resume_id(self, value):
        if value is not None and (value.uploaded_by_id != self.context['request'].user.id or value.category != ManagedFile.CATEGORY_APPLICATION_RESUME):
            raise serializers.ValidationError('只能绑定自己上传的简历。')
        return value

    def validate(self, attrs):
        event = self.context["event"]
        student = self.context["request"].user

        if not event.can_accept_applications():
            raise serializers.ValidationError("This event is not accepting applications.")
        if not event.has_available_slots():
            raise serializers.ValidationError("This event is already full.")

        duplicate_exists = EventApplication.objects.filter(
            event=event,
            student=student,
            status__in=[
                EventApplication.STATUS_PENDING,
                EventApplication.STATUS_APPROVED,
            ],
        ).exists()
        if duplicate_exists:
            raise serializers.ValidationError("You have already applied to this event.")

        return attrs


class EventApplicationReviewSerializer(serializers.Serializer):
    review_note = serializers.CharField(required=False, allow_blank=True, default="")
