from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.event.models import Event
from apps.file.models import ManagedFile
from .models import UserProfile


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class RoleLoginTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient(enforce_csrf_checks=True)
        self.leader = self.make_user('leader', 'admin', can_switch_roles=True)
        self.student = self.make_user('student', 'student')
        self.teacher = self.make_user('teacher', 'teacher')
        self.admin = self.make_user('admin', 'admin')

    def tearDown(self):
        cache.clear()

    def make_user(self, username, role, **extra):
        user = User.objects.create_user(username=username, password='test-password')
        UserProfile.objects.create(user=user, student_id=username, name=username, role=role, **extra)
        return user

    def login(self, user, role=None, admin=False, **extra):
        token = self.client.get('/api/user/csrf/').data['csrfToken']
        data = {'student_id': user.username, 'password': 'test-password', **extra}
        if role is not None:
            data['role'] = role
        return self.client.post('/api/admin/login/' if admin else '/api/user/login/',
                                data, format='json', HTTP_X_CSRFTOKEN=token)

    def test_leader_has_only_selected_role_in_each_session(self):
        for role in ['student', 'teacher', 'admin']:
            with self.subTest(role=role):
                self.assertEqual(self.login(self.leader, role).status_code, 200)
                self.assertEqual(self.client.get('/api/user/me/profile/').data['role'], role)
                self.assertEqual(self.client.get('/api/admin/dashboard/').status_code, 200 if role == 'admin' else 403)
                self.assertEqual(self.client.get('/api/event/teacher/events/').status_code, 403 if role == 'student' else 200)
                self.assertEqual(self.client.get('/api/event/my-applications/').status_code, 200 if role == 'student' else 403)
        self.leader.profile.refresh_from_db()
        self.assertEqual(self.leader.profile.role, 'admin')

    def test_normal_accounts_cannot_select_another_role_or_grant_themselves_access(self):
        for user, role in [(self.student, 'admin'), (self.teacher, 'student'), (self.admin, 'teacher')]:
            self.assertEqual(self.login(user, role, can_switch_roles=True).status_code, 403)
        self.assertEqual(self.client.get('/api/user/me/profile/').status_code, 403)

    def test_legacy_login_uses_assigned_role(self):
        self.assertEqual(self.login(self.teacher).status_code, 200)
        self.assertEqual(self.client.get('/api/user/me/profile/').data['role'], 'teacher')

    def test_admin_entry_resets_selected_role(self):
        self.assertEqual(self.login(self.leader, 'teacher').status_code, 200)
        self.assertEqual(self.login(self.leader, admin=True).status_code, 200)
        self.assertEqual(self.client.get('/api/user/me/profile/').data['role'], 'admin')
        self.assertEqual(self.client.get('/api/admin/dashboard/').status_code, 200)

    def test_profile_update_cannot_grant_roles(self):
        self.login(self.student, 'student')
        token = self.client.get('/api/user/csrf/').data['csrfToken']
        response = self.client.patch('/api/user/me/profile/', {'role': 'admin', 'can_switch_roles': True, 'name': 'Updated'},
                                     format='json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.student.profile.refresh_from_db()
        self.assertEqual(self.student.profile.role, 'student')
        self.assertFalse(self.student.profile.can_switch_roles)

    def test_teacher_view_does_not_inherit_admin_ownership_bypass(self):
        event = Event.objects.create(teacher=self.teacher, title='Private draft', start_time=timezone.now(), expected_participants=1)
        self.login(self.leader, 'teacher')
        self.assertEqual(self.client.get(f'/api/event/teacher/events/{event.pk}/').status_code, 404)
        self.assertEqual(self.client.get(f'/api/event/events/{event.pk}/').status_code, 404)
        self.login(self.leader, 'admin')
        self.assertEqual(self.client.get(f'/api/event/teacher/events/{event.pk}/').status_code, 200)

    def test_student_view_does_not_inherit_admin_private_file_access(self):
        private_file = ManagedFile.objects.create(uploaded_by=self.teacher, original_name='private.txt', category='other')
        self.login(self.leader, 'student')
        self.assertEqual(self.client.get(f'/api/file/uploads/{private_file.pk}/').status_code, 404)
        self.login(self.leader, 'admin')
        self.assertEqual(self.client.get(f'/api/file/uploads/{private_file.pk}/').status_code, 200)
