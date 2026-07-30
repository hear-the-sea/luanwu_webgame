from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models


class ArenaVirtualDemand(models.Model):
    """Persisted virtual-player reserve demand for one arena activity."""

    class Status(models.TextChoices):
        ACTIVE = "active", "协调中"
        SATISFIED = "satisfied", "已完成补位"
        CLOSED = "closed", "已关闭"

    tournament = models.OneToOneField(
        "gameplay.ArenaTournament",
        on_delete=models.CASCADE,
        related_name="virtual_demand",
        null=True,
        blank=True,
        verbose_name="普通竞技场",
    )
    coop_event = models.OneToOneField(
        "gameplay.ArenaCoopEvent",
        on_delete=models.CASCADE,
        related_name="virtual_demand",
        null=True,
        blank=True,
        verbose_name="共斗活动",
    )
    status = models.CharField("协调状态", max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    version = models.PositiveIntegerField("需求版本", default=1)
    target_guest_count = models.PositiveSmallIntegerField("目标门客数", default=0)
    target_team_power = models.PositiveBigIntegerField("目标队伍战力", default=0)
    missing_entry_count = models.PositiveSmallIntegerField("缺少席位数", default=0)
    reserve_target_count = models.PositiveIntegerField("后备目标数", default=0)
    max_reserve_target_count = models.PositiveIntegerField("后备目标上限", default=0)
    created_profile_count = models.PositiveIntegerField("已创建虚拟玩家数", default=0)
    next_retry_at = models.DateTimeField("下次重试时间", null=True, blank=True, db_index=True)
    last_checked_at = models.DateTimeField("最近检查时间", null=True, blank=True)
    last_failure_reason = models.CharField("最近失败原因", max_length=64, blank=True, default="")
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "竞技场虚拟后备需求"
        verbose_name_plural = "竞技场虚拟后备需求"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(tournament__isnull=False, coop_event__isnull=True)
                    | models.Q(tournament__isnull=True, coop_event__isnull=False)
                ),
                name="arena_virtual_demand_one_event",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "next_retry_at"], name="arena_vd_status_retry_idx"),
        ]

    def clean(self) -> None:
        super().clean()
        if (self.tournament_id is None) == (self.coop_event_id is None):
            raise ValidationError("竞技场虚拟需求必须且只能关联一种活动")

    def __str__(self) -> str:
        mode = "普通" if self.tournament_id is not None else "共斗"
        event_id = self.tournament_id or self.coop_event_id
        return f"{mode}竞技场#{event_id} 虚拟后备需求"


class ArenaVirtualReserveMember(models.Model):
    """Current exclusive lease of a virtual player to one arena demand."""

    class State(models.TextChoices):
        READY = "ready", "可补位"
        TRAINING = "training", "培养中"
        EXHAUSTED = "exhausted", "培养已达上限"

    demand = models.ForeignKey(
        ArenaVirtualDemand,
        on_delete=models.CASCADE,
        related_name="reserve_members",
        verbose_name="后备需求",
    )
    profile = models.OneToOneField(
        "gameplay.BotProfile",
        on_delete=models.CASCADE,
        related_name="arena_virtual_reserve",
        verbose_name="虚拟玩家档案",
    )
    state = models.CharField("后备状态", max_length=16, choices=State.choices, default=State.TRAINING, db_index=True)
    evaluated_version = models.PositiveIntegerField("已评估版本", default=1)
    current_lineup_power = models.PositiveBigIntegerField("当前阵容战力", default=0)
    accelerated_growth_rounds = models.PositiveSmallIntegerField("加速成长轮次", default=0)
    next_acceleration_at = models.DateTimeField("下次加速时间", null=True, blank=True, db_index=True)
    last_checked_at = models.DateTimeField("最近检查时间", null=True, blank=True)
    growth_operation_id = models.CharField("成长操作 ID", max_length=64, default="", blank=True)
    growth_attempt_ordinal = models.PositiveIntegerField("成长尝试序号", default=0)
    growth_claim_token = models.UUIDField("成长认领令牌", null=True, blank=True)
    growth_claimed_at = models.DateTimeField("成长认领时间", null=True, blank=True)
    growth_claim_expires_at = models.DateTimeField(
        "成长认领过期时间",
        null=True,
        blank=True,
    )
    growth_requested_at = models.DateTimeField("成长请求时间", null=True, blank=True)
    growth_demand_version = models.PositiveIntegerField(
        "成长需求版本",
        null=True,
        blank=True,
    )
    growth_member_version = models.PositiveIntegerField(
        "成长成员版本",
        null=True,
        blank=True,
    )
    growth_power_before = models.PositiveBigIntegerField(
        "成长前阵容战力",
        null=True,
        blank=True,
    )
    growth_minimum_guest_count = models.PositiveSmallIntegerField(
        "成长最低门客数",
        null=True,
        blank=True,
    )
    growth_minimum_guest_level = models.PositiveIntegerField(
        "成长最低门客等级",
        null=True,
        blank=True,
    )
    growth_guest_rarity_cap = models.CharField(
        "成长门客稀有度上限",
        max_length=16,
        default="",
        blank=True,
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "竞技场虚拟后备成员"
        verbose_name_plural = "竞技场虚拟后备成员"
        constraints = [
            models.UniqueConstraint(
                fields=["demand", "profile"],
                name="arena_virtual_member_demand_profile",
            ),
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(growth_claim_token__isnull=True)
                        & models.Q(growth_claimed_at__isnull=True)
                        & models.Q(growth_claim_expires_at__isnull=True)
                        & models.Q(growth_requested_at__isnull=True)
                        & models.Q(growth_operation_id="")
                        & models.Q(growth_attempt_ordinal=0)
                        & models.Q(growth_demand_version__isnull=True)
                        & models.Q(growth_member_version__isnull=True)
                        & models.Q(growth_power_before__isnull=True)
                        & models.Q(growth_minimum_guest_count__isnull=True)
                        & models.Q(growth_minimum_guest_level__isnull=True)
                        & models.Q(growth_guest_rarity_cap="")
                    )
                    | (
                        models.Q(growth_claim_token__isnull=False)
                        & models.Q(growth_claimed_at__isnull=False)
                        & models.Q(growth_claim_expires_at__isnull=False)
                        & models.Q(growth_requested_at__isnull=False)
                        & ~models.Q(growth_operation_id="")
                        & models.Q(growth_attempt_ordinal__gte=1)
                        & models.Q(growth_demand_version__isnull=False)
                        & models.Q(growth_member_version__isnull=False)
                        & models.Q(growth_power_before__isnull=False)
                        & models.Q(growth_minimum_guest_count__isnull=False)
                        & models.Q(growth_minimum_guest_level__isnull=False)
                    )
                ),
                name="arena_vm_growth_claim_fields_together",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(growth_claim_expires_at__isnull=True)
                    | models.Q(growth_claim_expires_at__gt=models.F("growth_claimed_at"))
                ),
                name="arena_vm_growth_claim_expiry_gt_claim",
            ),
        ]
        indexes = [
            models.Index(fields=["demand", "state"], name="arena_vm_demand_state_idx"),
            models.Index(fields=["state", "next_acceleration_at"], name="arena_vm_state_accel_idx"),
            models.Index(
                fields=["growth_claim_expires_at"],
                name="arena_vm_growth_claim_exp_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"需求#{self.demand_id} / 档案#{self.profile_id} ({self.state})"
