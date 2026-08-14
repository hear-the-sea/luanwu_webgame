from __future__ import annotations

from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class BotProfile(models.Model):
    """System-controlled virtual player profile attached to a real Manor row."""

    class Archetype(models.TextChoices):
        BALANCED = "balanced", "均衡型"
        RICH = "rich", "肥羊型"
        DOJO = "dojo", "武馆型"
        GUARD = "guard", "护院型"
        ABANDONED = "abandoned", "弃坑型"

    class State(models.TextChoices):
        ACTIVE = "active", "正常成长"
        SLOWING = "slowing", "成长放缓"
        ABANDONED = "abandoned", "弃坑"
        STALE = "stale", "停滞"
        RETIRED = "retired", "休眠"

    manor = models.OneToOneField(
        "gameplay.Manor",
        on_delete=models.CASCADE,
        related_name="bot_profile",
        verbose_name="庄园",
    )
    archetype = models.CharField("类型", max_length=16, choices=Archetype.choices, default=Archetype.BALANCED)
    state = models.CharField("状态", max_length=16, choices=State.choices, default=State.ACTIVE)
    prestige_band = models.CharField("声望段", max_length=32, db_index=True)
    target_prestige_band = models.CharField("目标声望段", max_length=32, db_index=True, default="")
    current_prestige_band = models.CharField("当前声望段", max_length=32, db_index=True, default="")
    growth_seed = models.PositiveIntegerField("成长种子")
    growth_stage = models.PositiveSmallIntegerField("成长阶段", default=1)
    guest_count_target = models.PositiveSmallIntegerField("普通培养门客目标数", default=0)
    next_growth_at = models.DateTimeField("下次成长时间", db_index=True)
    # The recruitment scan always filters by the V2 profile dimensions first;
    # keep one composite due index instead of adding a redundant single-column
    # index that would increase write and migration cost.
    next_recruitment_at = models.DateTimeField("下次招募到期时间", null=True, blank=True)
    recruitment_schedule_snapshot = models.JSONField("每日招募配额快照", default=dict, blank=True)
    abandon_at = models.DateTimeField("弃坑时间", db_index=True)
    retire_at = models.DateTimeField("退场时间", db_index=True)
    loot_budget_daily = models.PositiveIntegerField("每日资源预算", default=0)
    inventory_template_keys = models.JSONField("库存模板池", default=list, blank=True)
    engine_version = models.PositiveSmallIntegerField("执行器版本", default=1, db_index=True)
    rng_version = models.PositiveSmallIntegerField("随机算法版本", default=0)
    plan_schema_version = models.PositiveSmallIntegerField("发展画像版本", default=0)
    policy_version = models.PositiveSmallIntegerField("策略版本", default=0)
    policy_checksum = models.CharField("策略校验和", max_length=64, default="", blank=True)
    development_profile = models.JSONField("发展画像", default=dict, blank=True)
    maintenance_sequence = models.PositiveIntegerField("维护序号", default=0)
    strength_budget_entries = models.JSONField("强度预算窗口", default=list, blank=True)
    last_strength_increase_at = models.DateTimeField("最近强度提升时间", null=True, blank=True)
    forced_settlement_daily_budget = models.JSONField("强制结算日预算", default=dict, blank=True)
    v2_enrolled_at = models.DateTimeField("V2 入组时间", null=True, blank=True)
    maintenance_started_at = models.DateTimeField("维护开始时间", null=True, blank=True)
    maintenance_stopped_at = models.DateTimeField("维护停止时间", null=True, blank=True)
    last_planned_at = models.DateTimeField("最近规划时间", null=True, blank=True)
    last_arena_participated_at = models.DateTimeField("最近竞技场参赛时间", null=True, blank=True, db_index=True)
    arena_participation_count = models.PositiveIntegerField("竞技场累计参赛次数", default=0)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "虚拟玩家档案"
        verbose_name_plural = "虚拟玩家档案"
        indexes = [
            models.Index(fields=["state", "next_growth_at"], name="bot_state_next_growth_idx"),
            models.Index(
                fields=["engine_version", "policy_version", "state", "next_growth_at", "id"],
                name="bot_due_identity_idx",
            ),
            models.Index(
                fields=["engine_version", "policy_version", "state", "next_recruitment_at", "id"],
                name="bot_recruit_due_idx",
            ),
            models.Index(fields=["prestige_band", "state"], name="bot_band_state_idx"),
            models.Index(
                fields=["target_prestige_band", "state"],
                name="bot_target_band_state_idx",
            ),
            models.Index(
                fields=["current_prestige_band", "state"],
                name="bot_current_band_state_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(engine_version__gte=1),
                name="bot_profile_engine_version_gte_1",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(engine_version=2)
                    | (
                        Q(rng_version__gte=1)
                        & Q(plan_schema_version__gte=1)
                        & Q(policy_version__gte=1)
                        & ~Q(policy_checksum="")
                        & Q(last_strength_increase_at__isnull=False)
                        & Q(v2_enrolled_at__isnull=False)
                    )
                ),
                name="bot_profile_v2_required_fields",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.manor.display_name} ({self.archetype}/{self.state})"


class BotPolicyRelease(models.Model):
    """Immutable, versioned virtual-player development policy payload."""

    version = models.PositiveSmallIntegerField("策略版本", primary_key=True)
    checksum = models.CharField("策略校验和", max_length=64, unique=True)
    payload = models.JSONField("策略快照")
    released_at = models.DateTimeField("发布时间")
    retire_not_before = models.DateTimeField("最早退役时间")
    retired_at = models.DateTimeField("退役时间", null=True, blank=True)

    class Meta:
        verbose_name = "虚拟玩家策略发布"
        verbose_name_plural = "虚拟玩家策略发布"
        constraints = [
            models.CheckConstraint(
                condition=~Q(checksum=""),
                name="bot_policy_checksum_nonempty",
            ),
            models.CheckConstraint(
                condition=Q(retire_not_before__gte=F("released_at")),
                name="bot_policy_retire_deadline_gte_release",
            ),
            models.CheckConstraint(
                condition=Q(retired_at__isnull=True) | Q(retired_at__gte=F("released_at")),
                name="bot_policy_retired_at_gte_release",
            ),
        ]

    def __str__(self) -> str:
        return f"policy-v{self.version}:{self.checksum[:12]}"


class BotExternalStrengthReconciliation(models.Model):
    """Durable two-phase reconciliation intent for externally committed Bot changes."""

    class Status(models.TextChoices):
        PENDING_PROFILE = "pending_profile", "待档案对账"
        CLAIMED_PROFILE = "claimed_profile", "档案对账中"
        PENDING_POPULATION = "pending_population", "待人口交接"
        CLAIMED_POPULATION = "claimed_population", "人口交接中"
        APPLIED = "applied", "已完成"
        QUARANTINED = "quarantined", "已隔离"

    class Phase(models.TextChoices):
        PROFILE = "profile", "档案"
        POPULATION = "population", "人口"

    profile_id = models.PositiveBigIntegerField("虚拟玩家档案 ID", db_index=True)
    domain_event_kind = models.CharField("领域事件类型", max_length=64)
    domain_event_id = models.CharField("领域事件 ID", max_length=128)
    origin_committed_at = models.DateTimeField("原事件提交时间", db_index=True)
    pre_strength_summary = models.JSONField("提交前强度摘要", default=dict)
    pre_prestige_band = models.CharField("提交前声望段", max_length=32)
    status = models.CharField(
        "状态",
        max_length=24,
        choices=Status.choices,
        default=Status.PENDING_PROFILE,
    )
    profile_attempt_count = models.PositiveSmallIntegerField("档案阶段尝试次数", default=0)
    population_attempt_count = models.PositiveSmallIntegerField("人口阶段尝试次数", default=0)
    available_at = models.DateTimeField("下次可处理时间", db_index=True)
    claim_token = models.UUIDField("认领令牌", null=True, blank=True)
    claimed_at = models.DateTimeField("认领时间", null=True, blank=True)
    claim_expires_at = models.DateTimeField("认领过期时间", null=True, blank=True)
    profile_completed_at = models.DateTimeField("档案阶段完成时间", null=True, blank=True)
    population_handoff_completed_at = models.DateTimeField("人口交接完成时间", null=True, blank=True)
    applied_at = models.DateTimeField("最终完成时间", null=True, blank=True)
    result_summary = models.JSONField("结果摘要", default=dict, blank=True)
    quarantined_at = models.DateTimeField("隔离时间", null=True, blank=True)
    quarantined_phase = models.CharField("隔离阶段", max_length=16, choices=Phase.choices, default="", blank=True)
    failure_code = models.CharField("失败代码", max_length=64, default="", blank=True)
    last_error_digest = models.CharField("最近错误摘要", max_length=64, default="", blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "虚拟玩家外部强度对账"
        verbose_name_plural = "虚拟玩家外部强度对账"
        constraints = [
            models.UniqueConstraint(
                fields=["profile_id", "domain_event_kind", "domain_event_id"],
                name="bot_ext_reconcile_domain_event_uniq",
            ),
            models.CheckConstraint(
                condition=(
                    (Q(claim_token__isnull=True) & Q(claimed_at__isnull=True) & Q(claim_expires_at__isnull=True))
                    | (Q(claim_token__isnull=False) & Q(claimed_at__isnull=False) & Q(claim_expires_at__isnull=False))
                ),
                name="bot_ext_reconcile_claim_fields_together",
            ),
            models.CheckConstraint(
                condition=Q(profile_attempt_count__lte=12),
                name="bot_ext_reconcile_profile_attempt_lte_12",
            ),
            models.CheckConstraint(
                condition=Q(population_attempt_count__lte=12),
                name="bot_ext_reconcile_pop_attempt_lte_12",
            ),
            models.CheckConstraint(
                condition=(
                    (Q(status="applied") & Q(applied_at__isnull=False))
                    | (~Q(status="applied") & Q(applied_at__isnull=True))
                ),
                name="bot_ext_reconcile_applied_timestamp",
            ),
            models.CheckConstraint(
                condition=(
                    Q(population_handoff_completed_at__isnull=True)
                    | (
                        Q(profile_completed_at__isnull=False)
                        & Q(applied_at__isnull=False)
                        & Q(population_handoff_completed_at__gte=F("profile_completed_at"))
                        & Q(applied_at__gte=F("population_handoff_completed_at"))
                    )
                ),
                name="bot_ext_reconcile_handoff_timestamps",
            ),
            models.CheckConstraint(
                condition=(
                    (
                        Q(status="quarantined")
                        & Q(quarantined_at__isnull=False)
                        & ~Q(quarantined_phase="")
                        & ~Q(failure_code="")
                    )
                    | (~Q(status="quarantined") & Q(quarantined_at__isnull=True))
                ),
                name="bot_ext_reconcile_quarantine_fields",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "available_at"], name="bot_ext_status_avail_idx"),
            models.Index(fields=["profile_id", "status", "id"], name="bot_ext_profile_status_idx"),
            models.Index(
                fields=["profile_id", "origin_committed_at", "id"],
                name="bot_ext_profile_order_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.profile_id}:{self.domain_event_kind}:{self.domain_event_id} ({self.status})"


class BotVirtualPlayerHealth(models.Model):
    """Self-healing circuit state for transient virtual-player dependencies."""

    GLOBAL_KEY = "virtual_players"

    class Status(models.TextChoices):
        HEALTHY = "healthy", "健康"
        DEGRADED = "degraded", "降级"
        RECOVERING = "recovering", "恢复中"

    key = models.CharField(max_length=32, primary_key=True, default=GLOBAL_KEY, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.HEALTHY)
    retryable_failure_streak = models.PositiveSmallIntegerField(default=0)
    clean_success_streak = models.PositiveSmallIntegerField(default=0)
    next_probe_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_failure_code = models.CharField(max_length=64, default="", blank=True)
    last_error_digest = models.CharField(max_length=64, default="", blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)
    last_recovered_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "虚拟玩家健康状态"
        verbose_name_plural = "虚拟玩家健康状态"
        constraints = [
            models.CheckConstraint(
                condition=Q(key="virtual_players"),
                name="bot_vp_health_singleton_key",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["healthy", "degraded", "recovering"]),
                name="bot_vp_health_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(retryable_failure_streak__lte=255) & Q(clean_success_streak__lte=255),
                name="bot_vp_health_streaks_lte_255",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.key}:{self.status}@{self.revision}"


class BotRuntimeRoutingState(models.Model):
    """Database-owned singleton for current virtual-player runtime routing."""

    GLOBAL_KEY = "virtual_players"

    class BootstrapMode(models.TextChoices):
        LEGACY_BEFORE_GATE = "legacy_before_gate", "Gate 前 V1"
        V2_ACTIVE = "v2_active", "V2 已启用"
        V2_PAUSED = "v2_paused", "V2 已暂停"

    class MaintenanceMode(models.TextChoices):
        LEGACY_BEFORE_GATE = "legacy_before_gate", "Gate 前 V1"
        V2_CUTOVER = "v2_cutover", "V2 切换中"
        V2_ACTIVE = "v2_active", "V2 已启用"
        V2_PAUSED = "v2_paused", "V2 已暂停"

    key = models.CharField(max_length=32, primary_key=True, default=GLOBAL_KEY, editable=False)
    bootstrap_mode = models.CharField(
        "Bootstrap 模式",
        max_length=24,
        choices=BootstrapMode.choices,
        default=BootstrapMode.LEGACY_BEFORE_GATE,
    )
    maintenance_mode = models.CharField(
        "Maintenance 模式",
        max_length=24,
        choices=MaintenanceMode.choices,
        default=MaintenanceMode.LEGACY_BEFORE_GATE,
    )
    calibration_routes = models.JSONField("参考校准路由", default=list, blank=True)
    policy_rollout_target_version = models.PositiveSmallIntegerField(
        "策略 rollout 目标版本",
        default=1,
    )
    policy_rollout_enabled = models.BooleanField("策略 rollout 已启用", default=False)
    policy_rollout_percent = models.PositiveSmallIntegerField(
        "策略 rollout 百分比",
        default=0,
    )
    revision = models.PositiveBigIntegerField("修订号", default=0)
    last_hourly_safety_window_end_at = models.DateTimeField("最近小时安全窗口", null=True, blank=True)
    last_daily_safety_window_end_at = models.DateTimeField("最近日安全窗口", null=True, blank=True)
    last_pause_window_id = models.CharField("最近暂停窗口 ID", max_length=128, default="", blank=True)
    pause_reason = models.TextField("暂停原因", default="", blank=True)
    paused_from_maintenance_mode = models.CharField(
        "暂停前运行模式",
        max_length=24,
        default="",
        blank=True,
    )
    safety_clean_window_streak = models.PositiveSmallIntegerField("连续安全窗口数", default=0)
    safety_clean_window_kind = models.CharField("连续安全窗口类型", max_length=16, default="", blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "虚拟玩家运行路由"
        verbose_name_plural = "虚拟玩家运行路由"
        constraints = [
            models.CheckConstraint(
                condition=Q(key="virtual_players"),
                name="bot_runtime_routing_singleton_key",
            ),
            models.CheckConstraint(
                condition=Q(bootstrap_mode__in=["legacy_before_gate", "v2_active", "v2_paused"]),
                name="bot_runtime_bootstrap_mode_valid",
            ),
            models.CheckConstraint(
                condition=Q(
                    maintenance_mode__in=[
                        "legacy_before_gate",
                        "v2_cutover",
                        "v2_active",
                        "v2_paused",
                    ]
                ),
                name="bot_runtime_maintenance_mode_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(paused_from_maintenance_mode="")
                    | Q(
                        paused_from_maintenance_mode__in=[
                            "v2_cutover",
                            "v2_active",
                        ]
                    )
                ),
                name="bot_runtime_paused_from_maintenance_mode_valid",
            ),
            models.CheckConstraint(
                condition=Q(policy_rollout_target_version__gte=1),
                name="bot_runtime_policy_target_gte_1",
            ),
            models.CheckConstraint(
                condition=Q(policy_rollout_percent__lte=100),
                name="bot_runtime_policy_percent_lte_100",
            ),
            models.CheckConstraint(
                condition=(
                    Q(policy_rollout_enabled=True, policy_rollout_percent__gte=1)
                    | Q(policy_rollout_enabled=False, policy_rollout_percent=0)
                ),
                name="bot_runtime_policy_rollout_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.key}@{self.revision}"


class BotPopulationRecomputeDemand(models.Model):
    """Durable, coalesced request to recompute one virtual-player population cell."""

    region = models.CharField("地区", max_length=32)
    prestige_band = models.CharField("声望段", max_length=32)
    requested_revision = models.PositiveBigIntegerField("请求修订号", default=0)
    completed_revision = models.PositiveBigIntegerField("完成修订号", default=0)
    claimed_revision = models.PositiveBigIntegerField("认领修订号", null=True, blank=True)
    claim_token = models.UUIDField("认领令牌", null=True, blank=True)
    claimed_at = models.DateTimeField("认领时间", null=True, blank=True)
    claim_expires_at = models.DateTimeField("认领过期时间", null=True, blank=True)
    available_at = models.DateTimeField("下次可处理时间", default=timezone.now, db_index=True)
    consecutive_failure_count = models.PositiveIntegerField("连续失败次数", default=0)
    last_error_digest = models.CharField("最近错误摘要", max_length=64, default="", blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "虚拟玩家人口重算需求"
        verbose_name_plural = "虚拟玩家人口重算需求"
        constraints = [
            models.UniqueConstraint(
                fields=["region", "prestige_band"],
                name="bot_pop_recompute_cell_uniq",
            ),
            models.CheckConstraint(
                condition=Q(completed_revision__lte=F("requested_revision")),
                name="bot_pop_req_completed_lte",
            ),
            models.CheckConstraint(
                condition=(
                    (
                        Q(claimed_revision__isnull=True)
                        & Q(claim_token__isnull=True)
                        & Q(claimed_at__isnull=True)
                        & Q(claim_expires_at__isnull=True)
                    )
                    | (
                        Q(claimed_revision__isnull=False)
                        & Q(claim_token__isnull=False)
                        & Q(claimed_at__isnull=False)
                        & Q(claim_expires_at__isnull=False)
                    )
                ),
                name="bot_pop_claim_fields_together",
            ),
            models.CheckConstraint(
                condition=(
                    Q(claimed_revision__isnull=True)
                    | (
                        Q(claimed_revision__gt=F("completed_revision"))
                        & Q(claimed_revision__lte=F("requested_revision"))
                    )
                ),
                name="bot_pop_claim_revision_range",
            ),
        ]
        indexes = [
            models.Index(
                fields=["available_at", "claim_expires_at"],
                name="bot_pop_recompute_avail_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.region}:{self.prestige_band} " f"({self.completed_revision}/{self.requested_revision})"


class BotArenaShortageBaseline(models.Model):
    """Immutable Arena shortage ratio baseline for one monitored scope."""

    class Mode(models.TextChoices):
        TOURNAMENT = "tournament", "普通竞技场"
        COOP = "coop", "共斗竞技场"

    class Source(models.TextChoices):
        PRE_ACTIVATION = "pre_activation", "上线前冻结"
        RUNTIME_BOOTSTRAP = "runtime_bootstrap", "运行时自动基线"

    mode = models.CharField("竞技场模式", max_length=16, choices=Mode.choices)
    prestige_band = models.CharField("声望段", max_length=32)
    baseline_ratio = models.DecimalField(
        "短缺基线比例",
        max_digits=13,
        decimal_places=12,
    )
    source = models.CharField(
        "来源",
        max_length=24,
        choices=Source.choices,
        default=Source.PRE_ACTIVATION,
    )
    expires_at = models.DateTimeField("过期时间", null=True, blank=True, db_index=True)
    max_real_entry_count = models.PositiveIntegerField("最大真实参赛人数", null=True, blank=True)
    frozen_at = models.DateTimeField("冻结时间")
    evidence_id = models.CharField("证据 ID", max_length=128)
    evidence_checksum = models.CharField("证据校验和", max_length=64)
    payload_digest = models.CharField("载荷摘要", max_length=64)
    created_at = models.DateTimeField("写入时间", auto_now_add=True)

    class Meta:
        verbose_name = "竞技场短缺基线"
        verbose_name_plural = "竞技场短缺基线"
        constraints = [
            models.UniqueConstraint(
                fields=["mode", "prestige_band"],
                name="bot_arena_shortage_scope_uniq",
            ),
            models.CheckConstraint(
                condition=Q(mode__in=["tournament", "coop"]),
                name="bot_arena_shortage_mode_valid",
            ),
            models.CheckConstraint(
                condition=Q(source__in=["pre_activation", "runtime_bootstrap"]),
                name="bot_arena_shortage_source_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        source="pre_activation",
                        expires_at__isnull=True,
                        max_real_entry_count__isnull=True,
                    )
                    | Q(
                        source="runtime_bootstrap",
                        expires_at__isnull=False,
                        expires_at__gt=F("frozen_at"),
                        max_real_entry_count__isnull=False,
                        max_real_entry_count__gte=1,
                    )
                ),
                name="bot_arena_shortage_source_meta_shape",
            ),
            models.CheckConstraint(
                condition=(Q(baseline_ratio__gte=0) & Q(baseline_ratio__lte=1)),
                name="bot_arena_shortage_ratio_range",
            ),
            models.CheckConstraint(
                condition=~Q(prestige_band=""),
                name="bot_arena_shortage_band_nonempty",
            ),
            models.CheckConstraint(
                condition=~Q(evidence_id=""),
                name="bot_arena_shortage_evid_nonempty",
            ),
            models.CheckConstraint(
                condition=~Q(evidence_checksum=""),
                name="bot_arena_shortage_cksum_nonempty",
            ),
            models.CheckConstraint(
                condition=~Q(payload_digest=""),
                name="bot_arena_shortage_digest_nonempty",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.mode}:{self.prestige_band}={self.baseline_ratio}"


class BotSafetyMetricEvent(models.Model):
    """Append-only safety metric event with a canonical immutable payload."""

    event_id = models.CharField("事件 ID", max_length=128, unique=True)
    metric_name = models.CharField("指标名", max_length=128)
    occurred_at = models.DateTimeField("发生时间")
    dimensions = models.JSONField("规范维度", default=dict)
    value = models.DecimalField(
        "指标值",
        max_digits=32,
        decimal_places=12,
    )
    payload_digest = models.CharField("载荷摘要", max_length=64)
    created_at = models.DateTimeField("写入时间", auto_now_add=True)

    class Meta:
        verbose_name = "虚拟玩家安全指标事件"
        verbose_name_plural = "虚拟玩家安全指标事件"
        constraints = [
            models.CheckConstraint(
                condition=~Q(event_id=""),
                name="bot_safety_event_id_nonempty",
            ),
            models.CheckConstraint(
                condition=~Q(metric_name=""),
                name="bot_safety_metric_nonempty",
            ),
            models.CheckConstraint(
                condition=~Q(payload_digest=""),
                name="bot_safety_event_digest_nonempty",
            ),
        ]
        indexes = [
            models.Index(fields=["occurred_at"], name="bot_safe_evt_occ_idx"),
            models.Index(
                fields=["metric_name", "occurred_at"],
                name="bot_safe_evt_metric_occ_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.metric_name}:{self.event_id}"


class BotSafetyMetricWindow(models.Model):
    """Open lock row or immutable finalized safety metric window snapshot."""

    class Kind(models.TextChoices):
        HOURLY = "hourly", "小时"
        DAILY = "daily", "日"

    window_id = models.CharField("窗口 ID", max_length=128, unique=True)
    kind = models.CharField("窗口类型", max_length=8, choices=Kind.choices)
    window_start_at = models.DateTimeField("窗口开始时间")
    window_end_at = models.DateTimeField("窗口结束时间", db_index=True)
    snapshot = models.JSONField("最终快照", default=dict, blank=True)
    snapshot_digest = models.CharField("快照摘要", max_length=64, default="", blank=True)
    finalized_at = models.DateTimeField("冻结时间", null=True, blank=True)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "虚拟玩家安全指标窗口"
        verbose_name_plural = "虚拟玩家安全指标窗口"
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "window_start_at"],
                name="bot_safety_window_kind_start_uniq",
            ),
            models.CheckConstraint(
                condition=Q(window_end_at__gt=F("window_start_at")),
                name="bot_safety_window_end_gt_start",
            ),
            models.CheckConstraint(
                condition=(
                    Q(finalized_at__isnull=True, snapshot_digest="")
                    | (Q(finalized_at__isnull=False) & ~Q(snapshot_digest=""))
                ),
                name="bot_safety_window_finalize_fields",
            ),
            models.CheckConstraint(
                condition=(Q(finalized_at__isnull=True) | Q(finalized_at__gte=F("window_end_at"))),
                name="bot_safety_window_final_gte_end",
            ),
        ]
        indexes = [
            models.Index(
                fields=["window_end_at", "kind"],
                name="bot_safe_win_end_kind_idx",
            ),
            models.Index(
                fields=["finalized_at", "window_end_at"],
                name="bot_safe_win_final_end_idx",
            ),
        ]

    def __str__(self) -> str:
        state = "finalized" if self.finalized_at is not None else "open"
        return f"{self.window_id} ({state})"


class BotPopulationControl(models.Model):
    """Singleton row used to serialize automatic virtual-player expansion."""

    GLOBAL_KEY = "global"

    key = models.CharField(max_length=16, primary_key=True, default=GLOBAL_KEY, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "虚拟玩家人口协调"
        verbose_name_plural = "虚拟玩家人口协调"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(key="global"),
                name="bot_population_control_global_only",
            ),
        ]

    def __str__(self) -> str:
        return self.key


class VirtualPlayerGrowthControlRun(models.Model):
    """One all-cells transaction of the daily aggregate growth-control scan."""

    class Status(models.TextChoices):
        RUNNING = "running", "执行中"
        COMPLETE = "complete", "已完成"
        FAILED = "failed", "已失败"

    run_key = models.CharField("运行 key", max_length=64, unique=True)
    control_date = models.DateField("控制日期")
    policy_version = models.PositiveSmallIntegerField("Bot 策略版本", default=2)
    policy_checksum = models.CharField("Bot 策略校验和", max_length=64)
    source_sample_count = models.PositiveIntegerField("源样本数", default=0)
    cell_count = models.PositiveIntegerField("单元格数", default=0)
    fallback_count = models.PositiveIntegerField("fallback 单元格数", default=0)
    run_digest = models.CharField("运行摘要", max_length=64, unique=True)
    status = models.CharField("运行状态", max_length=16, choices=Status.choices, default=Status.RUNNING)
    failure_digest = models.CharField("失败摘要", max_length=64, default="", blank=True)
    failure_reason = models.CharField("失败原因", max_length=512, default="", blank=True)
    started_at = models.DateTimeField("开始时间")
    completed_at = models.DateTimeField("完成时间", null=True, blank=True)
    failed_at = models.DateTimeField("失败时间", null=True, blank=True)
    created_at = models.DateTimeField("写入时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "虚拟玩家成长控制运行"
        verbose_name_plural = "虚拟玩家成长控制运行"
        constraints = [
            models.CheckConstraint(condition=Q(policy_version=2), name="bot_growth_control_run_policy_v2"),
            models.CheckConstraint(condition=~Q(policy_checksum=""), name="bot_growth_control_run_checksum"),
            models.CheckConstraint(condition=~Q(run_digest=""), name="bot_growth_control_run_digest"),
            models.CheckConstraint(
                condition=(Q(status="complete", completed_at__isnull=False) | ~Q(status="complete")),
                name="bot_growth_control_run_complete_marker",
            ),
            models.CheckConstraint(
                condition=(Q(status="failed", failed_at__isnull=False) | ~Q(status="failed")),
                name="bot_growth_control_run_failed_marker",
            ),
        ]
        indexes = [
            models.Index(fields=["control_date", "status"], name="bot_gctrl_run_date_idx"),
            models.Index(fields=["status", "updated_at"], name="bot_gctrl_run_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.control_date}:{self.run_digest[:12]}:{self.status}"


class VirtualPlayerGrowthControlPointer(models.Model):
    """Singleton pointer to the last complete growth-control run."""

    GLOBAL_KEY = "global"

    key = models.CharField(max_length=16, primary_key=True, default=GLOBAL_KEY, editable=False)
    current_run = models.ForeignKey(
        VirtualPlayerGrowthControlRun,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="current_pointers",
        verbose_name="当前完整运行",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "虚拟玩家成长控制当前指针"
        verbose_name_plural = "虚拟玩家成长控制当前指针"
        constraints = [
            models.CheckConstraint(condition=Q(key="global"), name="bot_growth_control_pointer_global_only"),
        ]

    def __str__(self) -> str:
        return self.key


class VirtualPlayerGrowthControlSnapshot(models.Model):
    """Daily, aggregate-only real-player growth control input."""

    control_date = models.DateField("控制日期")
    run = models.ForeignKey(
        VirtualPlayerGrowthControlRun,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="snapshots",
        verbose_name="完整运行",
    )
    region = models.CharField("地区", max_length=32)
    prestige_band = models.CharField("声望段", max_length=32)
    policy_version = models.PositiveSmallIntegerField("Bot 策略版本", default=2)
    policy_checksum = models.CharField("Bot 策略校验和", max_length=64)
    sample_count = models.PositiveIntegerField("样本数", default=0)
    strength_p50 = models.PositiveBigIntegerField("综合实力 P50", default=0)
    strength_p75 = models.PositiveBigIntegerField("综合实力 P75", default=0)
    growth_24h_bps = models.IntegerField("24 小时成长基点")
    growth_7d_bps = models.IntegerField("7 日成长基点")
    component_statistics = models.JSONField("组件聚合统计", default=dict, blank=True)
    effective_until = models.DateTimeField("有效截止时间")
    is_fallback = models.BooleanField("是否 fallback", default=False)
    snapshot_digest = models.CharField("快照摘要", max_length=64)
    created_at = models.DateTimeField("写入时间", auto_now_add=True)

    class Meta:
        verbose_name = "虚拟玩家成长控制快照"
        verbose_name_plural = "虚拟玩家成长控制快照"
        constraints = [
            models.UniqueConstraint(
                fields=["control_date", "region", "prestige_band", "snapshot_digest"],
                name="bot_growth_control_snapshot_uniq",
            ),
            models.CheckConstraint(condition=Q(policy_version=2), name="bot_growth_control_policy_v2"),
            models.CheckConstraint(condition=~Q(policy_checksum=""), name="bot_growth_control_policy_checksum"),
            models.CheckConstraint(condition=~Q(snapshot_digest=""), name="bot_growth_control_digest_nonempty"),
            models.CheckConstraint(condition=Q(sample_count__gte=0), name="bot_growth_control_sample_nonnegative"),
        ]
        indexes = [
            models.Index(fields=["region", "prestige_band", "effective_until"], name="bot_growth_control_lookup_idx"),
            models.Index(
                fields=["run", "region", "prestige_band", "effective_until"],
                name="bot_growth_control_run_idx",
            ),
            models.Index(fields=["control_date"], name="bot_growth_control_date_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.control_date}:{self.region}:{self.prestige_band}"


class BotMaintenanceRecovery(models.Model):
    """Durable per-entity recovery/quarantine state."""

    class Scope(models.TextChoices):
        ARENA_MEMBER = "arena_member", "竞技场成员"
        ARENA_DEMAND = "arena_demand", "竞技场需求"
        PROFILE = "profile", "虚拟玩家档案"
        POPULATION_CELL = "population_cell", "人口单元格"
        GUEST = "guest", "门客"

    class Status(models.TextChoices):
        RETRY = "retry", "等待重试"
        QUARANTINED = "quarantined", "已隔离"
        REQUEUED = "requeued", "已重新排队"

    scope = models.CharField("恢复范围", max_length=24, choices=Scope.choices)
    entity_key = models.CharField("实体 key", max_length=128)
    status = models.CharField("状态", max_length=16, choices=Status.choices, default=Status.RETRY)
    failure_code = models.CharField("失败代码", max_length=64)
    failure_digest = models.CharField("失败摘要", max_length=64)
    failure_streak = models.PositiveSmallIntegerField("连续失败次数", default=1)
    first_failed_at = models.DateTimeField("首次失败时间")
    last_failed_at = models.DateTimeField("最近失败时间")
    next_retry_at = models.DateTimeField("下次重试时间", null=True, blank=True, db_index=True)
    quarantined_at = models.DateTimeField("隔离时间", null=True, blank=True)
    requeued_at = models.DateTimeField("重新排队时间", null=True, blank=True)
    last_success_at = models.DateTimeField("最近成功时间", null=True, blank=True)
    last_operation_id = models.CharField("最近操作 ID", max_length=64, default="", blank=True)
    payload = models.JSONField("恢复载荷", default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "虚拟玩家维护恢复状态"
        verbose_name_plural = "虚拟玩家维护恢复状态"
        constraints = [
            models.UniqueConstraint(fields=["scope", "entity_key"], name="bot_maint_recovery_scope_entity"),
            models.CheckConstraint(condition=~Q(entity_key=""), name="bot_maint_recovery_entity_nonempty"),
            models.CheckConstraint(condition=~Q(failure_digest=""), name="bot_maint_recovery_digest_nonempty"),
            models.CheckConstraint(
                condition=(Q(status="quarantined", quarantined_at__isnull=False) | ~Q(status="quarantined")),
                name="bot_maint_recovery_quarantine_marker",
            ),
        ]
        indexes = [
            models.Index(fields=["scope", "status", "next_retry_at"], name="bot_maint_recovery_due_idx"),
            models.Index(
                fields=["scope", "entity_key", "status", "next_retry_at"],
                name="bot_maint_recovery_entity_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.scope}:{self.entity_key}:{self.status}"


class BotInventoryDailyCounter(models.Model):
    """Daily global inventory budget consumed by virtual player projections."""

    category = models.CharField("类别", max_length=32)
    counter_date = models.DateField("计数日期")
    quantity = models.PositiveIntegerField("数量", default=0)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "虚拟玩家每日库存计数"
        verbose_name_plural = "虚拟玩家每日库存计数"
        constraints = [
            models.UniqueConstraint(
                fields=["category", "counter_date"],
                name="bot_inventory_daily_counter_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["counter_date", "category"], name="bot_inv_counter_day_cat_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.counter_date}:{self.category}={self.quantity}"


class BotBackfillDemand(models.Model):
    """Aggregated map/scout demand for later virtual player population rolls."""

    region = models.CharField("地区", max_length=32)
    prestige_band = models.CharField("声望段", max_length=32)
    needed = models.PositiveIntegerField("需求数量", default=0)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "虚拟玩家补量需求"
        verbose_name_plural = "虚拟玩家补量需求"
        constraints = [
            models.UniqueConstraint(
                fields=["region", "prestige_band"],
                name="bot_backfill_demand_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["region", "prestige_band"], name="bot_backfill_region_band_idx"),
            models.Index(fields=["updated_at"], name="bot_backfill_updated_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.region}:{self.prestige_band} needs {self.needed}"
