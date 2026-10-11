import uuid
from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.user.models import UserProfile

from .models import FileAuditLog, FileTemplate, ManagedFile


class FileAPITestCase(TestCase):
    def setUp(self):
        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=media.name)
        override.enable()
        self.addCleanup(override.disable)
        self.client = APIClient()

        self.user = User.objects.create_user(
            username="fileuser",
            email="file@example.com",
            password="password123",
        )
        UserProfile.objects.create(
            user=self.user,
            name="File User",
            student_id="2023999",
            role=UserProfile.ROLE_STUDENT,
            class_name="Class 1",
        )

        self.admin = User.objects.create_user(
            username="adminuser",
            email="admin@example.com",
            password="password123",
        )
        UserProfile.objects.create(
            user=self.admin,
            name="Admin User",
            student_id="A999",
            role=UserProfile.ROLE_ADMIN,
            class_name="Admin",
        )

    def test_upload_returns_uuid_metadata(self):
        self.client.force_authenticate(user=self.user)
        upload = SimpleUploadedFile(
            "resume.zip",
            b"fake zip bytes",
            content_type="application/zip",
        )

        response = self.client.post(
            "/api/file/uploads/",
            {
                "file": upload,
                "category": ManagedFile.CATEGORY_APPLICATION_RESUME,
                "description": "测试简历压缩包",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        uuid.UUID(response.data["id"])
        self.assertEqual(response.data["original_name"], "resume.zip")
        self.assertEqual(response.data["category"], ManagedFile.CATEGORY_APPLICATION_RESUME)
        self.assertFalse(response.data["is_referenced"])
        self.assertTrue(response.data["can_delete"])
        self.assertIn(f"/api/file/uploads/{response.data['id']}/download/", response.data["file_url"])
        self.assertTrue(
            FileAuditLog.objects.filter(action=FileAuditLog.ACTION_UPLOAD, actor=self.user).exists()
        )

    def test_upload_rejects_invalid_extension_for_resume(self):
        self.client.force_authenticate(user=self.user)
        upload = SimpleUploadedFile(
            "resume.exe",
            b"fake exe bytes",
            content_type="application/octet-stream",
        )

        response = self.client.post(
            "/api/file/uploads/",
            {
                "file": upload,
                "category": ManagedFile.CATEGORY_APPLICATION_RESUME,
                "description": "非法简历文件",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_rejects_oversized_file(self):
        self.client.force_authenticate(user=self.user)
        upload = SimpleUploadedFile(
            "resume.pdf",
            b"x" * (21 * 1024 * 1024),
            content_type="application/pdf",
        )

        response = self.client.post(
            "/api/file/uploads/",
            {
                "file": upload,
                "category": ManagedFile.CATEGORY_APPLICATION_RESUME,
                "description": "过大简历文件",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_my_files_only_returns_current_user_files(self):
        ManagedFile.objects.create(
            file=SimpleUploadedFile("mine.zip", b"123"),
            original_name="mine.zip",
            category=ManagedFile.CATEGORY_OTHER,
            uploaded_by=self.user,
            file_size=3,
        )
        ManagedFile.objects.create(
            file=SimpleUploadedFile("admin.zip", b"123"),
            original_name="admin.zip",
            category=ManagedFile.CATEGORY_OTHER,
            uploaded_by=self.admin,
            file_size=3,
        )

        self.client.force_authenticate(user=self.user)
        response = self.client.get("/api/file/my-files/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["original_name"], "mine.zip")

    def test_template_list_returns_active_templates(self):
        managed_file = ManagedFile.objects.create(
            file=SimpleUploadedFile("application-form.docx", b"docx bytes"),
            original_name="application-form.docx",
            category=ManagedFile.CATEGORY_TEMPLATE,
            uploaded_by=self.admin,
            file_size=10,
        )
        FileTemplate.objects.create(
            key="application-form",
            name="申请表模板",
            description="固定申请表模板",
            file=managed_file,
            is_active=True,
        )

        self.client.force_authenticate(user=self.user)
        response = self.client.get("/api/file/templates/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["key"], "application-form")

    def test_admin_can_create_template_binding(self):
        managed_file = ManagedFile.objects.create(
            file=SimpleUploadedFile("application-form.docx", b"docx bytes"),
            original_name="application-form.docx",
            category=ManagedFile.CATEGORY_TEMPLATE,
            uploaded_by=self.admin,
            file_size=10,
        )

        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/file/admin/templates/",
            {
                "key": "application-form",
                "name": "申请表模板",
                "description": "固定申请表模板",
                "file_id": str(managed_file.id),
                "is_active": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["key"], "application-form")
        self.assertTrue(
            FileAuditLog.objects.filter(
                action=FileAuditLog.ACTION_TEMPLATE_CREATE,
                actor=self.admin,
                template_key="application-form",
            ).exists()
        )

    def test_non_admin_cannot_create_template_binding(self):
        managed_file = ManagedFile.objects.create(
            file=SimpleUploadedFile("application-form.docx", b"docx bytes"),
            original_name="application-form.docx",
            category=ManagedFile.CATEGORY_TEMPLATE,
            uploaded_by=self.user,
            file_size=10,
        )

        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            "/api/file/admin/templates/",
            {
                "key": "application-form",
                "name": "申请表模板",
                "description": "固定申请表模板",
                "file_id": str(managed_file.id),
                "is_active": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_delete_template_disables_it(self):
        managed_file = ManagedFile.objects.create(
            file=SimpleUploadedFile("application-form.docx", b"docx bytes"),
            original_name="application-form.docx",
            category=ManagedFile.CATEGORY_TEMPLATE,
            uploaded_by=self.admin,
            file_size=10,
        )
        FileTemplate.objects.create(
            key="application-form",
            name="申请表模板",
            description="固定申请表模板",
            file=managed_file,
            is_active=True,
        )

        self.client.force_authenticate(user=self.admin)
        response = self.client.delete("/api/file/admin/templates/application-form/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(FileTemplate.objects.get(key="application-form").is_active)
        self.assertTrue(
            FileAuditLog.objects.filter(
                action=FileAuditLog.ACTION_TEMPLATE_DISABLE,
                actor=self.admin,
                template_key="application-form",
            ).exists()
        )

    def test_owner_can_delete_unreferenced_file(self):
        managed_file = ManagedFile.objects.create(
            file=SimpleUploadedFile("temp.zip", b"123"),
            original_name="temp.zip",
            category=ManagedFile.CATEGORY_OTHER,
            uploaded_by=self.user,
            file_size=3,
        )

        self.client.force_authenticate(user=self.user)
        response = self.client.delete(f"/api/file/uploads/{managed_file.id}/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ManagedFile.objects.filter(pk=managed_file.id).exists())
        self.assertTrue(
            FileAuditLog.objects.filter(
                action=FileAuditLog.ACTION_DELETE,
                actor=self.user,
                metadata__original_name="temp.zip",
            ).exists()
        )

    def test_referenced_file_cannot_be_deleted(self):
        managed_file = ManagedFile.objects.create(
            file=SimpleUploadedFile("application-form.docx", b"docx bytes"),
            original_name="application-form.docx",
            category=ManagedFile.CATEGORY_TEMPLATE,
            uploaded_by=self.admin,
            file_size=10,
        )
        FileTemplate.objects.create(
            key="application-form",
            name="申请表模板",
            description="固定申请表模板",
            file=managed_file,
            is_active=True,
        )

        self.client.force_authenticate(user=self.admin)
        response = self.client.delete(f"/api/file/uploads/{managed_file.id}/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(ManagedFile.objects.filter(pk=managed_file.id).exists())
        self.assertTrue(
            FileAuditLog.objects.filter(
                action=FileAuditLog.ACTION_DELETE_BLOCKED,
                actor=self.admin,
                managed_file=managed_file,
            ).exists()
        )

    def test_owner_can_download_own_file(self):
        managed_file = ManagedFile.objects.create(
            file=SimpleUploadedFile("temp.zip", b"123"),
            original_name="temp.zip",
            category=ManagedFile.CATEGORY_OTHER,
            uploaded_by=self.user,
            file_size=3,
            content_type="application/zip",
        )

        self.client.force_authenticate(user=self.user)
        response = self.client.get(f"/api/file/uploads/{managed_file.id}/download/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertEqual(
            response["Content-Disposition"],
            'attachment; filename="temp.zip"',
        )
        self.assertTrue(
            FileAuditLog.objects.filter(
                action=FileAuditLog.ACTION_DOWNLOAD,
                actor=self.user,
                managed_file=managed_file,
            ).exists()
        )

    def test_user_cannot_download_other_users_private_file(self):
        managed_file = ManagedFile.objects.create(
            file=SimpleUploadedFile("private.zip", b"123"),
            original_name="private.zip",
            category=ManagedFile.CATEGORY_OTHER,
            uploaded_by=self.admin,
            file_size=3,
        )

        self.client.force_authenticate(user=self.user)
        response = self.client.get(f"/api/file/uploads/{managed_file.id}/download/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
