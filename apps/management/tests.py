from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch
from smtplib import SMTPServerDisconnected

from django.contrib.auth.models import User
from django.http import Http404
from django.core.cache import cache
from django.core.signals import request_finished
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import connections, close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature

from apps.event.models import Event, EventApplication
from apps.event.views import lock_application_for_review
from apps.file.models import ManagedFile
from apps.user.models import EmailVerificationCode, UserProfile


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEBUG_LOG_API=False,
)
class IntegrationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.admin = self.make_user('admin', 'admin')
        self.teacher = self.make_user('teacher', 'teacher')
        self.student = self.make_user('student', 'student')
        self.client = APIClient(enforce_csrf_checks=True)

    def make_user(self, username, role):
        user = User.objects.create_user(username, f'{username}@example.com', 'testpass123')
        UserProfile.objects.create(user=user, student_id=username, name=username, role=role)
        return user

    def write(self, method, path, data=None):
        token = self.client.get('/api/user/csrf/').data['csrfToken']
        return getattr(self.client, method)(path, data or {}, format='json', HTTP_X_CSRFTOKEN=token)

    def login(self, user, admin=False):
        response = self.write('post', '/api/admin/login/' if admin else '/api/user/login/', {
            'student_id': user.username, 'password': 'testpass123',
        })
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn('session_id', response.data)

    def close_download(self, response):
        # Manual streaming response cleanup must preserve TestCase's transaction.
        request_finished.disconnect(close_old_connections)
        try:
            response.close()
        finally:
            request_finished.connect(close_old_connections)

    def make_event(self, **kwargs):
        data = dict(teacher=self.teacher, title='联调项目', event_type='科研',
                    description='测试项目', start_time=timezone.now() + timedelta(days=1),
                    expected_participants=1, status='published')
        data.update(kwargs)
        return Event.objects.create(**data)

    def test_login_requires_csrf_for_both_portals(self):
        for path in ['/api/user/login/', '/api/admin/login/']:
            response = self.client.post(path, {'student_id': 'admin', 'password': 'testpass123'}, format='json')
            self.assertEqual(response.status_code, 403)
        self.login(self.admin, admin=True)

    def test_browser_session_project_application_review_flow(self):
        self.login(self.teacher)
        response = self.write('post', '/api/event/teacher/events/', {
            'title': '科研项目', 'event_type': '科研', 'description': '项目说明',
            'start_time': (timezone.now() + timedelta(days=1)).isoformat(), 'expected_participants': 1,
        })
        self.assertEqual(response.status_code, 201, response.data)
        event_id = response.data['id']
        self.assertEqual(self.write('post', f'/api/event/teacher/events/{event_id}/publish/').status_code, 200)
        self.login(self.student)
        response = self.write('post', f'/api/event/events/{event_id}/apply/', {'statement': '申请说明'})
        self.assertEqual(response.status_code, 201, response.data)
        application_id = response.data['id']
        self.assertEqual(self.client.get('/api/event/my-applications/').data['count'], 1)
        self.login(self.teacher)
        self.assertEqual(self.write('post', f'/api/event/teacher/applications/{application_id}/approve/').status_code, 200)
        event = Event.objects.get(pk=event_id)
        self.assertEqual(event.current_participants, 1)
        self.assertEqual(self.write('post', f'/api/event/teacher/applications/{application_id}/approve/').status_code, 200)
        event.refresh_from_db()
        self.assertEqual(event.current_participants, 1)
        self.login(self.student)
        self.assertEqual(self.client.get('/api/event/my-applications/').data['results'][0]['status'], 'approved')

    def test_registration_with_email_code(self):
        response = self.write('post', '/api/user/send-register-code/', {'email': 'new@example.com'})
        self.assertEqual(response.status_code, 200)
        code = EmailVerificationCode.objects.get(email='new@example.com').code
        response = self.write('post', '/api/user/register/', {
            'student_id': 'newstudent', 'name': '新同学', 'email': 'new@example.com',
            'password': 'testpass123', 'password_confirm': 'testpass123', 'code': code,
        })
        self.assertEqual(response.status_code, 201, response.data)
        self.login(User.objects.get(username='newstudent'))

    @patch('apps.user.views.send_mail', side_effect=SMTPServerDisconnected('disconnected'))
    def test_failed_email_returns_retryable_error_without_valid_code(self, send_mail):
        response = self.write('post', '/api/user/send-register-code/', {'email': 'new@example.com'})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(EmailVerificationCode.objects.filter(email='new@example.com').exists())

    def test_admin_user_and_event_create_update_delete_routes(self):
        self.login(self.admin, admin=True)
        data = dict(username='newteacher', student_id='newteacher', name='新教师', email='newteacher@example.com', password='testpass123', role='teacher')
        response = self.write('post', '/api/admin/users/create/', data)
        self.assertEqual(response.status_code, 201, response.data)
        user_id = response.data['id']
        response = self.write('put', f'/api/admin/users/{user_id}/update/', {**data, 'name': '更新教师', 'is_active': True})
        self.assertEqual(response.status_code, 200, response.data)
        event_data = dict(teacher_id=user_id, title='管理员项目', description='说明', event_type='科研', expected_participants=2, start_time=(timezone.now() + timedelta(days=1)).isoformat(), end_time=None, attachment_id=None)
        response = self.write('post', '/api/admin/events/create/', event_data)
        self.assertEqual(response.status_code, 201, response.data)
        event_id = response.data['id']
        response = self.write('put', f'/api/admin/events/{event_id}/update/', {**event_data, 'title': '更新项目'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.write('delete', f'/api/admin/events/{event_id}/').status_code, 200)
        self.assertEqual(self.write('delete', f'/api/admin/users/{user_id}/delete/').status_code, 204)

    def test_admin_cannot_overbook_but_can_review_new_ongoing_application(self):
        self.login(self.admin, admin=True)
        event = self.make_event(current_participants=1)
        application = EventApplication.objects.create(event=event, student=self.student)
        self.assertEqual(self.write('post', f'/api/admin/applications/{application.pk}/approve/').status_code, 400)
        event.current_participants = 0
        event.start_time = timezone.now() - timedelta(hours=1)
        event.save()
        self.assertEqual(self.write('post', f'/api/admin/applications/{application.pk}/approve/').status_code, 200)
        self.assertEqual(self.write('post', f'/api/admin/applications/{application.pk}/reject/').status_code, 400)

    def test_student_can_download_published_attachment_but_not_private_resume(self):
        attachment = ManagedFile.objects.create(file=SimpleUploadedFile('attachment.pdf', b'public'), original_name='attachment.pdf', category='event_attachment', uploaded_by=self.teacher)
        private = ManagedFile.objects.create(file=SimpleUploadedFile('private.pdf', b'private'), original_name='private.pdf', category='application_resume', uploaded_by=self.teacher)
        self.make_event(attachment=attachment)
        self.login(self.student)
        response = self.client.get(f'/api/file/uploads/{attachment.pk}/download/')
        self.assertEqual(response.status_code, 200)
        self.close_download(response)
        self.assertEqual(self.client.get(f'/api/file/uploads/{private.pk}/download/').status_code, 404)

    def test_deleting_accepted_student_releases_only_their_slots(self):
        self.login(self.admin, admin=True)
        event = self.make_event(expected_participants=2, current_participants=2)
        pending_event = self.make_event(current_participants=0)
        other = self.make_user('other-student', 'student')
        EventApplication.objects.create(event=event, student=self.student, status='approved')
        EventApplication.objects.create(event=event, student=other, status='approved')
        EventApplication.objects.create(event=pending_event, student=self.student, status='pending')
        response = self.write('delete', f'/api/admin/users/{self.student.pk}/delete/')
        self.assertEqual(response.status_code, 204)
        event.refresh_from_db()
        pending_event.refresh_from_db()
        self.assertEqual(event.current_participants, 1)
        self.assertEqual(event.applications.filter(status='approved').count(), 1)
        self.assertEqual(pending_event.current_participants, 0)

    def test_review_of_application_deleted_after_initial_read_returns_not_found(self):
        event = self.make_event()
        application = EventApplication.objects.create(event=event, student=self.student)
        stale_application = EventApplication.objects.get(pk=application.pk)
        application.delete()
        with self.assertRaises(Http404):
            lock_application_for_review(stale_application)
        event.delete()
        with self.assertRaises(Http404):
            lock_application_for_review(stale_application)

    def test_cannot_bind_another_users_private_file(self):
        private = ManagedFile.objects.create(file=SimpleUploadedFile('private.pdf', b'private'), original_name='private.pdf', category='application_resume', uploaded_by=self.teacher)
        event = self.make_event()
        self.login(self.student)
        self.assertEqual(self.write('post', f'/api/event/events/{event.pk}/apply/', {'resume_id': str(private.pk)}).status_code, 400)

    def test_template_publish_download_and_disable(self):
        self.login(self.admin, admin=True)
        token = self.client.get('/api/user/csrf/').data['csrfToken']
        response = self.client.post('/api/file/uploads/', {'file': SimpleUploadedFile('template.pdf', b'template'), 'category': 'template'}, format='multipart', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 201, response.data)
        response = self.write('post', '/api/file/admin/templates/', {'key': 'resume', 'name': '简历模板', 'file_id': response.data['id']})
        self.assertEqual(response.status_code, 201, response.data)
        download_url = response.data['file']['file_url']
        self.login(self.student)
        self.assertEqual(self.client.get('/api/file/templates/').data['count'], 1)
        response = self.client.get(download_url)
        self.assertEqual(response.status_code, 200)
        self.close_download(response)
        self.login(self.admin, admin=True)
        self.assertEqual(self.write('delete', '/api/file/admin/templates/resume/').status_code, 200)
        self.login(self.student)
        self.assertEqual(self.client.get('/api/file/templates/').data['count'], 0)

    def test_template_rejects_wrong_file_category(self):
        self.login(self.admin, admin=True)
        file = ManagedFile.objects.create(file=SimpleUploadedFile('private.pdf', b'private'), original_name='private.pdf', category='application_resume', uploaded_by=self.admin)
        self.assertEqual(self.write('post', '/api/file/admin/templates/', {'key': 'wrong', 'name': '错误模板', 'file_id': str(file.pk)}).status_code, 400)

    def test_pagination_can_reach_records_after_twenty(self):
        for i in range(25): self.make_event(title=f'项目{i}')
        first = self.client.get('/api/event/events/').data
        second = self.client.get('/api/event/events/?page=2').data
        self.assertEqual(first['count'], 25)
        self.assertEqual(len(first['results']), 20)
        self.assertEqual(len(second['results']), 5)
        self.assertEqual(len(self.client.get('/api/event/events/?page_size=100').data['results']), 25)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
@skipUnlessDBFeature('has_select_for_update')
class PostgreSQLConcurrencyTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        teacher = User.objects.create_user('concurrent-teacher')
        UserProfile.objects.create(user=teacher, student_id=teacher.username, role='teacher')
        self.teacher_id = teacher.pk
        self.event = Event.objects.create(teacher=teacher, title='最后一个名额', event_type='科研', description='并发测试', start_time=timezone.now() + timedelta(days=1), expected_participants=1, status='published')
        self.student_ids = []
        for name in ['concurrent-student-1', 'concurrent-student-2']:
            student = User.objects.create_user(name)
            UserProfile.objects.create(user=student, student_id=name, role='student')
            self.student_ids.append(student.pk)

    def run_concurrently(self, tasks):
        barrier = Barrier(2)
        def worker(task):
            user_id, path, *method = task
            try:
                client = APIClient()
                client.force_authenticate(User.objects.get(pk=user_id))
                barrier.wait(timeout=10)
                return getattr(client, method[0] if method else 'post')(path, {}, format='json').status_code
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return sorted(pool.map(worker, tasks))

    def test_two_approvals_cannot_take_the_same_last_slot(self):
        applications = [EventApplication.objects.create(event=self.event, student_id=pk) for pk in self.student_ids]
        results = self.run_concurrently([(self.teacher_id, f'/api/event/teacher/applications/{app.pk}/approve/') for app in applications])
        self.assertEqual(results, [200, 400])
        self.event.refresh_from_db()
        self.assertEqual(self.event.current_participants, 1)
        self.assertEqual(self.event.applications.filter(status='approved').count(), 1)

    def test_simultaneous_duplicate_applications_create_one_record(self):
        path = f'/api/event/events/{self.event.pk}/apply/'
        results = self.run_concurrently([(self.student_ids[0], path)] * 2)
        self.assertEqual(results, [201, 400])
        self.assertEqual(self.event.applications.count(), 1)

    def test_user_deletion_during_review_never_leaves_a_reserved_slot(self):
        admin = User.objects.create_user('concurrent-admin')
        UserProfile.objects.create(user=admin, student_id=admin.username, role='admin')
        application = EventApplication.objects.create(event=self.event, student_id=self.student_ids[0])
        results = self.run_concurrently([
            (self.teacher_id, f'/api/event/teacher/applications/{application.pk}/approve/'),
            (admin.pk, f'/api/admin/users/{self.student_ids[0]}/delete/', 'delete'),
        ])
        self.assertIn(results, [[200, 204], [204, 404]])
        self.event.refresh_from_db()
        self.assertEqual(self.event.current_participants, 0)
        self.assertEqual(self.event.applications.count(), 0)
