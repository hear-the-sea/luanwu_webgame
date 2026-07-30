from __future__ import annotations

from django.db import models
from django.db.models import F, Q


class BotMaintenanceExecution(models.Model):
    """Immutable receipt committed atomically with one maintenance cycle."""

    class Trigger(models.TextChoices):
        SCHEDULED = "scheduled", "定时维护"
        ARENA_ACCELERATION = "arena_acceleration", "竞技场加速"
        ADMIN = "admin", "后台维护"

    class Outcome(models.TextChoices):
        APPLIED = "applied", "已执行"
        NO_ACTION = "no_action", "无动作"

    class ScheduleDisposition(models.TextChoices):
        ADVANCE_NORMAL_SCHEDULE = "advance_normal_schedule", "推进常规计划"
        PRESERVE_NORMAL_SCHEDULE = "preserve_normal_schedule", "保留常规计划"

    operation_id = models.CharField("操作 ID", max_length=64, unique=True)
    profile = models.ForeignKey(
        "gameplay.BotProfile",
        on_delete=models.CASCADE,
        related_name="maintenance_executions",
        verbose_name="虚拟玩家档案",
    )
    attempt_ordinal = models.PositiveIntegerField("提交尝试序号")
    trigger = models.CharField("触发来源", max_length=32, choices=Trigger.choices)
    outcome = models.CharField("执行结果", max_length=16, choices=Outcome.choices)
    schedule_disposition = models.CharField(
        "计划处理方式",
        max_length=32,
        choices=ScheduleDisposition.choices,
    )
    maintenance_sequence_before = models.PositiveIntegerField("执行前维护序号")
    maintenance_sequence_after = models.PositiveIntegerField("执行后维护序号")
    next_growth_at_before = models.DateTimeField("执行前下次成长时间")
    next_growth_at_after = models.DateTimeField("执行后下次成长时间")
    action_kind = models.CharField("动作类型", max_length=32, default="", blank=True)
    reason = models.CharField("结果原因", max_length=64, default="", blank=True)
    request_digest = models.CharField("请求摘要", max_length=64)
    requested_at = models.DateTimeField("请求时间")
    safety_started_at = models.DateTimeField(
        "安全指标开始时间",
        null=True,
        blank=True,
    )
    completed_at = models.DateTimeField("提交时间", auto_now_add=True)

    class Meta:
        verbose_name = "虚拟玩家维护执行回执"
        verbose_name_plural = "虚拟玩家维护执行回执"
        constraints = [
            models.CheckConstraint(
                condition=~Q(operation_id=""),
                name="bot_maint_exec_operation_nonempty",
            ),
            models.CheckConstraint(
                condition=Q(attempt_ordinal__gte=1),
                name="bot_maint_exec_attempt_gte_1",
            ),
            models.CheckConstraint(
                condition=Q(maintenance_sequence_after__gte=F("maintenance_sequence_before")),
                name="bot_maint_exec_sequence_nondecreasing",
            ),
            models.CheckConstraint(
                condition=~Q(request_digest=""),
                name="bot_maint_exec_digest_nonempty",
            ),
            models.CheckConstraint(
                condition=(~Q(trigger="arena_acceleration") | Q(schedule_disposition="preserve_normal_schedule")),
                name="bot_maint_exec_arena_preserves_schedule",
            ),
        ]
        indexes = [
            models.Index(
                fields=["profile", "completed_at"],
                name="bot_maint_exec_prof_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.operation_id}:{self.outcome}"
