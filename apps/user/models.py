from django.conf import settings
from django.db import models


def avatar_upload_to(instance, filename):
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "jpg"
    return f"avatars/{instance.user_id}.{ext}"


class UserProfile(models.Model):
    ROLE_STUDENT = "student"
    ROLE_TEACHER = "teacher"
    ROLE_ADMIN = "admin"

    ROLE_CHOICES = (
        (ROLE_STUDENT, "学生"),
        (ROLE_TEACHER, "教师"),
        (ROLE_ADMIN, "管理员"),
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name="账号",
    )
    name = models.CharField("姓名", max_length=20)
    student_id = models.CharField("学号/工号", max_length=20, unique=True)
    class_name = models.CharField("班级/部门", max_length=50)
    phone = models.CharField("手机号", max_length=20, blank=True)
    wechat_id = models.CharField("微信号", max_length=50, blank=True)
    bio = models.TextField("个人简介", blank=True, default="这个人什么也没有写")
    role = models.CharField(
        "角色",
        max_length=10,
        choices=ROLE_CHOICES,
        default=ROLE_STUDENT,
    )
    avatar = models.ImageField("头像", upload_to=avatar_upload_to, blank=True)
    can_switch_roles = models.BooleanField("允许切换身份", default=False)

    def __str__(self):
        return f"{self.name} ({self.student_id}) - {self.get_role_display()}"

    def has_role(self, *roles):
        return self.role in roles

    def is_student(self):
        return self.has_role(self.ROLE_STUDENT)

    def is_teacher(self):
        return self.has_role(self.ROLE_TEACHER)

    def is_admin(self):
        return self.has_role(self.ROLE_ADMIN)


class EmailVerificationCode(models.Model):
    PURPOSE_CHOICES = (
        ("register", "注册"),
        ("change_email", "修改邮箱"),
    )

    email = models.EmailField("邮箱")
    code = models.CharField("验证码", max_length=6)
    purpose = models.CharField("用途", max_length=20, choices=PURPOSE_CHOICES)
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def delete_expired(cls, minutes=6):
        """
        删除过期的验证码记录。
        默认保留最近 6 分钟内的记录。
        """
        from datetime import timedelta

        from django.utils import timezone

        expired_time = timezone.now() - timedelta(minutes=minutes)
        expired_records = cls.objects.filter(created_at__lt=expired_time)
        count = expired_records.count()
        expired_records.delete()
        return count

    class Meta:
        indexes = [
            models.Index(fields=["email", "purpose", "created_at"]),
        ]
