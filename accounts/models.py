from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user for future gameplay/运营字段扩展。"""

    email = models.EmailField("email address", unique=True, null=True, blank=True)  # type: ignore[assignment]
    email_verified = models.BooleanField("邮箱已验证", default=True, db_default=True, db_index=True)
    title = models.CharField("头衔", max_length=64, blank=True)

    class Meta:
        verbose_name = "用户"
        verbose_name_plural = "用户"

    def __str__(self) -> str:
        return self.get_full_name() or self.username

    def save(self, *args, **kwargs) -> None:
        normalized_email = (self.email or "").strip().lower()
        self.email = normalized_email or None
        super().save(*args, **kwargs)


class UserActiveSession(models.Model):
    """Authoritative single-session record per user."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="active_session")
    session_key = models.CharField(max_length=40, unique=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "用户活跃会话"
        verbose_name_plural = "用户活跃会话"

    def __str__(self) -> str:
        return f"{self.user_id}:{self.session_key[:8]}"


class EmailSendQuota(models.Model):
    """Monthly registration-email send budget."""

    month = models.DateField("月份", unique=True, db_index=True)
    sent_count = models.PositiveIntegerField("已预占发信数", default=0)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "月度邮件额度"
        verbose_name_plural = "月度邮件额度"
        ordering = ("-month",)

    def __str__(self) -> str:
        return f"{self.month}:{self.sent_count}"
