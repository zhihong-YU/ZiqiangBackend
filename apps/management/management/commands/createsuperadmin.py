import os

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from apps.user.models import UserProfile


class Command(BaseCommand):
    help = "创建或重置管理员账号（从 .env 读取 ADMIN_USERNAME / ADMIN_PASSWORD / ADMIN_EMAIL）"

    def handle(self, *args, **options):
        username = os.getenv("ADMIN_USERNAME", "admin")
        password = os.getenv("ADMIN_PASSWORD", "")
        if len(password) < 12 or password.startswith('replace-with'):
            raise CommandError('Set a strong ADMIN_PASSWORD of at least 12 characters before provisioning an administrator.')
        email = os.getenv("ADMIN_EMAIL", "admin@example.com")

        user, created = User.objects.update_or_create(
            username=username,
            defaults={"email": email},
        )

        if not created:
            user.set_password(password)
            user.is_active = True
            user.save()
            user.profile.role = UserProfile.ROLE_ADMIN
            user.profile.save()
            self.stdout.write(
                self.style.SUCCESS(f"管理员账号已重置: {username} ({email})")
            )
        else:
            user.set_password(password)
            user.save()
            UserProfile.objects.create(
                user=user,
                name="系统管理员",
                student_id=username,
                role=UserProfile.ROLE_ADMIN,
            )
            self.stdout.write(
                self.style.SUCCESS(f"管理员账号已创建: {username} ({email})")
            )
