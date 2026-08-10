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
    cycle_id = models.CharField("培养周期 ID", max_length=64, default="", blank=True)
    round_ordinal = models.PositiveIntegerField("培养轮次", null=True, blank=True)
    action_ordinal_in_round = models.PositiveSmallIntegerField("轮内动作序号", null=True, blank=True)
    slot_attempt_ordinal = models.PositiveSmallIntegerField("槽位尝试序号", null=True, blank=True)
    shadow_cost = models.JSONField("标准化影子成本", default=dict, blank=True)
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
            models.CheckConstraint(
                condition=(
                    Q(
                        round_ordinal__isnull=True,
                        action_ordinal_in_round__isnull=True,
                        slot_attempt_ordinal__isnull=True,
                    )
                    | (
                        Q(round_ordinal__gte=1)
                        & Q(action_ordinal_in_round__gte=1)
                        & Q(action_ordinal_in_round__lte=16)
                        & Q(slot_attempt_ordinal__gte=1)
                        & Q(slot_attempt_ordinal__lte=5)
                    )
                ),
                name="bot_maint_exec_slot_fields_together",
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


class BotMaintenanceCycle(models.Model):
    """Durable fixed-budget maintenance cycle for one V2 profile."""

    class Trigger(models.TextChoices):
        SCHEDULED = "scheduled", "定时维护"
        ARENA_ACCELERATION = "arena_acceleration", "竞技场加速"

    class Status(models.TextChoices):
        OPEN = "open", "进行中"
        COMPLETED = "completed", "已完成"
        RECOVERY_REQUIRED = "recovery_required", "需要恢复"

    class ActionState(models.TextChoices):
        READY = "ready", "等待槽位"
        PLANNING = "planning", "规划中"
        SUBMITTED = "submitted", "已提交"
        NO_ACTION = "no_action", "无动作"
        COMPLETED = "completed", "已完成"
        RECOVERY = "recovery", "待恢复"

    cycle_id = models.CharField("周期 ID", max_length=64, unique=True)
    interval_seed = models.CharField("槽位间隔种子", max_length=64, default="", blank=True)
    profile = models.ForeignKey(
        "gameplay.BotProfile",
        on_delete=models.CASCADE,
        related_name="maintenance_cycles",
        verbose_name="虚拟玩家档案",
    )
    cycle_ordinal = models.PositiveIntegerField("周期序号")
    trigger = models.CharField("触发来源", max_length=32, choices=Trigger.choices)
    max_actions = models.PositiveSmallIntegerField("动作槽位上限")
    action_ordinal = models.PositiveSmallIntegerField("已分配动作槽位", default=0)
    high_cost_actions_used = models.PositiveSmallIntegerField("已提交高价动作数", default=0)
    current_action_state = models.CharField(
        "当前动作状态",
        max_length=16,
        choices=ActionState.choices,
        default=ActionState.READY,
    )
    last_action_completion_source = models.CharField("最近动作完成来源", max_length=32, default="", blank=True)
    next_slot_due_at = models.DateTimeField("下一槽位到期时间", null=True, blank=True, db_index=True)
    next_decision_at = models.DateTimeField("下一次决策时间", null=True, blank=True, db_index=True)
    status = models.CharField("周期状态", max_length=24, choices=Status.choices, default=Status.OPEN)
    covered_action_kinds = models.JSONField("已覆盖动作类别", default=list, blank=True)
    used_business_keys = models.JSONField("已使用动作 identity", default=list, blank=True)
    healing_operation_id = models.CharField("治疗前置操作 ID", max_length=64, default="", blank=True)
    salary_operation_id = models.CharField("工资批次操作 ID", max_length=64, default="", blank=True)
    started_at = models.DateTimeField("开始时间")
    completed_at = models.DateTimeField("完成时间", null=True, blank=True)
    last_reason = models.CharField("最近原因", max_length=64, default="", blank=True)
    payload = models.JSONField("周期审计载荷", default=dict, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "虚拟玩家维护周期"
        verbose_name_plural = "虚拟玩家维护周期"
        constraints = [
            models.UniqueConstraint(fields=["profile", "cycle_ordinal"], name="bot_maint_cycle_profile_ordinal"),
            models.CheckConstraint(
                condition=Q(max_actions__gte=1) & Q(max_actions__lte=16), name="bot_maint_cycle_actions_1_16"
            ),
            models.CheckConstraint(
                condition=Q(action_ordinal__lte=F("max_actions")), name="bot_maint_cycle_ordinal_lte_max"
            ),
            models.CheckConstraint(
                condition=Q(high_cost_actions_used__lte=F("max_actions")),
                name="bot_maint_cycle_high_cost_lte_max",
            ),
            models.CheckConstraint(
                condition=Q(high_cost_actions_used__lte=F("action_ordinal")),
                name="bot_maint_cycle_high_cost_lte_ordinal",
            ),
            models.CheckConstraint(
                condition=Q(cycle_id__isnull=False) & ~Q(cycle_id=""), name="bot_maint_cycle_id_nonempty"
            ),
        ]
        indexes = [
            models.Index(fields=["profile", "status", "started_at"], name="bot_maint_cycle_profile_idx"),
            models.Index(fields=["profile", "status", "next_slot_due_at"], name="bot_maint_cycle_due_idx"),
            models.Index(
                fields=["profile", "trigger", "status", "-cycle_ordinal", "-id"],
                name="bot_maint_cycle_latest_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.cycle_id}:{self.status}"


class BotMaintenanceCompletionEvent(models.Model):
    """Durable inbox for domain completions that may wake a V2 cycle."""

    class DomainKind(models.TextChoices):
        BUILDING_UPGRADE = "building_upgrade", "建筑升级"
        TECHNOLOGY_UPGRADE = "technology_upgrade", "科技升级"
        GUEST_TRAINING = "guest_training", "门客训练"
        GUEST_RECRUITMENT = "guest_recruitment", "门客招募"

    class Status(models.TextChoices):
        PENDING = "pending", "待对账"
        APPLIED = "applied", "已对账"

    profile = models.ForeignKey(
        "gameplay.BotProfile",
        on_delete=models.CASCADE,
        related_name="maintenance_completion_events",
        verbose_name="虚拟玩家档案",
    )
    domain_event_id = models.CharField("领域事件 ID", max_length=160, unique=True)
    domain_event_kind = models.CharField("领域事件类型", max_length=32, choices=DomainKind.choices)
    domain_object_id = models.PositiveBigIntegerField("领域对象 ID")
    origin_completed_at = models.DateTimeField("领域完成时间", db_index=True)
    status = models.CharField(
        "状态",
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    available_at = models.DateTimeField("下次可处理时间", db_index=True)
    attempt_count = models.PositiveSmallIntegerField("对账尝试次数", default=0)
    processed_at = models.DateTimeField("对账完成时间", null=True, blank=True)
    result_summary = models.JSONField("对账结果摘要", default=dict, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "虚拟玩家维护完成事件"
        verbose_name_plural = "虚拟玩家维护完成事件"
        constraints = [
            models.CheckConstraint(
                condition=~Q(domain_event_id=""),
                name="bot_maint_completion_event_id_nonempty",
            ),
            models.CheckConstraint(
                condition=Q(domain_object_id__gte=1),
                name="bot_maint_completion_object_id_gte_1",
            ),
            models.CheckConstraint(
                condition=Q(attempt_count__lte=32),
                name="bot_maint_completion_attempt_lte_32",
            ),
            models.CheckConstraint(
                condition=(
                    (Q(status="applied") & Q(processed_at__isnull=False))
                    | (~Q(status="applied") & Q(processed_at__isnull=True))
                ),
                name="bot_maint_completion_processed_fields",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "available_at", "id"],
                name="bot_maint_completion_due_idx",
            ),
            models.Index(
                fields=["profile", "status", "origin_completed_at"],
                name="bot_maint_completion_prof_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.domain_event_id}:{self.status}"


class BotMaintenanceAttempt(models.Model):
    """Immutable business-attempt audit, including BUSY and NO_ACTION."""

    class Outcome(models.TextChoices):
        BUSY = "busy", "忙碌"
        NO_ACTION = "no_action", "无动作"
        APPLIED = "applied", "已执行"
        COMMIT_UNCERTAIN = "commit_uncertain", "提交未知"
        PROGRAMMER_ERROR = "programmer_error", "程序错误"

    operation_id = models.CharField("操作 ID", max_length=64, unique=True)
    profile = models.ForeignKey("gameplay.BotProfile", on_delete=models.CASCADE, related_name="maintenance_attempts")
    cycle = models.ForeignKey(
        BotMaintenanceCycle,
        on_delete=models.CASCADE,
        related_name="attempts",
        null=True,
        blank=True,
    )
    trigger = models.CharField("触发来源", max_length=32)
    archetype = models.CharField("类型快照", max_length=16, default="", blank=True, db_index=True)
    action_kind = models.CharField("动作类型", max_length=32, default="", blank=True, db_index=True)
    round_ordinal = models.PositiveIntegerField("培养轮次", null=True, blank=True)
    action_ordinal_in_round = models.PositiveSmallIntegerField("轮内动作序号", null=True, blank=True)
    attempt_ordinal = models.PositiveSmallIntegerField("尝试序号")
    outcome = models.CharField("尝试结果", max_length=24, choices=Outcome.choices)
    reason = models.CharField("尝试原因", max_length=64, default="", blank=True)
    reason_category = models.CharField("原因分类", max_length=32, default="", blank=True, db_index=True)
    dispatched = models.BooleanField("已 dispatch", default=False)
    receipt_operation_id = models.CharField("回执操作 ID", max_length=64, default="", blank=True)
    shadow_cost = models.JSONField("标准化影子成本", default=dict, blank=True)
    silver_cost = models.PositiveBigIntegerField("银两影子消耗", default=0)
    grain_cost = models.PositiveBigIntegerField("粮食影子消耗", default=0)
    salary_runway_days = models.PositiveSmallIntegerField("工资 runway 天数", default=0)
    salary_runway_silver = models.PositiveBigIntegerField("工资 runway 银两", default=0)
    started_at = models.DateTimeField("开始时间")
    completed_at = models.DateTimeField("完成时间", auto_now_add=True)

    class Meta:
        verbose_name = "虚拟玩家维护尝试"
        verbose_name_plural = "虚拟玩家维护尝试"
        constraints = [
            models.CheckConstraint(
                condition=Q(attempt_ordinal__gte=1) & Q(attempt_ordinal__lte=5), name="bot_maint_attempt_1_5"
            ),
            models.CheckConstraint(condition=~Q(operation_id=""), name="bot_maint_attempt_operation_nonempty"),
        ]
        indexes = [
            models.Index(fields=["profile", "completed_at"], name="bot_maint_attempt_profile_idx"),
            models.Index(
                fields=["cycle", "round_ordinal", "action_ordinal_in_round"], name="bot_maint_attempt_cycle_idx"
            ),
            models.Index(
                fields=["archetype", "trigger", "action_kind", "outcome", "completed_at"],
                name="bot_maint_attempt_dims_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.operation_id}:{self.outcome}"
