from django.contrib.auth import login, logout
from django.contrib.auth.models import User
from django.db.models import Count, Q
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from django.middleware.csrf import get_token
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.authentication import SessionAuthentication
from apps.user.throttles import LoginRateThrottle

from core.error_codes import (
    APPLICATION_ONLY_PENDING,
    AUTH_INVALID_CREDENTIALS,
    AUTH_NOT_ADMIN,
    EVENT_ALREADY_CLOSED,
    EVENT_ONLY_DRAFT_PUBLISH,
    FILE_IN_USE,
    USER_CANNOT_CHANGE_OWN_ROLE,
    USER_CANNOT_DELETE_SELF,
    error_response,
)

from apps.user.models import UserProfile
from apps.user.authentication import authenticate_login_id
from apps.user.permissions import IsAdmin
from apps.event.models import Event, EventApplication
from apps.event.views import TeacherApplicationApproveView, TeacherApplicationRejectView
from apps.event.serializers import EventApplicationSerializer, EventSerializer
from apps.file.models import ManagedFile
from apps.notification.services import (
    notify_event_application_reviewed,
)

from .serializers import (
    AdminApplicationReviewSerializer,
    AdminEventWriteSerializer,
    AdminFileSerializer,
    AdminLoginSerializer,
    AdminUserCreateSerializer,
    AdminUserDetailSerializer,
    AdminUserListSerializer,
    AdminUserUpdateSerializer,
)


class AdminLoginView(APIView):
    """
    管理员登录 - 仅限 role=admin 的用户
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [LoginRateThrottle]

    def post(self, request):
        SessionAuthentication().enforce_csrf(request)
        serializer = AdminLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        student_id = serializer.validated_data["student_id"]
        password = serializer.validated_data["password"]
        remember_me = serializer.validated_data.get("remember_me", False)

        user = authenticate_login_id(request, student_id, password)
        if not user:
            return error_response(AUTH_INVALID_CREDENTIALS)

        try:
            profile = user.profile
        except UserProfile.DoesNotExist:
            return error_response(AUTH_NOT_ADMIN, status_code=status.HTTP_403_FORBIDDEN)

        if not profile.is_admin():
            return error_response(AUTH_NOT_ADMIN, status_code=status.HTTP_403_FORBIDDEN)

        login(request, user)
        request.session['active_role'] = UserProfile.ROLE_ADMIN

        if remember_me:
            request.session.set_expiry(60 * 60 * 24 * 14)
        else:
            request.session.set_expiry(0)

        return Response(
            {
                "detail": "Login successful",
                "role": "admin",
            },
            status=status.HTTP_200_OK,
        )


class AdminLogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def post(self, request):
        logout(request)
        return Response({"detail": "Logged out"}, status=status.HTTP_200_OK)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class AdminCsrfTokenView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        token = get_token(request)
        return Response({"csrfToken": token}, status=status.HTTP_200_OK)


class AdminDashboardView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get(self, request):
        now = timezone.now()
        total_users = User.objects.count()
        total_students = UserProfile.objects.filter(role=UserProfile.ROLE_STUDENT).count()
        total_teachers = UserProfile.objects.filter(role=UserProfile.ROLE_TEACHER).count()
        total_admins = UserProfile.objects.filter(role=UserProfile.ROLE_ADMIN).count()

        total_events = Event.objects.count()
        draft_events = Event.objects.filter(status=Event.STATUS_DRAFT).count()
        published_events = Event.objects.filter(
            status=Event.STATUS_PUBLISHED, start_time__gt=now
        ).count()
        ongoing_events = Event.objects.filter(
            status=Event.STATUS_PUBLISHED,
            start_time__lte=now,
        ).filter(Q(end_time__isnull=True) | Q(end_time__gte=now)).count()
        closed_events = Event.objects.filter(
            Q(status=Event.STATUS_CLOSED)
            | Q(status=Event.STATUS_PUBLISHED, end_time__lt=now)
        ).count()

        total_applications = EventApplication.objects.count()
        pending_applications = EventApplication.objects.filter(
            status=EventApplication.STATUS_PENDING
        ).count()
        approved_applications = EventApplication.objects.filter(
            status=EventApplication.STATUS_APPROVED
        ).count()
        rejected_applications = EventApplication.objects.filter(
            status=EventApplication.STATUS_REJECTED
        ).count()
        cancelled_applications = EventApplication.objects.filter(
            status=EventApplication.STATUS_CANCELLED
        ).count()

        total_files = ManagedFile.objects.count()

        return Response(
            {
                "total_users": total_users,
                "total_students": total_students,
                "total_teachers": total_teachers,
                "total_admins": total_admins,
                "total_events": total_events,
                "events_by_status": {
                    "draft": draft_events,
                    "published": published_events,
                    "ongoing": ongoing_events,
                    "closed": closed_events,
                },
                "total_applications": total_applications,
                "applications_by_status": {
                    "pending": pending_applications,
                    "approved": approved_applications,
                    "rejected": rejected_applications,
                    "cancelled": cancelled_applications,
                },
                "total_files": total_files,
            },
            status=status.HTTP_200_OK,
        )


class AdminUserListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    serializer_class = AdminUserListSerializer

    def get_queryset(self):
        qs = User.objects.select_related("profile").prefetch_related(
            "created_events", "event_applications"
        )
        role = self.request.query_params.get("role")
        search = self.request.query_params.get("search")

        if role:
            qs = qs.filter(profile__role=role)

        if search:
            qs = qs.filter(
                Q(profile__name__icontains=search)
                | Q(profile__student_id__icontains=search)
                | Q(email__icontains=search)
            )

        return qs.order_by("-date_joined")


class AdminUserDetailView(generics.RetrieveAPIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    serializer_class = AdminUserDetailSerializer

    def get_queryset(self):
        return User.objects.select_related("profile").prefetch_related(
            "created_events", "event_applications"
        )


class AdminUserCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def post(self, request):
        serializer = AdminUserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return Response(
            AdminUserDetailSerializer(user).data,
            status=status.HTTP_201_CREATED,
        )


class AdminUserUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def put(self, request, pk):
        target_user = get_object_or_404(
            User.objects.select_related("profile"), pk=pk
        )

        serializer = AdminUserUpdateSerializer(
            data=request.data,
            context={"target_user": target_user},
        )
        serializer.is_valid(raise_exception=True)

        if target_user == request.user:
            if serializer.validated_data.get("role") != request.user.profile.role:
                return error_response(USER_CANNOT_CHANGE_OWN_ROLE)

        user = serializer.update(target_user, serializer.validated_data)
        return Response(
            AdminUserDetailSerializer(user).data,
            status=status.HTTP_200_OK,
        )


class AdminUserDeleteView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    @transaction.atomic
    def delete(self, request, pk):
        target_user = get_object_or_404(User, pk=pk)

        if target_user == request.user:
            return error_response(USER_CANNOT_DELETE_SELF)

        # Match the event lock order used by application review before cascading
        # the account's applications, so approval cannot race slot release.
        event_ids = target_user.event_applications.values_list('event_id', flat=True)
        events = list(Event.objects.select_for_update().filter(pk__in=event_ids).order_by('pk'))
        accepted_counts = dict(target_user.event_applications.filter(status=EventApplication.STATUS_APPROVED)
                               .values('event_id').annotate(total=Count('pk'))
                               .values_list('event_id', 'total'))
        target_user.delete()
        for event in events:
            released = accepted_counts.get(event.pk, 0)
            if released:
                Event.objects.filter(pk=event.pk).update(
                    current_participants=max(0, event.current_participants - released),
                    updated_at=timezone.now(),
                )
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminEventListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    serializer_class = EventSerializer

    def get_queryset(self):
        qs = Event.objects.select_related("teacher__profile", "attachment").annotate(
            pending_applications_count=Count(
                "applications",
                filter=Q(applications__status=EventApplication.STATUS_PENDING),
            )
        )

        status_value = self.request.query_params.get("status")
        teacher_name = self.request.query_params.get("teacher_name")
        search = self.request.query_params.get("search")
        now = timezone.now()

        if status_value:
            if status_value == Event.STATUS_DRAFT:
                qs = qs.filter(status=Event.STATUS_DRAFT)
            elif status_value == Event.STATUS_PUBLISHED:
                qs = qs.filter(status=Event.STATUS_PUBLISHED, start_time__gt=now)
            elif status_value == "ongoing":
                qs = qs.filter(
                    status=Event.STATUS_PUBLISHED,
                    start_time__lte=now,
                ).filter(Q(end_time__isnull=True) | Q(end_time__gte=now))
            elif status_value == Event.STATUS_CLOSED:
                qs = qs.filter(
                    Q(status=Event.STATUS_CLOSED)
                    | Q(status=Event.STATUS_PUBLISHED, end_time__lt=now)
                )

        if teacher_name:
            qs = qs.filter(
                Q(teacher__profile__name__icontains=teacher_name)
                | Q(teacher__username__icontains=teacher_name)
            )

        if search:
            qs = qs.filter(title__icontains=search)

        return qs.order_by("-created_at")


class AdminEventDetailView(generics.RetrieveDestroyAPIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    serializer_class = EventSerializer

    def get_queryset(self):
        return Event.objects.select_related("teacher__profile", "attachment").annotate(
            pending_applications_count=Count(
                "applications",
                filter=Q(applications__status=EventApplication.STATUS_PENDING),
            )
        )

    def destroy(self, request, *args, **kwargs):
        event = self.get_object()
        applications_count = event.applications.count()
        event.delete()
        return Response(
            {
                "detail": "Event deleted successfully",
                "applications_deleted": applications_count,
            },
            status=status.HTTP_200_OK,
        )


class AdminEventCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def post(self, request):
        serializer = AdminEventWriteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        event = serializer.save(status=Event.STATUS_DRAFT)

        return Response(
            EventSerializer(event).data,
            status=status.HTTP_201_CREATED,
        )


class AdminEventUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def put(self, request, pk):
        event = get_object_or_404(Event, pk=pk)
        serializer = AdminEventWriteSerializer(event, data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        event = serializer.save()

        return Response(
            EventSerializer(event).data,
            status=status.HTTP_200_OK,
        )


class AdminEventDeleteView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def delete(self, request, pk):
        event = get_object_or_404(
            Event.objects.prefetch_related("applications"), pk=pk
        )

        applications_count = event.applications.count()

        event.delete()

        return Response(
            {
                "detail": "Event deleted successfully",
                "applications_deleted": applications_count,
            },
            status=status.HTTP_200_OK,
        )


class AdminEventPublishView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def post(self, request, pk):
        event = get_object_or_404(Event, pk=pk)
        if event.status != Event.STATUS_DRAFT:
            return error_response(EVENT_ONLY_DRAFT_PUBLISH)
        event.status = Event.STATUS_PUBLISHED
        event.save()
        return Response(
            {"detail": "Event published successfully"},
            status=status.HTTP_200_OK,
        )


class AdminEventCloseView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def post(self, request, pk):
        event = get_object_or_404(Event, pk=pk)
        if event.status == Event.STATUS_CLOSED:
            return error_response(EVENT_ALREADY_CLOSED)
        event.status = Event.STATUS_CLOSED
        event.save()
        return Response(
            {"detail": "Event closed successfully"},
            status=status.HTTP_200_OK,
        )


class AdminApplicationListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    serializer_class = EventApplicationSerializer

    def get_queryset(self):
        qs = EventApplication.objects.select_related(
            "event", "student__profile", "resume"
        )

        event_id = self.request.query_params.get("event")
        status_value = self.request.query_params.get("status")

        if event_id:
            qs = qs.filter(event_id=event_id)

        if status_value:
            qs = qs.filter(status=status_value)

        return qs.order_by("-created_at")


class AdminApplicationApproveView(TeacherApplicationApproveView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]


class AdminApplicationRejectView(TeacherApplicationRejectView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]


class AdminFileListView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    serializer_class = AdminFileSerializer

    def get_queryset(self):
        qs = ManagedFile.objects.select_related("uploaded_by__profile")

        category = self.request.query_params.get("category")
        search = self.request.query_params.get("search")

        if category:
            qs = qs.filter(category=category)

        if search:
            qs = qs.filter(original_name__icontains=search)

        return qs.order_by("-created_at")


class AdminFileDeleteView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def delete(self, request, pk):
        managed_file = get_object_or_404(ManagedFile, pk=pk)

        if managed_file.is_referenced():
            return error_response(FILE_IN_USE)

        managed_file.file.delete(save=False)
        managed_file.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
