from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class WorldChatSendAttempt(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "待发布"
        PUBLISHED = "published", "已发布"
        REFUND_PENDING = "refund_pending", "待退款"
        REFUNDED = "refunded", "已退款"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="world_chat_send_attempts",
        verbose_name="用户",
    )
    manor = models.ForeignKey(
        "gameplay.Manor",
        on_delete=models.CASCADE,
        related_name="world_chat_send_attempts",
        verbose_name="庄园",
    )
    operation_id = models.UUIDField("操作ID")
    message_id = models.UUIDField("消息ID", default=uuid.uuid4, unique=True, editable=False)
    text = models.CharField("消息文本", max_length=200)
    status = models.CharField("状态", max_length=16, choices=Status.choices, default=Status.PENDING)
    trumpet_consumed = models.BooleanField("已消耗小喇叭", default=False)
    attempts = models.PositiveIntegerField("尝试次数", default=0)
    last_error = models.TextField("最后错误", blank=True)
    publish_claim_token = models.UUIDField(
        "发布 claim token",
        null=True,
        blank=True,
        editable=False,
    )
    publish_claimed_at = models.DateTimeField("发布 claim 时间", null=True, blank=True)
    published_at = models.DateTimeField("发布时间", null=True, blank=True)
    refunded_at = models.DateTimeField("退款时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "世界聊天发送记录"
        verbose_name_plural = "世界聊天发送记录"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "operation_id"],
                name="world_chat_user_operation_uniq",
            )
        ]
        indexes = [
            models.Index(
                fields=["status", "created_at"],
                name="world_chat_status_created_idx",
            )
        ]
