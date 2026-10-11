from django.db import transaction
from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.conf import settings
from rest_framework import generics, permissions, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.notification.services import (
    notify_event_application_reviewed,
    notify_event_application_submitted,
)
from apps.user.models import UserProfile
from apps.user.permissions import IsAdminOrTeacher, IsStudent
from apps.user.roles import get_active_role

from .models import Event, EventApplication
from .serializers import (
    EventApplicationCreateSerializer,
    EventApplicationReviewSerializer,
    EventApplicationSerializer,
    EventSerializer,
    EventWriteSerializer,
)


def parse_bool_query_param(value):
    if value is None:
        return None

    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False

    raise ValidationError("Boolean query parameter must be true or false.")


def apply_event_filters(queryset, request):
    teacher_name = request.query_params.get("teacher_name")
    status_value = request.query_params.get("status")
    can_recruit = parse_bool_query_param(request.query_params.get("can_recruit"))
    now = timezone.now()

    if teacher_name:
        queryset = queryset.filter(
            Q(teacher__profile__name__icontains=teacher_name)
            | Q(teacher__username__icontains=teacher_name)
        )

    if status_value:
        if status_value == Event.STATUS_DRAFT:
            queryset = queryset.filter(status=Event.STATUS_DRAFT)
        elif status_value == Event.STATUS_PUBLISHED:
            queryset = queryset.filter(status=Event.STATUS_PUBLISHED, start_time__gt=now)
        elif status_value == "ongoing":
            queryset = queryset.filter(
                status=Event.STATUS_PUBLISHED,
                start_time__lte=now,
            ).filter(Q(end_time__isnull=True) | Q(end_time__gte=now))
        elif status_value == Event.STATUS_CLOSED:
            queryset = queryset.filter(
                Q(status=Event.STATUS_CLOSED)
                | Q(status=Event.STATUS_PUBLISHED, end_time__lt=now)
            )
        else:
            raise ValidationError("Unsupported event status filter.")

    if can_recruit is not None:
        recruitable_condition = Q(
            status=Event.STATUS_PUBLISHED,
            start_time__gt=now,
            current_participants__lt=F("expected_participants"),
        )
        queryset = queryset.filter(recruitable_condition) if can_recruit else queryset.exclude(recruitable_condition)

    return queryset


def apply_application_filters(queryset, request):
    event_id = request.query_params.get("event")
    status_value = request.query_params.get("status")

    if event_id:
        queryset = queryset.filter(event_id=event_id)

    if status_value:
        allowed_statuses = {
            EventApplication.STATUS_PENDING,
            EventApplication.STATUS_APPROVED,
            EventApplication.STATUS_REJECTED,
            EventApplication.STATUS_CANCELLED,
        }
        if status_value not in allowed_statuses:
            raise ValidationError("Unsupported application status filter.")
        queryset = queryset.filter(status=status_value)

    return queryset


def get_event_queryset():
    return Event.objects.select_related("teacher", "teacher__profile").annotate(
        pending_applications_count=Count(
            "applications",
            filter=Q(applications__status=EventApplication.STATUS_PENDING),
        )
    ).order_by("-created_at")


class PublicEventListView(generics.ListAPIView):
    serializer_class = EventSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        queryset = get_event_queryset().exclude(status=Event.STATUS_DRAFT)
        return apply_event_filters(queryset, self.request)


class PublicEventDetailView(generics.RetrieveAPIView):
    serializer_class = EventSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        return get_event_queryset()

    def get_object(self):
        event = get_object_or_404(self.get_queryset(), pk=self.kwargs["pk"])

        if event.status != Event.STATUS_DRAFT:
            return event

        user = self.request.user
        if user.is_authenticated and (
            event.teacher_id == user.id
            or (hasattr(user, "profile") and get_active_role(self.request) == UserProfile.ROLE_ADMIN)
        ):
            return event

        raise NotFound("This event does not exist.")


class TeacherEventListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminOrTeacher]

    def get_queryset(self):
        queryset = get_event_queryset()
        if get_active_role(self.request) == UserProfile.ROLE_ADMIN:
            return apply_event_filters(queryset, self.request)
        return apply_event_filters(queryset.filter(teacher=self.request.user), self.request)

    def get_serializer_class(self):
        if self.request.method == "POST":
            return EventWriteSerializer
        return EventSerializer

    def perform_create(self, serializer):
        serializer.save(teacher=self.request.user, status=Event.STATUS_DRAFT)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        event = get_event_queryset().get(pk=serializer.instance.pk)
        data = EventSerializer(event, context={"request": request}).data
        return Response(data, status=status.HTTP_201_CREATED)


class TeacherEventDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminOrTeacher]

    def get_queryset(self):
        queryset = get_event_queryset()
        if get_active_role(self.request) == UserProfile.ROLE_ADMIN:
            return queryset
        return queryset.filter(teacher=self.request.user)

    def get_serializer_class(self):
        if self.request.method in {"PUT", "PATCH"}:
            return EventWriteSerializer
        return EventSerializer

    def perform_update(self, serializer):
        event = self.get_object()
        if not event.can_edit():
            raise ValidationError("Closed events cannot be edited.")
        serializer.save()

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        instance.refresh_from_db()
        data = EventSerializer(instance, context={"request": request}).data
        return Response(data)

    def perform_destroy(self, instance):
        if not instance.can_delete():
            raise ValidationError("Only draft events can be deleted.")
        instance.delete()


class PublishEventView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminOrTeacher]

    def post(self, request, pk):
        event = self._get_event(request, pk)
        if not event.can_publish():
            raise ValidationError("Only draft events can be published.")

        event.status = Event.STATUS_PUBLISHED
        event.save(update_fields=["status", "updated_at"])
        return Response(EventSerializer(event, context={"request": request}).data)

    def _get_event(self, request, pk):
        queryset = get_event_queryset()
        if get_active_role(request) != UserProfile.ROLE_ADMIN:
            queryset = queryset.filter(teacher=request.user)
        return get_object_or_404(queryset, pk=pk)


class CloseEventView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminOrTeacher]

    def post(self, request, pk):
        event = self._get_event(request, pk)
        if not event.can_close():
            raise ValidationError("Only published events that have not started can be closed.")

        event.status = Event.STATUS_CLOSED
        event.save(update_fields=["status", "updated_at"])
        return Response(EventSerializer(event, context={"request": request}).data)

    def _get_event(self, request, pk):
        queryset = get_event_queryset()
        if get_active_role(request) != UserProfile.ROLE_ADMIN:
            queryset = queryset.filter(teacher=request.user)
        return get_object_or_404(queryset, pk=pk)


class StudentApplicationListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated, IsStudent]

    def get_queryset(self):
        queryset = EventApplication.objects.filter(student=self.request.user).select_related(
            "event",
            "student",
            "student__profile",
        )
        return apply_application_filters(queryset, self.request)

    def get_serializer_class(self):
        if self.request.method == "POST":
            return EventApplicationCreateSerializer
        return EventApplicationSerializer

    def get_event(self):
        return get_object_or_404(get_event_queryset(), pk=self.kwargs["event_pk"])

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.request.method == "POST":
            context["event"] = getattr(self, '_locked_event', None) or self.get_event()
        return context

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        if 'event_pk' not in self.kwargs:
            raise ValidationError('请通过项目详情提交申请。')
        event = get_object_or_404(Event.objects.select_for_update(), pk=self.kwargs['event_pk'])
        self._locked_event = event
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        application = EventApplication.objects.create(
            event=event,
            student=request.user,
            statement=serializer.validated_data.get("statement", ""),
            resume=serializer.validated_data.get("resume"),
        )
        if settings.ENABLE_NOTIFICATIONS:
            notify_event_application_submitted(
                teacher=event.teacher,
                student=request.user,
                event=event,
                application=application,
            )
        data = EventApplicationSerializer(application, context={"request": request}).data
        return Response(data, status=status.HTTP_201_CREATED)

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = EventApplicationSerializer(page, many=True, context={"request": request})
            return self.get_paginated_response(serializer.data)

        serializer = EventApplicationSerializer(queryset, many=True, context={"request": request})
        return Response(serializer.data)


class StudentApplicationDetailView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated, IsStudent]
    serializer_class = EventApplicationSerializer

    def get_queryset(self):
        return EventApplication.objects.filter(student=self.request.user).select_related(
            "event",
            "student",
            "student__profile",
        )


class TeacherApplicationListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminOrTeacher]
    serializer_class = EventApplicationSerializer

    def get_queryset(self):
        queryset = EventApplication.objects.select_related(
            "event",
            "event__teacher",
            "event__teacher__profile",
            "student",
            "student__profile",
        )
        if get_active_role(self.request) == UserProfile.ROLE_ADMIN:
            return apply_application_filters(queryset, self.request)
        return apply_application_filters(queryset.filter(event__teacher=self.request.user), self.request)


class TeacherApplicationApproveView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminOrTeacher]

    @transaction.atomic
    def post(self, request, pk):
        application = self._get_application(request, pk)
        application = lock_application_for_review(application)
        if not application.can_change_decision():
            raise ValidationError("This application cannot be approved.")
        if application.status == EventApplication.STATUS_CANCELLED:
            raise ValidationError("Cancelled applications cannot be approved.")
        if (
            application.status != EventApplication.STATUS_APPROVED
            and not application.event.has_available_slots()
        ):
            raise ValidationError("This event has no available slots.")

        serializer = EventApplicationReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if application.status != EventApplication.STATUS_APPROVED:
            application.event.current_participants += 1
            application.event.save(update_fields=["current_participants", "updated_at"])

        application.status = EventApplication.STATUS_APPROVED
        application.review_note = serializer.validated_data["review_note"]
        application.reviewed_at = timezone.now()
        application.save(update_fields=["status", "review_note", "reviewed_at", "updated_at"])
        if settings.ENABLE_NOTIFICATIONS:
            notify_event_application_reviewed(
                student=application.student,
                event=application.event,
                application=application,
                approved=True,
            )

        data = EventApplicationSerializer(application, context={"request": request}).data
        return Response(data)

    def _get_application(self, request, pk):
        queryset = EventApplication.objects.select_related(
            "event",
            "event__teacher",
            "student",
            "student__profile",
        )
        if get_active_role(request) != UserProfile.ROLE_ADMIN:
            queryset = queryset.filter(event__teacher=request.user)
        return get_object_or_404(queryset, pk=pk)


class TeacherApplicationRejectView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminOrTeacher]

    @transaction.atomic
    def post(self, request, pk):
        application = self._get_application(request, pk)
        application = lock_application_for_review(application)
        if not application.can_change_decision():
            raise ValidationError("This application cannot be rejected.")
        if application.status == EventApplication.STATUS_CANCELLED:
            raise ValidationError("Cancelled applications cannot be rejected.")

        serializer = EventApplicationReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if application.status == EventApplication.STATUS_APPROVED:
            application.event.current_participants = max(0, application.event.current_participants - 1)
            application.event.save(update_fields=["current_participants", "updated_at"])

        application.status = EventApplication.STATUS_REJECTED
        application.review_note = serializer.validated_data["review_note"]
        application.reviewed_at = timezone.now()
        application.save(update_fields=["status", "review_note", "reviewed_at", "updated_at"])
        if settings.ENABLE_NOTIFICATIONS:
            notify_event_application_reviewed(
                student=application.student,
                event=application.event,
                application=application,
                approved=False,
            )

        data = EventApplicationSerializer(application, context={"request": request}).data
        return Response(data)

    def _get_application(self, request, pk):
        queryset = EventApplication.objects.select_related(
            "event",
            "event__teacher",
            "student",
            "student__profile",
        )
        if get_active_role(request) != UserProfile.ROLE_ADMIN:
            queryset = queryset.filter(event__teacher=request.user)
        return get_object_or_404(queryset, pk=pk)


def lock_application_for_review(application):
    # Lock the shared capacity first so reviews of different applications serialize.
    event = get_object_or_404(Event.objects.select_for_update(), pk=application.event_id)
    application = get_object_or_404(EventApplication.objects.select_for_update(), pk=application.pk)
    application.event = event
    return application
