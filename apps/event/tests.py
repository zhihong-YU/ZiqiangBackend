from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.notification.models import Notification
from apps.user.models import UserProfile

from .models import Event, EventApplication


@override_settings(ENABLE_NOTIFICATIONS=True)
class EventAPITestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.now = timezone.now()

        self.teacher = User.objects.create_user(
            username="teacher1",
            email="teacher@example.com",
            password="password123",
        )
        UserProfile.objects.create(
            user=self.teacher,
            name="Teacher One",
            student_id="T001",
            role=UserProfile.ROLE_TEACHER,
            class_name="Teachers",
        )

        self.student = User.objects.create_user(
            username="student1",
            email="student@example.com",
            password="password123",
        )
        UserProfile.objects.create(
            user=self.student,
            name="Student One",
            student_id="2023001",
            role=UserProfile.ROLE_STUDENT,
            class_name="Class 1",
        )

        self.admin = User.objects.create_user(
            username="admin1",
            email="admin@example.com",
            password="password123",
        )
        UserProfile.objects.create(
            user=self.admin,
            name="Admin One",
            student_id="A001",
            role=UserProfile.ROLE_ADMIN,
            class_name="Admin",
        )

        self.other_teacher = User.objects.create_user(
            username="teacher2",
            email="teacher2@example.com",
            password="password123",
        )
        UserProfile.objects.create(
            user=self.other_teacher,
            name="Teacher Two",
            student_id="T002",
            role=UserProfile.ROLE_TEACHER,
            class_name="Teachers",
        )

    def create_published_event(self, **kwargs):
        data = {
            "teacher": self.teacher,
            "title": "公开项目",
            "event_type": "科研",
            "start_time": self.now + timedelta(days=1),
            "end_time": self.now + timedelta(days=2),
            "location": "主楼 102",
            "description": "项目简介",
            "expected_participants": 2,
            "status": Event.STATUS_PUBLISHED,
        }
        data.update(kwargs)
        return Event.objects.create(**data)

    def test_teacher_can_create_draft_event(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(
            "/api/event/teacher/events/",
            {
                "title": "后端招新项目",
                "event_type": "科研",
                "start_time": (self.now + timedelta(days=3)).isoformat(),
                "end_time": (self.now + timedelta(days=5)).isoformat(),
                "location": "主楼 101",
                "description": "项目简介",
                "expected_participants": 10,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        event = Event.objects.get(title="后端招新项目")
        self.assertEqual(event.status, Event.STATUS_DRAFT)
        self.assertEqual(event.teacher, self.teacher)

    def test_teacher_can_create_event_without_end_time_and_location(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(
            "/api/event/teacher/events/",
            {
                "title": "长期项目",
                "event_type": "实践",
                "start_time": (self.now + timedelta(days=3)).isoformat(),
                "description": "截止时间待定，地点待定",
                "expected_participants": 20,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        event = Event.objects.get(title="长期项目")
        self.assertIsNone(event.end_time)
        self.assertEqual(event.location, "")

    def test_student_cannot_create_event(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            "/api/event/teacher/events/",
            {
                "title": "非法项目",
                "event_type": "科研",
                "start_time": (self.now + timedelta(days=3)).isoformat(),
                "end_time": (self.now + timedelta(days=5)).isoformat(),
                "location": "主楼 101",
                "description": "项目简介",
                "expected_participants": 10,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_teacher_can_publish_own_draft_event(self):
        event = Event.objects.create(
            teacher=self.teacher,
            title="待发布项目",
            event_type="科研",
            start_time=self.now + timedelta(days=1),
            end_time=self.now + timedelta(days=2),
            location="主楼 101",
            description="项目简介",
            expected_participants=8,
        )

        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(f"/api/event/teacher/events/{event.id}/publish/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event.refresh_from_db()
        self.assertEqual(event.status, Event.STATUS_PUBLISHED)

    def test_public_list_only_returns_published_events(self):
        Event.objects.create(
            teacher=self.teacher,
            title="草稿项目",
            event_type="科研",
            start_time=self.now + timedelta(days=1),
            end_time=self.now + timedelta(days=2),
            location="主楼 101",
            description="项目简介",
            expected_participants=8,
            status=Event.STATUS_DRAFT,
        )
        self.create_published_event(title="公开项目")

        response = self.client.get("/api/event/events/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["title"], "公开项目")
        self.assertEqual(response.data["results"][0]["pending_applications_count"], 0)

    def test_public_event_list_can_filter_by_teacher_name(self):
        self.create_published_event(title="老师一项目")
        self.create_published_event(title="老师二项目", teacher=self.other_teacher)

        response = self.client.get("/api/event/events/?teacher_name=Teacher Two")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["title"], "老师二项目")

    def test_public_event_list_can_filter_by_status(self):
        self.create_published_event(title="招募中项目")
        self.create_published_event(
            title="进行中项目",
            start_time=self.now - timedelta(hours=1),
            end_time=self.now + timedelta(days=1),
        )
        self.create_published_event(
            title="历史项目",
            start_time=self.now - timedelta(days=3),
            end_time=self.now - timedelta(days=1),
        )

        response = self.client.get("/api/event/events/?status=ongoing")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["title"], "进行中项目")

    def test_public_event_list_can_filter_by_can_recruit(self):
        self.create_published_event(title="还能招人", expected_participants=2, current_participants=0)
        self.create_published_event(title="已满员", expected_participants=1, current_participants=1)
        self.create_published_event(
            title="进行中项目",
            start_time=self.now - timedelta(hours=1),
            end_time=self.now + timedelta(days=1),
            expected_participants=2,
            current_participants=0,
        )

        response = self.client.get("/api/event/events/?can_recruit=true")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["title"], "还能招人")

    def test_published_event_cannot_be_deleted(self):
        event = self.create_published_event()
        self.client.force_authenticate(user=self.teacher)
        response = self.client.delete(f"/api/event/teacher/events/{event.id}/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())

    def test_draft_event_can_be_deleted(self):
        event = Event.objects.create(
            teacher=self.teacher,
            title="草稿项目",
            event_type="科研",
            start_time=self.now + timedelta(days=1),
            end_time=self.now + timedelta(days=2),
            location="主楼 102",
            description="项目简介",
            expected_participants=8,
            status=Event.STATUS_DRAFT,
        )

        self.client.force_authenticate(user=self.teacher)
        response = self.client.delete(f"/api/event/teacher/events/{event.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Event.objects.filter(pk=event.pk).exists())

    def test_published_event_before_start_can_be_closed(self):
        event = self.create_published_event(title="待关闭项目")
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(f"/api/event/teacher/events/{event.id}/close/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event.refresh_from_db()
        self.assertEqual(event.status, Event.STATUS_CLOSED)

    def test_ongoing_event_cannot_be_closed(self):
        event = self.create_published_event(
            title="进行中项目",
            start_time=self.now - timedelta(days=1),
            end_time=self.now + timedelta(days=1),
        )
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(f"/api/event/teacher/events/{event.id}/close/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        event.refresh_from_db()
        self.assertEqual(event.status, Event.STATUS_PUBLISHED)

    def test_closed_event_cannot_be_edited(self):
        event = self.create_published_event(
            title="历史项目",
            start_time=self.now - timedelta(days=3),
            end_time=self.now - timedelta(days=1),
        )
        self.client.force_authenticate(user=self.teacher)
        response = self.client.patch(
            f"/api/event/teacher/events/{event.id}/",
            {"title": "不应修改"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_can_view_teacher_event_list(self):
        self.create_published_event(title="老师项目")
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/event/teacher/events/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)

    def test_admin_teacher_event_list_can_filter_by_teacher_name(self):
        self.create_published_event(title="老师一项目")
        self.create_published_event(title="老师二项目", teacher=self.other_teacher)
        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/event/teacher/events/?teacher_name=Teacher Two")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["title"], "老师二项目")

    def test_student_can_apply_to_published_event(self):
        event = self.create_published_event()
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            f"/api/event/events/{event.id}/apply/",
            {"statement": "我想参加这个项目"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        application = EventApplication.objects.get(event=event, student=self.student)
        self.assertEqual(application.status, EventApplication.STATUS_PENDING)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.teacher,
                notification_type=Notification.TYPE_EVENT_APPLICATION_SUBMITTED,
                metadata__application_id=application.id,
            ).exists()
        )

        event_list_response = self.client.get("/api/event/events/")
        self.assertEqual(event_list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(event_list_response.data["results"][0]["pending_applications_count"], 1)

    def test_student_can_apply_to_ongoing_event(self):
        event = self.create_published_event(
            start_time=self.now - timedelta(hours=1),
            end_time=self.now + timedelta(days=1),
        )
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            f"/api/event/events/{event.id}/apply/",
            {"statement": "项目开始后也希望加入"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        application = EventApplication.objects.get(event=event, student=self.student)
        self.assertEqual(application.status, EventApplication.STATUS_PENDING)

    def test_student_application_list_can_filter_by_event_and_status(self):
        event1 = self.create_published_event(title="项目一")
        event2 = self.create_published_event(title="项目二", teacher=self.other_teacher)
        EventApplication.objects.create(
            event=event1,
            student=self.student,
            statement="申请一",
            status=EventApplication.STATUS_PENDING,
        )
        EventApplication.objects.create(
            event=event2,
            student=self.student,
            statement="申请二",
            status=EventApplication.STATUS_REJECTED,
        )

        self.client.force_authenticate(user=self.student)
        response = self.client.get(
            f"/api/event/my-applications/?event={event2.id}&status={EventApplication.STATUS_REJECTED}"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["event_title"], "项目二")

    def test_student_cannot_apply_to_full_event(self):
        event = self.create_published_event(expected_participants=1, current_participants=1)
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            f"/api/event/events/{event.id}/apply/",
            {"statement": "我想参加这个满员项目"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_student_cannot_apply_twice(self):
        event = self.create_published_event()
        EventApplication.objects.create(event=event, student=self.student, statement="第一次申请")
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            f"/api/event/events/{event.id}/apply/",
            {"statement": "第二次申请"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_student_cannot_cancel_application(self):
        event = self.create_published_event()
        application = EventApplication.objects.create(
            event=event,
            student=self.student,
            statement="不可撤回申请",
        )
        self.client.force_authenticate(user=self.student)
        response = self.client.delete(f"/api/event/my-applications/{application.id}/")
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_teacher_can_approve_application(self):
        event = self.create_published_event(expected_participants=1)
        application = EventApplication.objects.create(
            event=event,
            student=self.student,
            statement="请老师通过",
        )
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(
            f"/api/event/teacher/applications/{application.id}/approve/",
            {"review_note": "欢迎加入"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        application.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual(application.status, EventApplication.STATUS_APPROVED)
        self.assertEqual(event.current_participants, 1)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.student,
                notification_type=Notification.TYPE_EVENT_APPLICATION_APPROVED,
                metadata__application_id=application.id,
            ).exists()
        )

    def test_teacher_can_change_approved_application_to_rejected_before_ongoing(self):
        event = self.create_published_event(expected_participants=1, current_participants=1)
        application = EventApplication.objects.create(
            event=event,
            student=self.student,
            statement="请老师查看",
            status=EventApplication.STATUS_APPROVED,
        )
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(
            f"/api/event/teacher/applications/{application.id}/reject/",
            {"review_note": "改判拒绝"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        application.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual(application.status, EventApplication.STATUS_REJECTED)
        self.assertEqual(event.current_participants, 0)

    def test_teacher_can_approve_rejected_application_before_ongoing(self):
        event = self.create_published_event(expected_participants=1)
        application = EventApplication.objects.create(
            event=event,
            student=self.student,
            statement="请老师查看",
            status=EventApplication.STATUS_REJECTED,
        )
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(
            f"/api/event/teacher/applications/{application.id}/approve/",
            {"review_note": "补录通过"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        application.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual(application.status, EventApplication.STATUS_APPROVED)
        self.assertEqual(event.current_participants, 1)

    def test_teacher_can_reject_application(self):
        event = self.create_published_event()
        application = EventApplication.objects.create(
            event=event,
            student=self.student,
            statement="请老师查看",
        )
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(
            f"/api/event/teacher/applications/{application.id}/reject/",
            {"review_note": "名额不匹配"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        application.refresh_from_db()
        self.assertEqual(application.status, EventApplication.STATUS_REJECTED)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.student,
                notification_type=Notification.TYPE_EVENT_APPLICATION_REJECTED,
                metadata__application_id=application.id,
            ).exists()
        )

    def test_teacher_application_list_can_filter_by_event_and_status(self):
        event1 = self.create_published_event(title="项目一")
        event2 = self.create_published_event(title="项目二")
        EventApplication.objects.create(
            event=event1,
            student=self.student,
            statement="申请一",
            status=EventApplication.STATUS_PENDING,
        )
        EventApplication.objects.create(
            event=event2,
            student=self.student,
            statement="申请二",
            status=EventApplication.STATUS_REJECTED,
        )

        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(
            f"/api/event/teacher/applications/?event={event2.id}&status={EventApplication.STATUS_REJECTED}"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["event_title"], "项目二")

    def test_teacher_can_review_pending_application_after_ongoing(self):
        event = self.create_published_event(
            start_time=self.now - timedelta(hours=1),
            end_time=self.now + timedelta(days=1),
        )
        application = EventApplication.objects.create(
            event=event,
            student=self.student,
            statement="中途报名",
        )
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(
            f"/api/event/teacher/applications/{application.id}/approve/",
            {"review_note": "允许加入"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        application.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual(application.status, EventApplication.STATUS_APPROVED)
        self.assertEqual(event.current_participants, 1)
        # Existing decisions stay fixed once the project starts.
        response = self.client.post(
            f"/api/event/teacher/applications/{application.id}/reject/", {}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_teacher_cannot_approve_when_event_is_full(self):
        event = self.create_published_event(expected_participants=1, current_participants=1)
        application = EventApplication.objects.create(
            event=event,
            student=self.student,
            statement="请老师查看",
        )
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(
            f"/api/event/teacher/applications/{application.id}/approve/",
            {"review_note": "名额已满"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
