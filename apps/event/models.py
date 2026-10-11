from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Event(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_CLOSED = "closed"

    STATUS_CHOICES = (
        (STATUS_DRAFT, "草稿"),
        (STATUS_PUBLISHED, "已发布"),
        (STATUS_CLOSED, "已关闭"),
    )

    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_events",
        verbose_name="发布教师",
    )
    title = models.CharField("项目名称", max_length=100)
    event_type = models.CharField("项目类型", max_length=50)
    start_time = models.DateTimeField("开始时间")
    end_time = models.DateTimeField("结束时间", blank=True, null=True)
    location = models.CharField("地点", max_length=200, blank=True, default="")
    description = models.TextField("项目简介")
    attachment = models.ForeignKey(
        "file.ManagedFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events_as_attachment",
        verbose_name="附件",
    )
    expected_participants = models.PositiveIntegerField("期望人数")
    current_participants = models.PositiveIntegerField("当前参与人数", default=0)
    status = models.CharField(
        "项目状态",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.get_runtime_status()})"

    def clean(self):
        if self.end_time and self.start_time >= self.end_time:
            raise ValidationError("End time must be later than start time.")
        if self.current_participants > self.expected_participants:
            raise ValidationError("Current participants cannot exceed expected participants.")

    def get_runtime_status(self):
        if self.status == self.STATUS_DRAFT:
            return self.STATUS_DRAFT
        if self.status == self.STATUS_CLOSED:
            return self.STATUS_CLOSED

        now = timezone.now()
        if now < self.start_time:
            return self.STATUS_PUBLISHED
        if self.end_time is None or self.start_time <= now <= self.end_time:
            return "ongoing"
        return self.STATUS_CLOSED

    def can_publish(self):
        return self.status == self.STATUS_DRAFT

    def can_delete(self):
        return self.status == self.STATUS_DRAFT

    def can_close(self):
        return self.status == self.STATUS_PUBLISHED and timezone.now() < self.start_time

    def can_edit(self):
        return self.get_runtime_status() != self.STATUS_CLOSED

    def can_accept_applications(self):
        return self.get_runtime_status() in {self.STATUS_PUBLISHED, "ongoing"}

    def has_available_slots(self):
        return self.current_participants < self.expected_participants


class EventApplication(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = (
        (STATUS_PENDING, "待审核"),
        (STATUS_APPROVED, "已通过"),
        (STATUS_REJECTED, "已拒绝"),
        (STATUS_CANCELLED, "已撤回"),
    )

    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="applications",
        verbose_name="项目",
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="event_applications",
        verbose_name="申请学生",
    )
    statement = models.TextField("申请说明", blank=True, default="")
    resume = models.ForeignKey(
        "file.ManagedFile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="applications_as_resume",
        verbose_name="简历",
    )
    status = models.CharField(
        "申请状态",
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    review_note = models.TextField("审核备注", blank=True, default="")
    reviewed_at = models.DateTimeField("审核时间", blank=True, null=True)
    created_at = models.DateTimeField("申请时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.student.username} -> {self.event.title} ({self.status})"

    def can_change_decision(self):
        runtime_status = self.event.get_runtime_status()
        return runtime_status == Event.STATUS_PUBLISHED or (
            runtime_status == "ongoing" and self.status == self.STATUS_PENDING
        )
