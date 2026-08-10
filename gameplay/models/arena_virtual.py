from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

ARENA_RESERVE_MEMBER_LEASE_AGE = timedelta(hours=12)


def default_arena_reserve_member_lease_expires_at():
    return timezone.now() + ARENA_RESERVE_MEMBER_LEASE_AGE


class ArenaVirtualDemand(models.Model):
    """Persisted virtual-player reserve demand for one arena activity."""

    class Status(models.TextChoices):
        ACTIVE = "active", "协调中"
        SATISFIED = "satisfied", "已完成补位"
        BLOCKED = "blocked", "补位受阻"
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
    arena_training_policy_version = models.PositiveSmallIntegerField("竞技场培养策略版本", default=0)
    arena_training_policy_checksum = models.CharField("竞技场培养策略校验和", max_length=64, default="", blank=True)
    arena_strength_segment = models.CharField("竞技场强度段", max_length=32, default="", blank=True)
    arena_strength_envelope_digest = models.CharField("竞技场强度包络摘要", max_length=64, default="", blank=True)
    arena_supply_prestige_band = models.CharField("竞技场供给声望段", max_length=32, default="", blank=True)
    arena_supply_prestige_band_priority = models.JSONField("竞技场供给声望段优先级", default=list, blank=True)
    arena_supply_prestige = models.PositiveBigIntegerField("竞技场供给声望", default=0)
    missing_entry_count = models.PositiveSmallIntegerField("缺少席位数", default=0)
    reserve_target_count = models.PositiveIntegerField("后备目标数", default=0)
    warm_target_count = models.PositiveIntegerField("当前预热目标数", default=0)
    max_reserve_target_count = models.PositiveIntegerField("后备目标上限", default=0)
    admission_attempt_high_water = models.PositiveIntegerField("准入尝试高水位", default=0)
    admission_paused_at = models.DateTimeField("准入止损时间", null=True, blank=True)
    admission_pause_reason = models.CharField("准入止损原因", max_length=64, blank=True, default="")
    admission_probe_target_ordinal = models.PositiveIntegerField(
        "准入探测目标序号",
        null=True,
        blank=True,
    )
    next_retry_at = models.DateTimeField("下次重试时间", null=True, blank=True, db_index=True)
    last_checked_at = models.DateTimeField("最近检查时间", null=True, blank=True)
    consecutive_failure_count = models.PositiveSmallIntegerField("连续失败次数", default=0)
    last_progress_at = models.DateTimeField("最近取得进展时间", null=True, blank=True)
    last_input_change_at = models.DateTimeField("最近需求输入变化时间", null=True, blank=True)
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
            models.CheckConstraint(
                condition=models.Q(warm_target_count__lte=models.F("reserve_target_count")),
                name="arena_virtual_demand_warm_target_lte_reserve",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(admission_pause_reason="", admission_paused_at__isnull=True)
                    | (~models.Q(admission_pause_reason="") & models.Q(admission_paused_at__isnull=False))
                ),
                name="arena_vd_admission_pause_fields_together",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(admission_probe_target_ordinal__isnull=True)
                    | (
                        models.Q(admission_pause_reason="no_effective_progress")
                        & models.Q(admission_paused_at__isnull=False)
                        & models.Q(admission_probe_target_ordinal__gte=1)
                        & models.Q(admission_probe_target_ordinal__lte=models.F("max_reserve_target_count"))
                        & (
                            models.Q(admission_probe_target_ordinal=models.F("admission_attempt_high_water"))
                            | models.Q(admission_probe_target_ordinal=models.F("admission_attempt_high_water") + 1)
                        )
                    )
                ),
                name="arena_vd_admission_probe_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        arena_training_policy_version=0,
                        arena_training_policy_checksum="",
                        arena_strength_segment="",
                        arena_strength_envelope_digest="",
                        arena_supply_prestige_band="",
                        arena_supply_prestige_band_priority=[],
                        arena_supply_prestige=0,
                    )
                    | (
                        models.Q(arena_training_policy_version__gte=1)
                        & ~models.Q(arena_training_policy_checksum="")
                        & (
                            models.Q(
                                status="blocked",
                                arena_strength_segment="",
                                arena_strength_envelope_digest="",
                                arena_supply_prestige_band="",
                                arena_supply_prestige_band_priority=[],
                                arena_supply_prestige=0,
                            )
                            | (
                                ~models.Q(arena_strength_segment="")
                                & ~models.Q(arena_strength_envelope_digest="")
                                & ~models.Q(arena_supply_prestige_band="")
                                & ~models.Q(arena_supply_prestige_band_priority=[])
                            )
                        )
                    )
                ),
                name="arena_vd_training_policy_snapshot_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "next_retry_at"], name="arena_vd_status_retry_idx"),
        ]

    def clean(self) -> None:
        super().clean()
        if (self.tournament_id is None) == (self.coop_event_id is None):
            raise ValidationError("竞技场虚拟需求必须且只能关联一种活动")
        if self.arena_training_policy_version >= 1 and self.arena_strength_segment:
            priority = self.arena_supply_prestige_band_priority
            if (
                not isinstance(priority, list)
                or not priority
                or any(not isinstance(band, str) or not band for band in priority)
                or len(set(priority)) != len(priority)
                or priority[0] != self.arena_supply_prestige_band
            ):
                raise ValidationError("竞技场供给声望段优先级快照无效")

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
    roster_target_count = models.PositiveSmallIntegerField(
        "虚拟阵容目标门客数",
        null=True,
        blank=True,
    )
    growth_rounds_started = models.PositiveIntegerField("已开始培养轮次", default=0)
    growth_applied_action_count = models.PositiveIntegerField("已成功成长动作数", default=0)
    growth_action_ordinal_in_round = models.PositiveSmallIntegerField("当前轮动作序号", default=0)
    growth_slot_attempt_ordinal = models.PositiveSmallIntegerField("当前槽位尝试序号", default=0)
    growth_execution_attempt_count = models.PositiveSmallIntegerField("窗口内实际执行次数", default=0)
    growth_round_training_guest_ids = models.JSONField("当前轮已分配训练门客", default=list, blank=True)
    growth_round_id = models.CharField("当前培养轮 ID", max_length=64, default="", blank=True)
    arena_growth_budget_entries = models.JSONField("竞技场成长预算窗口", default=list, blank=True)
    growth_retry_streak = models.PositiveSmallIntegerField("成长延期连续次数", default=0)
    growth_retry_reason = models.CharField("最近延期原因", max_length=64, default="", blank=True)
    next_acceleration_at = models.DateTimeField("下次加速时间", null=True, blank=True, db_index=True)
    last_checked_at = models.DateTimeField("最近检查时间", null=True, blank=True)
    growth_operation_id = models.CharField("成长操作 ID", max_length=64, default="", blank=True)
    growth_request_digest_schema = models.PositiveSmallIntegerField(
        "成长请求摘要 schema",
        default=2,
    )
    growth_control_snapshot_digest = models.CharField(
        "成长控制快照摘要",
        max_length=64,
        default="",
        blank=True,
    )
    growth_policy_checksum = models.CharField(
        "成长策略校验和",
        max_length=64,
        default="",
        blank=True,
    )
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
    growth_eligible_guest_count_before = models.PositiveSmallIntegerField(
        "成长前可参赛门客数",
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
    growth_objective_payload = models.JSONField(
        "成长目标快照",
        default=dict,
        blank=True,
    )
    lease_expires_at = models.DateTimeField(
        "培养租期截止时间",
        default=default_arena_reserve_member_lease_expires_at,
    )
    lease_paused_at = models.DateTimeField("培养租期暂停时间", null=True, blank=True)
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
                        & models.Q(growth_eligible_guest_count_before__isnull=True)
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
                        & models.Q(growth_eligible_guest_count_before__isnull=False)
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
            models.CheckConstraint(
                condition=models.Q(growth_request_digest_schema__in=[1, 2, 3]),
                name="arena_vm_growth_digest_schema_valid",
            ),
            models.CheckConstraint(
                condition=(models.Q(growth_claim_token__isnull=False) | models.Q(growth_request_digest_schema=2)),
                name="arena_vm_unclaimed_digest_schema_current",
            ),
            models.CheckConstraint(
                condition=models.Q(lease_expires_at__gt=models.F("created_at")),
                name="arena_vm_lease_deadline_valid",
            ),
            models.CheckConstraint(
                condition=(models.Q(lease_paused_at__isnull=True) | models.Q(state="training")),
                name="arena_vm_lease_pause_training",
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


class ArenaReserveTrainingAssignment(models.Model):
    """Durable same-round training guest allocation.

    The uniqueness boundary is deliberately persisted instead of living in a
    worker-local set: retries and claim takeover may replay one operation, but
    a guest cannot be assigned to a second slot in the same round.
    """

    class Status(models.TextChoices):
        ASSIGNED = "assigned", "已分配"
        APPLIED = "applied", "已执行"
        NO_ACTION = "no_action", "无动作"
        RELEASED = "released", "已释放"

    member = models.ForeignKey(
        ArenaVirtualReserveMember,
        on_delete=models.CASCADE,
        related_name="training_assignments",
        verbose_name="后备成员",
    )
    guest = models.ForeignKey(
        "guests.Guest",
        on_delete=models.CASCADE,
        related_name="arena_reserve_training_assignments",
        verbose_name="门客",
    )
    round_ordinal = models.PositiveIntegerField("培养轮次")
    action_ordinal_in_round = models.PositiveSmallIntegerField("轮内动作序号")
    operation_id = models.CharField("动作操作 ID", max_length=64)
    status = models.CharField("分配状态", max_length=16, choices=Status.choices, default=Status.ASSIGNED)
    reason = models.CharField("结果原因", max_length=64, default="", blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "竞技场后备训练分配"
        verbose_name_plural = "竞技场后备训练分配"
        constraints = [
            models.UniqueConstraint(
                fields=["member", "round_ordinal", "guest"],
                name="arena_training_assignment_member_round_guest",
            ),
            models.UniqueConstraint(
                fields=["member", "round_ordinal", "action_ordinal_in_round"],
                name="arena_training_assignment_member_round_slot",
            ),
            models.CheckConstraint(
                condition=models.Q(round_ordinal__gte=1),
                name="arena_training_assignment_round_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(action_ordinal_in_round__gte=1) & models.Q(action_ordinal_in_round__lte=8),
                name="arena_training_assignment_slot_1_8",
            ),
            models.CheckConstraint(
                condition=~models.Q(operation_id=""),
                name="arena_training_assignment_operation_nonempty",
            ),
        ]
        indexes = [
            models.Index(fields=["member", "round_ordinal", "status"], name="arena_train_assign_st_idx"),
        ]

    def __str__(self) -> str:
        return f"member#{self.member_id}/round#{self.round_ordinal}/guest#{self.guest_id}"
