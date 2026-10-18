"""帮会嗜血铁骑训练记录。"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from .base import Guild


class GuildCavalryTraining(models.Model):
    guild = models.ForeignKey(
        Guild, on_delete=models.CASCADE, related_name="cavalry_trainings", verbose_name="所属帮会"
    )
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="guild_cavalry_trainings"
    )
    quantity = models.PositiveIntegerField(verbose_name="训练人数")
    started_at = models.DateTimeField(default=timezone.now, verbose_name="开始时间")
    complete_at = models.DateTimeField(verbose_name="预计完成时间")
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="实际入库时间")

    class Meta:
        db_table = "guild_cavalry_trainings"
        verbose_name = "帮会铁骑训练"
        verbose_name_plural = "帮会铁骑训练"
        constraints = [
            models.UniqueConstraint(
                fields=["guild"], condition=models.Q(completed_at__isnull=True), name="guild_cavalry_active_uniq"
            ),
            models.CheckConstraint(condition=models.Q(quantity__gte=1), name="guild_cavalry_quantity_positive"),
        ]
        indexes = [models.Index(fields=["completed_at", "complete_at"], name="guild_cavalry_due_idx")]
