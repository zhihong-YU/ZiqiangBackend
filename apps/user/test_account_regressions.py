from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.management.serializers import AdminUserUpdateSerializer
from .models import EmailVerificationCode, UserProfile
from .serializers import RegisterSerializer


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AccountRegressionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_login_accepts_changed_academic_id_for_both_portals(self):
        for role, path in [('student', '/api/user/login/'), ('admin', '/api/admin/login/')]:
            user = User.objects.create_user('internal-' + role, password='test-password')
            UserProfile.objects.create(user=user, student_id='old-' + role, role=role)
            UserProfile.objects.filter(user=user).update(student_id='new-' + role)
            client = APIClient(enforce_csrf_checks=True)
            token = client.get('/api/user/csrf/').data['csrfToken']
            response = client.post(path, {'student_id': 'new-' + role, 'password': 'test-password'},
                                   format='json', HTTP_X_CSRFTOKEN=token)
            self.assertEqual(response.status_code, 200, response.data)

    def test_admin_password_update_rejects_short_password_but_allows_no_change(self):
        user = User.objects.create_user('student', password='test-password', email='student@example.com')
        UserProfile.objects.create(user=user, student_id='student', role='student')
        data = dict(email=user.email, name='学生', student_id='student', role='student', is_active=True)
        short = AdminUserUpdateSerializer(data={**data, 'password': 'x'}, context={'target_user': user})
        self.assertFalse(short.is_valid())
        self.assertIn('password', short.errors)
        unchanged = AdminUserUpdateSerializer(data={**data, 'password': ''}, context={'target_user': user})
        self.assertTrue(unchanged.is_valid(), unchanged.errors)
        unchanged.update(user, unchanged.validated_data)
        self.assertTrue(user.check_password('test-password'))


@skipUnlessDBFeature('has_select_for_update')
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class RegistrationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def attempt_registrations(self, codes):
        barrier = Barrier(2)
        def worker(item):
            index, code = item
            try:
                serializer = RegisterSerializer(data={
                    'student_id': 'race-' + str(index), 'name': '测试',
                    'email': 'race@example.com', 'code': code,
                    'password': 'test-password', 'password_confirm': 'test-password',
                })
                serializer.is_valid(raise_exception=True)
                # Both validations complete before either transaction writes.
                barrier.wait(timeout=10)
                try:
                    serializer.save()
                    return 'created'
                except ValidationError:
                    return 'rejected'
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, enumerate(codes)))
        self.assertCountEqual(results, ['created', 'rejected'])
        self.assertEqual(User.objects.filter(email='race@example.com').count(), 1)
        self.assertEqual(EmailVerificationCode.objects.filter(is_used=True).count(), 1)

    def test_same_code_is_consumed_only_once(self):
        EmailVerificationCode.objects.create(email='race@example.com', purpose='register', code='123456')
        self.attempt_registrations(['123456', '123456'])

    def test_different_codes_for_same_mailbox_cannot_create_duplicate_users(self):
        for code in ['123456', '654321']:
            EmailVerificationCode.objects.create(email='race@example.com', purpose='register', code=code)
        self.attempt_registrations(['123456', '654321'])
