from __future__ import annotations

import logging
import math
import random
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import Case, CharField, Count, DateTimeField, Exists, F, IntegerField, OuterRef, Q, Subquery, When
from django.db.models.expressions import RawSQL
from django.db.models.functions import Cast
from django.utils import timezone

from core.exceptions import (
    BuildingConcurrentUpgradeLimitError,
    BuildingMaxLevelError,
    BuildingUpgradingError,
    EquipmentError,
    EquipmentSlotFullError,
    GuestCapacityFullError,
    GuestFullHpError,
    GuestItemConfigurationError,
    GuestItemOwnershipError,
    GuestMaxLevelError,
    GuestNotIdleError,
    GuestNotRequirementError,
    GuestOwnershipError,
    GuestSkillAlreadyLearnedError,
    GuestTrainingInProgressError,
    InsufficientResourceError,
    InsufficientStockError,
    InvalidHealAmountError,
    ItemNotFoundError,
    NoGuestsError,
    SalaryAlreadyPaidError,
    SkillSlotFullError,
    TechnologyConcurrentUpgradeLimitError,
    TechnologyMaxLevelError,
    TechnologyNotFoundError,
    TechnologyUpgradeInProgressError,
    TroopRecruitmentError,
)
from core.utils.cache_lock import acquire_action_lock, release_action_lock
from gameplay.constants import BuildingKeys
from gameplay.models import (
    ArenaReserveTrainingAssignment,
    BotExternalStrengthReconciliation,
    BotInventoryDailyCounter,
    BotMaintenanceAttempt,
    BotMaintenanceCycle,
    BotMaintenanceExecution,
    BotMaintenanceRecovery,
    BotPolicyRelease,
    BotProfile,
    Building,
    InventoryItem,
    ItemTemplate,
    Manor,
    PlayerTechnology,
    PlayerTroop,
    ResourceEvent,
    ResourceType,
)
from gameplay.services.arena.virtual_protection import is_virtual_profile_arena_protected
from gameplay.services.inventory.core import GRAIN_ITEM_KEY
from gameplay.services.manor.core import (
    BuildingUpgradeQuote,
    BuildingUpgradeQuoteStaleError,
    apply_building_upgrade_free_locked,
    apply_building_upgrade_locked,
    quote_building_upgrade,
    start_building_upgrade_locked,
)
from gameplay.services.recruitment.recruitment import (
    TroopRecruitmentQuote,
    get_recruitment_options,
    quote_troop_recruitment,
    recruit_troops_locked,
)
from gameplay.services.resources import (
    ResourceProductionBasis,
    grant_resources_locked,
    load_resource_production_bases,
    load_resource_production_basis,
    preview_resource_production,
    settle_resource_production_locked,
    spend_resources_locked,
)
from gameplay.services.runtime_configs import (
    CalibrationRoute,
    RuntimeRoutingError,
    RuntimeRoutingSnapshot,
    read_virtual_player_routing,
)
from gameplay.services.technology import (
    TechnologyUpgradeQuote,
    TechnologyUpgradeQuoteStaleError,
    apply_technology_upgrade_locked,
    get_troop_classes,
    quote_technology_upgrade,
    start_technology_upgrade_locked,
)
from guests.guest_upkeep_rules import get_guest_salary_for_rarity
from guests.models import (
    GearItem,
    GearTemplate,
    Guest,
    GuestRarity,
    GuestRecruitment,
    GuestSkill,
    GuestStatus,
    GuestTemplate,
    SalaryPayment,
    Skill,
)
from guests.rarity import GUEST_RARITY_ORDER
from guests.services.equipment import equip_guest_from_inventory_locked, equip_guest_from_virtual_template_locked
from guests.services.health import MedicineUseQuote, apply_medicine_item_for_guest_locked, quote_medicine_item_for_guest
from guests.services.recruitment_finalize_helpers import remaining_guest_capacity
from guests.services.recruitment_guests import build_recruitment_custom_name, create_guest_from_template
from guests.services.salary import SalaryBatchQuote, bulk_check_salary_paid, pay_all_salaries_locked
from guests.services.skills import learn_guest_skill_from_virtual_book_locked, learn_guest_skill_locked
from guests.services.training import apply_training_locked, project_training_completion, quote_training
from guests.utils.training_calculator import get_training_duration

from . import profile_store
from .archetype_pacing import (
    HIGH_COST_ACTION_KINDS,
    ArchetypeBudgetState,
    ArchetypePacing,
    ArchetypePacingError,
    pacing_from_cycle_payload,
    resolve_archetype_pacing,
)
from .arena_healing import run_arena_guest_healing_sweep
from .bootstrap import lifecycle_dates
from .config import (
    MaintenanceMode,
    VirtualPlayerConfigError,
    VirtualPlayerV2Config,
    load_virtual_player_config,
    load_virtual_player_v2_config,
    policy_checksum,
)
from .contracts import (
    AcceleratedGrowthOutcome,
    ArenaGrowthObjective,
    InvalidStrengthBudgetError,
    MaintenanceOutcome,
    MaintenanceResult,
    MaintenanceScheduleDisposition,
    MaintenanceTrigger,
    MaintenanceTriggerPolicy,
    StrengthBudgetEntry,
    calculate_positive_growth_bps,
    maintenance_trigger_policy,
    parse_strength_budget_entries,
    prune_strength_budget_entries,
)
from .database_clock import database_utc_sql_expression, normalize_database_utc
from .economy import ForcedSettlementDecision, parse_forced_settlement_budget, plan_forced_settlement
from .growth_control import growth_control_reference_selection
from .inventory_budget import apply_inventory_daily_caps, inventory_daily_cap_limits
from .maintenance_action_specs import (
    BuildingUpgradeActionSpec,
    EquipmentEquipActionSpec,
    GuestRecruitmentActionSpec,
    InventoryAcquisitionActionSpec,
    MaintenanceActionSpec,
    MaintenanceActionSpecError,
    SkillLearningActionSpec,
    TechnologyUpgradeActionSpec,
    maintenance_action_spec_payload,
    project_maintenance_action_intent,
)
from .maintenance_arena_projection import ArenaSelectedPowerProjection, project_arena_candidate_selected_power
from .maintenance_candidate_assessment import CandidateAssessment, CandidateAssessmentError, select_candidate_assessment
from .maintenance_candidates import (
    MaintenanceCandidateError,
    build_equipment_equip_candidates,
    build_inventory_acquisition_candidates,
    build_skill_learning_candidates,
)
from .maintenance_cycle import (
    ACTION_COMPLETION_SOURCE_CANDIDATE_EXHAUSTED,
    ACTION_COMPLETION_SOURCE_MAINTENANCE_COMMIT,
    ORDINARY_CYCLE_NEXT_START_MAX_DELAY,
    CycleTrigger,
    append_durable_cycle_action_locked,
    classify_maintenance_reason,
    close_durable_cycle_locked,
    cycle_retry_due_at,
    next_ordinary_slot_due_at,
    record_durable_attempts_locked,
)
from .maintenance_resources import (
    ResourcePlanningError,
    ResourcePlanningSnapshot,
    build_resource_planning_snapshot,
    salary_runway_commitment,
)
from .maintenance_rules import (
    MaintenanceNoActionReason,
    MaintenanceRuleError,
    PrestigeBandGrowthPolicy,
    evaluate_controlled_action,
    next_normal_strength_check_at,
    parse_prestige_band_growth_policy,
)
from .maintenance_scoring import candidate_efficiency_score
from .maintenance_upgrade_candidates import (
    MaintenanceUpgradeCandidateError,
    build_building_upgrade_candidates,
    build_technology_upgrade_candidates,
)
from .policy_registry import PolicyRegistryError, get_policy_release
from .projection import (
    PRESTIGE_BANDS,
    DevelopmentIntent,
    GuestHealingCandidate,
    ProjectionRuleError,
    ReferenceSelection,
    StrengthSummary,
    calculate_guest_arena_power,
    project_guest_healing_development_intent,
    project_training_development_intent,
    project_troop_recruitment_development_intent,
    select_guest_healing_candidate,
)
from .random_context import (
    RandomContext,
    UnsupportedRandomDomainError,
    UnsupportedRngVersionError,
    canonical_json_bytes,
)
from .recovery import (
    RecoveryFailureClass,
    classify_failure,
    clear_recovery_failure,
    exclude_blocked_profile_recoveries,
    record_recovery_failure,
    recovery_circuit_is_open,
)
from .reference_snapshots import (
    CORE_BUILDING_KEYS,
    ReferenceSnapshotError,
    build_strength_summary,
    load_manor_strength_summaries,
    load_manor_strength_summary,
)
from .safety_metrics import (
    MaintenanceAttempt,
    MaintenanceAttemptResult,
    finish_maintenance_attempt,
    finish_maintenance_attempts,
    log_safety_metric_failure,
    normalize_maintenance_attempt_ordinal,
    normalize_maintenance_operation_id,
    start_maintenance_attempt,
    start_maintenance_attempts,
)
from .safety_preflight import check_v2_development_write_preflight
from .safety_provider import SafetyProviderError
from .selectors import (
    prestige_band_for_value,
    profile_target_prestige_band,
    unresolved_external_reconciliation_profile_ids,
    without_unresolved_external_reconciliations,
)
from .stage_metrics import (
    STAGE_ACTION_DOMAIN_WRITES,
    STAGE_CYCLE_ATTEMPT_RECEIPT,
    STAGE_DUE_BACKLOG_SELECTION,
    STAGE_PLANNING_SNAPSHOT_PRELOAD,
    STAGE_PROFILE_PLAN_REVALIDATION,
    STAGE_SAFETY_TASK_WRAPUP,
    record_maintenance_stage,
)
from .strategy import BotDevelopmentPlan, DevelopmentPlanError, development_plan_catalog_v1, parse_development_plan
from .virtual_assets import free_arena_shadow_cost
from .virtual_candidate_pools import (
    VirtualCandidatePoolError,
    build_virtual_equipment_candidates,
    build_virtual_inventory_batch_candidate,
    build_virtual_skill_learning_candidates,
    build_virtual_troop_candidates,
)

logger = logging.getLogger(__name__)

V2_MAINTENANCE_ENGINE_VERSION = 2
SCHEDULED_MAINTENANCE_BATCH_LOCK_TIMEOUT_SECONDS = 180
# Keep the scheduled maintenance slice large enough to drain the configured
# population without removing the separate hard-cap safety boundary below.
SCHEDULED_MAINTENANCE_DEFAULT_BATCH_SIZE = 200
# The population audit currently fixes the maintained-profile hard cap at
# 1000.  Keep the due selection bounded even when a caller bypasses the
# public batch entry point.
SCHEDULED_MAINTENANCE_DUE_SCAN_HARD_CAP = 1000

# 这些动作不直接消耗银两；工资短缺时允许作为安全回退。
# guest_recruitment 刻意排除，因为它会增加下一轮工资负担；training 和
# technology_upgrade 当前规则都消耗银两，也不能归入安全回退。
_SALARY_SAFE_ACTION_KINDS = frozenset(
    {
        "guest_healing",
        "troop_recruitment",
        EquipmentEquipActionSpec.action_kind,
        SkillLearningActionSpec.action_kind,
        InventoryAcquisitionActionSpec.action_kind,
    }
)
_ARENA_ACTION_KINDS = frozenset(
    {
        "guest_healing",
        GuestRecruitmentActionSpec.action_kind,
        "training",
        EquipmentEquipActionSpec.action_kind,
    }
)
_ARENA_V2_ACTION_KINDS = frozenset(
    {
        GuestRecruitmentActionSpec.action_kind,
        "training",
        EquipmentEquipActionSpec.action_kind,
        SkillLearningActionSpec.action_kind,
    }
)


def _arena_action_is_allowed(
    candidate: DevelopmentIntent,
    *,
    virtual_asset_policy: bool,
    arena_replenishment: bool,
    action_spec: MaintenanceActionSpec | None = None,
) -> bool:
    """Keep the arena trigger narrow while allowing its capacity prerequisite."""

    allowed_kinds = _ARENA_V2_ACTION_KINDS if virtual_asset_policy else _ARENA_ACTION_KINDS
    if candidate.action_kind in allowed_kinds:
        return True
    return bool(
        arena_replenishment
        and candidate.action_kind == BuildingUpgradeActionSpec.action_kind
        and isinstance(action_spec, BuildingUpgradeActionSpec)
        and action_spec.building_key == BuildingKeys.JUXIAN_ZHUANG
    )


def _configured_projection_keys(value: Any) -> set[str] | None:
    """Normalize projection selectors while preserving ``__all__`` semantics."""

    if isinstance(value, str):
        normalized = value.strip()
        if normalized in {"__all__", "__all_tradeable__"}:
            return None
        return {normalized} if normalized else set()
    if isinstance(value, (list, tuple, set, frozenset)):
        return {str(key).strip() for key in value if isinstance(key, str) and key.strip()}
    return set()


class V2MaintenanceError(ValueError):
    pass


class MaintenanceExecutionConflict(V2MaintenanceError):
    pass


class InventoryAcquisitionUnavailable(V2MaintenanceError):
    pass


class GuestRecruitmentTemplateChangedError(V2MaintenanceError):
    pass


class _V2MaintenanceOutcomeError(V2MaintenanceError):
    def __init__(self, outcome: MaintenanceOutcome, reason: str) -> None:
        super().__init__(reason)
        self.outcome = MaintenanceOutcome(outcome)
        self.reason = str(reason)


class _V2MaintenanceCandidateRejected(V2MaintenanceError):
    """A lock-time business constraint that may be replaced by another candidate."""

    def __init__(self, *, business_key: str, reason: str) -> None:
        super().__init__(reason)
        self.business_key = str(business_key)
        self.reason = str(reason)


_ORDINARY_CYCLE_COVERAGE_KINDS = (
    BuildingUpgradeActionSpec.action_kind,
    TechnologyUpgradeActionSpec.action_kind,
    "guest",
    EquipmentEquipActionSpec.action_kind,
    SkillLearningActionSpec.action_kind,
    InventoryAcquisitionActionSpec.action_kind,
)


def _ordinary_cycle_coverage_kind(action_kind: str) -> str:
    if action_kind in {"training", GuestRecruitmentActionSpec.action_kind}:
        return "guest"
    return str(action_kind)


_SCHEDULED_PLANNING_PROFILE_ERRORS = (
    DevelopmentPlanError,
    MaintenanceRuleError,
    PolicyRegistryError,
    ProjectionRuleError,
    ReferenceSnapshotError,
    ResourcePlanningError,
    V2MaintenanceError,
    VirtualPlayerConfigError,
)


@dataclass(frozen=True, slots=True)
class _MaintenanceExecutionReceiptContext:
    operation_id: str
    attempt_ordinal: int
    request_digest: str
    requested_at: datetime
    request_digest_schema: int = 3
    safety_started_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class _MaintenancePlanningSnapshot:
    profile: BotProfile
    guests: tuple[Guest, ...]
    buildings: tuple[Building, ...]
    technologies: tuple[PlayerTechnology, ...]
    gear_items: tuple[GearItem, ...]
    strength: StrengthSummary
    paid_guest_ids: frozenset[int]
    troop_counts: tuple[tuple[str, int], ...]
    medicine_items: tuple[InventoryItem, ...]
    guest_skills: tuple[GuestSkill, ...]
    skills: tuple[Skill, ...]
    warehouse_items: tuple[InventoryItem, ...]
    inventory_templates: tuple[ItemTemplate, ...]
    production_basis: ResourceProductionBasis
    policy_release: BotPolicyRelease | None
    grain_template: ItemTemplate | None = None
    virtual_skill_books: tuple[ItemTemplate, ...] = ()
    virtual_skills: tuple[Skill, ...] = ()
    virtual_gear_templates: tuple[GearTemplate, ...] = ()
    virtual_inventory_templates: tuple[ItemTemplate, ...] = ()
    rare_inventory_quantity_today: int = 0


@dataclass(frozen=True, slots=True)
class _VirtualProjectionPools:
    skill_books: tuple[ItemTemplate, ...]
    skills: tuple[Skill, ...]
    gear_templates: tuple[GearTemplate, ...]
    inventory_templates: tuple[ItemTemplate, ...]
    rare_inventory_quantity_today: int


def _load_virtual_projection_pools(*, planned_at: datetime) -> _VirtualProjectionPools:
    config = load_virtual_player_config()
    projection = (config or {}).get("projection") or {}
    rare_counter_quantity = BotInventoryDailyCounter.objects.filter(
        category="rare",
        counter_date=timezone.localdate(planned_at),
    ).values("quantity")[:1]
    configured_skill_keys = _configured_projection_keys(projection.get("skill_book_template_keys", []))
    skill_books = tuple(
        ItemTemplate.objects.filter(
            effect_type=ItemTemplate.EffectType.SKILL_BOOK,
            **({"key__in": configured_skill_keys} if configured_skill_keys else {}),
        )
        .annotate(
            virtual_rare_inventory_quantity_today=Subquery(
                rare_counter_quantity,
                output_field=IntegerField(),
            )
        )
        .order_by("key", "id")
    )
    # The candidate builder below only admits skills with a player-facing
    # skill-book definition.  Keeping the catalog snapshot complete lets
    # malformed book references fail closed during candidate construction.
    skills = tuple(Skill.objects.order_by("key", "id"))
    configured_gear_keys = _configured_projection_keys(projection.get("gear_template_keys", []))
    gear_templates = tuple(
        GearTemplate.objects.filter(**({"key__in": configured_gear_keys} if configured_gear_keys else {})).order_by(
            "slot", "key", "id"
        )
    )
    inventory_templates = tuple(
        template
        for template in ItemTemplate.objects.filter(tradeable=True)
        .exclude(key=GRAIN_ITEM_KEY)
        .annotate(
            virtual_rare_inventory_quantity_today=Subquery(
                rare_counter_quantity,
                output_field=IntegerField(),
            )
        )
        .order_by("key", "id")
        if bool(template.tradeable) and str(template.key) != GRAIN_ITEM_KEY
    )
    pool_templates = (*skill_books, *inventory_templates)
    rare_inventory_quantity_today = int(
        next(
            (getattr(template, "virtual_rare_inventory_quantity_today", 0) for template in pool_templates),
            0,
        )
        or 0
    )
    return _VirtualProjectionPools(
        skill_books=skill_books,
        skills=skills,
        gear_templates=gear_templates,
        inventory_templates=inventory_templates,
        rare_inventory_quantity_today=rare_inventory_quantity_today,
    )


@dataclass(frozen=True, slots=True)
class MaintenancePlan:
    profile_id: int
    manor_id: int
    expected_sequence: int
    trigger_policy: MaintenanceTriggerPolicy
    planned_at: datetime
    routing_revision: int
    engine_version: int
    rng_version: int
    plan_schema_version: int
    policy_version: int
    policy_checksum: str
    development_plan: BotDevelopmentPlan
    growth_policy: PrestigeBandGrowthPolicy
    profile_state: str
    region: str
    current_prestige_band: str
    reference_snapshot_version: int
    control_snapshot_digest: str
    reference_selection: ReferenceSelection
    target_reference_selection: ReferenceSelection | None
    strength_before: StrengthSummary
    strength_budget_entries_before: tuple[StrengthBudgetEntry, ...]
    resource_production_deltas: tuple[tuple[str, int], ...]
    forced_settlement_decision: ForcedSettlementDecision
    salary_quote: SalaryBatchQuote
    resource_planning_snapshot: ResourcePlanningSnapshot
    last_strength_increase_at_before: datetime | None
    next_growth_at_before: datetime | None
    next_growth_at_after: datetime | None
    next_growth_at_after_no_action: datetime | None
    action_kind: str
    target_id: int | None
    training_levels: int
    rng_seed: int | None
    troop_recruitment_quote: TroopRecruitmentQuote | None
    medicine_quote: MedicineUseQuote | None
    action_spec: MaintenanceActionSpec | None
    precondition_digest: str
    intent: DevelopmentIntent | None
    candidate_assessments: tuple[CandidateAssessment, ...]
    calibration_route: CalibrationRoute | None
    target_calibration_route: CalibrationRoute | None
    minimum_guest_count: int | None
    minimum_guest_level: int | None
    guest_rarity_cap: str | None
    max_guest_level_step: int | None
    arena_growth_objective: ArenaGrowthObjective | None
    arena_excluded_training_guest_ids: tuple[int, ...] = ()
    cycle_covered_action_kinds: tuple[str, ...] = ()
    cycle_high_cost_actions_used: int = 0
    cycle_budget_state: ArchetypeBudgetState | None = None
    cycle_pacing: ArchetypePacing | None = None
    candidate_exclusions: tuple[str, ...] = ()
    scheduled_cycle_slot_due: bool = False
    domain_availability: tuple[tuple[str, tuple[datetime, ...]], ...] = ()
    virtual_projection_pools: _VirtualProjectionPools | None = dataclass_field(
        default=None,
        compare=False,
        repr=False,
    )
    planning_snapshot: _MaintenancePlanningSnapshot | None = dataclass_field(
        default=None,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        for field in ("profile_id", "manor_id"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise V2MaintenanceError(f"{field} must be a positive integer")
        if (
            isinstance(self.expected_sequence, bool)
            or not isinstance(self.expected_sequence, int)
            or self.expected_sequence < 0
        ):
            raise V2MaintenanceError("expected_sequence must be a non-negative integer")
        if not isinstance(self.trigger_policy, MaintenanceTriggerPolicy):
            raise V2MaintenanceError("trigger_policy must be a MaintenanceTriggerPolicy")
        if timezone.is_naive(self.planned_at):
            raise V2MaintenanceError("planned_at must be timezone-aware")
        if (
            isinstance(self.routing_revision, bool)
            or not isinstance(self.routing_revision, int)
            or self.routing_revision < 0
        ):
            raise V2MaintenanceError("routing_revision must be a non-negative integer")
        if self.engine_version != V2_MAINTENANCE_ENGINE_VERSION:
            raise V2MaintenanceError("V2 maintenance plan requires engine_version=2")
        for field in (
            "rng_version",
            "plan_schema_version",
            "policy_version",
            "reference_snapshot_version",
        ):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise V2MaintenanceError(f"{field} must be a positive integer")
        if not isinstance(self.policy_checksum, str) or not self.policy_checksum:
            raise V2MaintenanceError("policy_checksum must not be empty")
        if type(self.scheduled_cycle_slot_due) is not bool:
            raise V2MaintenanceError("scheduled_cycle_slot_due must be a boolean")
        for domain_name, completion_times in self.domain_availability:
            if not isinstance(domain_name, str) or not domain_name:
                raise V2MaintenanceError("domain availability names must be non-empty strings")
            if tuple(sorted(completion_times)) != tuple(completion_times):
                raise V2MaintenanceError("domain availability timestamps must be ordered")
            if any(timezone.is_naive(value) for value in completion_times):
                raise V2MaintenanceError("domain availability timestamps must be timezone-aware")
        if self.policy_version == 2:
            if not isinstance(self.control_snapshot_digest, str) or len(self.control_snapshot_digest) != 64:
                raise V2MaintenanceError("policy 2 maintenance requires a frozen control snapshot digest")
            try:
                bytes.fromhex(self.control_snapshot_digest)
            except ValueError as exc:
                raise V2MaintenanceError("control_snapshot_digest must be a SHA-256 hex digest") from exc
        if not isinstance(self.development_plan, BotDevelopmentPlan):
            raise V2MaintenanceError("development_plan must be a BotDevelopmentPlan")
        if self.development_plan.schema_version != self.plan_schema_version:
            raise V2MaintenanceError("development plan schema does not match the profile identity")
        if not isinstance(self.growth_policy, PrestigeBandGrowthPolicy):
            raise V2MaintenanceError("growth_policy must be a PrestigeBandGrowthPolicy")
        if not isinstance(self.profile_state, str) or not self.profile_state:
            raise V2MaintenanceError("profile_state must not be empty")
        if not isinstance(self.region, str) or not self.region:
            raise V2MaintenanceError("region must not be empty")
        if not isinstance(self.current_prestige_band, str) or not self.current_prestige_band:
            raise V2MaintenanceError("current_prestige_band must not be empty")
        if not isinstance(self.reference_selection, ReferenceSelection):
            raise V2MaintenanceError("reference_selection must be a ReferenceSelection")
        if self.reference_selection.prestige_band != self.current_prestige_band:
            raise V2MaintenanceError("reference selection does not match the current prestige band")
        if self.target_reference_selection is not None and not isinstance(
            self.target_reference_selection,
            ReferenceSelection,
        ):
            raise V2MaintenanceError("target_reference_selection must be a ReferenceSelection or None")
        if not isinstance(self.strength_before, StrengthSummary):
            raise V2MaintenanceError("strength_before must be a StrengthSummary")
        if any(not isinstance(entry, StrengthBudgetEntry) for entry in self.strength_budget_entries_before):
            raise V2MaintenanceError("strength_budget_entries_before must contain StrengthBudgetEntry values")
        if not isinstance(
            self.forced_settlement_decision,
            ForcedSettlementDecision,
        ):
            raise V2MaintenanceError("forced_settlement_decision must be a ForcedSettlementDecision")
        normalized_production_deltas: list[tuple[str, int]] = []
        seen_resources: set[str] = set()
        for entry in self.resource_production_deltas:
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise V2MaintenanceError("resource_production_deltas must contain resource/delta pairs")
            resource, delta = entry
            if resource not in {ResourceType.SILVER, ResourceType.GRAIN}:
                raise V2MaintenanceError(f"unsupported resource production delta: {resource!r}")
            if resource in seen_resources:
                raise V2MaintenanceError(f"duplicate resource production delta: {resource}")
            if isinstance(delta, bool) or not isinstance(delta, int) or delta == 0:
                raise V2MaintenanceError("resource production deltas must be non-zero integers")
            seen_resources.add(resource)
            normalized_production_deltas.append((resource, delta))
        if tuple(sorted(normalized_production_deltas)) != self.resource_production_deltas:
            raise V2MaintenanceError("resource_production_deltas must use canonical resource order")
        requested_silver = max(0, dict(self.resource_production_deltas).get(ResourceType.SILVER, 0))
        requested_grain = max(0, dict(self.resource_production_deltas).get(ResourceType.GRAIN, 0))
        if (
            self.forced_settlement_decision.silver_units > requested_silver
            or self.forced_settlement_decision.grain_units > requested_grain
        ):
            raise V2MaintenanceError("forced settlement exceeds the frozen production request")
        if not isinstance(self.salary_quote, SalaryBatchQuote):
            raise V2MaintenanceError("salary_quote must be a SalaryBatchQuote")
        if not isinstance(self.resource_planning_snapshot, ResourcePlanningSnapshot):
            raise V2MaintenanceError("resource_planning_snapshot must be a ResourcePlanningSnapshot")
        if self.resource_planning_snapshot.current_salary_quote != self.salary_quote:
            raise V2MaintenanceError("resource planning salary quote differs from the plan salary quote")
        if self.resource_planning_snapshot.production_deltas != self.resource_production_deltas:
            raise V2MaintenanceError("resource planning production differs from the plan production")
        if self.salary_quote.guest_ids != tuple(sorted(self.salary_quote.guest_ids)):
            raise V2MaintenanceError("salary guest ids must use canonical order")
        if self.salary_quote.unpaid_guest_ids != tuple(sorted(self.salary_quote.unpaid_guest_ids)):
            raise V2MaintenanceError("unpaid salary guest ids must use canonical order")
        if not set(self.salary_quote.unpaid_guest_ids).issubset(self.salary_quote.guest_ids):
            raise V2MaintenanceError("unpaid salary guests must belong to the Manor")
        if (
            isinstance(self.salary_quote.total_amount, bool)
            or not isinstance(self.salary_quote.total_amount, int)
            or self.salary_quote.total_amount < 0
        ):
            raise V2MaintenanceError("salary total must be a non-negative integer")
        if self.last_strength_increase_at_before is not None and timezone.is_naive(
            self.last_strength_increase_at_before
        ):
            raise V2MaintenanceError("last_strength_increase_at_before must be timezone-aware")
        for field in (
            "next_growth_at_before",
            "next_growth_at_after",
            "next_growth_at_after_no_action",
        ):
            value = getattr(self, field)
            if value is not None and timezone.is_naive(value):
                raise V2MaintenanceError(f"{field} must be timezone-aware")
        if self.intent is None:
            if (
                self.action_kind
                or self.target_id is not None
                or self.training_levels != 0
                or self.rng_seed is not None
                or self.troop_recruitment_quote is not None
                or self.medicine_quote is not None
                or self.action_spec is not None
            ):
                raise V2MaintenanceError("an empty maintenance intent must not carry action metadata")
        else:
            if not isinstance(self.intent, DevelopmentIntent):
                raise V2MaintenanceError("intent must be a DevelopmentIntent")
            if self.action_kind != self.intent.action_kind:
                raise V2MaintenanceError("maintenance action_kind must match its intent")
            if self.intent.source_prestige_band != self.current_prestige_band:
                raise V2MaintenanceError("maintenance intent source does not match the current prestige band")
            if self.action_kind == "training":
                if isinstance(self.target_id, bool) or not isinstance(self.target_id, int) or self.target_id < 1:
                    raise V2MaintenanceError("training maintenance requires a positive target_id")
                if self.intent.business_key != f"training:guest:{self.target_id}":
                    raise V2MaintenanceError("training intent does not match the selected target")
                if (
                    isinstance(self.training_levels, bool)
                    or not isinstance(self.training_levels, int)
                    or self.training_levels < 1
                ):
                    raise V2MaintenanceError("training maintenance requires positive training_levels")
                if isinstance(self.rng_seed, bool) or not isinstance(self.rng_seed, int) or self.rng_seed < 0:
                    raise V2MaintenanceError("training maintenance requires a non-negative rng_seed")
                if self.troop_recruitment_quote is not None:
                    raise V2MaintenanceError("training maintenance must not carry a troop quote")
                if self.medicine_quote is not None:
                    raise V2MaintenanceError("training maintenance must not carry a medicine quote")
                if self.action_spec is not None:
                    raise V2MaintenanceError("training maintenance must not carry an action spec")
            elif self.action_kind == "troop_recruitment":
                quote = self.troop_recruitment_quote
                if not isinstance(quote, TroopRecruitmentQuote):
                    raise V2MaintenanceError("troop recruitment maintenance requires a typed quote")
                if self.target_id is not None or self.training_levels != 0 or self.rng_seed is not None:
                    raise V2MaintenanceError("troop recruitment must not reuse training metadata")
                if quote.manor_id != self.manor_id:
                    raise V2MaintenanceError("troop recruitment quote does not belong to the plan Manor")
                if self.intent.business_key != (f"troop_recruitment:{quote.troop_key}:{quote.quantity}"):
                    raise V2MaintenanceError("troop recruitment intent does not match its quote")
                if self.medicine_quote is not None:
                    raise V2MaintenanceError("troop recruitment must not carry a medicine quote")
                if self.action_spec is not None:
                    raise V2MaintenanceError("troop recruitment must not carry an action spec")
            elif self.action_kind == "guest_healing":
                medicine_quote_value = self.medicine_quote
                if not isinstance(medicine_quote_value, MedicineUseQuote):
                    raise V2MaintenanceError("guest healing maintenance requires a typed medicine quote")
                if isinstance(self.target_id, bool) or not isinstance(self.target_id, int) or self.target_id < 1:
                    raise V2MaintenanceError("guest healing maintenance requires a positive target_id")
                if self.training_levels != 0 or self.rng_seed is not None or self.troop_recruitment_quote is not None:
                    raise V2MaintenanceError("guest healing must not reuse growth action metadata")
                if medicine_quote_value.manor_id != self.manor_id or medicine_quote_value.guest_id != self.target_id:
                    raise V2MaintenanceError("medicine quote does not belong to the plan target")
                if self.intent.business_key != (
                    f"guest_healing:guest:{self.target_id}:item:{medicine_quote_value.item_key}"
                ):
                    raise V2MaintenanceError("guest healing intent does not match its medicine quote")
                if self.action_spec is not None:
                    raise V2MaintenanceError("guest healing must not carry an action spec")
            elif self.action_kind == GuestRecruitmentActionSpec.action_kind:
                spec = self.action_spec
                if not isinstance(spec, GuestRecruitmentActionSpec):
                    raise V2MaintenanceError("guest recruitment maintenance requires a typed action spec")
                if self.trigger_policy.trigger is not MaintenanceTrigger.ARENA_ACCELERATION:
                    raise V2MaintenanceError("instant guest recruitment is reserved for arena acceleration")
                if self.intent.business_key != spec.business_key:
                    raise V2MaintenanceError("guest recruitment intent does not match its action spec")
                if (
                    self.target_id is not None
                    or self.training_levels != 0
                    or self.rng_seed is not None
                    or self.troop_recruitment_quote is not None
                    or self.medicine_quote is not None
                ):
                    raise V2MaintenanceError("guest recruitment must not reuse another action's metadata")
            elif self.action_kind in {
                BuildingUpgradeActionSpec.action_kind,
                EquipmentEquipActionSpec.action_kind,
                SkillLearningActionSpec.action_kind,
                InventoryAcquisitionActionSpec.action_kind,
                TechnologyUpgradeActionSpec.action_kind,
            }:
                spec = self.action_spec
                if not isinstance(
                    spec,
                    (
                        BuildingUpgradeActionSpec,
                        EquipmentEquipActionSpec,
                        InventoryAcquisitionActionSpec,
                        SkillLearningActionSpec,
                        TechnologyUpgradeActionSpec,
                    ),
                ):
                    raise V2MaintenanceError("typed maintenance action requires a matching action spec")
                if spec.action_kind != self.action_kind:
                    raise V2MaintenanceError("maintenance action spec kind does not match its intent")
                if self.intent.business_key != spec.business_key:
                    raise V2MaintenanceError("maintenance action spec does not match its intent")
                if (
                    self.training_levels != 0
                    or self.rng_seed is not None
                    or self.troop_recruitment_quote is not None
                    or self.medicine_quote is not None
                ):
                    raise V2MaintenanceError("typed maintenance action must not reuse legacy action metadata")
                if isinstance(
                    spec,
                    (EquipmentEquipActionSpec, SkillLearningActionSpec),
                ):
                    if self.target_id != spec.guest_id:
                        raise V2MaintenanceError("guest maintenance target does not match its action spec")
                elif self.target_id is not None:
                    raise V2MaintenanceError("non-guest typed maintenance action must not carry a target_id")
            else:
                raise V2MaintenanceError(f"unsupported V2 maintenance action: {self.action_kind}")
        if any(not isinstance(assessment, CandidateAssessment) for assessment in self.candidate_assessments):
            raise V2MaintenanceError("candidate_assessments must contain CandidateAssessment values")
        assessment_keys = tuple(assessment.intent.business_key for assessment in self.candidate_assessments)
        if len(assessment_keys) != len(set(assessment_keys)):
            raise V2MaintenanceError("candidate assessments must have unique business keys")
        if self.intent is not None and self.intent.business_key not in set(assessment_keys):
            raise V2MaintenanceError("selected intent must have a candidate assessment")
        target_band = self.current_prestige_band if self.intent is None else self.intent.target_prestige_band
        transition_distance = abs(PRESTIGE_BANDS.index(self.current_prestige_band) - PRESTIGE_BANDS.index(target_band))
        if transition_distance == 1:
            if self.target_reference_selection is None:
                raise V2MaintenanceError("adjacent-band maintenance requires a target reference selection")
            if self.target_reference_selection.prestige_band != target_band:
                raise V2MaintenanceError("target reference selection does not match the maintenance intent")
        elif self.target_reference_selection is not None:
            raise V2MaintenanceError("target reference selection is only valid for adjacent-band maintenance")
        if not isinstance(self.precondition_digest, str) or len(self.precondition_digest) != 64:
            raise V2MaintenanceError("precondition_digest must be a SHA-256 hex digest")
        try:
            bytes.fromhex(self.precondition_digest)
        except ValueError as exc:
            raise V2MaintenanceError("precondition_digest must be a SHA-256 hex digest") from exc
        if self.calibration_route is not None and (
            self.calibration_route.policy_version != self.policy_version
            or self.calibration_route.prestige_band != self.current_prestige_band
        ):
            raise V2MaintenanceError("calibration route does not match the plan identity")
        if self.target_calibration_route is not None and (
            self.target_reference_selection is None
            or self.target_calibration_route.policy_version != self.policy_version
            or self.target_calibration_route.prestige_band != self.target_reference_selection.prestige_band
        ):
            raise V2MaintenanceError("target calibration route does not match the plan identity")
        for field in ("minimum_guest_count", "minimum_guest_level"):
            value = getattr(self, field)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise V2MaintenanceError(f"{field} must be a non-negative integer or None")
        if self.max_guest_level_step is not None and (
            isinstance(self.max_guest_level_step, bool)
            or not isinstance(self.max_guest_level_step, int)
            or self.max_guest_level_step < 1
        ):
            raise V2MaintenanceError("max_guest_level_step must be a positive integer or None")
        if self.guest_rarity_cap is not None and self.guest_rarity_cap not in _GUEST_RARITY_RANK:
            raise V2MaintenanceError("recruitment_rarity_cap must use a configured guest rarity")
        if self.arena_growth_objective is not None:
            if not isinstance(self.arena_growth_objective, ArenaGrowthObjective):
                raise V2MaintenanceError("arena_growth_objective must be an ArenaGrowthObjective or None")
            if self.trigger_policy.trigger is not MaintenanceTrigger.ARENA_ACCELERATION:
                raise V2MaintenanceError("arena growth objective requires arena acceleration")
            if self.minimum_guest_count != self.arena_growth_objective.critical_guest_count:
                raise V2MaintenanceError("arena growth objective guest count differs from the compatibility alias")
            if self.minimum_guest_level != self.arena_growth_objective.minimum_guest_level:
                raise V2MaintenanceError("arena growth objective guest level differs from the compatibility alias")
            if self.guest_rarity_cap != self.arena_growth_objective.recruitment_rarity_cap:
                raise V2MaintenanceError("arena growth objective rarity cap differs from the compatibility alias")
            if self.max_guest_level_step != self.arena_growth_objective.max_guest_level_step:
                raise V2MaintenanceError("arena growth objective level step differs from the compatibility alias")
            for assessment in self.candidate_assessments:
                if assessment.event_power_cap is not None and (
                    assessment.event_power_cap != self.arena_growth_objective.selected_power_upper_bound
                ):
                    raise V2MaintenanceError("candidate event power cap differs from the arena growth objective")
        elif any(assessment.event_power_cap is not None for assessment in self.candidate_assessments):
            raise V2MaintenanceError("candidate event power projection requires an arena growth objective")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in self.arena_excluded_training_guest_ids
        ):
            raise V2MaintenanceError("arena excluded training guest ids must be positive integers")
        if tuple(dict.fromkeys(self.arena_excluded_training_guest_ids)) != self.arena_excluded_training_guest_ids:
            raise V2MaintenanceError("arena excluded training guest ids must be unique")
        normalized_covered = tuple(
            dict.fromkeys(str(value).strip() for value in self.cycle_covered_action_kinds if str(value).strip())
        )
        if len(normalized_covered) != len(self.cycle_covered_action_kinds):
            raise V2MaintenanceError("cycle covered action kinds must be unique and non-empty")
        object.__setattr__(self, "cycle_covered_action_kinds", normalized_covered)
        if (
            isinstance(self.cycle_high_cost_actions_used, bool)
            or not isinstance(self.cycle_high_cost_actions_used, int)
            or self.cycle_high_cost_actions_used < 0
        ):
            raise V2MaintenanceError("cycle_high_cost_actions_used must be a non-negative integer")
        if self.cycle_budget_state is not None and not isinstance(self.cycle_budget_state, ArchetypeBudgetState):
            raise V2MaintenanceError("cycle_budget_state must be an ArchetypeBudgetState or None")
        if self.cycle_pacing is not None and not isinstance(self.cycle_pacing, ArchetypePacing):
            raise V2MaintenanceError("cycle_pacing must be an ArchetypePacing or None")
        if self.cycle_pacing is not None and (
            self.policy_version != 2 or self.trigger_policy.trigger is not MaintenanceTrigger.SCHEDULED
        ):
            raise V2MaintenanceError("cycle_pacing is only valid for scheduled policy-2 maintenance")
        normalized_exclusions = tuple(
            dict.fromkeys(str(value).strip() for value in self.candidate_exclusions if str(value).strip())
        )
        if len(normalized_exclusions) != len(self.candidate_exclusions):
            raise V2MaintenanceError("candidate exclusions must be unique and non-empty")
        object.__setattr__(self, "candidate_exclusions", normalized_exclusions)

    @property
    def reference_sample_count(self) -> int:
        return self.reference_selection.local_sample_count

    @property
    def reference_strength_cap(self) -> StrengthSummary:
        return self.reference_selection.cap

    @property
    def target_reference_sample_count(self) -> int | None:
        if self.target_reference_selection is None:
            return None
        return self.target_reference_selection.local_sample_count

    @property
    def target_reference_strength_cap(self) -> StrengthSummary | None:
        if self.target_reference_selection is None:
            return None
        return self.target_reference_selection.cap

    @property
    def budget_before(self) -> tuple[StrengthBudgetEntry, ...]:
        return self.strength_budget_entries_before

    @property
    def selected_candidate_assessment(self) -> CandidateAssessment | None:
        if self.intent is None:
            return None
        return next(
            assessment
            for assessment in self.candidate_assessments
            if assessment.intent.business_key == self.intent.business_key
        )

    @property
    def recruitment_rarity_cap(self) -> str | None:
        """Canonical V2 name; guest_rarity_cap remains the external alias."""
        return self.guest_rarity_cap

    @property
    def requested_settlement_silver(self) -> int:
        return max(
            0,
            dict(self.resource_production_deltas).get(ResourceType.SILVER, 0),
        )

    @property
    def requested_settlement_grain(self) -> int:
        return max(
            0,
            dict(self.resource_production_deltas).get(ResourceType.GRAIN, 0),
        )


def _replace_virtual_pool_item(
    items: tuple[Any, ...],
    *,
    item_id: int,
    current: Any | None,
) -> tuple[Any, ...]:
    if current is None:
        return tuple(item for item in items if int(item.id) != int(item_id))
    return tuple(current if int(item.id) == int(item_id) else item for item in items)


def _refresh_virtual_projection_pools_for_plan(
    plan: MaintenancePlan,
    pools: _VirtualProjectionPools,
) -> _VirtualProjectionPools:
    """Refresh only selected catalog rows before lock-time revalidation."""

    spec = plan.action_spec
    if isinstance(spec, EquipmentEquipActionSpec) and spec.source == "virtual":
        gear_template = GearTemplate.objects.select_for_update().filter(pk=int(spec.item_template_id)).first()
        return _VirtualProjectionPools(
            skill_books=pools.skill_books,
            skills=pools.skills,
            gear_templates=_replace_virtual_pool_item(
                pools.gear_templates,
                item_id=spec.item_template_id,
                current=gear_template,
            ),
            inventory_templates=pools.inventory_templates,
            rare_inventory_quantity_today=pools.rare_inventory_quantity_today,
        )
    if isinstance(spec, SkillLearningActionSpec) and spec.source == "virtual":
        template = (
            ItemTemplate.objects.select_for_update()
            .filter(
                pk=int(spec.item_template_id),
                effect_type=ItemTemplate.EffectType.SKILL_BOOK,
            )
            .first()
        )
        skill = Skill.objects.select_for_update().filter(pk=int(spec.skill_id)).first()
        return _VirtualProjectionPools(
            skill_books=_replace_virtual_pool_item(
                pools.skill_books,
                item_id=spec.item_template_id,
                current=template,
            ),
            skills=_replace_virtual_pool_item(
                pools.skills,
                item_id=spec.skill_id,
                current=skill,
            ),
            gear_templates=pools.gear_templates,
            inventory_templates=pools.inventory_templates,
            rare_inventory_quantity_today=pools.rare_inventory_quantity_today,
        )
    if isinstance(spec, InventoryAcquisitionActionSpec) and spec.source == "virtual":
        batch_items = spec.batch_items or ((spec.item_template_id, spec.item_key, spec.daily_caps, spec.quantity),)
        template_ids = tuple(dict.fromkeys(int(entry[0]) for entry in batch_items))
        templates_by_id = {
            int(template.id): template
            for template in ItemTemplate.objects.select_for_update().filter(
                pk__in=template_ids,
                tradeable=True,
            )
        }
        inventory_templates = tuple(
            templates_by_id.get(int(item.id), item)
            for item in pools.inventory_templates
            if int(item.id) not in template_ids or int(item.id) in templates_by_id
        )
        rare_inventory_quantity_today = int(
            BotInventoryDailyCounter.objects.select_for_update()
            .filter(
                category="rare",
                counter_date=timezone.localdate(plan.planned_at),
            )
            .values_list("quantity", flat=True)
            .first()
            or 0
        )
        return _VirtualProjectionPools(
            skill_books=pools.skill_books,
            skills=pools.skills,
            gear_templates=pools.gear_templates,
            inventory_templates=inventory_templates,
            rare_inventory_quantity_today=rare_inventory_quantity_today,
        )
    return pools


def apply_forced_resource_settlement_locked(
    profile: BotProfile,
    manor: Manor,
    *,
    now,
    requested_silver: int,
    requested_grain: int,
) -> ForcedSettlementDecision:
    """在已锁 Profile -> Manor 的事务内执行有界资源结算。"""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("apply_forced_resource_settlement_locked must be called inside transaction.atomic()")
    if profile.engine_version != 2:
        raise profile_store.ProfileStateConflict(f"profile {profile.id} is not V2")
    if profile.manor_id != manor.id:
        raise profile_store.ProfileStateConflict(f"profile {profile.id} does not own manor {manor.id}")

    budget_before = parse_forced_settlement_budget(profile.forced_settlement_daily_budget)
    planned = plan_forced_settlement(
        budget_before,
        now=now,
        silver_capacity=int(manor.silver_capacity or 0),
        grain_capacity=int(manor.grain_capacity or 0),
        requested_silver=requested_silver,
        requested_grain=requested_grain,
    )
    if planned.combined_units == 0:
        profile_store.record_forced_settlement_budget(profile, decision=planned)
        return planned

    credited, _overflow = grant_resources_locked(
        manor,
        {
            ResourceType.SILVER: planned.silver_units,
            ResourceType.GRAIN: planned.grain_units,
        },
        note="虚拟玩家强制资源结算",
        reason=ResourceEvent.Reason.PRODUCE,
        sync_production=False,
    )
    applied = plan_forced_settlement(
        budget_before,
        now=now,
        silver_capacity=int(manor.silver_capacity or 0),
        grain_capacity=int(manor.grain_capacity or 0),
        requested_silver=int(credited.get(ResourceType.SILVER, 0) or 0),
        requested_grain=int(credited.get(ResourceType.GRAIN, 0) or 0),
    )
    profile_store.record_forced_settlement_budget(profile, decision=applied)
    return applied


def _apply_due_resource_production_settlement_locked(
    profile: BotProfile,
    manor: Manor,
    *,
    now: datetime,
    expected_production_deltas: tuple[tuple[str, int], ...],
    expected_decision: ForcedSettlementDecision,
    production_basis: ResourceProductionBasis,
    grain_template: ItemTemplate | None = None,
    grain_template_resolved: bool = False,
) -> ForcedSettlementDecision:
    """按冻结产出和预算在 Profile -> Manor 锁内完成到期结算。"""
    current_production_deltas = tuple(
        sorted(
            (str(resource), int(delta))
            for resource, delta in preview_resource_production(
                manor,
                now=now,
                production_basis=production_basis,
            ).items()
            if int(delta) != 0
        )
    )
    if current_production_deltas != expected_production_deltas:
        raise profile_store.ProfileStateConflict(f"profile {profile.id} resource production changed while planned")

    production_delta_by_resource = dict(current_production_deltas)
    budget_before = parse_forced_settlement_budget(profile.forced_settlement_daily_budget)
    replanned = plan_forced_settlement(
        budget_before,
        now=now,
        silver_capacity=int(manor.silver_capacity or 0),
        grain_capacity=int(manor.grain_capacity or 0),
        requested_silver=max(
            0,
            production_delta_by_resource.get(ResourceType.SILVER, 0),
        ),
        requested_grain=max(
            0,
            production_delta_by_resource.get(ResourceType.GRAIN, 0),
        ),
    )
    if replanned != expected_decision:
        raise profile_store.ProfileStateConflict(f"profile {profile.id} forced settlement changed while planned")

    settled = settle_resource_production_locked(
        manor,
        now=now,
        positive_limits={
            ResourceType.SILVER: replanned.silver_units,
            ResourceType.GRAIN: replanned.grain_units,
        },
        note="虚拟玩家强制资源结算",
        production_basis=production_basis,
        grain_template=grain_template,
        grain_template_resolved=grain_template_resolved,
    )
    applied_positive = (
        max(0, int(settled.get(ResourceType.SILVER, 0))),
        max(0, int(settled.get(ResourceType.GRAIN, 0))),
    )
    if applied_positive != (replanned.silver_units, replanned.grain_units):
        raise profile_store.ProfileStateConflict(
            f"profile {profile.id} forced settlement applied a stale resource delta"
        )
    profile_store.record_forced_settlement_budget(
        profile,
        decision=replanned,
    )
    return replanned


def retire_locked_virtual_player_if_unprotected(profile: BotProfile, *, now: Any) -> bool:
    if is_virtual_profile_arena_protected(profile_id=profile.id, manor_id=profile.manor_id):
        profile_store.defer_profile_retirement(profile, now=now, retry_after=timedelta(hours=1))
        return False
    profile_store.mark_profile_retired(profile, now=now)
    return True


@transaction.atomic
def retire_virtual_player_if_unprotected(profile_id: int, *, now: Any = None) -> bool:
    current_time = now or timezone.now()
    profile = profile_store.lock_maintained_profile(profile_id)
    if profile is None:
        return False
    return retire_locked_virtual_player_if_unprotected(profile, now=current_time)


def _should_reactivate_retired_player(
    *,
    now,
    region: str,
    prestige_band: str,
    profile_id: int,
    chance: float,
) -> bool:
    normalized_chance = max(0.0, min(1.0, float(chance)))
    if normalized_chance <= 0:
        return False
    if normalized_chance >= 1:
        return True
    local_date = timezone.localtime(now).date() if timezone.is_aware(now) else now.date()
    payload = f"{local_date.isoformat()}:{region}:{prestige_band}:{int(profile_id)}".encode()
    value = int.from_bytes(sha256(payload).digest()[:8], "big") / 2**64
    return value < normalized_chance


def reactivate_locked_virtual_player_profile(profile: BotProfile, *, now) -> BotProfile:
    current_time = now
    local_date = timezone.localtime(current_time).date() if timezone.is_aware(current_time) else current_time.date()
    config = load_virtual_player_config()
    lifecycle_rng = random.Random(f"reactivate:{local_date.isoformat()}:{profile.id}")
    _next_growth_at, abandon_at, retire_at = lifecycle_dates(current_time, lifecycle_rng, config)
    profile_store.reactivate_profile(
        profile,
        now=current_time,
        next_growth_at=_next_growth_at,
        abandon_at=abandon_at,
        retire_at=retire_at,
    )
    logger.info(
        "Virtual player reactivated: profile_id=%s manor_id=%s region=%s prestige_band=%s",
        profile.id,
        profile.manor_id,
        profile.manor.region,
        profile_target_prestige_band(profile),
        extra={
            "event": "virtual_player_reactivated",
            "profile_id": profile.id,
            "manor_id": profile.manor_id,
            "region": profile.manor.region,
            "current_prestige_band": profile.current_prestige_band,
            "target_prestige_band": profile_target_prestige_band(profile),
        },
    )
    return profile


def _datetime_payload(value: datetime | None) -> str | None:
    if value is None:
        return None
    if timezone.is_naive(value):
        raise V2MaintenanceError("maintenance timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _domain_availability_snapshot(
    *,
    buildings: Sequence[Building],
    technologies: Sequence[PlayerTechnology],
    guests: Sequence[Guest],
) -> tuple[tuple[str, tuple[datetime, ...]], ...]:
    """Project existing domain timers without creating a second timer source."""

    def ordered_future_or_active_times(values: Iterable[datetime | None]) -> tuple[datetime, ...]:
        return tuple(sorted(value for value in values if value is not None))

    return (
        (
            "building",
            ordered_future_or_active_times(
                building.upgrade_complete_at for building in buildings if bool(building.is_upgrading)
            ),
        ),
        (
            "technology",
            ordered_future_or_active_times(
                technology.upgrade_complete_at for technology in technologies if bool(technology.is_upgrading)
            ),
        ),
        (
            "guest_training",
            ordered_future_or_active_times(
                guest.training_complete_at for guest in guests if guest.training_complete_at is not None
            ),
        ),
    )


def _domain_availability_payload(
    domain_availability: tuple[tuple[str, tuple[datetime, ...]], ...],
) -> dict[str, dict[str, Any]]:
    return {
        domain_name: {
            "busy_count": len(completion_times),
            "completion_at": _datetime_payload(completion_times[0] if completion_times else None),
            "completion_times": [_datetime_payload(value) for value in completion_times],
        }
        for domain_name, completion_times in domain_availability
    }


def _current_domain_availability_for_profile(
    profile: BotProfile,
) -> tuple[tuple[str, tuple[datetime, ...]], ...]:
    """Read the existing domain completion sources after a committed action."""

    def quoted_table(model) -> str:
        return connection.ops.quote_name(model._meta.db_table or "")

    def quoted_column(model, field_name: str) -> str:
        return connection.ops.quote_name(model._meta.get_field(field_name).column or "")

    building_table = quoted_table(Building)
    technology_table = quoted_table(PlayerTechnology)
    guest_table = quoted_table(Guest)
    recruitment_table = quoted_table(GuestRecruitment)
    building_manor = quoted_column(Building, "manor")
    technology_manor = quoted_column(PlayerTechnology, "manor")
    guest_manor = quoted_column(Guest, "manor")
    recruitment_manor = quoted_column(GuestRecruitment, "manor")
    building_upgrading = quoted_column(Building, "is_upgrading")
    technology_upgrading = quoted_column(PlayerTechnology, "is_upgrading")
    guest_completion = quoted_column(Guest, "training_complete_at")
    building_completion = quoted_column(Building, "upgrade_complete_at")
    technology_completion = quoted_column(PlayerTechnology, "upgrade_complete_at")
    recruitment_completion = quoted_column(GuestRecruitment, "complete_at")
    recruitment_status = quoted_column(GuestRecruitment, "status")
    query = " UNION ALL ".join(
        (
            f"SELECT %s AS domain_name, {building_completion} AS completion_at "
            f"FROM {building_table} WHERE {building_manor} = %s AND {building_upgrading} = %s",
            f"SELECT %s AS domain_name, {technology_completion} AS completion_at "
            f"FROM {technology_table} WHERE {technology_manor} = %s AND {technology_upgrading} = %s",
            f"SELECT %s AS domain_name, {guest_completion} AS completion_at "
            f"FROM {guest_table} WHERE {guest_manor} = %s AND {guest_completion} IS NOT NULL",
            f"SELECT %s AS domain_name, {recruitment_completion} AS completion_at "
            f"FROM {recruitment_table} WHERE {recruitment_manor} = %s AND {recruitment_status} = %s",
        )
    )
    params = (
        "building_upgrade",
        int(profile.manor_id),
        True,
        "technology_upgrade",
        int(profile.manor_id),
        True,
        "guest_training",
        int(profile.manor_id),
        "guest_recruitment",
        int(profile.manor_id),
        GuestRecruitment.Status.PENDING,
    )
    by_domain: dict[str, list[datetime]] = defaultdict(list)
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        for domain_name, completion_at in cursor.fetchall():
            if completion_at is None:
                continue
            if timezone.is_naive(completion_at):
                completion_at = completion_at.replace(tzinfo=UTC)
            by_domain[str(domain_name)].append(completion_at)
    return tuple(
        (
            domain_name,
            tuple(sorted(by_domain.get(domain_name, ()))),
        )
        for domain_name in ("building_upgrade", "technology_upgrade", "guest_training", "guest_recruitment")
    )


def _domain_retry_at(
    *,
    profile: BotProfile,
    domain_availability: tuple[tuple[str, tuple[datetime, ...]], ...],
    now: datetime,
) -> datetime | None:
    completion_times = tuple(value for _domain_name, values in domain_availability for value in values if value > now)
    if not completion_times:
        return None
    jitter_minutes = 1 + ((int(profile.growth_seed) + int(profile.maintenance_sequence)) % 3)
    return min(completion_times) + timedelta(minutes=jitter_minutes)


def _strength_payload(summary: StrengthSummary) -> dict[str, Any]:
    return {
        "composite": summary.composite,
        "components": dict(summary.components),
    }


def _reference_selection_payload(
    selection: ReferenceSelection,
) -> dict[str, Any]:
    return {
        "anchor": (None if selection.anchor is None else selection.anchor.business_key),
        "cap": _strength_payload(selection.cap),
        "local_sample_count": selection.local_sample_count,
        "nearest_candidate_keys": list(selection.nearest_candidate_keys),
        "prestige_band": selection.prestige_band,
        "source": selection.source.value,
        "tier": selection.tier.value,
    }


def _intent_payload(intent: DevelopmentIntent | None) -> dict[str, Any] | None:
    if intent is None:
        return None
    return {
        "action_kind": intent.action_kind,
        "business_key": intent.business_key,
        "constraint_violations": list(intent.constraint_violations),
        "source_prestige_band": intent.source_prestige_band,
        "strength_after": _strength_payload(intent.strength_after),
        "strength_before": _strength_payload(intent.strength_before),
        "target_prestige_band": intent.target_prestige_band,
        "utility_score": intent.utility_score,
    }


def _forced_settlement_payload(
    decision: ForcedSettlementDecision,
) -> dict[str, Any]:
    return {
        "budget_after": (None if decision.budget_after is None else decision.budget_after.to_payload()),
        "budget_before": (None if decision.budget_before is None else decision.budget_before.to_payload()),
        "grain_units": decision.grain_units,
        "silver_units": decision.silver_units,
    }


def _salary_quote_payload(quote: SalaryBatchQuote) -> dict[str, Any]:
    return {
        "for_date": quote.for_date.isoformat(),
        "guest_ids": list(quote.guest_ids),
        "total_amount": quote.total_amount,
        "unpaid_guest_ids": list(quote.unpaid_guest_ids),
    }


def _profile_precondition_payload(profile: BotProfile) -> dict[str, Any]:
    return {
        "abandon_at": _datetime_payload(profile.abandon_at),
        "archetype": str(profile.archetype),
        "current_prestige_band": str(profile.current_prestige_band),
        "engine_version": int(profile.engine_version),
        "forced_settlement_daily_budget": profile.forced_settlement_daily_budget,
        "growth_seed": int(profile.growth_seed),
        "id": int(profile.id),
        "last_strength_increase_at": _datetime_payload(profile.last_strength_increase_at),
        "maintenance_sequence": int(profile.maintenance_sequence),
        "next_growth_at": _datetime_payload(profile.next_growth_at),
        "plan_schema_version": int(profile.plan_schema_version),
        "policy_checksum": str(profile.policy_checksum),
        "policy_version": int(profile.policy_version),
        "prestige_band": str(profile.prestige_band),
        "retire_at": _datetime_payload(profile.retire_at),
        "rng_version": int(profile.rng_version),
        "state": str(profile.state),
        "target_prestige_band": str(profile.target_prestige_band),
    }


def _manor_precondition_payload(manor: Manor) -> dict[str, Any]:
    return {
        "grain": int(manor.grain or 0),
        "grain_capacity": int(manor.grain_capacity or 0),
        "id": int(manor.id),
        "prestige": int(manor.prestige or 0),
        "region": str(manor.region),
        "resource_updated_at": _datetime_payload(manor.resource_updated_at),
        "silver": int(manor.silver or 0),
        "silver_capacity": int(manor.silver_capacity or 0),
    }


def _guest_precondition_payload(guest: Guest | None) -> dict[str, Any] | None:
    if guest is None:
        return None
    return {
        "agility": int(guest.agility),
        "attack_bonus": int(guest.attack_bonus),
        "attribute_points": int(guest.attribute_points),
        "current_hp": int(guest.current_hp),
        "defense_bonus": int(guest.defense_bonus),
        "defense_stat": int(guest.defense_stat),
        "experience": int(guest.experience),
        "force": int(guest.force),
        "gear_set_bonus": dict(guest.gear_set_bonus or {}),
        "hp_bonus": int(guest.hp_bonus),
        "id": int(guest.id),
        "intellect": int(guest.intellect),
        "level": int(guest.level),
        "luck": int(guest.luck),
        "manor_id": int(guest.manor_id),
        "status": str(guest.status),
        "template": {
            "archetype": str(guest.template.archetype),
            "attribute_weights": dict(guest.template.attribute_weights or {}),
            "base_hp": int(guest.template.base_hp),
            "growth_range": list(guest.template.growth_range or []),
            "id": int(guest.template_id),
            "rarity": str(guest.template.rarity),
        },
        "training_complete_at": _datetime_payload(guest.training_complete_at),
        "training_remaining_seconds": guest.training_remaining_seconds,
        "training_target_level": int(guest.training_target_level),
        "troop_capacity_bonus": int(guest.troop_capacity_bonus),
    }


def _equipment_target_gear_precondition_payload(
    *,
    target_guest: Guest | None,
    action_spec: MaintenanceActionSpec | None,
    gear_items: tuple[GearItem, ...],
) -> list[dict[str, Any]] | None:
    if target_guest is None or not isinstance(
        action_spec,
        EquipmentEquipActionSpec,
    ):
        return None
    return [
        {
            "guest_id": int(gear.guest_id),
            "id": int(gear.id),
            "inventory_backed": bool(gear.inventory_backed),
            "level": int(gear.level),
            "manor_id": int(gear.manor_id),
            "template": {
                "attack_bonus": int(gear.template.attack_bonus),
                "defense_bonus": int(gear.template.defense_bonus),
                "extra_stats": dict(gear.template.extra_stats or {}),
                "id": int(gear.template_id),
                "key": str(gear.template.key),
                "name": str(gear.template.name),
                "rarity": str(gear.template.rarity),
                "set_bonus": gear.template.set_bonus or {},
                "set_description": str(gear.template.set_description),
                "set_key": str(gear.template.set_key),
                "slot": str(gear.template.slot),
            },
        }
        for gear in gear_items
        if gear.guest_id is not None and int(gear.guest_id) == int(target_guest.id)
    ]


def _equipment_inventory_precondition_payload(
    *,
    action_spec: MaintenanceActionSpec | None,
    warehouse_items: tuple[InventoryItem, ...],
) -> dict[str, Any] | None:
    if not isinstance(action_spec, EquipmentEquipActionSpec):
        return None
    item = next(
        (candidate for candidate in warehouse_items if int(candidate.id) == action_spec.inventory_item_id),
        None,
    )
    if item is None:
        return {
            "id": action_spec.inventory_item_id,
            "missing": True,
        }
    return {
        "id": int(item.id),
        "manor_id": int(item.manor_id),
        "quantity": int(item.quantity),
        "storage_location": str(item.storage_location),
        "template": {
            "effect_payload": dict(item.template.effect_payload or {}),
            "effect_type": str(item.template.effect_type),
            "id": int(item.template_id),
            "key": str(item.template.key),
            "name": str(item.template.name),
            "price": int(item.template.price),
            "rarity": str(item.template.rarity),
        },
    }


def _troop_counts_by_key(manor_id: int) -> tuple[tuple[str, int], ...]:
    return tuple(
        (str(troop_key), int(count))
        for troop_key, count in PlayerTroop.objects.filter(manor_id=manor_id)
        .order_by("troop_template__key")
        .values_list("troop_template__key", "count")
    )


def _maintenance_precondition_digest(
    *,
    profile: BotProfile,
    manor: Manor,
    target_guest: Guest | None,
    trigger_policy: MaintenanceTriggerPolicy,
    planned_at: datetime,
    routing_revision: int,
    development_plan: BotDevelopmentPlan,
    reference_snapshot_version: int,
    control_snapshot_digest: str,
    reference_selection: ReferenceSelection,
    target_reference_selection: ReferenceSelection | None,
    strength_before: StrengthSummary,
    budget_entries: tuple[StrengthBudgetEntry, ...],
    resource_production_deltas: tuple[tuple[str, int], ...],
    forced_settlement_decision: ForcedSettlementDecision,
    salary_quote: SalaryBatchQuote,
    resource_planning_snapshot: ResourcePlanningSnapshot,
    intent: DevelopmentIntent | None,
    action_kind: str,
    target_id: int | None,
    training_levels: int,
    rng_seed: int | None,
    troop_recruitment_quote: TroopRecruitmentQuote | None,
    medicine_quote: MedicineUseQuote | None,
    action_spec: MaintenanceActionSpec | None,
    candidate_assessments: tuple[CandidateAssessment, ...],
    gear_items: tuple[GearItem, ...],
    warehouse_items: tuple[InventoryItem, ...],
    troop_counts: tuple[tuple[str, int], ...],
    calibration_route: CalibrationRoute | None,
    target_calibration_route: CalibrationRoute | None,
    minimum_guest_count: int | None,
    minimum_guest_level: int | None,
    guest_rarity_cap: str | None,
    max_guest_level_step: int | None,
    arena_growth_objective: ArenaGrowthObjective | None,
    next_growth_at_after: datetime | None,
    next_growth_at_after_no_action: datetime | None,
    cycle_covered_action_kinds: tuple[str, ...],
    cycle_high_cost_actions_used: int,
    cycle_budget_state: ArchetypeBudgetState | None,
    cycle_pacing: ArchetypePacing | None,
    candidate_exclusions: tuple[str, ...],
) -> str:
    payload = {
        "action": {
            "action_kind": action_kind,
            "action_spec": maintenance_action_spec_payload(action_spec),
            "equipment_inventory": _equipment_inventory_precondition_payload(
                action_spec=action_spec,
                warehouse_items=warehouse_items,
            ),
            "intent": _intent_payload(intent),
            "rng_seed": rng_seed,
            "target_id": target_id,
            "target_gear": _equipment_target_gear_precondition_payload(
                target_guest=target_guest,
                action_spec=action_spec,
                gear_items=gear_items,
            ),
            "training_levels": training_levels,
            "medicine_quote": (None if medicine_quote is None else medicine_quote.to_payload()),
            "troop_recruitment_quote": (
                None if troop_recruitment_quote is None else troop_recruitment_quote.to_payload()
            ),
        },
        "budget_entries": [entry.to_payload() for entry in budget_entries],
        "candidate_assessments": [assessment.summary_payload() for assessment in candidate_assessments],
        "calibration_route": (None if calibration_route is None else calibration_route.to_payload()),
        "target_calibration_route": (
            None if target_calibration_route is None else target_calibration_route.to_payload()
        ),
        "development_plan": development_plan.to_payload(),
        "manor": _manor_precondition_payload(manor),
        "mandatory_settlement": {
            "forced_resource": _forced_settlement_payload(forced_settlement_decision),
            "resource_production_deltas": dict(resource_production_deltas),
            "resource_planning_snapshot": resource_planning_snapshot.to_payload(),
            "salary": _salary_quote_payload(salary_quote),
        },
        "planner_constraints": {
            "arena_growth_objective": (None if arena_growth_objective is None else arena_growth_objective.to_payload()),
            "cycle_covered_action_kinds": list(cycle_covered_action_kinds),
            "cycle_high_cost_actions_used": int(cycle_high_cost_actions_used),
            "cycle_budget_state": (None if cycle_budget_state is None else cycle_budget_state.to_payload()),
            "cycle_pacing": None if cycle_pacing is None else cycle_pacing.to_payload(),
            "candidate_exclusions": list(candidate_exclusions),
            "recruitment_rarity_cap": guest_rarity_cap,
            "max_guest_level_step": max_guest_level_step,
            "minimum_guest_count": minimum_guest_count,
            "minimum_guest_level": minimum_guest_level,
        },
        "planned_at": _datetime_payload(planned_at),
        "profile": _profile_precondition_payload(profile),
        "reference": {
            "control_snapshot_digest": control_snapshot_digest,
            "selection": _reference_selection_payload(reference_selection),
            "snapshot_version": reference_snapshot_version,
            "target_selection": (
                None if target_reference_selection is None else _reference_selection_payload(target_reference_selection)
            ),
        },
        "routing_revision": routing_revision,
        "schedule": {
            "next_growth_at_after": _datetime_payload(next_growth_at_after),
            "next_growth_at_after_no_action": _datetime_payload(next_growth_at_after_no_action),
            "requires_due": trigger_policy.requires_due,
            "schedule_disposition": trigger_policy.schedule_disposition.value,
            "trigger": trigger_policy.trigger.value,
        },
        "strength_before": _strength_payload(strength_before),
        "target_guest": _guest_precondition_payload(target_guest),
        "troop_counts": dict(troop_counts),
    }
    return sha256(canonical_json_bytes(payload)).hexdigest()


def _guest_arena_power(
    guest: Guest,
    *,
    force: int,
    intellect: int,
    defense: int,
    agility: int,
) -> int:
    return calculate_guest_arena_power(
        force=force,
        intellect=intellect,
        defense=defense,
        agility=agility,
        hp_bonus=int(guest.hp_bonus),
        archetype=str(guest.template.archetype),
        base_hp=int(guest.template.base_hp),
    )


def _arena_growth_priority_guests(
    guests: tuple[Guest, ...],
    objective: ArenaGrowthObjective | None,
) -> tuple[Guest, ...]:
    if objective is None or not guests:
        return guests
    target_count = min(
        len(guests),
        max(objective.critical_guest_count, objective.preferred_guest_count),
    )
    if target_count <= 0:
        return guests
    return tuple(
        sorted(
            guests,
            key=lambda guest: (
                -_guest_arena_power(
                    guest,
                    force=int(guest.force),
                    intellect=int(guest.intellect),
                    defense=int(guest.defense_stat),
                    agility=int(guest.agility),
                ),
                int(guest.id),
            ),
        )[:target_count]
    )


def _build_locked_snapshot_strength(
    *,
    manor: Manor,
    guests: tuple[Guest, ...],
    buildings: tuple[Building, ...],
    troop_total: int,
) -> StrengthSummary:
    return build_strength_summary(
        prestige=int(manor.prestige or 0),
        core_building_level=max(
            (
                int(building.level or 0)
                for building in buildings
                if str(building.building_type.key) in CORE_BUILDING_KEYS
            ),
            default=0,
        ),
        guest_count=len(guests),
        max_guest_level=max((int(guest.level or 0) for guest in guests), default=0),
        arena_lineup_power=sum(
            _guest_arena_power(
                guest,
                force=int(guest.force or 0),
                intellect=int(guest.intellect or 0),
                defense=int(guest.defense_stat or 0),
                agility=int(guest.agility or 0),
            )
            for guest in guests
        ),
        troop_total=troop_total,
    )


def _normalize_optional_non_negative_int(
    value: int | None,
    *,
    field: str,
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer or None")
    return value


def _normalize_optional_positive_int(
    value: int | None,
    *,
    field: str,
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer or None")
    return value


def _maintenance_context(profile: BotProfile) -> RandomContext:
    return RandomContext(
        rng_version=int(profile.rng_version),
        growth_seed=int(profile.growth_seed),
        engine_version=int(profile.engine_version),
        plan_schema_version=int(profile.plan_schema_version),
        policy_version=int(profile.policy_version),
        maintenance_sequence=int(profile.maintenance_sequence),
    )


def _maintenance_reference_for_band(
    *,
    config: VirtualPlayerV2Config,
    release: BotPolicyRelease,
    profile: BotProfile,
    routing: RuntimeRoutingSnapshot,
    context: RandomContext,
    region: str,
    prestige_band: str,
    now: datetime,
    manor_strength: StrengthSummary | None = None,
    expected_control_digest: str | None = None,
) -> tuple[int, ReferenceSelection, CalibrationRoute | None, str]:
    band = next(
        (candidate for candidate in config.bands if candidate.name == prestige_band),
        None,
    )
    if band is None:
        raise V2MaintenanceError(f"maintenance reference uses an unknown prestige band: {prestige_band}")
    if int(profile.policy_version) == 2:
        # Policy 2 consumes only the daily aggregate control row.  In
        # particular, do not call the legacy starter/reference/calibration
        # chain here: a missing or stale control row is handled by the
        # resolver's fixed safe fallback and never becomes a Gate D2 pause.
        resolved_manor_strength = manor_strength or load_manor_strength_summary(manor_id=int(profile.manor_id))
        snapshot_version, selection, control_digest = growth_control_reference_selection(
            manor_strength=resolved_manor_strength,
            context=context,
            region=region,
            prestige_band=band.name,
            now=now,
            expected_digest=expected_control_digest,
        )
        return snapshot_version, selection, None, control_digest
    raise V2MaintenanceError("only policy 2 growth-control references are supported")


def _resolve_maintenance_policy(
    *,
    profile: BotProfile,
    manor: Manor,
    routing: RuntimeRoutingSnapshot,
    context: RandomContext,
    now: datetime,
    policy_release: BotPolicyRelease | None = None,
    manor_strength: StrengthSummary | None = None,
    expected_control_digest: str | None = None,
) -> tuple[
    BotDevelopmentPlan,
    PrestigeBandGrowthPolicy,
    int,
    ReferenceSelection,
    CalibrationRoute | None,
    str,
    VirtualPlayerV2Config,
    BotPolicyRelease,
]:
    config = load_virtual_player_v2_config()
    if config is None:
        raise V2MaintenanceError("bot_development_v2 is not configured")
    if int(profile.policy_version) != 2:
        raise V2MaintenanceError("only policy 2 is supported by the active V2 maintenance engine")
    if routing.maintenance_mode is not MaintenanceMode.V2_ACTIVE:
        raise V2MaintenanceError("V2 maintenance requires maintenance_mode=v2_active")
    if (
        config.engine_version != int(profile.engine_version)
        or config.rng_version != int(profile.rng_version)
        or config.plan_schema_version != int(profile.plan_schema_version)
    ):
        raise V2MaintenanceError("V2 profile identity does not match the configured runtime")
    configured_policy = config.policy(int(profile.policy_version))
    if configured_policy.checksum != str(profile.policy_checksum):
        raise V2MaintenanceError("V2 profile policy checksum does not match configuration")
    release = policy_release
    if release is None:
        release = get_policy_release(
            version=int(profile.policy_version),
            expected_checksum=str(profile.policy_checksum),
        )
    elif (
        int(release.version) != int(profile.policy_version)
        or release.checksum != str(profile.policy_checksum)
        or policy_checksum(release.payload) != release.checksum
    ):
        raise PolicyRegistryError("cached policy release does not match profile identity")
    expected_action_slots = 16
    if int(release.payload.get("max_development_actions") or 0) != expected_action_slots:
        raise V2MaintenanceError(
            f"policy {int(profile.policy_version)} requires max_development_actions={expected_action_slots}"
        )
    development_plan = parse_development_plan(
        profile.development_profile,
        catalog=development_plan_catalog_v1(),
    )
    if development_plan.schema_version != int(profile.plan_schema_version):
        raise V2MaintenanceError("V2 development profile schema does not match its identity")
    band = config.band_for_prestige(int(manor.prestige or 0))
    if band.name != str(profile.current_prestige_band):
        raise V2MaintenanceError("persisted current prestige band does not match Manor prestige")
    growth_policy = parse_prestige_band_growth_policy(release.payload.get("prestige_band_growth"))
    snapshot_version, reference_selection, calibration_route, control_snapshot_digest = _maintenance_reference_for_band(
        config=config,
        release=release,
        profile=profile,
        routing=routing,
        context=context,
        region=str(manor.region),
        prestige_band=band.name,
        now=now,
        manor_strength=manor_strength,
        expected_control_digest=expected_control_digest,
    )
    return (
        development_plan,
        growth_policy,
        snapshot_version,
        reference_selection,
        calibration_route,
        control_snapshot_digest,
        config,
        release,
    )


def _healable_guests(guests: tuple[Guest, ...]) -> tuple[Guest, ...]:
    return tuple(
        guest
        for guest in guests
        if guest.status in {GuestStatus.IDLE, GuestStatus.INJURED} and int(guest.current_hp) < int(guest.max_hp)
    )


def _available_medicine_items(manor_id: int) -> tuple[InventoryItem, ...]:
    return tuple(
        InventoryItem.objects.filter(
            manor_id=manor_id,
            quantity__gt=0,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            template__effect_type=ItemTemplate.EffectType.MEDICINE,
        )
        .select_related("template")
        .order_by("template__key", "id")
    )


def _available_warehouse_items(manor_id: int) -> tuple[InventoryItem, ...]:
    return tuple(
        InventoryItem.objects.filter(
            manor_id=manor_id,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .filter(Q(quantity__gt=0) | Q(template__key=GRAIN_ITEM_KEY))
        .select_related("template")
        .order_by("template__key", "id")
    )


def _warehouse_grain_quantity_from_snapshot(
    manor: Manor,
    warehouse_items: tuple[InventoryItem, ...],
) -> int:
    """Read the already-loaded grain row without mutating the Manor instance."""
    grain_item = next((item for item in warehouse_items if item.template.key == GRAIN_ITEM_KEY), None)
    quantity = (
        max(0, int(grain_item.quantity or 0))
        if grain_item is not None
        else max(0, int(getattr(manor, "grain", 0) or 0))
    )
    # The query above deliberately includes zero-quantity grain rows, so a missing
    # row is a complete read of the ledger state rather than an assumption.
    return quantity


def _equipped_gear_items(
    manor_id: int,
    *,
    guest_ids: tuple[int, ...],
) -> tuple[GearItem, ...]:
    if not guest_ids:
        return ()
    return tuple(
        GearItem.objects.filter(
            manor_id=manor_id,
            guest_id__in=guest_ids,
        )
        .select_related("template")
        .order_by("guest_id", "id")
    )


def _guest_skills_for_guests(guests: tuple[Guest, ...]) -> tuple[GuestSkill, ...]:
    guest_ids = tuple(int(guest.id) for guest in guests)
    if not guest_ids:
        return ()
    return tuple(
        GuestSkill.objects.filter(guest_id__in=guest_ids).select_related("skill").order_by("guest_id", "skill_id")
    )


def _skills_for_warehouse_items(
    warehouse_items: tuple[InventoryItem, ...],
) -> tuple[Skill, ...]:
    skill_keys = tuple(
        dict.fromkeys(
            skill_key.strip()
            for item in warehouse_items
            if item.template.effect_type == ItemTemplate.EffectType.SKILL_BOOK
            and isinstance(item.template.effect_payload, dict)
            and isinstance(
                skill_key := item.template.effect_payload.get("skill_key"),
                str,
            )
            and skill_key.strip()
        )
    )
    if not skill_keys:
        return ()
    return tuple(Skill.objects.filter(key__in=skill_keys).order_by("key"))


def _inventory_template_query_keys(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        dict.fromkeys(
            entry.strip()
            for entry in value
            if isinstance(entry, str) and entry.strip() and entry.strip() != GRAIN_ITEM_KEY
        )
    )


def _inventory_templates_for_profile(profile: BotProfile) -> tuple[ItemTemplate, ...]:
    keys = _inventory_template_query_keys(profile.inventory_template_keys)
    if not keys:
        return ()
    return tuple(ItemTemplate.objects.filter(key__in=keys).order_by("key"))


def _lock_virtual_inventory_templates_and_items(
    *,
    manor: Manor,
    template_ids: tuple[int, ...],
) -> tuple[dict[int, ItemTemplate], dict[int, InventoryItem]]:
    """Lock inventory templates and their warehouse rows in one SQL round trip."""

    if not template_ids:
        return {}, {}

    template_table = connection.ops.quote_name(ItemTemplate._meta.db_table or "")
    inventory_table = connection.ops.quote_name(InventoryItem._meta.db_table or "")

    def quoted_column(model, field_name: str) -> str:
        return connection.ops.quote_name(model._meta.get_field(field_name).column or "")

    template_id = quoted_column(ItemTemplate, "id")
    template_tradeable = quoted_column(ItemTemplate, "tradeable")
    inventory_id = quoted_column(InventoryItem, "id")
    inventory_template_id = quoted_column(InventoryItem, "template")
    inventory_manor_id = quoted_column(InventoryItem, "manor")
    inventory_storage_location = quoted_column(InventoryItem, "storage_location")
    inventory_quantity = quoted_column(InventoryItem, "quantity")
    inventory_updated_at = quoted_column(InventoryItem, "updated_at")
    placeholders = ", ".join(["%s"] * len(template_ids))
    lock_clause = " FOR UPDATE" if connection.features.has_select_for_update else ""
    query = (
        f"SELECT template.*, "
        f"inventory.{inventory_id} AS virtual_inventory_id, "
        f"inventory.{inventory_quantity} AS virtual_inventory_quantity, "
        f"inventory.{inventory_updated_at} AS virtual_inventory_updated_at "
        f"FROM {template_table} AS template "
        f"LEFT JOIN {inventory_table} AS inventory "
        f"ON inventory.{inventory_template_id} = template.{template_id} "
        f"AND inventory.{inventory_manor_id} = %s "
        f"AND inventory.{inventory_storage_location} = %s "
        f"WHERE template.{template_id} IN ({placeholders}) "
        f"AND template.{template_tradeable} = %s"
        f"{lock_clause}"
    )
    params = (
        int(manor.id),
        InventoryItem.StorageLocation.WAREHOUSE,
        *template_ids,
        True,
    )
    templates_by_id: dict[int, ItemTemplate] = {}
    existing_by_template_id: dict[int, InventoryItem] = {}
    for template in ItemTemplate.objects.raw(query, params):
        template_id_value = int(template.id)
        templates_by_id[template_id_value] = template
        existing_id = getattr(template, "virtual_inventory_id", None)
        if existing_id is None:
            continue
        updated_at_value = getattr(template, "virtual_inventory_updated_at", None)
        updated_at = updated_at_value if isinstance(updated_at_value, datetime) else timezone.now()
        existing_by_template_id[template_id_value] = InventoryItem(
            id=int(existing_id),
            manor_id=int(manor.id),
            template_id=template_id_value,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            quantity=int(getattr(template, "virtual_inventory_quantity", 0) or 0),
            updated_at=updated_at,
        )
    return templates_by_id, existing_by_template_id


def _apply_inventory_acquisition_locked(
    manor: Manor,
    spec: InventoryAcquisitionActionSpec,
    *,
    now: datetime,
) -> InventoryItem:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("_apply_inventory_acquisition_locked must run inside transaction.atomic()")
    config = load_virtual_player_config()
    batch_items = spec.batch_items or ((spec.item_template_id, spec.item_key, spec.daily_caps, spec.quantity),)
    template_ids = tuple(dict.fromkeys(int(entry[0]) for entry in batch_items))
    templates_by_id, existing_by_template_id = _lock_virtual_inventory_templates_and_items(
        manor=manor,
        template_ids=template_ids,
    )
    pending_new_by_template_id: dict[int, InventoryItem] = {}
    updated_existing_by_template_id: dict[int, InventoryItem] = {}
    first_template_id: int | None = None
    for item_template_id, item_key, expected_caps, requested_quantity in batch_items:
        template = templates_by_id.get(int(item_template_id))
        if template is None or template.key != item_key or item_key == "grain":
            if spec.batch_items:
                continue
            raise InventoryAcquisitionUnavailable("inventory acquisition template is no longer eligible")
        if (
            inventory_daily_cap_limits(
                template,
                config=config,
            )
            != expected_caps
        ):
            if spec.batch_items:
                continue
            raise InventoryAcquisitionUnavailable("inventory acquisition cap inputs changed")
        existing = existing_by_template_id.get(int(template.id))
        if existing is not None and int(existing.quantity or 0) > 0:
            if spec.batch_items:
                continue
            raise InventoryAcquisitionUnavailable("inventory acquisition target is already stocked")
        allowed = apply_inventory_daily_caps(
            template,
            quantity=requested_quantity,
            config=config,
            now=now,
        )
        if allowed <= 0:
            if spec.batch_items:
                continue
            raise InventoryAcquisitionUnavailable("inventory acquisition daily cap is exhausted")
        if existing is None:
            pending = InventoryItem(
                manor=manor,
                template=template,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
                quantity=allowed,
            )
            pending_new_by_template_id[int(template.id)] = pending
            # Keep duplicate entries in one batch replay-safe: once a template
            # has a positive planned quantity, the later duplicate is skipped
            # exactly as it was when each item was saved immediately.
            existing_by_template_id[int(template.id)] = pending
        else:
            existing.quantity = int(existing.quantity or 0) + allowed
            updated_existing_by_template_id[int(template.id)] = existing
        if first_template_id is None:
            first_template_id = int(template.id)

    pending_items = list(pending_new_by_template_id.values())
    if pending_items:
        try:
            # All new warehouse rows are materialized as one batch inside the
            # caller's Manor transaction.  The savepoint only isolates a
            # legacy concurrent insert so the outer cap reservations remain
            # atomic with the inventory result.
            with transaction.atomic():
                InventoryItem.objects.bulk_create(pending_items)
        except IntegrityError:
            locked_conflicts = {
                int(item.template_id): item
                for item in InventoryItem.objects.select_for_update()
                .select_related("template")
                .filter(
                    manor_id=int(manor.id),
                    template_id__in=tuple(pending_new_by_template_id),
                    storage_location=InventoryItem.StorageLocation.WAREHOUSE,
                )
            }
            missing_template_ids = set(pending_new_by_template_id) - set(locked_conflicts)
            if missing_template_ids:
                raise
            for template_id, pending in pending_new_by_template_id.items():
                conflict = locked_conflicts[template_id]
                conflict.quantity = int(conflict.quantity or 0) + int(pending.quantity or 0)
                updated_existing_by_template_id[template_id] = conflict
                existing_by_template_id[template_id] = conflict
            pending_items = []
    if updated_existing_by_template_id:
        InventoryItem.objects.bulk_update(
            list(updated_existing_by_template_id.values()),
            ["quantity", "updated_at"],
        )
    if first_template_id is None:
        raise InventoryAcquisitionUnavailable("inventory acquisition batch has no eligible candidate")
    first_created = existing_by_template_id.get(first_template_id)
    if first_created is None:
        raise InventoryAcquisitionUnavailable("inventory acquisition batch result disappeared")
    return first_created


def _guest_investment_tiers(guests: tuple[Guest, ...]) -> dict[int, str]:
    if not guests:
        return {}
    power_by_id = {
        int(guest.id): _guest_arena_power(
            guest,
            force=int(guest.force),
            intellect=int(guest.intellect),
            defense=int(guest.defense_stat),
            agility=int(guest.agility),
        )
        for guest in guests
    }
    ordered = sorted(
        guests,
        key=lambda guest: (-power_by_id[int(guest.id)], int(guest.id)),
    )
    core_count = max(1, math.ceil(len(ordered) * 0.2))
    secondary_count = max(0, math.ceil(len(ordered) * 0.35))
    return {
        int(guest.id): (
            "core" if index < core_count else "secondary" if index < core_count + secondary_count else "bench"
        )
        for index, guest in enumerate(ordered)
    }


def _guest_healing_candidate(
    *,
    manor: Manor,
    prestige_band: str,
    strength_before: StrengthSummary,
    context: RandomContext,
    guests: tuple[Guest, ...],
    medicine_items: tuple[InventoryItem, ...],
) -> tuple[DevelopmentIntent | None, Guest | None, MedicineUseQuote | None]:
    healable = _healable_guests(guests)
    if not healable or not medicine_items:
        return None, None, None
    investment_tiers = _guest_investment_tiers(guests)
    candidates: list[GuestHealingCandidate] = []
    metadata: dict[str, tuple[Guest, MedicineUseQuote]] = {}
    for guest in healable:
        quotes: list[MedicineUseQuote] = []
        for item in medicine_items:
            try:
                quotes.append(quote_medicine_item_for_guest(manor, guest, item))
            except (
                GuestFullHpError,
                GuestItemConfigurationError,
                GuestItemOwnershipError,
                GuestNotIdleError,
                GuestOwnershipError,
                InsufficientStockError,
            ):
                continue
        if not quotes:
            continue
        quote = min(
            quotes,
            key=lambda candidate_quote: (
                -int(candidate_quote.injury_cured),
                -candidate_quote.healed,
                candidate_quote.heal_amount - candidate_quote.healed,
                candidate_quote.item_key,
                candidate_quote.item_id,
            ),
        )
        candidate = GuestHealingCandidate(
            guest_id=int(guest.id),
            item_id=quote.item_id,
            item_key=quote.item_key,
            investment_tier=investment_tiers[int(guest.id)],
            is_injured=guest.status == GuestStatus.INJURED,
            current_hp=int(guest.current_hp),
            max_hp=int(guest.max_hp),
        )
        candidates.append(candidate)
        metadata[candidate.business_key] = (guest, quote)
    selected = select_guest_healing_candidate(candidates, context=context)
    if selected is None:
        return None, None, None
    intent = project_guest_healing_development_intent(
        candidate=selected,
        prestige_band=prestige_band,
        strength_before=strength_before,
    )
    guest, quote = metadata[selected.business_key]
    return intent, guest, quote


_QUANTITY_PHASE_REPEATABLE_RARITIES = (GuestRarity.BLACK, GuestRarity.GRAY)
_GUEST_RARITY_RANK = {rarity: rank for rank, rarity in enumerate(GUEST_RARITY_ORDER)}


def _normalize_recruitment_rarity_cap(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("recruitment rarity cap must be a string or None")
    normalized = value.strip()
    if normalized not in _GUEST_RARITY_RANK:
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.PAUSED,
            "invalid_recruitment_rarity_cap",
        )
    return normalized


def _growth_parameters_from_objective(
    arena_growth_objective: ArenaGrowthObjective | None,
    *,
    minimum_guest_count: int | None,
    minimum_guest_level: int | None,
    guest_rarity_cap: str | None,
    max_guest_level_step: int | None,
) -> tuple[int | None, int | None, str | None, int | None]:
    if arena_growth_objective is None:
        return (
            minimum_guest_count,
            minimum_guest_level,
            guest_rarity_cap,
            max_guest_level_step,
        )
    if not isinstance(arena_growth_objective, ArenaGrowthObjective):
        raise ValueError("arena_growth_objective must be an ArenaGrowthObjective or None")
    aliases = (
        ("minimum_guest_count", minimum_guest_count, arena_growth_objective.critical_guest_count),
        ("minimum_guest_level", minimum_guest_level, arena_growth_objective.minimum_guest_level),
        ("guest_rarity_cap", guest_rarity_cap, arena_growth_objective.recruitment_rarity_cap),
        ("max_guest_level_step", max_guest_level_step, arena_growth_objective.max_guest_level_step),
    )
    conflicts = [field for field, alias, canonical in aliases if alias is not None and alias != canonical]
    if conflicts:
        raise ValueError("arena growth objective conflicts with compatibility aliases: " + ", ".join(conflicts))
    return (
        arena_growth_objective.critical_guest_count,
        arena_growth_objective.minimum_guest_level,
        arena_growth_objective.recruitment_rarity_cap,
        arena_growth_objective.max_guest_level_step,
    )


def _quantity_phase_guest_template(
    *,
    manor: Manor,
    development_plan: BotDevelopmentPlan,
    context: RandomContext,
    current_guest_count: int,
    recruitment_rarity_cap: str | None,
) -> tuple[GuestTemplate, bool] | None:
    """Select a low-rarity template without turning quantity growth into quality growth."""

    base_queryset = GuestTemplate.objects.filter(recruitable=True, is_hermit=False)
    if recruitment_rarity_cap is not None:
        cap_rank = _GUEST_RARITY_RANK[recruitment_rarity_cap]
        base_queryset = base_queryset.filter(
            rarity__in=GUEST_RARITY_ORDER[: cap_rank + 1],
        )
    base_queryset = base_queryset.order_by(
        "rarity",
        "key",
        "id",
    )
    repeatable = tuple(base_queryset.filter(rarity__in=_QUANTITY_PHASE_REPEATABLE_RARITIES))
    candidates = repeatable
    is_repeatable = True
    if not candidates:
        owned_template_ids = tuple(manor.guests.values_list("template_id", flat=True))
        candidates = tuple(base_queryset.exclude(id__in=owned_template_ids))
        is_repeatable = False
    if not candidates:
        return None

    preferred = tuple(
        candidate
        for candidate in candidates
        if str(candidate.archetype) in {str(value) for value in development_plan.preferred_guest_archetypes}
    )
    ranked = preferred or candidates
    index = context.bucket(
        domain="roster",
        discriminator={
            "current_guest_count": int(current_guest_count),
            "purpose": "quantity-phase-template",
            "template_keys": [candidate.key for candidate in ranked],
        },
        bucket_count=len(ranked),
    )
    return ranked[index], is_repeatable


def _guest_recruitment_candidate(
    *,
    manor: Manor,
    prestige_band: str,
    strength_before: StrengthSummary,
    context: RandomContext,
    development_plan: BotDevelopmentPlan,
    minimum_guest_count: int | None,
    recruitment_rarity_cap: str | None,
    guests: tuple[Guest, ...],
    max_quantity: int | None = None,
    allow_instant_recruitment: bool = False,
) -> tuple[
    DevelopmentIntent | None,
    GuestRecruitmentActionSpec | None,
    tuple[int, ...],
]:
    """Build a bounded V2 roster-expansion action for the quantity phase."""

    if not allow_instant_recruitment or minimum_guest_count is None:
        return None, None, ()
    current_guest_count = len(guests)
    missing = max(0, int(minimum_guest_count) - current_guest_count)
    if missing <= 0:
        return None, None, ()
    remaining_capacity = remaining_guest_capacity(manor)
    if remaining_capacity <= 0:
        return None, None, ()
    selected = _quantity_phase_guest_template(
        manor=manor,
        development_plan=development_plan,
        context=context,
        current_guest_count=current_guest_count,
        recruitment_rarity_cap=recruitment_rarity_cap,
    )
    if selected is None:
        return None, None, ()
    template, is_repeatable = selected
    quantity = min(
        2 if max_quantity is None else max(1, int(max_quantity)),
        missing,
        remaining_capacity,
    )
    if not is_repeatable:
        quantity = 1
    rng_seed = context.seed(
        domain="roster",
        discriminator={
            "current_guest_count": current_guest_count,
            "minimum_guest_count": int(minimum_guest_count),
            "purpose": "quantity-phase-recruitment",
            "quantity": quantity,
            "template_id": int(template.id),
        },
    )
    projected_guests = list(guests)
    added_power = 0
    projected_guest_powers: list[int] = []
    for ordinal in range(quantity):
        rng = random.Random(rng_seed + ordinal)
        projected = create_guest_from_template(
            manor=manor,
            template=template,
            rarity=str(template.rarity),
            archetype=str(template.archetype),
            custom_name=build_recruitment_custom_name(template, rng),
            rng=rng,
            grant_skills=False,
            save=False,
        )
        projected_guests.append(projected)
        projected_power = _guest_arena_power(
            projected,
            force=int(projected.force),
            intellect=int(projected.intellect),
            defense=int(projected.defense_stat),
            agility=int(projected.agility),
        )
        projected_guest_powers.append(projected_power)
        added_power += projected_power
    components_after = dict(strength_before.components)
    components_after["guest_count"] = int(components_after["guest_count"]) + quantity
    components_after["max_guest_level"] = max(int(components_after["max_guest_level"]), 1)
    components_after["arena_lineup_power"] = int(components_after["arena_lineup_power"]) + added_power
    strength_after = StrengthSummary(
        composite=float(components_after["arena_lineup_power"] + 2 * components_after["troop_total"]),
        components=components_after,
    )
    spec = GuestRecruitmentActionSpec(
        template_id=int(template.id),
        template_key=str(template.key),
        rarity=str(template.rarity),
        archetype=str(template.archetype),
        quantity=quantity,
        rng_seed=rng_seed,
    )
    intent = project_maintenance_action_intent(
        spec=spec,
        source_prestige_band=prestige_band,
        target_prestige_band=prestige_band,
        strength_before=strength_before,
        strength_after=strength_after,
        utility_score=max(1.0, float(added_power) / max(1, quantity)),
    )
    return intent, spec, tuple(projected_guest_powers)


def _training_candidates(
    *,
    manor: Manor,
    prestige_band: str,
    strength_before: StrengthSummary,
    context: RandomContext,
    development_plan: BotDevelopmentPlan,
    minimum_guest_count: int | None,
    minimum_guest_level: int | None,
    max_guest_level_step: int | None,
    max_projected_growth_bps: int | None = None,
    guests: tuple[Guest, ...] | None = None,
    defer_completion: bool = False,
    free_subsidy: bool = False,
) -> tuple[
    tuple[DevelopmentIntent, ...],
    dict[str, tuple[Guest, int, int, tuple[tuple[str, int], ...]]],
]:
    guest_count = int(strength_before.components.get("guest_count", 0))
    if minimum_guest_count is not None and guest_count < minimum_guest_count:
        return (), {}
    candidate_guests = (
        [
            guest
            for guest in guests
            if guest.status == GuestStatus.IDLE
            and (free_subsidy or (guest.training_complete_at is None and guest.training_remaining_seconds is None))
            and int(guest.current_hp) >= int(guest.max_hp)
        ]
        if guests is not None
        else list(
            Guest.objects.filter(
                manor_id=manor.id,
                status=GuestStatus.IDLE,
                training_complete_at__isnull=True,
            )
            .select_related("template")
            .order_by("id")
        )
    )
    candidate_guests = [guest for guest in candidate_guests if int(guest.current_hp) >= int(guest.max_hp)]
    power_before_by_id = {
        int(guest.id): _guest_arena_power(
            guest,
            force=int(guest.force),
            intellect=int(guest.intellect),
            defense=int(guest.defense_stat),
            agility=int(guest.agility),
        )
        for guest in candidate_guests
    }
    investment_order = sorted(
        candidate_guests,
        key=lambda guest: (-power_before_by_id[int(guest.id)], int(guest.id)),
    )
    investment_rank = {int(guest.id): index for index, guest in enumerate(investment_order)}
    investment_weights = (0.60, 0.25, 0.10, 0.05)
    candidates: list[DevelopmentIntent] = []
    metadata: dict[str, tuple[Guest, int, int, tuple[tuple[str, int], ...]]] = {}
    for guest in candidate_guests:
        current_level = int(guest.level)
        if minimum_guest_level is not None:
            target_gap = max(0, minimum_guest_level - current_level)
            if target_gap == 0:
                continue
        else:
            target_gap = 1
        max_levels = min(
            target_gap,
            (1 if defer_completion else (max_guest_level_step if max_guest_level_step is not None else 1)),
        )
        power_before = power_before_by_id[int(guest.id)]
        rank = investment_rank[int(guest.id)]
        investment_weight = investment_weights[min(rank, len(investment_weights) - 1)]
        role_importance = 1.0 + (
            development_plan.roster_focus
            if str(guest.template.archetype) in development_plan.preferred_guest_archetypes
            else 0.0
        )
        selected: tuple[DevelopmentIntent, int, int, tuple[tuple[str, int], ...]] | None = None
        smallest_projected: (
            tuple[
                DevelopmentIntent,
                int,
                int,
                tuple[tuple[str, int], ...],
            ]
            | None
        ) = None
        for levels in range(max_levels, 0, -1):
            rng_seed = context.seed(
                domain="training",
                discriminator={
                    "guest_id": int(guest.id),
                    "levels": levels,
                },
            )
            try:
                completion = project_training_completion(
                    guest,
                    levels=levels,
                    rng=random.Random(rng_seed),
                    allow_active_training=free_subsidy,
                )
            except (
                GuestMaxLevelError,
                GuestNotIdleError,
                GuestTrainingInProgressError,
            ):
                continue
            power_after = _guest_arena_power(
                guest,
                force=completion.force,
                intellect=completion.intellect,
                defense=completion.defense_stat,
                agility=completion.agility,
            )
            normalized_cost = max(
                1,
                sum(int(value) for value in completion.quote.resource_cost.values()),
            )
            utility_score = (
                investment_weight
                * max(1, target_gap)
                * role_importance
                * (
                    max(1, power_after - power_before)
                    if not defer_completion
                    else max(1, int(completion.quote.resource_cost.get(ResourceType.SILVER, 0)))
                )
                / normalized_cost
            )
            intent = project_training_development_intent(
                guest_id=int(guest.id),
                prestige_band=prestige_band,
                strength_before=strength_before,
                guest_level_after=(int(guest.level) if defer_completion else completion.level),
                guest_arena_power_before=power_before,
                guest_arena_power_after=(power_before if defer_completion else power_after),
                utility_score=utility_score,
            )
            resource_costs = tuple(
                sorted(
                    (str(resource), int(amount))
                    for resource, amount in completion.quote.resource_cost.items()
                    if int(amount) > 0
                )
            )
            if free_subsidy:
                resource_costs = ()
            smallest_projected = (intent, levels, rng_seed, resource_costs)
            if (
                max_projected_growth_bps is not None
                and calculate_positive_growth_bps(
                    pre_score=intent.strength_before.composite,
                    post_score=intent.strength_after.composite,
                )
                > max_projected_growth_bps
            ):
                continue
            selected = (intent, levels, rng_seed, resource_costs)
            break
        if selected is None:
            # Preserve the smallest concrete training intent for assessment.
            # This lets the planner retain the real cap reason when even a
            # one-level step is blocked, instead of degrading to a generic
            # no-candidate/domain result.
            selected = smallest_projected
        if selected is None:
            continue
        intent, levels, rng_seed, resource_costs = selected
        candidates.append(intent)
        metadata[intent.business_key] = (guest, levels, rng_seed, resource_costs)
    return tuple(candidates), metadata


def _building_upgrade_quotes(
    *,
    manor: Manor,
    development_plan: BotDevelopmentPlan,
    buildings: tuple[Building, ...] | None = None,
    technology_levels: dict[str, int] | None = None,
    building_keys: tuple[str, ...] | None = None,
) -> tuple[BuildingUpgradeQuote, ...]:
    resolved_building_keys = (
        development_plan.building_focuses
        if building_keys is None
        else tuple(dict.fromkeys(str(key).strip() for key in building_keys if str(key).strip()))
    )
    building_snapshot = (
        tuple(
            Building.objects.filter(
                manor_id=manor.id,
                building_type__key__in=resolved_building_keys,
            )
            .select_related("building_type")
            .order_by("building_type__key", "id")
        )
        if buildings is None
        else buildings
    )
    buildings_by_key = {
        str(building.building_type.key): building
        for building in building_snapshot
        if str(building.building_type.key) in resolved_building_keys
    }
    quotes: list[BuildingUpgradeQuote] = []
    for building_key in resolved_building_keys:
        building = buildings_by_key.get(building_key)
        if building is None:
            continue
        try:
            quotes.append(
                quote_building_upgrade(
                    manor,
                    building,
                    buildings=buildings,
                    technology_levels=technology_levels,
                )
            )
        except (
            BuildingConcurrentUpgradeLimitError,
            BuildingMaxLevelError,
            BuildingUpgradingError,
        ):
            continue
    return tuple(quotes)


def _technology_upgrade_quotes(
    *,
    manor: Manor,
    development_plan: BotDevelopmentPlan,
    technologies: tuple[PlayerTechnology, ...] | None = None,
    technology_focuses: tuple[str, ...] | None = None,
) -> tuple[TechnologyUpgradeQuote, ...]:
    quotes: list[TechnologyUpgradeQuote] = []
    resolved_technology_focuses = (
        development_plan.technology_focuses
        if technology_focuses is None
        else tuple(dict.fromkeys(str(key).strip() for key in technology_focuses if str(key).strip()))
    )
    for technology_key in resolved_technology_focuses:
        try:
            quotes.append(
                quote_technology_upgrade(
                    manor,
                    technology_key,
                    technologies=technologies,
                )
            )
        except (
            TechnologyConcurrentUpgradeLimitError,
            TechnologyMaxLevelError,
            TechnologyNotFoundError,
            TechnologyUpgradeInProgressError,
        ):
            continue
    return tuple(quotes)


def _building_quote_matches_spec(
    quote: BuildingUpgradeQuote,
    spec: BuildingUpgradeActionSpec,
) -> bool:
    return (
        quote.building_id == spec.building_id
        and quote.building_key == spec.building_key
        and quote.current_level == spec.level_before
        and quote.target_level == spec.level_after
        and quote.resource_cost == spec.resource_costs
    )


def _salary_fallback_action_allowed(plan: MaintenancePlan) -> bool:
    if plan.policy_version == 2 and plan.action_kind == "troop_recruitment":
        # Policy 2 troop projection consumes real silver and grain, so it must
        # remain behind the ordinary wage-runway guard.
        return False
    return plan.action_kind in _SALARY_SAFE_ACTION_KINDS


def _technology_quote_matches_spec(
    quote: TechnologyUpgradeQuote,
    spec: TechnologyUpgradeActionSpec,
) -> bool:
    return (
        quote.technology_key == spec.technology_key
        and quote.current_level == spec.level_before
        and quote.target_level == spec.level_after
        and (("silver", quote.silver_cost),) == spec.resource_costs
    )


def _troop_recruitment_candidates(
    *,
    manor: Manor,
    prestige_band: str,
    strength_before: StrengthSummary,
    development_plan: BotDevelopmentPlan,
    troop_counts: tuple[tuple[str, int], ...],
) -> tuple[
    tuple[DevelopmentIntent, ...],
    dict[str, TroopRecruitmentQuote],
]:
    """按发展计划的目标兵种占比生成可立即执行的单兵恢复候选。"""
    if int(manor.retainer_count or 0) <= 0:
        return (), {}
    class_catalog = get_troop_classes()
    option_by_key = {str(option["key"]): option for option in get_recruitment_options(manor) if option.get("key")}
    troop_class_by_key = {
        str(troop_key): str(troop_class)
        for troop_class, class_info in class_catalog.items()
        if isinstance(class_info, dict)
        for troop_key in (class_info.get("troops") or ())
    }
    counts_by_class: dict[str, int] = {}
    for troop_key, count in troop_counts:
        troop_class = troop_class_by_key.get(troop_key)
        if troop_class:
            counts_by_class[troop_class] = counts_by_class.get(troop_class, 0) + count
    total_before = max(
        0,
        int(strength_before.components.get("troop_total", 0)),
    )
    target_mix = dict(development_plan.troop_mix)
    denominator_before = max(1, total_before)
    distance_before = sum(
        abs(counts_by_class.get(troop_class, 0) / denominator_before - target_weight)
        for troop_class, target_weight in development_plan.troop_mix
    )

    candidates: list[DevelopmentIntent] = []
    quotes: dict[str, TroopRecruitmentQuote] = {}
    for troop_class, target_weight in development_plan.troop_mix:
        class_info = class_catalog.get(troop_class)
        if not isinstance(class_info, dict):
            continue
        troop_keys = tuple(
            str(troop_key).strip() for troop_key in (class_info.get("troops") or ()) if str(troop_key).strip()
        )
        quote = None
        for troop_key in reversed(troop_keys):
            option = option_by_key.get(troop_key)
            if (
                option is None
                or not option.get("is_unlocked")
                or not option.get("can_afford")
                or option.get("is_recruiting")
            ):
                continue
            try:
                quote = quote_troop_recruitment(manor, troop_key, quantity=1)
            except TroopRecruitmentError:
                continue
            break
        if quote is None:
            continue

        counts_after = dict(counts_by_class)
        counts_after[troop_class] = counts_after.get(troop_class, 0) + 1
        denominator_after = total_before + 1
        distance_after = sum(
            abs(counts_after.get(candidate_class, 0) / denominator_after - candidate_weight)
            for candidate_class, candidate_weight in development_plan.troop_mix
        )
        mix_improvement = max(0.0, distance_before - distance_after)
        consumed_units = max(
            1,
            quote.retainer_cost + sum(cost for _item_key, cost in quote.equipment_costs),
        )
        utility_score = (float(target_mix[troop_class]) + mix_improvement) / consumed_units
        intent = project_troop_recruitment_development_intent(
            troop_key=quote.troop_key,
            quantity=quote.quantity,
            prestige_band=prestige_band,
            strength_before=strength_before,
            utility_score=utility_score,
        )
        candidates.append(intent)
        quotes[intent.business_key] = quote
    return tuple(candidates), quotes


def _next_v2_growth_at(
    *,
    profile: BotProfile,
    trigger_policy: MaintenanceTriggerPolicy,
    growth_policy: PrestigeBandGrowthPolicy,
    context: RandomContext,
    prestige_band: str,
    now: datetime,
    preserve_current_schedule: bool = False,
) -> datetime | None:
    if trigger_policy.schedule_disposition is MaintenanceScheduleDisposition.PRESERVE_NORMAL_SCHEDULE:
        return profile.next_growth_at
    if preserve_current_schedule and profile.next_growth_at is not None and profile.next_growth_at > now:
        return profile.next_growth_at
    next_strength_check = next_normal_strength_check_at(
        policy=growth_policy,
        prestige_band=prestige_band,
        context=context,
        now=now,
    )
    next_salary_date = timezone.localdate(now) + timedelta(days=1)
    next_salary_settlement = timezone.make_aware(
        datetime.combine(next_salary_date, datetime.min.time()),
        timezone.get_current_timezone(),
    )
    return min(next_strength_check, next_salary_settlement)


def _build_v2_maintenance_plan_from_profile(
    *,
    profile: BotProfile,
    manor: Manor,
    routing: RuntimeRoutingSnapshot,
    trigger_policy: MaintenanceTriggerPolicy,
    planned_at: datetime,
    minimum_guest_count: int | None,
    minimum_guest_level: int | None,
    guest_rarity_cap: str | None,
    max_guest_level_step: int | None,
    arena_growth_objective: ArenaGrowthObjective | None,
    arena_excluded_training_guest_ids: tuple[int, ...] = (),
    cycle_covered_action_kinds: tuple[str, ...] = (),
    cycle_high_cost_actions_used: int = 0,
    cycle_budget_state: ArchetypeBudgetState | None = None,
    cycle_pacing: ArchetypePacing | None = None,
    candidate_exclusions: tuple[str, ...] = (),
    scheduled_cycle_slot_due: bool = False,
    planning_snapshot: _MaintenancePlanningSnapshot | None = None,
    frozen_control_snapshot_digest: str | None = None,
) -> MaintenancePlan:
    if routing.maintenance_mode is not MaintenanceMode.V2_ACTIVE:
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.PAUSED,
            f"maintenance_mode_{routing.maintenance_mode.value}",
        )
    if not routing.persisted or routing.revision is None:
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.PAUSED,
            "routing_not_persisted",
        )
    if profile.engine_version != V2_MAINTENANCE_ENGINE_VERSION:
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.INELIGIBLE,
            "profile_engine_ineligible",
        )
    if profile.state not in {BotProfile.State.ACTIVE, BotProfile.State.SLOWING}:
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.INELIGIBLE,
            "profile_state_ineligible",
        )
    profile_due = trigger_policy.is_due(
        next_growth_at=profile.next_growth_at,
        now=planned_at,
    )
    if (
        trigger_policy.trigger is not MaintenanceTrigger.ARENA_ACCELERATION
        and not profile_due
        and not scheduled_cycle_slot_due
    ):
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.INELIGIBLE,
            "profile_not_due",
        )
    if profile.manor_id != manor.id:
        raise V2MaintenanceError("profile Manor changed during maintenance planning")
    if planning_snapshot is not None and (
        int(planning_snapshot.profile.id) != int(profile.id) or int(planning_snapshot.profile.manor_id) != int(manor.id)
    ):
        raise V2MaintenanceError("maintenance planning snapshot identity changed")
    cycle_covered_action_kinds = tuple(
        dict.fromkeys(str(value).strip() for value in cycle_covered_action_kinds if str(value).strip())
    )
    if (
        isinstance(cycle_high_cost_actions_used, bool)
        or not isinstance(cycle_high_cost_actions_used, int)
        or cycle_high_cost_actions_used < 0
    ):
        raise V2MaintenanceError("cycle_high_cost_actions_used must be a non-negative integer")
    if cycle_budget_state is not None and not isinstance(cycle_budget_state, ArchetypeBudgetState):
        raise V2MaintenanceError("cycle_budget_state must be an ArchetypeBudgetState or None")
    if cycle_pacing is not None and not isinstance(cycle_pacing, ArchetypePacing):
        raise V2MaintenanceError("cycle_pacing must be an ArchetypePacing or None")
    candidate_exclusions = tuple(
        dict.fromkeys(str(value).strip() for value in candidate_exclusions if str(value).strip())
    )

    planning_strength = None if planning_snapshot is None else planning_snapshot.strength
    context = _maintenance_context(profile)
    (
        development_plan,
        growth_policy,
        reference_snapshot_version,
        reference_selection,
        calibration_route,
        control_snapshot_digest,
        v2_config,
        resolved_policy_release,
    ) = _resolve_maintenance_policy(
        profile=profile,
        manor=manor,
        routing=routing,
        context=context,
        now=planned_at,
        policy_release=(None if planning_snapshot is None else planning_snapshot.policy_release),
        manor_strength=planning_strength,
        expected_control_digest=frozen_control_snapshot_digest,
    )
    if trigger_policy.trigger is MaintenanceTrigger.ARENA_ACCELERATION and not trigger_policy.is_due(
        next_growth_at=profile.next_growth_at,
        now=planned_at,
        arena_bypass_due=growth_policy.arena_acceleration_bypass.due,
    ):
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.INELIGIBLE,
            "profile_not_due",
        )
    if planning_snapshot is None:
        guests = tuple(Guest.objects.filter(manor_id=manor.id).select_related("template").order_by("id"))
        strength_before = load_manor_strength_summary(
            manor_id=manor.id,
            guests=guests,
        )
        gear_items = _equipped_gear_items(
            int(manor.id),
            guest_ids=tuple(int(guest.id) for guest in guests),
        )
        buildings = tuple(
            Building.objects.filter(manor_id=manor.id)
            .select_related("building_type")
            .order_by("building_type__key", "id")
        )
        technologies = tuple(PlayerTechnology.objects.filter(manor_id=manor.id).order_by("tech_key", "id"))
        technology_levels = {str(technology.tech_key): int(technology.level) for technology in technologies}
        production_basis = load_resource_production_basis(manor)
        paid_guest_ids = None
        warehouse_items = _available_warehouse_items(int(manor.id))
        medicine_items = (
            tuple(item for item in warehouse_items if item.template.effect_type == ItemTemplate.EffectType.MEDICINE)
            if _healable_guests(guests)
            else ()
        )
        guest_skills = _guest_skills_for_guests(guests)
        skills = None
        inventory_templates = _inventory_templates_for_profile(profile)
    else:
        guests = planning_snapshot.guests
        buildings = planning_snapshot.buildings
        technologies = planning_snapshot.technologies
        technology_levels = {str(technology.tech_key): int(technology.level) for technology in technologies}
        gear_items = planning_snapshot.gear_items
        strength_before = planning_snapshot.strength
        production_basis = planning_snapshot.production_basis
        paid_guest_ids = set(planning_snapshot.paid_guest_ids)
        medicine_items = planning_snapshot.medicine_items
        guest_skills = planning_snapshot.guest_skills
        skills = planning_snapshot.skills
        warehouse_items = planning_snapshot.warehouse_items
        inventory_templates = planning_snapshot.inventory_templates
        virtual_skill_books = planning_snapshot.virtual_skill_books
        virtual_skills = planning_snapshot.virtual_skills
        virtual_gear_templates = planning_snapshot.virtual_gear_templates
        virtual_inventory_templates = planning_snapshot.virtual_inventory_templates
        rare_inventory_quantity_today = planning_snapshot.rare_inventory_quantity_today
    domain_availability = _domain_availability_snapshot(
        buildings=buildings,
        technologies=technologies,
        guests=guests,
    )
    budget_entries = parse_strength_budget_entries(
        profile.strength_budget_entries,
        now=planned_at,
    )
    resource_planning_snapshot, forced_settlement_decision = build_resource_planning_snapshot(
        manor=manor,
        guests=guests,
        paid_guest_ids=paid_guest_ids,
        planned_at=planned_at,
        production_basis=production_basis,
        forced_settlement_budget=parse_forced_settlement_budget(
            profile.forced_settlement_daily_budget,
        ),
        current_grain=_warehouse_grain_quantity_from_snapshot(
            manor,
            warehouse_items,
        ),
    )
    resource_production_deltas = resource_planning_snapshot.production_deltas
    salary_quote = resource_planning_snapshot.current_salary_quote
    salary_shortfall = resource_planning_snapshot.salary_shortfall
    arena_acceleration = trigger_policy.trigger is MaintenanceTrigger.ARENA_ACCELERATION
    healing_intent, healing_guest, medicine_quote = _guest_healing_candidate(
        manor=manor,
        prestige_band=str(profile.current_prestige_band),
        strength_before=strength_before,
        context=context,
        guests=guests,
        medicine_items=medicine_items,
    )
    (
        guest_recruitment_intent,
        guest_recruitment_spec,
        recruited_guest_powers,
    ) = _guest_recruitment_candidate(
        manor=manor,
        prestige_band=str(profile.current_prestige_band),
        strength_before=strength_before,
        context=context,
        development_plan=development_plan,
        minimum_guest_count=minimum_guest_count,
        recruitment_rarity_cap=guest_rarity_cap,
        guests=guests,
        max_quantity=(1 if int(profile.policy_version) == 2 and arena_acceleration else None),
        allow_instant_recruitment=arena_acceleration,
    )
    quantity_target_pending = minimum_guest_count is not None and len(guests) < int(minimum_guest_count)
    quantity_phase = quantity_target_pending and guest_recruitment_intent is not None
    virtual_asset_policy = int(profile.policy_version) == 2
    # Arena acceleration without a durable reserve objective must not receive
    # the replenishment-only instant/free rules.
    arena_replenishment = bool(arena_acceleration and virtual_asset_policy and arena_growth_objective is not None)
    # Policy v2 treats healing as an independent roster sweep.  It must never
    # occupy a scheduled or arena action slot.
    if virtual_asset_policy and (trigger_policy.trigger is MaintenanceTrigger.SCHEDULED or arena_acceleration):
        healing_intent = None
        healing_guest = None
        medicine_quote = None
    growth_guests = (
        _arena_growth_priority_guests(
            tuple(guest for guest in guests if guest.status == GuestStatus.IDLE),
            arena_growth_objective,
        )
        if arena_acceleration
        else guests
    )
    arena_growth_bps_cap = (
        growth_policy.cadence_for(str(profile.current_prestige_band)).composite_growth_bps_per_controlled_action_max
        if arena_acceleration
        else None
    )
    training_candidates, candidate_metadata = _training_candidates(
        manor=manor,
        prestige_band=str(profile.current_prestige_band),
        strength_before=strength_before,
        context=context,
        development_plan=development_plan,
        minimum_guest_count=(minimum_guest_count if quantity_phase else None),
        minimum_guest_level=minimum_guest_level,
        max_guest_level_step=max_guest_level_step,
        max_projected_growth_bps=(None if arena_replenishment else arena_growth_bps_cap),
        guests=tuple(
            guest
            for guest in growth_guests
            if int(guest.id) not in {int(value) for value in arena_excluded_training_guest_ids}
        ),
        defer_completion=(virtual_asset_policy and not arena_replenishment),
        free_subsidy=arena_replenishment,
    )
    maintenance_config = load_virtual_player_config()
    if cycle_pacing is not None and not (virtual_asset_policy and not arena_acceleration):
        raise V2MaintenanceError("cycle pacing is only valid for ordinary policy-2 maintenance")
    archetype_pacing: ArchetypePacing | None = (
        cycle_pacing
        if cycle_pacing is not None
        else (resolve_archetype_pacing(maintenance_config, str(profile.archetype)) if virtual_asset_policy else None)
    )
    if cycle_budget_state is not None and not (virtual_asset_policy and not arena_acceleration):
        raise V2MaintenanceError("cycle budget state is only valid for ordinary policy-2 maintenance")
    if virtual_asset_policy and not arena_acceleration:
        if cycle_budget_state is None:
            cycle_budget_state = ArchetypeBudgetState.from_spendable_resources(
                resource_planning_snapshot.spendable_resources
            )
    if virtual_asset_policy:
        projection_config = maintenance_config.get("projection") or {}
        if planning_snapshot is None:
            virtual_pools = _load_virtual_projection_pools(planned_at=planned_at)
            virtual_skill_books = virtual_pools.skill_books
            virtual_skills = virtual_pools.skills
            virtual_gear_templates = virtual_pools.gear_templates
            virtual_inventory_templates = virtual_pools.inventory_templates
            rare_inventory_quantity_today = virtual_pools.rare_inventory_quantity_today
        try:
            skill_candidates, skill_specs = build_virtual_skill_learning_candidates(
                prestige_band=str(profile.current_prestige_band),
                strength_before=strength_before,
                development_plan=development_plan,
                guests=growth_guests,
                skill_books=virtual_skill_books,
                skills=virtual_skills,
                guest_skills=guest_skills,
            )
        except VirtualCandidatePoolError as exc:
            raise V2MaintenanceError(str(exc)) from exc

        if planning_snapshot is None:
            # The pool was loaded together with the skill catalog above.
            assert virtual_gear_templates is not None
        equipment_candidates, equipment_specs = build_virtual_equipment_candidates(
            manor_id=int(manor.id),
            prestige_band=str(profile.current_prestige_band),
            strength_before=strength_before,
            development_plan=development_plan,
            guests=growth_guests,
            gear_templates=virtual_gear_templates,
            equipped_items=gear_items,
            growth_stage=int(profile.growth_stage),
            rarity_stage_caps=projection_config.get("gear_max_rarity_by_stage") or {},
        )
    else:
        skill_candidates, skill_specs = build_skill_learning_candidates(
            manor_id=int(manor.id),
            prestige_band=str(profile.current_prestige_band),
            strength_before=strength_before,
            development_plan=development_plan,
            guests=guests,
            guest_skills=guest_skills,
            warehouse_items=warehouse_items,
            skills=skills,
        )
        equipment_candidates, equipment_specs = build_equipment_equip_candidates(
            manor_id=int(manor.id),
            prestige_band=str(profile.current_prestige_band),
            strength_before=strength_before,
            development_plan=development_plan,
            growth_stage=int(profile.growth_stage),
            config=maintenance_config,
            guests=growth_guests,
            gear_items=gear_items,
            warehouse_items=warehouse_items,
        )
    arena_capacity_blocked = bool(
        arena_replenishment and quantity_target_pending and int(manor.guest_capacity) <= len(guests)
    )
    # Capacity is a hard prerequisite for the quantity phase.  Keep the
    # expansion candidate in its own priority group so quality actions cannot
    # consume an arena slot while recruitment is still impossible.
    arena_capacity_expansion_required = arena_capacity_blocked
    arena_building_focuses: tuple[str, ...] | None = (
        (BuildingKeys.JUXIAN_ZHUANG,) if arena_capacity_blocked else (() if arena_replenishment else None)
    )
    resolved_building_focuses = (
        arena_building_focuses
        if arena_building_focuses is not None
        else (
            tuple(
                dict.fromkeys(
                    (
                        *(archetype_pacing.building_targets if archetype_pacing is not None else ()),
                        *development_plan.building_focuses,
                    )
                )
            )
        )
    )
    resolved_technology_focuses = tuple(
        dict.fromkeys(
            (
                *(archetype_pacing.technology_targets if archetype_pacing is not None else ()),
                *development_plan.technology_focuses,
            )
        )
    )
    building_quotes = _building_upgrade_quotes(
        manor=manor,
        development_plan=development_plan,
        buildings=buildings,
        technology_levels=technology_levels,
        building_keys=resolved_building_focuses,
    )
    building_candidates, building_specs = build_building_upgrade_candidates(
        manor=manor,
        prestige_band=str(profile.current_prestige_band),
        strength_before=strength_before,
        development_plan=development_plan,
        quotes=building_quotes,
        prestige_band_for=lambda prestige: prestige_band_for_value(
            prestige,
            maintenance_config,
        ),
        building_focuses=resolved_building_focuses,
        defer_completion=(virtual_asset_policy and not arena_replenishment),
        free_subsidy=arena_replenishment,
    )
    technology_quotes = _technology_upgrade_quotes(
        manor=manor,
        development_plan=development_plan,
        technologies=technologies,
        technology_focuses=resolved_technology_focuses,
    )
    technology_candidates, technology_specs = build_technology_upgrade_candidates(
        manor=manor,
        prestige_band=str(profile.current_prestige_band),
        strength_before=strength_before,
        development_plan=development_plan,
        quotes=technology_quotes,
        prestige_band_for=lambda prestige: prestige_band_for_value(
            prestige,
            maintenance_config,
        ),
        technology_focuses=resolved_technology_focuses,
    )
    if virtual_asset_policy:
        projection_config = maintenance_config.get("projection") or {}
        if planning_snapshot is None:
            # ``virtual_pools`` was loaded before the skill candidate builder.
            assert virtual_inventory_templates is not None
        inventory_intent, inventory_spec = build_virtual_inventory_batch_candidate(
            manor_id=int(manor.id),
            prestige_band=str(profile.current_prestige_band),
            growth_stage=int(profile.growth_stage),
            archetype=str(profile.archetype),
            strength_before=strength_before,
            inventory_templates=virtual_inventory_templates,
            config=maintenance_config,
            seed=context.seed(
                domain="inventory",
                discriminator={
                    "archetype": str(profile.archetype),
                    "prestige_band": str(profile.current_prestige_band),
                    "maintenance_sequence": int(profile.maintenance_sequence),
                },
            ),
            rare_count_today=rare_inventory_quantity_today,
        )
        inventory_candidates: tuple[DevelopmentIntent, ...] = () if inventory_intent is None else (inventory_intent,)
        inventory_specs: dict[str, MaintenanceActionSpec] = (
            {}
            if inventory_spec is None or inventory_intent is None
            else {inventory_intent.business_key: inventory_spec}
        )
    else:
        inventory_candidates, inventory_specs = build_inventory_acquisition_candidates(
            manor_id=int(manor.id),
            prestige_band=str(profile.current_prestige_band),
            strength_before=strength_before,
            inventory_template_keys=profile.inventory_template_keys,
            inventory_cap_config=maintenance_config,
            inventory_templates=inventory_templates,
            warehouse_items=warehouse_items,
        )
    troop_counts = (
        planning_snapshot.troop_counts
        if planning_snapshot is not None
        else (() if int(manor.retainer_count or 0) <= 0 else _troop_counts_by_key(int(manor.id)))
    )
    if virtual_asset_policy:
        try:
            troop_candidates, troop_quotes = build_virtual_troop_candidates(
                manor=manor,
                prestige_band=str(profile.current_prestige_band),
                strength_before=strength_before,
                development_plan=development_plan,
                troop_classes=get_troop_classes(),
                technology_levels=technology_levels,
                archetype=str(profile.archetype),
                config=(maintenance_config.get("projection") or {}),
            )
        except VirtualCandidatePoolError as exc:
            raise V2MaintenanceError(str(exc)) from exc
    else:
        troop_candidates, troop_quotes = _troop_recruitment_candidates(
            manor=manor,
            prestige_band=str(profile.current_prestige_band),
            strength_before=strength_before,
            development_plan=development_plan,
            troop_counts=troop_counts,
        )
    healing_candidates = () if healing_intent is None else (healing_intent,)
    recruitment_candidates = () if guest_recruitment_intent is None else (guest_recruitment_intent,)
    scheduled_quality_candidates = (
        *training_candidates,
        *building_candidates,
        *technology_candidates,
        *equipment_candidates,
        *skill_candidates,
        *troop_candidates,
        *inventory_candidates,
    )
    if virtual_asset_policy and not arena_acceleration:
        bias_config = (maintenance_config.get("projection") or {}).get("daily_action_bias") or {}
        raw_bias = bias_config.get(str(profile.archetype), {}) if isinstance(bias_config, Mapping) else {}
        if isinstance(raw_bias, Mapping):
            biased_candidates: list[DevelopmentIntent] = []
            for candidate in scheduled_quality_candidates:
                try:
                    multiplier = float(raw_bias.get(candidate.action_kind, 1.0))
                except (TypeError, ValueError):
                    multiplier = 1.0
                multiplier = multiplier if math.isfinite(multiplier) and multiplier > 0 else 1.0
                biased_candidates.append(
                    replace(
                        candidate,
                        utility_score=max(0.001, float(candidate.utility_score) * multiplier),
                    )
                )
            scheduled_quality_candidates = tuple(biased_candidates)
    scheduled_quality_groups: tuple[tuple[DevelopmentIntent, ...], ...] = (scheduled_quality_candidates,)
    if virtual_asset_policy and trigger_policy.trigger is MaintenanceTrigger.SCHEDULED:
        covered = {_ordinary_cycle_coverage_kind(str(action_kind)) for action_kind in cycle_covered_action_kinds}
        # A virtual inventory batch is a once-per-cycle action.  The durable
        # cycle is the source of truth, so do not regenerate it after the first
        # committed batch even if the profile remains due for another slot.
        scheduled_quality_candidates = tuple(
            candidate
            for candidate in scheduled_quality_candidates
            if not (
                candidate.action_kind == InventoryAcquisitionActionSpec.action_kind and candidate.action_kind in covered
            )
        )
        if covered:
            coverage_order = _ORDINARY_CYCLE_COVERAGE_KINDS
            candidates_by_kind = {
                kind: tuple(
                    candidate
                    for candidate in scheduled_quality_candidates
                    if _ordinary_cycle_coverage_kind(candidate.action_kind) == kind
                )
                for kind in coverage_order
            }
            uncovered_groups = tuple(
                candidates_by_kind[kind] for kind in coverage_order if kind not in covered and candidates_by_kind[kind]
            )
            covered_group = tuple(
                candidate
                for candidate in scheduled_quality_candidates
                if _ordinary_cycle_coverage_kind(candidate.action_kind) in covered
                or _ordinary_cycle_coverage_kind(candidate.action_kind) not in set(coverage_order)
            )
            scheduled_quality_candidates = tuple(
                candidate for group in (*uncovered_groups, covered_group) for candidate in group
            )
            scheduled_quality_groups = (*uncovered_groups, covered_group)
    arena_quality_candidates = (
        (
            *(building_candidates if arena_replenishment and not arena_capacity_expansion_required else ()),
            *training_candidates,
            *equipment_candidates,
            *skill_candidates,
        )
        if virtual_asset_policy
        else (*training_candidates, *equipment_candidates)
    )
    all_generated_candidates = (
        *healing_candidates,
        *recruitment_candidates,
        *scheduled_quality_candidates,
    )
    if candidate_exclusions:
        excluded_keys = set(candidate_exclusions)
        all_generated_candidates = tuple(
            candidate for candidate in all_generated_candidates if candidate.business_key not in excluded_keys
        )
    typed_action_specs = {
        **building_specs,
        **technology_specs,
        **equipment_specs,
        **skill_specs,
        **inventory_specs,
    }
    ordinary_pacing = archetype_pacing if virtual_asset_policy and not arena_acceleration else None
    active_training_count = sum(
        1
        for guest in guests
        if (getattr(guest, "training_complete_at", None) is not None and guest.training_complete_at > planned_at)
        or int(getattr(guest, "training_remaining_seconds", 0) or 0) > 0
    )
    high_cost_actions_used = int(cycle_high_cost_actions_used)
    building_quote_by_id = {int(quote.building_id): quote for quote in building_quotes}
    technology_by_key = {str(technology.tech_key): technology for technology in technologies}

    def _candidate_resource_costs(candidate: DevelopmentIntent) -> tuple[tuple[str, int], ...]:
        if arena_replenishment and candidate.action_kind in _ARENA_V2_ACTION_KINDS:
            # Arena policy 2 uses a shadow subsidy.  It must not borrow from
            # the normal silver/grain/wage ledger, including the future wage
            # commitment implied by a recruitment action.
            return ()
        if candidate.action_kind == "training":
            return candidate_metadata[candidate.business_key][3]
        if candidate.action_kind == "troop_recruitment":
            quote = troop_quotes.get(candidate.business_key)
            if quote is not None and getattr(quote, "source", "recruitment") == "virtual":
                return tuple(
                    sorted(
                        (resource, amount)
                        for resource, amount in (
                            (ResourceType.SILVER, int(quote.virtual_silver_cost)),
                            (ResourceType.GRAIN, int(quote.virtual_grain_cost)),
                        )
                        if amount > 0
                    )
                )
            return ()
        if candidate.action_kind == GuestRecruitmentActionSpec.action_kind:
            if guest_recruitment_spec is None:
                raise V2MaintenanceError("guest recruitment candidate is missing its action spec")
            next_day_salary = (
                get_guest_salary_for_rarity(guest_recruitment_spec.rarity) * guest_recruitment_spec.quantity
            )
            salary_commitment = salary_runway_commitment(next_day_salary)
            return ((ResourceType.SILVER, salary_commitment),) if salary_commitment > 0 else ()
        spec = typed_action_specs.get(candidate.business_key)
        if (
            arena_replenishment
            and isinstance(spec, BuildingUpgradeActionSpec)
            and spec.building_key == BuildingKeys.JUXIAN_ZHUANG
        ):
            return ()
        if isinstance(spec, (BuildingUpgradeActionSpec, TechnologyUpgradeActionSpec)):
            return spec.resource_costs
        return ()

    def _candidate_timing(candidate: DevelopmentIntent) -> tuple[int, str]:
        if arena_replenishment:
            return 0, ""
        if candidate.action_kind == "training":
            guest, levels, _rng_seed, _costs = candidate_metadata[candidate.business_key]
            return int(get_training_duration(guest, levels)), "guest_training"
        spec = typed_action_specs.get(candidate.business_key)
        if isinstance(spec, BuildingUpgradeActionSpec):
            quote = building_quote_by_id.get(int(spec.building_id))
            return (0, "") if quote is None else (int(quote.duration_seconds), "building")
        if isinstance(spec, TechnologyUpgradeActionSpec):
            technology = technology_by_key.get(str(spec.technology_key))
            if technology is None:
                technology = PlayerTechnology(
                    manor=manor,
                    tech_key=str(spec.technology_key),
                    level=int(spec.level_before),
                )
            return int(technology.upgrade_duration()), "technology"
        return 0, ""

    def _candidate_score_payload(
        candidate: DevelopmentIntent,
        *,
        resource_costs: tuple[tuple[str, int], ...],
    ) -> dict[str, Any]:
        completion_seconds, queue_name = _candidate_timing(candidate)
        expected_strength_gain = max(
            0,
            int(round(float(candidate.strength_after.composite) - float(candidate.strength_before.composite))),
        )
        return {
            "completion_seconds": completion_seconds,
            "queue_name": queue_name,
            "expected_strength_gain": expected_strength_gain,
            "selection_score": candidate_efficiency_score(
                base_utility_score=float(candidate.utility_score),
                expected_strength_gain=expected_strength_gain,
                resource_costs=dict(resource_costs),
                completion_seconds=completion_seconds,
            ),
        }

    if arena_acceleration:
        if salary_shortfall and not virtual_asset_policy:
            blocked_arena_candidates = (
                *recruitment_candidates,
                *training_candidates,
            )
            candidate_groups = tuple(
                group
                for group in (
                    healing_candidates,
                    equipment_candidates,
                    blocked_arena_candidates,
                )
                if group
            )
        else:
            candidate_groups = tuple(
                group
                for group in (
                    healing_candidates,
                    recruitment_candidates if quantity_target_pending else (),
                    building_candidates if arena_capacity_expansion_required else (),
                    arena_quality_candidates,
                )
                if group
            )
    elif salary_shortfall:

        def _salary_safe_candidate(candidate: DevelopmentIntent) -> bool:
            if virtual_asset_policy and candidate.action_kind == "troop_recruitment":
                return False
            return candidate.action_kind in _SALARY_SAFE_ACTION_KINDS

        salary_safe_candidates = tuple(
            candidate
            for candidate in (
                *healing_candidates,
                *equipment_candidates,
                *skill_candidates,
                *troop_candidates,
                *inventory_candidates,
            )
            if _salary_safe_candidate(candidate)
        )
        salary_blocked_candidates = tuple(
            candidate for candidate in all_generated_candidates if not _salary_safe_candidate(candidate)
        )
        candidate_groups = tuple(
            group
            for group in (
                healing_candidates,
                salary_safe_candidates,
                salary_blocked_candidates,
            )
            if group
        )
    else:
        candidate_groups = tuple(
            group
            for group in (
                healing_candidates,
                recruitment_candidates if quantity_target_pending else (),
                *scheduled_quality_groups,
            )
            if group
        )
    if candidate_exclusions:
        excluded_keys = set(candidate_exclusions)
        candidate_groups = tuple(
            tuple(candidate for candidate in group if candidate.business_key not in excluded_keys)
            for group in candidate_groups
        )
        candidate_groups = tuple(group for group in candidate_groups if group)

    target_context_by_band: dict[str, tuple[ReferenceSelection, CalibrationRoute | None]] = {}
    target_context_by_business_key: dict[
        str,
        tuple[ReferenceSelection | None, CalibrationRoute | None],
    ] = {}

    def _target_context_for_candidate(
        candidate: DevelopmentIntent,
    ) -> tuple[ReferenceSelection | None, CalibrationRoute | None]:
        transition_distance = abs(
            PRESTIGE_BANDS.index(candidate.source_prestige_band) - PRESTIGE_BANDS.index(candidate.target_prestige_band)
        )
        if transition_distance != 1:
            return None, None
        cached = target_context_by_band.get(candidate.target_prestige_band)
        if cached is not None:
            return cached
        (
            target_snapshot_version,
            target_selection,
            target_route,
            _target_control_digest,
        ) = _maintenance_reference_for_band(
            config=v2_config,
            release=resolved_policy_release,
            profile=profile,
            routing=routing,
            context=context,
            region=str(manor.region),
            prestige_band=candidate.target_prestige_band,
            now=planned_at,
            manor_strength=strength_before,
        )
        if target_snapshot_version != reference_snapshot_version:
            raise V2MaintenanceError("source and target maintenance references use different snapshot versions")
        target_context_by_band[candidate.target_prestige_band] = (target_selection, target_route)
        return target_selection, target_route

    # Priority is decided only after every generated candidate has been
    # assessed. A healing intent can still fail the live event cap or resource
    # guard, in which case the selector must be able to fall back to training.
    candidates_for_assessment = (
        all_generated_candidates if arena_acceleration else (healing_candidates or all_generated_candidates)
    )
    eligible_arena_guest_powers = tuple(
        (
            int(guest.id),
            _guest_arena_power(
                guest,
                force=int(guest.force),
                intellect=int(guest.intellect),
                defense=int(guest.defense_stat),
                agility=int(guest.agility),
            ),
        )
        for guest in guests
        if guest.status == GuestStatus.IDLE
    )
    next_recruited_guest_id = max((int(guest.id) for guest in guests), default=0) + 1

    def _arena_candidate_power_projection(
        candidate: DevelopmentIntent,
    ) -> ArenaSelectedPowerProjection | None:
        if (
            not arena_acceleration
            or arena_growth_objective is None
            or not _arena_action_is_allowed(
                candidate,
                virtual_asset_policy=virtual_asset_policy,
                arena_replenishment=arena_replenishment,
                action_spec=typed_action_specs.get(candidate.business_key),
            )
        ):
            return None
        existing_guest_power_after: tuple[int, int] | None = None
        newly_eligible_guest_power: tuple[int, int] | None = None
        added_guest_powers: tuple[int, ...] = ()
        added_guest_id_start: int | None = None
        if candidate.action_kind == "guest_healing":
            if healing_guest is None or medicine_quote is None:
                raise V2MaintenanceError("healing candidate is missing its projection metadata")
            healed_power = _guest_arena_power(
                healing_guest,
                force=int(healing_guest.force),
                intellect=int(healing_guest.intellect),
                defense=int(healing_guest.defense_stat),
                agility=int(healing_guest.agility),
            )
            if medicine_quote.status_after == str(GuestStatus.IDLE):
                projected_row = (int(healing_guest.id), healed_power)
                if healing_guest.status == GuestStatus.IDLE:
                    existing_guest_power_after = projected_row
                else:
                    newly_eligible_guest_power = projected_row
        elif candidate.action_kind == "training":
            training_guest = candidate_metadata[candidate.business_key][0]
            current_power = _guest_arena_power(
                training_guest,
                force=int(training_guest.force),
                intellect=int(training_guest.intellect),
                defense=int(training_guest.defense_stat),
                agility=int(training_guest.agility),
            )
            projected_delta = int(
                candidate.strength_after.components["arena_lineup_power"]
                - candidate.strength_before.components["arena_lineup_power"]
            )
            existing_guest_power_after = (
                int(training_guest.id),
                current_power + projected_delta,
            )
        elif candidate.action_kind == EquipmentEquipActionSpec.action_kind:
            equipment_spec = typed_action_specs.get(candidate.business_key)
            if not isinstance(equipment_spec, EquipmentEquipActionSpec):
                raise V2MaintenanceError("equipment candidate is missing its projection metadata")
            equipment_guest = next(guest for guest in guests if int(guest.id) == int(equipment_spec.guest_id))
            current_power = _guest_arena_power(
                equipment_guest,
                force=int(equipment_guest.force),
                intellect=int(equipment_guest.intellect),
                defense=int(equipment_guest.defense_stat),
                agility=int(equipment_guest.agility),
            )
            projected_delta = int(
                candidate.strength_after.components["arena_lineup_power"]
                - candidate.strength_before.components["arena_lineup_power"]
            )
            existing_guest_power_after = (
                int(equipment_guest.id),
                current_power + projected_delta,
            )
        elif candidate.action_kind == SkillLearningActionSpec.action_kind:
            skill_spec = typed_action_specs.get(candidate.business_key)
            if not isinstance(skill_spec, SkillLearningActionSpec):
                raise V2MaintenanceError("skill candidate is missing its projection metadata")
            skill_guest = next(guest for guest in guests if int(guest.id) == int(skill_spec.guest_id))
            current_power = _guest_arena_power(
                skill_guest,
                force=int(skill_guest.force),
                intellect=int(skill_guest.intellect),
                defense=int(skill_guest.defense_stat),
                agility=int(skill_guest.agility),
            )
            projected_delta = int(
                candidate.strength_after.components["arena_lineup_power"]
                - candidate.strength_before.components["arena_lineup_power"]
            )
            existing_guest_power_after = (
                int(skill_guest.id),
                current_power + projected_delta,
            )
        elif candidate.action_kind == GuestRecruitmentActionSpec.action_kind:
            added_guest_powers = recruited_guest_powers
            added_guest_id_start = next_recruited_guest_id
        return project_arena_candidate_selected_power(
            objective=arena_growth_objective,
            profile_id=int(profile.id),
            eligible_guest_powers_before=eligible_arena_guest_powers,
            existing_guest_power_after=existing_guest_power_after,
            newly_eligible_guest_power=newly_eligible_guest_power,
            added_guest_powers=added_guest_powers,
            added_guest_id_start=added_guest_id_start,
        )

    candidate_assessments: list[CandidateAssessment] = []
    for candidate in candidates_for_assessment:
        target_selection, target_route = _target_context_for_candidate(candidate)
        target_context_by_business_key[candidate.business_key] = (target_selection, target_route)
        rejection_reasons: list[str] = []
        if arena_acceleration and not _arena_action_is_allowed(
            candidate,
            virtual_asset_policy=virtual_asset_policy,
            arena_replenishment=arena_replenishment,
            action_spec=typed_action_specs.get(candidate.business_key),
        ):
            # The trigger allowlist is a hard boundary.  Do not append
            # unrelated resource or strength diagnostics to a candidate that
            # cannot execute on this trigger; this keeps the audit reason
            # deterministic and prevents a future arena-only bypass from
            # accidentally widening the action surface.
            candidate_assessments.append(
                CandidateAssessment(
                    intent=candidate,
                    **_candidate_score_payload(candidate, resource_costs=()),
                    rejection_reasons=("trigger_action_disallowed",),
                )
            )
            continue
        if salary_shortfall and not arena_replenishment and candidate.action_kind not in _SALARY_SAFE_ACTION_KINDS:
            rejection_reasons.append("salary_runway_protected")
        (
            resource_costs,
            resources_before_action,
            resources_after_action,
            resource_rejections,
        ) = resource_planning_snapshot.assess_costs(
            _candidate_resource_costs(candidate),
        )
        rejection_reasons.extend(resource_rejections)
        if ordinary_pacing is not None:
            if candidate.action_kind == "training" and active_training_count >= ordinary_pacing.max_parallel_training:
                rejection_reasons.append("archetype_parallel_training_cap")
            if (
                candidate.action_kind in HIGH_COST_ACTION_KINDS
                and high_cost_actions_used >= ordinary_pacing.high_cost_actions_per_cycle
            ):
                rejection_reasons.append("archetype_high_cost_cap")
            budget_limits = (
                dict(cycle_budget_state.remaining_limits(ordinary_pacing)) if cycle_budget_state is not None else {}
            )
            for resource, amount in resource_costs:
                budget_limit = budget_limits.get(resource)
                if budget_limit is not None and int(amount) > budget_limit:
                    rejection_reasons.append(f"archetype_budget_{resource}")
        arena_power_projection = _arena_candidate_power_projection(candidate)
        projected_selected_power = (
            None if arena_power_projection is None else arena_power_projection.projected_selected_power
        )
        event_power_cap = None
        if arena_power_projection is not None:
            assert arena_growth_objective is not None
            event_power_cap = arena_growth_objective.selected_power_upper_bound
        roster_completion_recruitment = (
            candidate.action_kind == GuestRecruitmentActionSpec.action_kind and quantity_target_pending
        )
        if (
            arena_power_projection is not None
            and event_power_cap is not None
            and arena_power_projection.has_legal_lineup_after
            and arena_power_projection.projected_selected_power > event_power_cap
            and arena_power_projection.projected_selected_power > arena_power_projection.selected_power_before
            and not roster_completion_recruitment
        ):
            # During the quantity phase, the minimum roster is the hard
            # admission target.  Let the bounded recruitment action complete
            # that roster; once it exists, normal event-power validation is
            # restored for quality and lineup-selection actions.
            rejection_reasons.append("event_power_cap")
        if candidate.action_kind == "guest_healing":
            # Healing changes availability/HP but not the permanent strength
            # budget governed by evaluate_controlled_action.
            normalized_rejections = tuple(dict.fromkeys(rejection_reasons))
            candidate_assessments.append(
                CandidateAssessment(
                    intent=candidate,
                    resource_costs=resource_costs,
                    resources_before_action=resources_before_action,
                    resources_after_action=resources_after_action,
                    **_candidate_score_payload(candidate, resource_costs=resource_costs),
                    projected_selected_power=projected_selected_power,
                    event_power_cap=event_power_cap,
                    rejection_reasons=normalized_rejections,
                    retryable=bool(set(normalized_rejections) & {"insufficient_resource", "salary_runway_protected"}),
                )
            )
            continue
        controlled_decision = evaluate_controlled_action(
            policy=growth_policy,
            intent=candidate,
            now=planned_at,
            last_strength_increase_at=profile.last_strength_increase_at,
            budget_entries=budget_entries,
            policy_version=int(profile.policy_version),
            source_sample_count=reference_selection.local_sample_count,
            source_strength_cap=reference_selection.cap,
            target_sample_count=(None if target_selection is None else target_selection.local_sample_count),
            target_strength_cap=(None if target_selection is None else target_selection.cap),
            allow_roster_expansion=(candidate.action_kind == GuestRecruitmentActionSpec.action_kind),
            allow_arena_acceleration=(
                arena_acceleration and candidate.action_kind != GuestRecruitmentActionSpec.action_kind
            ),
            allow_arena_growth_cap_bypass=arena_replenishment,
        )
        rejection_reasons.extend(reason.value for reason in controlled_decision.skipped_action_reasons)
        normalized_rejections = tuple(dict.fromkeys(rejection_reasons))
        candidate_assessments.append(
            CandidateAssessment(
                intent=candidate,
                resource_costs=resource_costs,
                resources_before_action=resources_before_action,
                resources_after_action=resources_after_action,
                **_candidate_score_payload(candidate, resource_costs=resource_costs),
                controlled_decision=controlled_decision,
                projected_selected_power=projected_selected_power,
                event_power_cap=event_power_cap,
                rejection_reasons=normalized_rejections,
                retryable=bool(
                    set(normalized_rejections)
                    & {
                        MaintenanceNoActionReason.BAND_SPACING.value,
                        MaintenanceNoActionReason.BAND_ACTION_CAP.value,
                        MaintenanceNoActionReason.STRENGTH_CAP.value,
                        "insufficient_resource",
                        "salary_runway_protected",
                        "archetype_parallel_training_cap",
                        "archetype_budget_silver",
                        "archetype_budget_grain",
                    }
                ),
            )
        )

    assessments = tuple(candidate_assessments)
    selected_assessment = select_candidate_assessment(
        candidate_groups,
        assessments=assessments,
        context=context,
        optimization_bias=development_plan.optimization_bias,
    )
    intent = None if selected_assessment is None else selected_assessment.intent
    action_kind = ""
    target_id = None
    training_levels = 0
    rng_seed = None
    target_guest = None
    troop_recruitment_quote = None
    action_spec: MaintenanceActionSpec | None = None
    if intent is not None:
        action_kind = intent.action_kind
        if action_kind == "guest_healing":
            assert healing_guest is not None
            assert medicine_quote is not None
            target_guest = healing_guest
            target_id = int(target_guest.id)
        elif action_kind == "training":
            target_guest, training_levels, rng_seed, _resource_costs = candidate_metadata[intent.business_key]
            target_id = int(target_guest.id)
        elif action_kind == "troop_recruitment":
            troop_recruitment_quote = troop_quotes[intent.business_key]
        elif action_kind == GuestRecruitmentActionSpec.action_kind:
            if guest_recruitment_spec is None or intent.business_key != guest_recruitment_spec.business_key:
                raise V2MaintenanceError("guest recruitment intent does not match its action spec")
            action_spec = guest_recruitment_spec
        elif action_kind in {
            BuildingUpgradeActionSpec.action_kind,
            EquipmentEquipActionSpec.action_kind,
            TechnologyUpgradeActionSpec.action_kind,
            SkillLearningActionSpec.action_kind,
            InventoryAcquisitionActionSpec.action_kind,
        }:
            action_spec = typed_action_specs[intent.business_key]
            if isinstance(
                action_spec,
                (EquipmentEquipActionSpec, SkillLearningActionSpec),
            ):
                target_id = action_spec.guest_id
                target_guest = next(guest for guest in guests if int(guest.id) == target_id)
        else:
            raise V2MaintenanceError(f"unsupported planned maintenance action: {action_kind}")
    target_reference_selection, target_calibration_route = (
        (None, None) if intent is None else target_context_by_business_key[intent.business_key]
    )
    selected_medicine_quote = medicine_quote if action_kind == "guest_healing" else None
    virtual_projection_pools = (
        None
        if not virtual_asset_policy
        else _VirtualProjectionPools(
            skill_books=virtual_skill_books,
            skills=virtual_skills,
            gear_templates=virtual_gear_templates,
            inventory_templates=virtual_inventory_templates,
            rare_inventory_quantity_today=rare_inventory_quantity_today,
        )
    )

    preserve_cycle_schedule = scheduled_cycle_slot_due and not profile_due
    domain_retry_at = (
        _domain_retry_at(
            profile=profile,
            domain_availability=domain_availability,
            now=planned_at,
        )
        if trigger_policy.trigger is MaintenanceTrigger.SCHEDULED
        else None
    )
    next_growth_at_after_no_action = _next_v2_growth_at(
        profile=profile,
        trigger_policy=trigger_policy,
        growth_policy=growth_policy,
        context=context,
        prestige_band=str(profile.current_prestige_band),
        now=planned_at,
        preserve_current_schedule=preserve_cycle_schedule,
    )
    if domain_retry_at is not None:
        next_growth_at_after_no_action = domain_retry_at
    next_growth_at_after = next_growth_at_after_no_action
    if intent is not None and selected_assessment is not None and selected_assessment.allowed:
        next_growth_at_after = _next_v2_growth_at(
            profile=profile,
            trigger_policy=trigger_policy,
            growth_policy=growth_policy,
            context=context,
            prestige_band=(
                intent.target_prestige_band
                if intent.target_prestige_band != str(profile.current_prestige_band)
                else str(profile.current_prestige_band)
            ),
            now=planned_at,
            preserve_current_schedule=preserve_cycle_schedule,
        )
    precondition_digest = _maintenance_precondition_digest(
        profile=profile,
        manor=manor,
        target_guest=target_guest,
        trigger_policy=trigger_policy,
        planned_at=planned_at,
        routing_revision=int(routing.revision),
        development_plan=development_plan,
        reference_snapshot_version=reference_snapshot_version,
        control_snapshot_digest=control_snapshot_digest,
        reference_selection=reference_selection,
        target_reference_selection=target_reference_selection,
        strength_before=strength_before,
        budget_entries=budget_entries,
        resource_production_deltas=resource_production_deltas,
        forced_settlement_decision=forced_settlement_decision,
        salary_quote=salary_quote,
        resource_planning_snapshot=resource_planning_snapshot,
        intent=intent,
        action_kind=action_kind,
        target_id=target_id,
        training_levels=training_levels,
        rng_seed=rng_seed,
        troop_recruitment_quote=troop_recruitment_quote,
        medicine_quote=selected_medicine_quote,
        action_spec=action_spec,
        candidate_assessments=assessments,
        gear_items=gear_items,
        warehouse_items=warehouse_items,
        troop_counts=troop_counts,
        calibration_route=calibration_route,
        target_calibration_route=target_calibration_route,
        minimum_guest_count=minimum_guest_count,
        minimum_guest_level=minimum_guest_level,
        guest_rarity_cap=guest_rarity_cap,
        max_guest_level_step=max_guest_level_step,
        arena_growth_objective=arena_growth_objective,
        next_growth_at_after=next_growth_at_after,
        next_growth_at_after_no_action=next_growth_at_after_no_action,
        cycle_covered_action_kinds=cycle_covered_action_kinds,
        cycle_high_cost_actions_used=high_cost_actions_used,
        cycle_budget_state=cycle_budget_state,
        cycle_pacing=archetype_pacing if virtual_asset_policy and not arena_acceleration else None,
        candidate_exclusions=candidate_exclusions,
    )
    return MaintenancePlan(
        profile_id=int(profile.id),
        manor_id=int(manor.id),
        expected_sequence=int(profile.maintenance_sequence),
        trigger_policy=trigger_policy,
        planned_at=planned_at,
        routing_revision=int(routing.revision),
        engine_version=int(profile.engine_version),
        rng_version=int(profile.rng_version),
        plan_schema_version=int(profile.plan_schema_version),
        policy_version=int(profile.policy_version),
        policy_checksum=str(profile.policy_checksum),
        development_plan=development_plan,
        growth_policy=growth_policy,
        profile_state=str(profile.state),
        region=str(manor.region),
        current_prestige_band=str(profile.current_prestige_band),
        reference_snapshot_version=reference_snapshot_version,
        control_snapshot_digest=control_snapshot_digest,
        reference_selection=reference_selection,
        target_reference_selection=target_reference_selection,
        strength_before=strength_before,
        strength_budget_entries_before=budget_entries,
        resource_production_deltas=resource_production_deltas,
        forced_settlement_decision=forced_settlement_decision,
        salary_quote=salary_quote,
        resource_planning_snapshot=resource_planning_snapshot,
        last_strength_increase_at_before=profile.last_strength_increase_at,
        next_growth_at_before=profile.next_growth_at,
        next_growth_at_after=next_growth_at_after,
        next_growth_at_after_no_action=next_growth_at_after_no_action,
        action_kind=action_kind,
        target_id=target_id,
        training_levels=training_levels,
        rng_seed=rng_seed,
        troop_recruitment_quote=troop_recruitment_quote,
        medicine_quote=selected_medicine_quote,
        action_spec=action_spec,
        precondition_digest=precondition_digest,
        intent=intent,
        candidate_assessments=assessments,
        calibration_route=calibration_route,
        target_calibration_route=target_calibration_route,
        minimum_guest_count=minimum_guest_count,
        minimum_guest_level=minimum_guest_level,
        guest_rarity_cap=guest_rarity_cap,
        max_guest_level_step=max_guest_level_step,
        arena_growth_objective=arena_growth_objective,
        arena_excluded_training_guest_ids=tuple(
            dict.fromkeys(int(value) for value in arena_excluded_training_guest_ids)
        ),
        cycle_covered_action_kinds=cycle_covered_action_kinds,
        cycle_high_cost_actions_used=high_cost_actions_used,
        cycle_budget_state=cycle_budget_state,
        cycle_pacing=archetype_pacing if virtual_asset_policy and not arena_acceleration else None,
        candidate_exclusions=candidate_exclusions,
        scheduled_cycle_slot_due=bool(scheduled_cycle_slot_due),
        domain_availability=domain_availability,
        virtual_projection_pools=virtual_projection_pools,
        planning_snapshot=planning_snapshot,
    )


@record_maintenance_stage(STAGE_PROFILE_PLAN_REVALIDATION)
def build_virtual_player_v2_maintenance_plan(
    profile_id: int,
    *,
    trigger: MaintenanceTrigger,
    now: datetime | None = None,
    admin_requires_due: bool | None = None,
    admin_schedule_disposition: MaintenanceScheduleDisposition | None = None,
    minimum_guest_count: int | None = None,
    minimum_guest_level: int | None = None,
    guest_rarity_cap: str | None = None,
    max_guest_level_step: int | None = None,
    arena_growth_objective: ArenaGrowthObjective | None = None,
    _arena_excluded_training_guest_ids: tuple[int, ...] = (),
    _cycle_covered_action_kinds: tuple[str, ...] = (),
    _cycle_high_cost_actions_used: int = 0,
    _cycle_budget_state: ArchetypeBudgetState | None = None,
    _cycle_pacing: ArchetypePacing | None = None,
    _candidate_exclusions: tuple[str, ...] = (),
    _scheduled_cycle_slot_due: bool = False,
    _routing_snapshot: RuntimeRoutingSnapshot | None = None,
    _external_reconciliation_prechecked: bool = False,
    _planning_snapshot: _MaintenancePlanningSnapshot | None = None,
    _frozen_control_snapshot_digest: str | None = None,
) -> MaintenancePlan:
    """Build a deterministic, read-only V2 maintenance plan."""
    if isinstance(profile_id, bool) or not isinstance(profile_id, int) or profile_id < 1:
        raise ValueError("profile_id must be a positive integer")
    planned_at = now or timezone.now()
    if timezone.is_naive(planned_at):
        raise ValueError("now must be timezone-aware")
    (
        objective_minimum_count,
        objective_minimum_level,
        objective_rarity_cap,
        objective_max_step,
    ) = _growth_parameters_from_objective(
        arena_growth_objective,
        minimum_guest_count=minimum_guest_count,
        minimum_guest_level=minimum_guest_level,
        guest_rarity_cap=guest_rarity_cap,
        max_guest_level_step=max_guest_level_step,
    )
    normalized_minimum_count = _normalize_optional_non_negative_int(
        objective_minimum_count,
        field="minimum_guest_count",
    )
    normalized_minimum_level = _normalize_optional_positive_int(
        objective_minimum_level,
        field="minimum_guest_level",
    )
    normalized_max_step = _normalize_optional_positive_int(
        objective_max_step,
        field="max_guest_level_step",
    )
    recruitment_rarity_cap = _normalize_recruitment_rarity_cap(objective_rarity_cap)
    trigger_policy = maintenance_trigger_policy(
        trigger,
        admin_requires_due=admin_requires_due,
        admin_schedule_disposition=admin_schedule_disposition,
    )
    if arena_growth_objective is not None and trigger_policy.trigger is not MaintenanceTrigger.ARENA_ACCELERATION:
        raise ValueError("arena_growth_objective requires arena acceleration")
    if arena_growth_objective is not None and recruitment_rarity_cap != arena_growth_objective.recruitment_rarity_cap:
        arena_growth_objective = replace(
            arena_growth_objective,
            recruitment_rarity_cap=recruitment_rarity_cap,
        )
    if _routing_snapshot is None:
        try:
            routing = read_virtual_player_routing()
        except RuntimeRoutingError as exc:
            raise _V2MaintenanceOutcomeError(
                MaintenanceOutcome.PAUSED,
                "routing_unavailable",
            ) from exc
    else:
        routing = _routing_snapshot
    profile = (
        _planning_snapshot.profile
        if _planning_snapshot is not None
        else BotProfile.objects.select_related("manor").filter(pk=profile_id).first()
    )
    if profile is None:
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.INELIGIBLE,
            "profile_missing",
        )
    if not _external_reconciliation_prechecked and profile.id in unresolved_external_reconciliation_profile_ids(
        {profile.id}
    ):
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.PAUSED,
            "external_reconciliation_unresolved",
        )
    try:
        return _build_v2_maintenance_plan_from_profile(
            profile=profile,
            manor=profile.manor,
            routing=routing,
            trigger_policy=trigger_policy,
            planned_at=planned_at,
            minimum_guest_count=normalized_minimum_count,
            minimum_guest_level=normalized_minimum_level,
            guest_rarity_cap=recruitment_rarity_cap,
            max_guest_level_step=normalized_max_step,
            arena_growth_objective=arena_growth_objective,
            arena_excluded_training_guest_ids=_arena_excluded_training_guest_ids,
            cycle_covered_action_kinds=_cycle_covered_action_kinds,
            cycle_high_cost_actions_used=_cycle_high_cost_actions_used,
            cycle_budget_state=_cycle_budget_state,
            cycle_pacing=_cycle_pacing,
            candidate_exclusions=_candidate_exclusions,
            scheduled_cycle_slot_due=_scheduled_cycle_slot_due,
            planning_snapshot=_planning_snapshot,
            frozen_control_snapshot_digest=_frozen_control_snapshot_digest,
        )
    except _V2MaintenanceOutcomeError:
        raise
    except (
        DevelopmentPlanError,
        InvalidStrengthBudgetError,
        MaintenanceActionSpecError,
        CandidateAssessmentError,
        MaintenanceCandidateError,
        MaintenanceUpgradeCandidateError,
        ResourcePlanningError,
        MaintenanceRuleError,
        PolicyRegistryError,
        ProjectionRuleError,
        ReferenceSnapshotError,
        UnsupportedRandomDomainError,
        UnsupportedRngVersionError,
        ArchetypePacingError,
        V2MaintenanceError,
        VirtualPlayerConfigError,
    ) as exc:
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.PAUSED,
            "v2_profile_or_policy_invalid",
        ) from exc


def _uncommitted_maintenance_result(
    *,
    profile_id: int,
    trigger_policy: MaintenanceTriggerPolicy,
    outcome: MaintenanceOutcome,
    reason: str,
) -> MaintenanceResult:
    snapshot = BotProfile.objects.filter(pk=profile_id).values("maintenance_sequence", "next_growth_at").first()
    sequence = int(snapshot["maintenance_sequence"]) if snapshot else 0
    next_growth_at = snapshot["next_growth_at"] if snapshot else None
    return MaintenanceResult(
        outcome=outcome,
        trigger=trigger_policy.trigger,
        profile_id=profile_id,
        sequence_before=sequence,
        sequence_after=sequence,
        schedule_disposition=trigger_policy.schedule_disposition,
        next_growth_at_before=next_growth_at,
        next_growth_at_after=(next_growth_at if outcome is MaintenanceOutcome.BUSY else None),
        reason=reason,
    )


@transaction.atomic
def _persist_arena_training_assignment_from_plan(
    plan: MaintenancePlan,
    *,
    operation_id: str,
    member_id: int | None,
    round_ordinal: int | None,
    action_ordinal_in_round: int | None,
) -> None:
    """Reserve a training guest before dispatching a retryable slot.

    A planning result is the first durable knowledge of the selected guest.
    Persisting it before execution makes BUSY, retryable NO_ACTION and
    commit-uncertain paths occupy the same-round guest identity just like a
    committed receipt does.  The receipt finalizer only changes the status.
    """

    if (
        plan.trigger_policy.trigger is not MaintenanceTrigger.ARENA_ACCELERATION
        or plan.action_kind != "training"
        or plan.target_id is None
        or member_id is None
        or round_ordinal is None
        or action_ordinal_in_round is None
    ):
        return
    normalized_operation_id = str(operation_id).strip()
    if not normalized_operation_id:
        raise V2MaintenanceError("arena training assignment requires an operation_id")
    target_guest_id = int(plan.target_id)
    normalized_round = int(round_ordinal)
    normalized_action = int(action_ordinal_in_round)
    if not Guest.objects.filter(pk=target_guest_id, manor_id=int(plan.manor_id)).exists():
        raise V2MaintenanceError("arena training assignment target is not owned by the profile Manor")
    by_slot = (
        ArenaReserveTrainingAssignment.objects.select_for_update()
        .filter(
            member_id=int(member_id),
            round_ordinal=normalized_round,
            action_ordinal_in_round=normalized_action,
        )
        .first()
    )
    by_guest = (
        ArenaReserveTrainingAssignment.objects.select_for_update()
        .filter(
            member_id=int(member_id),
            round_ordinal=normalized_round,
            guest_id=target_guest_id,
        )
        .first()
    )
    if by_slot is not None and int(by_slot.guest_id) != target_guest_id:
        raise V2MaintenanceError("arena training slot already belongs to another guest")
    if by_guest is not None and int(by_guest.action_ordinal_in_round) != normalized_action:
        raise V2MaintenanceError("arena training guest was assigned to multiple slots in one round")
    assignment = by_guest or by_slot
    if assignment is None:
        ArenaReserveTrainingAssignment.objects.create(
            member_id=int(member_id),
            guest_id=target_guest_id,
            round_ordinal=normalized_round,
            action_ordinal_in_round=normalized_action,
            operation_id=normalized_operation_id,
            status=ArenaReserveTrainingAssignment.Status.ASSIGNED,
        )
        return
    if assignment.operation_id != normalized_operation_id:
        raise V2MaintenanceError("arena training slot was claimed by another operation")
    if assignment.status == ArenaReserveTrainingAssignment.Status.RELEASED:
        raise V2MaintenanceError("released arena training assignment cannot be reused in the same round")


def _raise_if_external_reconciliation_unresolved(profile_id: int) -> None:
    if profile_id in unresolved_external_reconciliation_profile_ids({profile_id}):
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.PAUSED,
            "external_reconciliation_unresolved",
        )


def _assert_locked_profile_matches_plan(
    profile: BotProfile,
    plan: MaintenancePlan,
    *,
    scheduled_cycle_id: str | None = None,
) -> BotMaintenanceCycle | None:
    scheduled_cycle: BotMaintenanceCycle | None = None
    if profile.engine_version != V2_MAINTENANCE_ENGINE_VERSION:
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.INELIGIBLE,
            "profile_engine_ineligible",
        )
    if profile.state not in {BotProfile.State.ACTIVE, BotProfile.State.SLOWING}:
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.INELIGIBLE,
            "profile_state_ineligible",
        )
    if plan.trigger_policy.trigger is MaintenanceTrigger.SCHEDULED:
        has_arena_reserve = getattr(profile, "maintenance_has_arena_reserve", None)
        if has_arena_reserve is None:
            has_arena_reserve = BotProfile.objects.filter(
                pk=profile.id,
                arena_virtual_reserve__isnull=False,
            ).exists()
        if has_arena_reserve:
            raise _V2MaintenanceOutcomeError(
                MaintenanceOutcome.INELIGIBLE,
                "arena_reserve_owned",
            )
    if plan.scheduled_cycle_slot_due:
        if not scheduled_cycle_id:
            raise V2MaintenanceError("scheduled cycle due plan requires a durable cycle identity")
        scheduled_cycle = (
            BotMaintenanceCycle.objects.select_for_update()
            .filter(
                cycle_id=str(scheduled_cycle_id),
                profile_id=profile.id,
                trigger=BotMaintenanceCycle.Trigger.SCHEDULED,
                status=BotMaintenanceCycle.Status.OPEN,
            )
            .first()
        )
        if scheduled_cycle is None or scheduled_cycle.next_slot_due_at is None:
            raise _V2MaintenanceOutcomeError(
                MaintenanceOutcome.INELIGIBLE,
                "scheduled_cycle_missing_or_closed",
            )
        if scheduled_cycle.next_slot_due_at > plan.planned_at:
            raise _V2MaintenanceOutcomeError(
                MaintenanceOutcome.INELIGIBLE,
                "scheduled_cycle_slot_not_due",
            )
    elif not plan.trigger_policy.is_due(
        next_growth_at=profile.next_growth_at,
        now=plan.planned_at,
        arena_bypass_due=(
            plan.growth_policy.arena_acceleration_bypass.due
            if plan.trigger_policy.trigger is MaintenanceTrigger.ARENA_ACCELERATION
            else None
        ),
    ):
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.INELIGIBLE,
            "profile_not_due",
        )
    if int(profile.maintenance_sequence) != plan.expected_sequence:
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.BUSY,
            "maintenance_sequence_conflict",
        )
    identity = (
        int(profile.manor_id),
        int(profile.engine_version),
        int(profile.rng_version),
        int(profile.plan_schema_version),
        int(profile.policy_version),
        str(profile.policy_checksum),
    )
    expected_identity = (
        plan.manor_id,
        plan.engine_version,
        plan.rng_version,
        plan.plan_schema_version,
        plan.policy_version,
        plan.policy_checksum,
    )
    if identity != expected_identity:
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.BUSY,
            "maintenance_identity_conflict",
        )
    return scheduled_cycle


def _cycle_preamble_operation_id(cycle_id: str, label: str, guest_id: int | None = None) -> str:
    digest = sha256(f"{cycle_id}:{label}:{'' if guest_id is None else int(guest_id)}".encode("utf-8")).hexdigest()[:20]
    return f"vp-pre-{str(cycle_id)[:28]}-{str(label)[:8]}-{digest}"[:64]


def _cycle_child_operation_id(parent_operation_id: str, *, kind: str, ordinal: int) -> str:
    """Build a stable, bounded id for a child audit of one cycle operation."""

    digest = sha256(f"{str(parent_operation_id)}:{str(kind)}:{int(ordinal)}".encode("utf-8")).hexdigest()[:48]
    return f"vp-child-{digest}"


def _record_ordinary_salary_batch_locked(
    *,
    profile: BotProfile,
    cycle: BotMaintenanceCycle,
    salary_quote: SalaryBatchQuote,
    guests: tuple[Guest, ...],
    salary_result: Mapping[str, Any] | None,
    reason: str,
    now: datetime,
) -> bool:
    """Persist one idempotent salary parent and one child per roster member."""

    if cycle.salary_operation_id:
        return False
    unpaid_ids = tuple(int(value) for value in salary_quote.unpaid_guest_ids)
    paid = salary_result is not None
    paid_ids = unpaid_ids if paid else ()
    total_amount = int(salary_quote.total_amount) if paid else 0
    parent_operation_id = _cycle_preamble_operation_id(cycle.cycle_id, "salary")
    parent_reason = "" if paid else (str(reason or "salary_no_action")[:64])
    attempt_specs: list[dict[str, Any]] = [
        {
            "operation_id": parent_operation_id,
            "trigger": CycleTrigger.SCHEDULED,
            "attempt_ordinal": 1,
            "outcome": (BotMaintenanceAttempt.Outcome.APPLIED if paid else BotMaintenanceAttempt.Outcome.NO_ACTION),
            "reason": parent_reason,
            "cycle": cycle,
            "receipt_operation_id": parent_operation_id,
            "shadow_cost": {
                "kind": "salary_batch",
                "guest_ids": list(unpaid_ids),
                "paid_guest_ids": list(paid_ids),
                "quoted_silver": int(salary_quote.total_amount),
                "real_silver": total_amount,
            },
            "started_at": now,
        },
    ]
    guests_by_id = {int(guest.id): guest for guest in guests}
    for guest_id in unpaid_ids:
        guest = guests_by_id.get(guest_id)
        guest_salary = 0 if not paid or guest is None else int(get_guest_salary_for_rarity(guest.template.rarity))
        child_paid = guest_id in paid_ids
        attempt_specs.append(
            {
                "operation_id": _cycle_preamble_operation_id(cycle.cycle_id, "salary", guest_id),
                "trigger": CycleTrigger.SCHEDULED,
                "attempt_ordinal": 1,
                "outcome": (
                    BotMaintenanceAttempt.Outcome.APPLIED if child_paid else BotMaintenanceAttempt.Outcome.NO_ACTION
                ),
                "reason": ("" if child_paid else parent_reason),
                "cycle": cycle,
                "receipt_operation_id": parent_operation_id,
                "shadow_cost": {
                    "kind": "salary_child",
                    "guest_id": guest_id,
                    "quoted_silver": guest_salary,
                    "real_silver": guest_salary if child_paid else 0,
                },
                "started_at": now,
            }
        )
    record_durable_attempts_locked(
        profile,
        attempts=tuple(attempt_specs),
        return_objects=False,
        assume_new=True,
    )
    cycle.salary_operation_id = parent_operation_id
    cycle.last_reason = parent_reason or "salary_applied"
    payload = dict(cycle.payload or {})
    payload["salary_batch"] = {
        "operation_id": parent_operation_id,
        "guest_ids": list(unpaid_ids),
        "paid_guest_ids": list(paid_ids),
        "quoted_silver": int(salary_quote.total_amount),
        "real_silver": total_amount,
    }
    cycle.payload = payload
    return True


def _run_ordinary_guest_healing_sweep_locked(
    *,
    profile: BotProfile,
    manor: Manor,
    guests: tuple[Guest, ...],
    resource_snapshot: ResourcePlanningSnapshot,
    cycle: BotMaintenanceCycle,
    now: datetime,
) -> bool:
    """Apply the one-per-cycle 1000-silver roster healing preamble."""

    if cycle.healing_operation_id:
        return False
    operation_id = f"{str(cycle.cycle_id)[:57]}-h"
    healable = tuple(
        guest
        for guest in guests
        if guest.status in {GuestStatus.IDLE, GuestStatus.INJURED} and int(guest.current_hp) < int(guest.max_hp)
    )
    total_cost = 1000 * len(healable)
    available_silver = dict(resource_snapshot.spendable_resources).get(ResourceType.SILVER, 0)
    outcome = BotMaintenanceAttempt.Outcome.NO_ACTION
    reason = "no_guests_to_heal"
    healed_ids: list[int] = []
    if healable and int(available_silver) >= total_cost:
        try:
            spend_resources_locked(
                manor,
                {ResourceType.SILVER: total_cost},
                note="虚拟玩家培养前全员治疗",
                reason=ResourceEvent.Reason.ITEM_USE,
                sync_production=False,
            )
            for guest in healable:
                guest.restore_full_hp()
                healed_ids.append(int(guest.id))
            outcome = BotMaintenanceAttempt.Outcome.APPLIED
            reason = ""
        except InsufficientResourceError:
            outcome = BotMaintenanceAttempt.Outcome.NO_ACTION
            reason = "healing_insufficient_resource"
    elif healable:
        reason = "healing_insufficient_resource"
    payload = {
        "kind": "guest_healing_sweep",
        "silver": total_cost if healed_ids else 0,
        "requested_silver": total_cost,
        "guest_ids": [int(guest.id) for guest in healable],
        "healed_guest_ids": healed_ids,
        "real_silver": total_cost if healed_ids else 0,
    }
    attempt_specs: list[dict[str, Any]] = [
        {
            "operation_id": operation_id,
            "trigger": CycleTrigger.SCHEDULED,
            "attempt_ordinal": 1,
            "outcome": outcome,
            "reason": reason,
            "cycle": cycle,
            "receipt_operation_id": operation_id,
            "shadow_cost": payload,
            "started_at": now,
        },
    ]
    for guest in healable:
        guest_healed = int(guest.id) in healed_ids
        attempt_specs.append(
            {
                "operation_id": _cycle_preamble_operation_id(cycle.cycle_id, "healing", int(guest.id)),
                "trigger": CycleTrigger.SCHEDULED,
                "attempt_ordinal": 1,
                "outcome": (
                    BotMaintenanceAttempt.Outcome.APPLIED if guest_healed else BotMaintenanceAttempt.Outcome.NO_ACTION
                ),
                "reason": ("" if guest_healed else reason),
                "cycle": cycle,
                "receipt_operation_id": operation_id,
                "shadow_cost": {
                    "kind": "guest_healing_child",
                    "guest_id": int(guest.id),
                    "quoted_silver": 1000,
                    "real_silver": 1000 if guest_healed else 0,
                },
                "started_at": now,
            }
        )
    record_durable_attempts_locked(
        profile,
        attempts=tuple(attempt_specs),
        return_objects=False,
        assume_new=True,
    )
    cycle.healing_operation_id = operation_id
    cycle.last_reason = reason or "healing_applied"
    cycle_payload = dict(cycle.payload or {})
    cycle_payload["healing_sweep"] = {
        "operation_id": operation_id,
        "guest_ids": [int(guest.id) for guest in healable],
        "healed_guest_ids": healed_ids,
        "quoted_silver": total_cost,
        "real_silver": total_cost if healed_ids else 0,
    }
    cycle.payload = cycle_payload
    return True


@transaction.atomic
@record_maintenance_stage(STAGE_ACTION_DOMAIN_WRITES)
def execute_virtual_player_v2_maintenance_plan(
    plan: MaintenancePlan,
    *,
    _policy_release: BotPolicyRelease | None = None,
    _routing_snapshot: RuntimeRoutingSnapshot | None = None,
    _grain_template: ItemTemplate | None = None,
    _grain_template_resolved: bool = False,
    _scheduled_cycle_id: str | None = None,
    _run_ordinary_preamble: bool = False,
) -> MaintenanceResult:
    """Revalidate and atomically execute one frozen V2 maintenance plan."""
    if not isinstance(plan, MaintenancePlan):
        raise V2MaintenanceError("plan must be a MaintenancePlan")
    try:
        profile = profile_store.lock_maintained_profile(
            plan.profile_id,
            nowait=True,
            expected_v2_routing=_routing_snapshot,
            include_arena_reserve_guard=(plan.trigger_policy.trigger is MaintenanceTrigger.SCHEDULED),
        )
    except profile_store.ProfileLockUnavailable as exc:
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.BUSY,
            "profile_busy",
        ) from exc
    if profile is None:
        still_eligible = BotProfile.objects.filter(
            pk=plan.profile_id,
            engine_version=V2_MAINTENANCE_ENGINE_VERSION,
            state__in=[BotProfile.State.ACTIVE, BotProfile.State.SLOWING],
        ).exists()
        raise _V2MaintenanceOutcomeError(
            (MaintenanceOutcome.BUSY if still_eligible else MaintenanceOutcome.INELIGIBLE),
            "profile_busy" if still_eligible else "profile_ineligible",
        )
    locked_scheduled_cycle = _assert_locked_profile_matches_plan(
        profile,
        plan,
        scheduled_cycle_id=_scheduled_cycle_id,
    )
    routing_guard_matches = bool(getattr(profile, "maintenance_routing_matches", False))
    if _routing_snapshot is None or not routing_guard_matches:
        try:
            routing = read_virtual_player_routing()
        except RuntimeRoutingError as exc:
            raise _V2MaintenanceOutcomeError(
                MaintenanceOutcome.PAUSED,
                "routing_unavailable",
            ) from exc
    else:
        routing = _routing_snapshot
    if (
        routing.maintenance_mode is not MaintenanceMode.V2_ACTIVE
        or not routing.persisted
        or routing.revision != plan.routing_revision
    ):
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.PAUSED,
            "maintenance_routing_changed",
        )

    unresolved_reconciliation = BotExternalStrengthReconciliation.objects.filter(
        profile_id=profile.id,
    ).exclude(status=BotExternalStrengthReconciliation.Status.APPLIED)
    current_grain_item = (
        InventoryItem.objects.filter(
            manor_id=OuterRef("pk"),
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            template__key=GRAIN_ITEM_KEY,
        )
        .order_by("id")
        .values("quantity")[:1]
    )
    manor = (
        Manor.objects.annotate(
            maintenance_current_grain=Subquery(current_grain_item),
            maintenance_has_unresolved_reconciliation=Exists(unresolved_reconciliation),
        )
        .select_for_update()
        .filter(pk=plan.manor_id)
        .first()
    )
    if manor is None or int(profile.manor_id) != int(manor.id):
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.INELIGIBLE,
            "profile_manor_ineligible",
        )
    if bool(getattr(manor, "maintenance_has_unresolved_reconciliation", False)):
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.PAUSED,
            "external_reconciliation_unresolved",
        )

    salary_paid = SalaryPayment.objects.filter(
        guest_id=OuterRef("pk"),
        for_date=timezone.localdate(plan.planned_at),
    )
    revalidation_guest_query = (
        Guest.objects.filter(manor_id=manor.id)
        .select_related("template")
        .annotate(maintenance_salary_paid=Exists(salary_paid))
    )
    if connection.features.has_select_for_update_of:
        revalidation_guest_query = revalidation_guest_query.select_for_update(of=("self",))
    else:
        revalidation_guest_query = revalidation_guest_query.select_for_update()
    revalidation_guests = tuple(revalidation_guest_query.order_by("id"))
    locked_guest = None
    if plan.target_id is not None and plan.action_kind != "guest_healing":
        locked_guest = next(
            (guest for guest in revalidation_guests if int(guest.id) == int(plan.target_id)),
            None,
        )
        if locked_guest is None:
            raise _V2MaintenanceOutcomeError(
                MaintenanceOutcome.BUSY,
                "maintenance_target_changed",
            )
    paid_guest_ids = frozenset(
        int(guest.id) for guest in revalidation_guests if bool(getattr(guest, "maintenance_salary_paid", False))
    )
    planning_snapshot = plan.planning_snapshot
    fast_batch_revalidation = bool(
        planning_snapshot is not None
        and _policy_release is not None
        and not plan.resource_production_deltas
        and plan.forced_settlement_decision.combined_units == 0
        and manor.resource_updated_at == plan.planned_at
    )
    if fast_batch_revalidation:
        assert planning_snapshot is not None
        source_profile = planning_snapshot.profile
        source_manor = source_profile.manor
        if _profile_precondition_payload(profile) != _profile_precondition_payload(source_profile):
            raise _V2MaintenanceOutcomeError(
                MaintenanceOutcome.BUSY,
                "maintenance_precondition_changed",
            )
        if _manor_precondition_payload(manor) != _manor_precondition_payload(source_manor):
            raise _V2MaintenanceOutcomeError(
                MaintenanceOutcome.BUSY,
                "maintenance_precondition_changed",
            )
        current_guest_ids = tuple(int(guest.id) for guest in revalidation_guests)
        if current_guest_ids != plan.salary_quote.guest_ids:
            raise _V2MaintenanceOutcomeError(
                MaintenanceOutcome.BUSY,
                "maintenance_precondition_changed",
            )
        current_unpaid_guest_ids = tuple(guest_id for guest_id in current_guest_ids if guest_id not in paid_guest_ids)
        if current_unpaid_guest_ids != plan.salary_quote.unpaid_guest_ids:
            raise _V2MaintenanceOutcomeError(
                MaintenanceOutcome.BUSY,
                "maintenance_precondition_changed",
            )
        current_grain = getattr(manor, "maintenance_current_grain", None)
        expected_resources = dict(plan.resource_planning_snapshot.current_resources)
        if int(manor.silver or 0) != int(expected_resources.get(ResourceType.SILVER, 0)) or int(
            current_grain if current_grain is not None else manor.grain or 0
        ) != int(expected_resources.get(ResourceType.GRAIN, 0)):
            raise _V2MaintenanceOutcomeError(
                MaintenanceOutcome.BUSY,
                "resource_snapshot_changed",
            )
        revalidation_strength = load_manor_strength_summary(
            manor_id=manor.id,
            guests=revalidation_guests,
        )
        if revalidation_strength != plan.strength_before:
            raise _V2MaintenanceOutcomeError(
                MaintenanceOutcome.BUSY,
                "maintenance_precondition_changed",
            )
        revalidation_buildings = planning_snapshot.buildings
        revalidation_technologies = planning_snapshot.technologies
        if plan.action_kind == BuildingUpgradeActionSpec.action_kind:
            revalidation_buildings = tuple(
                Building.objects.filter(manor_id=manor.id)
                .select_related("building_type")
                .order_by("building_type__key", "id")
            )
        elif plan.action_kind == TechnologyUpgradeActionSpec.action_kind:
            revalidation_technologies = tuple(
                PlayerTechnology.objects.select_for_update().filter(manor_id=manor.id).order_by("tech_key", "id")
            )
        revalidation_technology_levels = {
            str(technology.tech_key): int(technology.level) for technology in revalidation_technologies
        }
        revalidation_troop_counts = planning_snapshot.troop_counts
        if plan.action_kind == "troop_recruitment" and int(manor.retainer_count or 0) > 0:
            revalidation_troop_counts = _troop_counts_by_key(int(manor.id))
        revalidation_warehouse_items = planning_snapshot.warehouse_items
        revalidation_grain_template = planning_snapshot.grain_template
        revalidation_medicine_items: tuple[InventoryItem, ...] = ()
        production_basis = planning_snapshot.production_basis
        skip_resource_settlement = True
        revalidated = plan
    else:
        revalidation_buildings = tuple(
            Building.objects.filter(manor_id=manor.id)
            .select_related("building_type")
            .order_by("building_type__key", "id")
        )
        revalidation_technologies = tuple(
            PlayerTechnology.objects.select_for_update().filter(manor_id=manor.id).order_by("tech_key", "id")
        )
        revalidation_technology_levels = {
            str(technology.tech_key): int(technology.level) for technology in revalidation_technologies
        }
        revalidation_strength = load_manor_strength_summary(
            manor_id=manor.id,
            guests=revalidation_guests,
        )
        production_basis = (
            load_resource_production_basis(manor)
            if _policy_release is None
            else load_resource_production_basis(
                manor,
                guest_count=len(revalidation_guests),
                troop_count=int(revalidation_strength.components.get("troop_total", 0)),
                buildings=revalidation_buildings,
                technology_levels=revalidation_technology_levels,
            )
        )
        revalidation_troop_counts = () if int(manor.retainer_count or 0) <= 0 else _troop_counts_by_key(int(manor.id))
        revalidation_warehouse_items = _available_warehouse_items(int(manor.id))
        revalidation_grain_template = next(
            (item.template for item in revalidation_warehouse_items if item.template.key == GRAIN_ITEM_KEY),
            None,
        )
        revalidation_medicine_items = (
            tuple(
                item
                for item in revalidation_warehouse_items
                if item.template.effect_type == ItemTemplate.EffectType.MEDICINE
            )
            if _healable_guests(revalidation_guests)
            else ()
        )
        virtual_pools = _refresh_virtual_projection_pools_for_plan(
            plan,
            plan.virtual_projection_pools or _load_virtual_projection_pools(planned_at=plan.planned_at),
        )
        revalidation_snapshot = _MaintenancePlanningSnapshot(
            profile=profile,
            guests=revalidation_guests,
            buildings=revalidation_buildings,
            technologies=revalidation_technologies,
            gear_items=_equipped_gear_items(
                int(manor.id),
                guest_ids=tuple(int(guest.id) for guest in revalidation_guests),
            ),
            strength=revalidation_strength,
            paid_guest_ids=paid_guest_ids,
            troop_counts=revalidation_troop_counts,
            medicine_items=revalidation_medicine_items,
            guest_skills=_guest_skills_for_guests(revalidation_guests),
            skills=_skills_for_warehouse_items(revalidation_warehouse_items),
            warehouse_items=revalidation_warehouse_items,
            # Policy 2 uses the batch-loaded virtual inventory pool below; the
            # legacy profile allowlist is not part of its candidate source.
            inventory_templates=(),
            production_basis=production_basis,
            policy_release=_policy_release,
            virtual_skill_books=virtual_pools.skill_books,
            virtual_skills=virtual_pools.skills,
            virtual_gear_templates=virtual_pools.gear_templates,
            virtual_inventory_templates=virtual_pools.inventory_templates,
            rare_inventory_quantity_today=virtual_pools.rare_inventory_quantity_today,
        )
        skip_resource_settlement = False

        try:
            revalidated = _build_v2_maintenance_plan_from_profile(
                profile=profile,
                manor=manor,
                routing=routing,
                trigger_policy=plan.trigger_policy,
                planned_at=plan.planned_at,
                minimum_guest_count=plan.minimum_guest_count,
                minimum_guest_level=plan.minimum_guest_level,
                guest_rarity_cap=plan.guest_rarity_cap,
                max_guest_level_step=plan.max_guest_level_step,
                arena_growth_objective=plan.arena_growth_objective,
                arena_excluded_training_guest_ids=plan.arena_excluded_training_guest_ids,
                cycle_covered_action_kinds=plan.cycle_covered_action_kinds,
                cycle_high_cost_actions_used=plan.cycle_high_cost_actions_used,
                cycle_budget_state=plan.cycle_budget_state,
                cycle_pacing=plan.cycle_pacing,
                candidate_exclusions=plan.candidate_exclusions,
                scheduled_cycle_slot_due=plan.scheduled_cycle_slot_due,
                planning_snapshot=revalidation_snapshot,
                frozen_control_snapshot_digest=plan.control_snapshot_digest,
            )
        except _V2MaintenanceOutcomeError:
            raise
        except (
            DevelopmentPlanError,
            InvalidStrengthBudgetError,
            MaintenanceActionSpecError,
            CandidateAssessmentError,
            MaintenanceCandidateError,
            MaintenanceUpgradeCandidateError,
            ResourcePlanningError,
            MaintenanceRuleError,
            PolicyRegistryError,
            ProjectionRuleError,
            ReferenceSnapshotError,
            UnsupportedRandomDomainError,
            UnsupportedRngVersionError,
            V2MaintenanceError,
            VirtualPlayerConfigError,
        ) as exc:
            raise _V2MaintenanceOutcomeError(
                MaintenanceOutcome.PAUSED,
                "v2_profile_or_policy_invalid",
            ) from exc
        if revalidated.precondition_digest != plan.precondition_digest:
            raise _V2MaintenanceOutcomeError(
                MaintenanceOutcome.BUSY,
                "maintenance_precondition_changed",
            )
        if revalidated != plan:
            raise _V2MaintenanceOutcomeError(
                MaintenanceOutcome.BUSY,
                "maintenance_plan_changed",
            )

    arena_free_subsidy = bool(
        plan.policy_version == 2
        and plan.trigger_policy.trigger is MaintenanceTrigger.ARENA_ACCELERATION
        and plan.arena_growth_objective is not None
    )
    if not arena_free_subsidy and not skip_resource_settlement:
        _apply_due_resource_production_settlement_locked(
            profile,
            manor,
            now=plan.planned_at,
            expected_production_deltas=plan.resource_production_deltas,
            expected_decision=plan.forced_settlement_decision,
            production_basis=production_basis,
            grain_template=(
                revalidation_grain_template if revalidation_grain_template is not None else _grain_template
            ),
            grain_template_resolved=(revalidation_grain_template is not None or _grain_template_resolved),
        )
    salary_payment_failed = False
    salary_result: Mapping[str, Any] | None = None
    salary_failure_reason = ""
    if plan.salary_quote.unpaid_guest_ids and not arena_free_subsidy:
        try:
            salary_result = pay_all_salaries_locked(
                manor,
                for_date=plan.salary_quote.for_date,
                _quote=revalidated.salary_quote,
                _locked_guests=revalidation_guests,
            )
        except InsufficientResourceError:
            if plan.resource_planning_snapshot.current_salary_payable:
                raise _V2MaintenanceOutcomeError(
                    MaintenanceOutcome.BUSY,
                    "resource_snapshot_changed",
                )
            salary_payment_failed = True
            salary_failure_reason = "salary_shortfall"
        except (NoGuestsError, SalaryAlreadyPaidError) as exc:
            raise _V2MaintenanceOutcomeError(
                MaintenanceOutcome.BUSY,
                "maintenance_salary_changed",
            ) from exc
        else:
            if (
                int(salary_result["paid_count"]) != len(plan.salary_quote.unpaid_guest_ids)
                or int(salary_result["total_amount"]) != plan.salary_quote.total_amount
            ):
                raise V2MaintenanceError("committed salaries differ from the frozen salary quote")

    preamble_cycle: BotMaintenanceCycle | None = None
    if (
        _run_ordinary_preamble
        and _scheduled_cycle_id is not None
        and plan.policy_version == 2
        and plan.trigger_policy.trigger is MaintenanceTrigger.SCHEDULED
        and not arena_free_subsidy
    ):
        preamble_cycle = locked_scheduled_cycle
        if preamble_cycle is None:
            raise V2MaintenanceError("ordinary cycle preamble requires a locked scheduled cycle")
        salary_preamble_changed = _record_ordinary_salary_batch_locked(
            profile=profile,
            cycle=preamble_cycle,
            salary_quote=revalidated.salary_quote,
            guests=revalidation_guests,
            salary_result=salary_result,
            reason=salary_failure_reason
            or ("salary_already_paid" if not revalidated.salary_quote.unpaid_guest_ids else ""),
            now=plan.planned_at,
        )
        healing_preamble_changed = _run_ordinary_guest_healing_sweep_locked(
            profile=profile,
            manor=manor,
            guests=revalidation_guests,
            resource_snapshot=revalidated.resource_planning_snapshot,
            cycle=preamble_cycle,
            now=plan.planned_at,
        )
        if salary_preamble_changed or healing_preamble_changed:
            preamble_cycle.save(
                update_fields=[
                    "salary_operation_id",
                    "healing_operation_id",
                    "last_reason",
                    "payload",
                    "updated_at",
                ]
            )

    budget_entries_after = prune_strength_budget_entries(
        plan.strength_budget_entries_before,
        now=plan.planned_at,
    )
    last_strength_increase_at_after = plan.last_strength_increase_at_before
    selected_assessment = plan.selected_candidate_assessment
    planning_rejection_reason = (
        ""
        if selected_assessment is None or selected_assessment.allowed
        else selected_assessment.primary_rejection_reason
    )
    outcome = MaintenanceOutcome.NO_ACTION
    action_kind = plan.action_kind if plan.intent is not None else ""
    reason = planning_rejection_reason or (
        "arena_action_unavailable"
        if plan.trigger_policy.trigger is MaintenanceTrigger.ARENA_ACCELERATION
        else "no_eligible_candidate"
    )
    if plan.intent is not None and (
        not planning_rejection_reason
        and (arena_free_subsidy or not salary_payment_failed or _salary_fallback_action_allowed(plan))
    ):
        if plan.action_kind == "guest_healing":
            try:
                with transaction.atomic():
                    assert plan.target_id is not None
                    assert plan.medicine_quote is not None
                    apply_medicine_item_for_guest_locked(
                        manor,
                        plan.target_id,
                        plan.medicine_quote.item_id,
                        expected_quote=plan.medicine_quote,
                    )
                    final_strength = revalidation_strength
                    if final_strength != plan.intent.strength_after:
                        raise V2MaintenanceError("committed guest healing strength differs from its frozen intent")
            except (InsufficientResourceError, InsufficientStockError) as exc:
                raise _V2MaintenanceOutcomeError(
                    MaintenanceOutcome.BUSY,
                    "resource_snapshot_changed",
                ) from exc
            except (
                GuestFullHpError,
                GuestItemConfigurationError,
                GuestItemOwnershipError,
                GuestNotIdleError,
                GuestOwnershipError,
                InvalidHealAmountError,
            ):
                reason = MaintenanceNoActionReason.DOMAIN_CONSTRAINT.value
            else:
                outcome = MaintenanceOutcome.APPLIED
                action_kind = plan.action_kind
                reason = ""
        else:
            decision = evaluate_controlled_action(
                policy=plan.growth_policy,
                intent=plan.intent,
                now=plan.planned_at,
                last_strength_increase_at=plan.last_strength_increase_at_before,
                budget_entries=plan.strength_budget_entries_before,
                policy_version=plan.policy_version,
                source_sample_count=plan.reference_sample_count,
                source_strength_cap=plan.reference_strength_cap,
                target_sample_count=plan.target_reference_sample_count,
                target_strength_cap=plan.target_reference_strength_cap,
                allow_roster_expansion=(plan.action_kind == GuestRecruitmentActionSpec.action_kind),
                allow_arena_acceleration=(
                    plan.trigger_policy.trigger is MaintenanceTrigger.ARENA_ACCELERATION
                    and plan.action_kind != GuestRecruitmentActionSpec.action_kind
                ),
                allow_arena_growth_cap_bypass=arena_free_subsidy,
            )
            budget_entries_after = decision.budget_entries_after
            if not decision.allowed:
                reason = (
                    decision.reason.value
                    if decision.reason is not None
                    else MaintenanceNoActionReason.DOMAIN_CONSTRAINT.value
                )
            else:
                try:
                    with transaction.atomic():
                        final_strength_guests = revalidation_guests
                        final_strength_buildings = revalidation_buildings
                        final_troop_total = int(revalidation_strength.components.get("troop_total", 0))
                        if plan.action_kind == "training":
                            assert plan.target_id is not None
                            assert plan.rng_seed is not None
                            assert locked_guest is not None
                            if arena_free_subsidy:
                                applied_guest = apply_training_locked(
                                    manor,
                                    plan.target_id,
                                    levels=plan.training_levels,
                                    rng=random.Random(plan.rng_seed),
                                    sync_production=False,
                                    free_subsidy=True,
                                    grain_template=(
                                        revalidation_grain_template
                                        if revalidation_grain_template is not None
                                        else _grain_template
                                    ),
                                    grain_template_resolved=(
                                        revalidation_grain_template is not None or _grain_template_resolved
                                    ),
                                    _locked_guest=locked_guest,
                                )
                            elif plan.policy_version == 2:
                                # V2 training starts the formal timer.  The
                                # level is changed only by the normal training
                                # completion worker, so this action cannot
                                # create a high-level direct jump.
                                from guests.services.training import (
                                    ensure_auto_training,
                                    reduce_training_time_for_guest,
                                )

                                if not arena_free_subsidy:
                                    training_quote = quote_training(locked_guest, levels=1)
                                    spend_resources_locked(
                                        manor,
                                        training_quote.resource_cost,
                                        note=f"培养 {locked_guest.template.name}",
                                        reason=ResourceEvent.Reason.TRAINING_COST,
                                        sync_production=False,
                                        grain_template=(
                                            revalidation_grain_template
                                            if revalidation_grain_template is not None
                                            else _grain_template
                                        ),
                                        grain_template_resolved=(
                                            revalidation_grain_template is not None or _grain_template_resolved
                                        ),
                                    )
                                if not ensure_auto_training(locked_guest):
                                    raise GuestTrainingInProgressError(locked_guest)
                                locked_guest.refresh_from_db()
                                if locked_guest.training_complete_at is None:
                                    raise GuestTrainingInProgressError(locked_guest)
                                remaining_seconds = max(
                                    1,
                                    int((locked_guest.training_complete_at - timezone.now()).total_seconds()),
                                )
                                reduce_training_time_for_guest(
                                    locked_guest,
                                    max(60, min(remaining_seconds, remaining_seconds // 4 or 1)),
                                )
                                locked_guest.refresh_from_db()
                                applied_guest = locked_guest
                            else:
                                applied_guest = apply_training_locked(
                                    manor,
                                    plan.target_id,
                                    levels=plan.training_levels,
                                    rng=random.Random(plan.rng_seed),
                                    sync_production=False,
                                    grain_template=(
                                        revalidation_grain_template
                                        if revalidation_grain_template is not None
                                        else _grain_template
                                    ),
                                    grain_template_resolved=(
                                        revalidation_grain_template is not None or _grain_template_resolved
                                    ),
                                    _locked_guest=locked_guest,
                                )
                            final_strength_guests = tuple(
                                applied_guest if int(guest.id) == int(applied_guest.id) else guest
                                for guest in revalidation_guests
                            )
                        elif plan.action_kind == "troop_recruitment":
                            assert plan.troop_recruitment_quote is not None
                            if getattr(plan.troop_recruitment_quote, "source", "recruitment") == "virtual":
                                from battle.models import TroopTemplate

                                virtual_quote = plan.troop_recruitment_quote
                                troop_template = (
                                    TroopTemplate.objects.select_for_update()
                                    .filter(key=virtual_quote.troop_key)
                                    .first()
                                )
                                if troop_template is None:
                                    raise TroopRecruitmentError("virtual troop template is missing")
                                spend_resources_locked(
                                    manor,
                                    {
                                        ResourceType.SILVER: int(virtual_quote.virtual_silver_cost),
                                        ResourceType.GRAIN: int(virtual_quote.virtual_grain_cost),
                                    },
                                    note=f"虚拟培养护院 {virtual_quote.troop_name}",
                                    reason=ResourceEvent.Reason.RECRUIT_COST,
                                    sync_production=False,
                                    grain_template=(
                                        revalidation_grain_template
                                        if revalidation_grain_template is not None
                                        else _grain_template
                                    ),
                                    grain_template_resolved=(
                                        revalidation_grain_template is not None or _grain_template_resolved
                                    ),
                                )
                                troop_row = (
                                    PlayerTroop.objects.select_for_update()
                                    .filter(manor_id=manor.id, troop_template_id=troop_template.id)
                                    .first()
                                )
                                if troop_row is None:
                                    troop_row = PlayerTroop.objects.create(
                                        manor=manor,
                                        troop_template=troop_template,
                                        count=virtual_quote.quantity,
                                    )
                                else:
                                    troop_row.count = int(troop_row.count) + int(virtual_quote.quantity)
                                    troop_row.save(update_fields=["count", "updated_at"])
                            else:
                                recruit_troops_locked(
                                    manor,
                                    plan.troop_recruitment_quote,
                                    now=plan.planned_at,
                                )
                            final_troop_total += int(plan.troop_recruitment_quote.quantity)
                        elif plan.action_kind == GuestRecruitmentActionSpec.action_kind:
                            assert isinstance(plan.action_spec, GuestRecruitmentActionSpec)
                            if plan.trigger_policy.trigger is not MaintenanceTrigger.ARENA_ACCELERATION:
                                raise V2MaintenanceError("instant guest recruitment is reserved for arena acceleration")
                            recruitment_spec = plan.action_spec
                            if remaining_guest_capacity(manor) < recruitment_spec.quantity:
                                raise GuestCapacityFullError()
                            template = (
                                GuestTemplate.objects.select_for_update()
                                .filter(
                                    pk=recruitment_spec.template_id,
                                    key=recruitment_spec.template_key,
                                    recruitable=True,
                                    is_hermit=False,
                                )
                                .first()
                            )
                            if template is None:
                                raise GuestRecruitmentTemplateChangedError()
                            if str(template.rarity) != recruitment_spec.rarity or str(template.archetype) != (
                                recruitment_spec.archetype
                            ):
                                raise GuestRecruitmentTemplateChangedError()
                            if (
                                plan.recruitment_rarity_cap is not None
                                and _GUEST_RARITY_RANK.get(str(template.rarity), len(_GUEST_RARITY_RANK))
                                > _GUEST_RARITY_RANK[plan.recruitment_rarity_cap]
                            ):
                                raise GuestRecruitmentTemplateChangedError()
                            created_guest_rows: list[Guest] = []
                            for ordinal in range(recruitment_spec.quantity):
                                rng = random.Random(recruitment_spec.rng_seed + ordinal)
                                created_guest_rows.append(
                                    create_guest_from_template(
                                        manor=manor,
                                        template=template,
                                        rarity=recruitment_spec.rarity,
                                        archetype=recruitment_spec.archetype,
                                        custom_name=build_recruitment_custom_name(template, rng),
                                        rng=rng,
                                        grant_skills=False,
                                        save=True,
                                    )
                                )
                            created_guests = tuple(created_guest_rows)
                            final_strength_guests = (*revalidation_guests, *created_guests)
                        elif plan.action_kind == BuildingUpgradeActionSpec.action_kind:
                            assert isinstance(
                                plan.action_spec,
                                BuildingUpgradeActionSpec,
                            )
                            locked_building = (
                                Building.objects.select_for_update()
                                .select_related("building_type")
                                .filter(
                                    pk=plan.action_spec.building_id,
                                    manor_id=manor.id,
                                )
                                .first()
                            )
                            if locked_building is None:
                                raise BuildingUpgradeQuoteStaleError("building upgrade target no longer exists")
                            building_quote = quote_building_upgrade(
                                manor,
                                locked_building,
                                buildings=revalidation_buildings,
                                technology_levels=revalidation_technology_levels,
                            )
                            if not _building_quote_matches_spec(
                                building_quote,
                                plan.action_spec,
                            ):
                                raise BuildingUpgradeQuoteStaleError("building upgrade inputs changed")
                            if arena_free_subsidy:
                                if plan.action_spec.building_key != BuildingKeys.JUXIAN_ZHUANG:
                                    raise V2MaintenanceError("arena free building action must target Juxianzhuang")
                                apply_building_upgrade_free_locked(
                                    manor,
                                    locked_building,
                                    building_quote,
                                    buildings=revalidation_buildings,
                                    technology_levels=revalidation_technology_levels,
                                )
                            elif plan.policy_version == 2:
                                start_building_upgrade_locked(
                                    manor,
                                    locked_building,
                                    building_quote,
                                    sync_production=False,
                                    buildings=revalidation_buildings,
                                    technology_levels=revalidation_technology_levels,
                                    now=timezone.now(),
                                )
                            else:
                                apply_building_upgrade_locked(
                                    manor,
                                    locked_building,
                                    building_quote,
                                    sync_production=False,
                                    buildings=revalidation_buildings,
                                    technology_levels=revalidation_technology_levels,
                                )
                            final_strength_buildings = tuple(
                                locked_building if int(building.id) == int(locked_building.id) else building
                                for building in revalidation_buildings
                            )
                        elif plan.action_kind == EquipmentEquipActionSpec.action_kind:
                            assert locked_guest is not None
                            assert isinstance(
                                plan.action_spec,
                                EquipmentEquipActionSpec,
                            )
                            if plan.action_spec.source == "virtual":
                                equip_guest_from_virtual_template_locked(
                                    manor,
                                    locked_guest,
                                    plan.action_spec.item_template_id,
                                    expected_template_key=plan.action_spec.item_key,
                                    expected_slot=plan.action_spec.slot,
                                )
                            else:
                                equip_guest_from_inventory_locked(
                                    manor,
                                    locked_guest,
                                    plan.action_spec.inventory_item_id,
                                    expected_template_key=plan.action_spec.item_key,
                                    expected_slot=plan.action_spec.slot,
                                )
                            final_strength_guests = tuple(
                                locked_guest if int(guest.id) == int(locked_guest.id) else guest
                                for guest in revalidation_guests
                            )
                        elif plan.action_kind == SkillLearningActionSpec.action_kind:
                            assert locked_guest is not None
                            assert isinstance(
                                plan.action_spec,
                                SkillLearningActionSpec,
                            )
                            if plan.action_spec.source == "virtual":
                                learn_guest_skill_from_virtual_book_locked(
                                    manor,
                                    locked_guest,
                                    plan.action_spec.item_template_id,
                                    expected_skill_id=plan.action_spec.skill_id,
                                    expected_item_key=plan.action_spec.item_key,
                                )
                            else:
                                learn_guest_skill_locked(
                                    manor,
                                    locked_guest,
                                    plan.action_spec.inventory_item_id,
                                    expected_skill_id=plan.action_spec.skill_id,
                                )
                        elif plan.action_kind == InventoryAcquisitionActionSpec.action_kind:
                            assert isinstance(
                                plan.action_spec,
                                InventoryAcquisitionActionSpec,
                            )
                            _apply_inventory_acquisition_locked(
                                manor,
                                plan.action_spec,
                                now=plan.planned_at,
                            )
                        elif plan.action_kind == TechnologyUpgradeActionSpec.action_kind:
                            assert isinstance(
                                plan.action_spec,
                                TechnologyUpgradeActionSpec,
                            )
                            technology_quote = quote_technology_upgrade(
                                manor,
                                plan.action_spec.technology_key,
                                technologies=revalidation_technologies,
                            )
                            if not _technology_quote_matches_spec(
                                technology_quote,
                                plan.action_spec,
                            ):
                                raise TechnologyUpgradeQuoteStaleError("technology upgrade inputs changed")
                            if plan.policy_version == 2:
                                start_technology_upgrade_locked(
                                    manor,
                                    technology_quote,
                                    sync_production=False,
                                    technologies=revalidation_technologies,
                                    technologies_locked=True,
                                    now=timezone.now(),
                                )
                            else:
                                apply_technology_upgrade_locked(
                                    manor,
                                    technology_quote,
                                    sync_production=False,
                                    technologies=revalidation_technologies,
                                    technologies_locked=True,
                                )
                        else:
                            raise V2MaintenanceError(f"unsupported V2 maintenance action: {plan.action_kind}")

                        final_strength = _build_locked_snapshot_strength(
                            manor=manor,
                            guests=final_strength_guests,
                            buildings=final_strength_buildings,
                            troop_total=final_troop_total,
                        )
                        if final_strength != plan.intent.strength_after:
                            action_label = {
                                BuildingUpgradeActionSpec.action_kind: ("building upgrade"),
                                EquipmentEquipActionSpec.action_kind: "equipment equip",
                                GuestRecruitmentActionSpec.action_kind: "guest recruitment",
                                "training": "training",
                                "troop_recruitment": "troop recruitment",
                                SkillLearningActionSpec.action_kind: "skill learning",
                                InventoryAcquisitionActionSpec.action_kind: ("inventory acquisition"),
                                TechnologyUpgradeActionSpec.action_kind: ("technology upgrade"),
                            }[plan.action_kind]
                            raise V2MaintenanceError(
                                f"committed {action_label} strength differs from its frozen intent"
                            )
                except (InsufficientResourceError, InsufficientStockError) as exc:
                    raise _V2MaintenanceOutcomeError(
                        MaintenanceOutcome.BUSY,
                        "resource_snapshot_changed",
                    ) from exc
                except (
                    BuildingConcurrentUpgradeLimitError,
                    BuildingMaxLevelError,
                    BuildingUpgradeQuoteStaleError,
                    BuildingUpgradingError,
                    EquipmentError,
                    EquipmentSlotFullError,
                    GuestCapacityFullError,
                    GuestRecruitmentTemplateChangedError,
                    GuestMaxLevelError,
                    GuestItemConfigurationError,
                    GuestItemOwnershipError,
                    GuestNotIdleError,
                    GuestNotRequirementError,
                    GuestSkillAlreadyLearnedError,
                    GuestTrainingInProgressError,
                    InventoryAcquisitionUnavailable,
                    ItemNotFoundError,
                    SkillSlotFullError,
                    TechnologyConcurrentUpgradeLimitError,
                    TechnologyMaxLevelError,
                    TechnologyNotFoundError,
                    TechnologyUpgradeInProgressError,
                    TechnologyUpgradeQuoteStaleError,
                    TroopRecruitmentError,
                ) as exc:
                    if plan.policy_version == 2 and plan.trigger_policy.trigger is MaintenanceTrigger.SCHEDULED:
                        raise _V2MaintenanceCandidateRejected(
                            business_key=plan.intent.business_key,
                            reason="candidate_domain_constraint",
                        ) from exc
                    budget_entries_after = prune_strength_budget_entries(
                        plan.strength_budget_entries_before,
                        now=plan.planned_at,
                    )
                    reason = MaintenanceNoActionReason.DOMAIN_CONSTRAINT.value
                else:
                    outcome = MaintenanceOutcome.APPLIED
                    action_kind = plan.action_kind
                    reason = ""
                    if plan.intent.target_prestige_band != plan.current_prestige_band:
                        profile_store.set_current_prestige_band(
                            profile,
                            prestige_band=plan.intent.target_prestige_band,
                        )
                    last_strength_increase_at_after = decision.last_strength_increase_at_after

    committed_next_growth_at = (
        plan.next_growth_at_after if outcome is MaintenanceOutcome.APPLIED else plan.next_growth_at_after_no_action
    )
    committed_shadow_cost: Mapping[str, Any] = {}
    if arena_free_subsidy and outcome is MaintenanceOutcome.APPLIED:
        committed_shadow_cost = free_arena_shadow_cost()
    if arena_free_subsidy and plan.target_id is not None:
        committed_shadow_cost = {
            **committed_shadow_cost,
            "target_guest_id": int(plan.target_id),
        }
    result = profile_store.commit_maintenance_cycle(
        profile,
        trigger_policy=plan.trigger_policy,
        expected_sequence=plan.expected_sequence,
        now=plan.planned_at,
        outcome=outcome,
        expected_strength_budget_entries=plan.strength_budget_entries_before,
        strength_budget_entries_after=budget_entries_after,
        expected_last_strength_increase_at=(plan.last_strength_increase_at_before),
        last_strength_increase_at_after=last_strength_increase_at_after,
        next_growth_at_after=committed_next_growth_at,
        action_kind=action_kind,
        reason=reason,
        shadow_cost=committed_shadow_cost,
        target_id=plan.target_id,
        scheduled_cycle_slot_due=plan.scheduled_cycle_slot_due,
    )

    def _log_committed_maintenance() -> None:
        logger.info(
            "Virtual player V2 maintenance committed: profile_id=%s outcome=%s action=%s reason=%s",
            plan.profile_id,
            result.outcome.value,
            result.action_kind,
            result.reason,
            extra={
                "event": "virtual_player_v2_maintenance_committed",
                "profile_id": plan.profile_id,
                "manor_id": plan.manor_id,
                "maintenance_sequence": result.sequence_after,
                "outcome": result.outcome.value,
                "action_kind": result.action_kind,
                "reason": result.reason,
                "trigger": result.trigger.value,
                "candidate_rejections": [
                    assessment.summary_payload() for assessment in plan.candidate_assessments if not assessment.allowed
                ][:8],
            },
        )

    transaction.on_commit(_log_committed_maintenance)
    return result


@record_maintenance_stage(STAGE_SAFETY_TASK_WRAPUP)
def _finish_safety_attempt_best_effort(
    attempt: MaintenanceAttempt,
    *,
    result: MaintenanceResult | MaintenanceAttemptResult,
) -> None:
    try:
        finish_maintenance_attempt(attempt, result=result)
    except Exception as exc:
        log_safety_metric_failure(
            operation="maintenance_attempt_terminal",
            exc=exc,
        )


@record_maintenance_stage(STAGE_SAFETY_TASK_WRAPUP)
def _finish_safety_attempts_best_effort(
    attempts: list[tuple[MaintenanceAttempt, MaintenanceResult | MaintenanceAttemptResult]],
) -> None:
    try:
        finish_maintenance_attempts(attempts)
    except Exception as exc:
        log_safety_metric_failure(
            operation="maintenance_attempt_terminal_batch",
            exc=exc,
        )


def _finish_or_defer_safety_attempt(
    attempt: MaintenanceAttempt,
    *,
    result: MaintenanceResult | MaintenanceAttemptResult,
    terminal_batch: list[tuple[MaintenanceAttempt, MaintenanceResult | MaintenanceAttemptResult]] | None,
) -> None:
    if terminal_batch is None:
        _finish_safety_attempt_best_effort(attempt, result=result)
        return
    terminal_batch.append((attempt, result))


@record_maintenance_stage(STAGE_CYCLE_ATTEMPT_RECEIPT)
def _create_maintenance_execution_receipt(
    context: _MaintenanceExecutionReceiptContext,
    *,
    profile_id: int,
    trigger: MaintenanceTrigger,
    outcome: MaintenanceOutcome,
    schedule_disposition: MaintenanceScheduleDisposition,
    sequence_before: int,
    sequence_after: int,
    next_growth_at_before: datetime,
    next_growth_at_after: datetime,
    action_kind: str = "",
    reason: str = "",
    shadow_cost: Mapping[str, Any] | None = None,
    cycle_id: str = "",
    round_ordinal: int | None = None,
    action_ordinal_in_round: int | None = None,
    slot_attempt_ordinal: int | None = None,
) -> BotMaintenanceExecution:
    if outcome not in {MaintenanceOutcome.APPLIED, MaintenanceOutcome.NO_ACTION}:
        raise V2MaintenanceError("only committed maintenance outcomes may create an execution receipt")
    return BotMaintenanceExecution.objects.create(
        operation_id=context.operation_id,
        profile_id=profile_id,
        attempt_ordinal=context.attempt_ordinal,
        trigger=trigger.value,
        outcome=outcome.value,
        schedule_disposition=schedule_disposition.value,
        maintenance_sequence_before=sequence_before,
        maintenance_sequence_after=sequence_after,
        next_growth_at_before=next_growth_at_before,
        next_growth_at_after=next_growth_at_after,
        action_kind=action_kind,
        reason=reason,
        cycle_id=str(cycle_id or "")[:64],
        round_ordinal=round_ordinal,
        action_ordinal_in_round=action_ordinal_in_round,
        slot_attempt_ordinal=slot_attempt_ordinal,
        shadow_cost=dict(shadow_cost or {}),
        request_digest=context.request_digest,
        requested_at=context.requested_at,
        safety_started_at=context.safety_started_at,
    )


@transaction.atomic
def _execute_virtual_player_v2_maintenance_with_receipt(
    plan: MaintenancePlan,
    *,
    context: _MaintenanceExecutionReceiptContext,
    policy_release: BotPolicyRelease | None = None,
    routing_snapshot: RuntimeRoutingSnapshot | None = None,
    grain_template: ItemTemplate | None = None,
    grain_template_resolved: bool = False,
    scheduled_cycle_id: str | None = None,
    run_ordinary_preamble: bool = False,
    arena_member_id: int | None = None,
    arena_round_ordinal: int | None = None,
    arena_action_ordinal: int | None = None,
    arena_slot_attempt_ordinal: int | None = None,
) -> MaintenanceResult:
    result = (
        execute_virtual_player_v2_maintenance_plan(
            plan,
            _routing_snapshot=routing_snapshot,
            _grain_template=grain_template,
            _grain_template_resolved=grain_template_resolved,
            _scheduled_cycle_id=scheduled_cycle_id,
            _run_ordinary_preamble=run_ordinary_preamble,
        )
        if policy_release is None
        else execute_virtual_player_v2_maintenance_plan(
            plan,
            _policy_release=policy_release,
            _routing_snapshot=routing_snapshot,
            _grain_template=grain_template,
            _grain_template_resolved=grain_template_resolved,
            _scheduled_cycle_id=scheduled_cycle_id,
            _run_ordinary_preamble=run_ordinary_preamble,
        )
    )
    if result.outcome not in {
        MaintenanceOutcome.APPLIED,
        MaintenanceOutcome.NO_ACTION,
    }:
        return result
    if result.next_growth_at_before is None or result.next_growth_at_after is None:
        raise V2MaintenanceError("committed maintenance receipt requires a complete growth schedule")
    receipt_shadow_cost = {
        **dict(result.shadow_cost),
        "control_snapshot_digest": plan.control_snapshot_digest,
    }
    if plan.action_kind == InventoryAcquisitionActionSpec.action_kind and isinstance(
        plan.action_spec, InventoryAcquisitionActionSpec
    ):
        receipt_shadow_cost["inventory_batch"] = plan.action_spec.to_payload()
    _create_maintenance_execution_receipt(
        context,
        profile_id=result.profile_id,
        trigger=result.trigger,
        outcome=result.outcome,
        schedule_disposition=result.schedule_disposition,
        sequence_before=result.sequence_before,
        sequence_after=result.sequence_after,
        next_growth_at_before=result.next_growth_at_before,
        next_growth_at_after=result.next_growth_at_after,
        action_kind=result.action_kind,
        reason=result.reason,
        shadow_cost=receipt_shadow_cost,
        cycle_id=(
            ""
            if arena_member_id is None or arena_round_ordinal is None
            else f"arena-member-{int(arena_member_id)}-r{int(arena_round_ordinal)}"
        ),
        round_ordinal=arena_round_ordinal,
        action_ordinal_in_round=arena_action_ordinal,
        slot_attempt_ordinal=arena_slot_attempt_ordinal,
    )
    return result


@transaction.atomic
@record_maintenance_stage(STAGE_CYCLE_ATTEMPT_RECEIPT)
def _open_policy2_scheduled_cycle(
    profile_id: int,
    *,
    now: datetime,
) -> BotMaintenanceCycle:
    """Return the one open ordinary policy-2 cycle for a profile."""

    cycle = (
        BotMaintenanceCycle.objects.select_for_update()
        .select_related("profile")
        .filter(
            profile_id=profile_id,
            trigger=BotMaintenanceCycle.Trigger.SCHEDULED,
        )
        .order_by("-cycle_ordinal", "-id")
        .first()
    )
    # A profile without a cycle still needs the profile lock to serialize the
    # first-cycle insert.  Once a cycle exists, the joined FOR UPDATE query
    # locks the cycle and its profile in one round trip.
    profile = cycle.profile if cycle is not None else BotProfile.objects.select_for_update().get(pk=profile_id)
    initial_due_at = profile.next_growth_at
    if initial_due_at is not None and initial_due_at < now:
        initial_due_at = now
    cycle_pacing = None
    if cycle is not None and isinstance(cycle.payload, Mapping) and cycle.payload.get("archetype_pacing"):
        cycle_pacing = pacing_from_cycle_payload(cycle.payload)
    else:
        cycle_pacing = resolve_archetype_pacing(load_virtual_player_config(), str(profile.archetype))
    if (
        cycle is not None
        and cycle.status == BotMaintenanceCycle.Status.OPEN
        and int(cycle.action_ordinal) < int(cycle.max_actions)
    ):
        update_fields = []
        if not cycle.interval_seed:
            cycle.interval_seed = cycle.cycle_id
            update_fields.append("interval_seed")
        cycle_payload = dict(cycle.payload or {})
        if not cycle_payload.get("archetype_pacing"):
            cycle_payload["archetype_pacing"] = cycle_pacing.to_payload()
            cycle.payload = cycle_payload
            update_fields.append("payload")
        if cycle.next_slot_due_at is None:
            cycle.next_slot_due_at = initial_due_at
            cycle.next_decision_at = initial_due_at
            update_fields.extend(["next_slot_due_at", "next_decision_at"])
        cycle_is_due = cycle.next_slot_due_at is not None and cycle.next_slot_due_at <= now
        if cycle_is_due:
            cycle.current_action_state = BotMaintenanceCycle.ActionState.PLANNING
            cycle.next_decision_at = now
            update_fields.extend(["current_action_state", "next_decision_at"])
        if update_fields:
            cycle.save(update_fields=[*dict.fromkeys([*update_fields, "updated_at"])])
        return cycle
    if cycle is not None and cycle.status == BotMaintenanceCycle.Status.OPEN:
        close_durable_cycle_locked(
            cycle,
            reason="cycle_budget_recovered",
            completed_at=now,
        )
    cycle_ordinal = int(cycle.cycle_ordinal) + 1 if cycle is not None else 1
    cycle_id = f"vp-cycle-{int(profile.id)}-{cycle_ordinal}-{uuid4().hex[:20]}"
    cycle = BotMaintenanceCycle.objects.create(
        cycle_id=cycle_id,
        interval_seed=cycle_id,
        profile=profile,
        cycle_ordinal=cycle_ordinal,
        trigger=BotMaintenanceCycle.Trigger.SCHEDULED,
        max_actions=16,
        started_at=now,
        current_action_state=BotMaintenanceCycle.ActionState.READY,
        next_slot_due_at=initial_due_at,
        next_decision_at=initial_due_at,
        payload={"archetype_pacing": cycle_pacing.to_payload()},
    )
    if cycle.next_slot_due_at is not None and cycle.next_slot_due_at <= now:
        cycle.current_action_state = BotMaintenanceCycle.ActionState.PLANNING
        cycle.next_decision_at = now
        cycle.save(update_fields=["current_action_state", "next_decision_at", "updated_at"])
    return cycle


def _cap_policy2_cycle_restart_schedule(profile: BotProfile, *, now: datetime) -> None:
    """Keep the next cycle start within the ordinary 23-hour restart bound."""

    if profile.next_growth_at is None:
        return
    capped_next_growth_at = min(
        profile.next_growth_at,
        now + ORDINARY_CYCLE_NEXT_START_MAX_DELAY,
    )
    if capped_next_growth_at == profile.next_growth_at:
        return
    profile.next_growth_at = capped_next_growth_at
    profile.save(update_fields=["next_growth_at", "updated_at"])


@transaction.atomic
@record_maintenance_stage(STAGE_CYCLE_ATTEMPT_RECEIPT)
def _record_policy2_scheduled_cycle_retry(
    cycle_id: str,
    result: MaintenanceResult,
    *,
    now: datetime,
) -> None:
    """Back off a busy cycle without consuming its next action slot."""

    if result.outcome is not MaintenanceOutcome.BUSY:
        return
    profile = BotProfile.objects.select_for_update().get(pk=int(result.profile_id))
    cycle = (
        BotMaintenanceCycle.objects.select_for_update()
        .filter(
            cycle_id=str(cycle_id),
            profile_id=profile.id,
            trigger=BotMaintenanceCycle.Trigger.SCHEDULED,
            status=BotMaintenanceCycle.Status.OPEN,
        )
        .first()
    )
    if cycle is None:
        return
    normalized_reason = str(result.reason or "busy")[:64]
    reason_category = classify_maintenance_reason(normalized_reason).value
    retry_at = cycle_retry_due_at(
        cycle.interval_seed or cycle.cycle_id,
        now=now,
        reason=normalized_reason,
    )
    payload = dict(cycle.payload or {})
    retry_history = list(payload.get("retry_history") or [])
    retry_history.append(
        {
            "recorded_at": _datetime_payload(now),
            "reason": normalized_reason,
            "reason_category": reason_category,
            "retry_at": _datetime_payload(retry_at),
            "action_ordinal": int(cycle.action_ordinal),
        }
    )
    payload["retry_history"] = retry_history[-16:]
    payload["last_reason_category"] = reason_category
    cycle.payload = payload
    cycle.current_action_state = BotMaintenanceCycle.ActionState.READY
    cycle.last_action_completion_source = "retry_backoff"
    cycle.last_reason = normalized_reason
    cycle.next_slot_due_at = retry_at
    cycle.next_decision_at = retry_at
    cycle.save(
        update_fields=[
            "payload",
            "current_action_state",
            "last_action_completion_source",
            "last_reason",
            "next_slot_due_at",
            "next_decision_at",
            "updated_at",
        ]
    )


@transaction.atomic
@record_maintenance_stage(STAGE_CYCLE_ATTEMPT_RECEIPT)
def _record_policy2_scheduled_cycle_result(
    cycle_id: str,
    result: MaintenanceResult,
    *,
    plan: MaintenancePlan,
    now: datetime,
) -> None:
    """Persist a committed slot and keep the profile due until slot 16.

    A deterministic NO_ACTION is an audited cycle termination, not a fake
    successful slot.  Only an APPLIED action advances the 16-slot counter.
    """

    if result.outcome not in {MaintenanceOutcome.APPLIED, MaintenanceOutcome.NO_ACTION}:
        return
    cycle = BotMaintenanceCycle.objects.select_for_update().select_related("profile").get(cycle_id=str(cycle_id))
    profile = cycle.profile
    if int(profile.id) != int(result.profile_id):
        raise V2MaintenanceError("scheduled cycle profile differs from the committed maintenance result")
    payload = dict(cycle.payload or {})
    try:
        cycle_budget_state = ArchetypeBudgetState.from_payload(payload.get("archetype_budget"))
    except ArchetypePacingError as exc:
        raise V2MaintenanceError("scheduled cycle budget payload is invalid") from exc
    if cycle_budget_state is None:
        cycle_budget_state = plan.cycle_budget_state
    selected_assessment = (
        next(
            (
                assessment
                for assessment in plan.candidate_assessments
                if plan.intent is not None and assessment.intent.business_key == plan.intent.business_key
            ),
            None,
        )
        if plan.intent is not None
        else None
    )
    if cycle_budget_state is None:
        raise V2MaintenanceError("scheduled policy-2 result is missing its cycle budget state")
    if result.outcome is MaintenanceOutcome.APPLIED:
        if selected_assessment is None:
            raise V2MaintenanceError("applied scheduled result is missing its candidate assessment")
        try:
            cycle_budget_state = cycle_budget_state.consume(selected_assessment.resource_costs)
        except ArchetypePacingError as exc:
            raise V2MaintenanceError("applied scheduled result exceeds its cycle budget baseline") from exc
    payload["archetype_budget"] = cycle_budget_state.to_payload()
    cycle.payload = payload
    current_domain_availability = _current_domain_availability_for_profile(profile)
    coverage_kinds = _ORDINARY_CYCLE_COVERAGE_KINDS

    def _record_domain_snapshot(current_cycle: BotMaintenanceCycle) -> None:
        payload = dict(current_cycle.payload or {})
        history = list(payload.get("domain_availability_history") or [])
        history.append(
            {
                "recorded_at": _datetime_payload(now),
                "action_kind": str(result.action_kind or ""),
                "outcome": result.outcome.value,
                "domains": _domain_availability_payload(current_domain_availability),
            }
        )
        payload["domain_availability"] = _domain_availability_payload(current_domain_availability)
        payload["domain_availability_history"] = history[-16:]
        current_cycle.payload = payload

    completion_source_by_action = {
        BuildingUpgradeActionSpec.action_kind: "building.upgrade_complete_at",
        TechnologyUpgradeActionSpec.action_kind: "technology.upgrade_complete_at",
        "training": "guest.training_complete_at",
    }

    def _pending_domain_action(slot_ordinal: int) -> dict[str, Any] | None:
        action_kind = str(result.action_kind or "")
        completion_source = completion_source_by_action.get(action_kind)
        if completion_source is None:
            return None
        domain_event_kind: str
        domain_object_id: int | None
        completion_at: datetime | None
        if action_kind == BuildingUpgradeActionSpec.action_kind and isinstance(
            plan.action_spec,
            BuildingUpgradeActionSpec,
        ):
            domain_event_kind = "building_upgrade"
            domain_object_id = int(plan.action_spec.building_id)
            completion_at = (
                Building.objects.filter(
                    pk=domain_object_id,
                    manor_id=profile.manor_id,
                    is_upgrading=True,
                )
                .values_list("upgrade_complete_at", flat=True)
                .first()
            )
        elif action_kind == TechnologyUpgradeActionSpec.action_kind and isinstance(
            plan.action_spec,
            TechnologyUpgradeActionSpec,
        ):
            domain_event_kind = "technology_upgrade"
            technology = (
                PlayerTechnology.objects.filter(
                    manor_id=profile.manor_id,
                    tech_key=plan.action_spec.technology_key,
                    is_upgrading=True,
                )
                .order_by("upgrade_complete_at", "id")
                .values("id", "upgrade_complete_at")
                .first()
            )
            domain_object_id = None if technology is None else int(technology["id"])
            completion_at = None if technology is None else technology["upgrade_complete_at"]
        elif action_kind == "training" and plan.target_id is not None:
            domain_event_kind = "guest_training"
            domain_object_id = int(plan.target_id)
            completion_at = (
                Guest.objects.filter(
                    pk=domain_object_id,
                    manor_id=profile.manor_id,
                    training_complete_at__isnull=False,
                )
                .values_list("training_complete_at", flat=True)
                .first()
            )
        else:
            return None
        if domain_object_id is None or completion_at is None:
            return None
        return {
            "action_kind": action_kind,
            "action_ordinal": int(slot_ordinal),
            "completion_source": completion_source,
            "domain_event_kind": domain_event_kind,
            "domain_object_id": domain_object_id,
            "expected_completion_at": _datetime_payload(completion_at),
        }

    def _coverage_gaps(current_cycle: BotMaintenanceCycle) -> list[dict[str, Any]]:
        covered = {_ordinary_cycle_coverage_kind(str(value)) for value in (current_cycle.covered_action_kinds or [])}
        assessments_by_kind: dict[str, list[CandidateAssessment]] = defaultdict(list)
        for assessment in plan.candidate_assessments:
            assessments_by_kind[_ordinary_cycle_coverage_kind(assessment.intent.action_kind)].append(assessment)
        gaps: list[dict[str, Any]] = []
        for kind in coverage_kinds:
            if kind in covered:
                continue
            candidates = assessments_by_kind.get(kind, [])
            if any(assessment.allowed for assessment in candidates):
                continue
            reason = (
                next(
                    (
                        assessment.primary_rejection_reason
                        for assessment in candidates
                        if assessment.primary_rejection_reason
                    ),
                    "no_eligible_candidate",
                )
                if candidates
                else "no_candidate"
            )
            gaps.append({"action_kind": kind, "reason": reason})
        return gaps

    if result.outcome is MaintenanceOutcome.NO_ACTION:
        operation_id = _cycle_preamble_operation_id(
            cycle.cycle_id,
            "no-action",
            int(result.sequence_before),
        )
        normalized_reason = str(result.reason or "no_eligible_candidate")[:64]
        reason_category = classify_maintenance_reason(normalized_reason).value
        gaps = _coverage_gaps(cycle)
        record_durable_attempts_locked(
            profile,
            attempts=(
                {
                    "operation_id": operation_id,
                    "trigger": CycleTrigger.SCHEDULED,
                    "attempt_ordinal": 1,
                    "outcome": BotMaintenanceAttempt.Outcome.NO_ACTION,
                    "reason": (result.reason or "no_eligible_candidate")[:64],
                    "cycle": cycle,
                    "receipt_operation_id": operation_id,
                    "shadow_cost": {
                        "kind": "cycle_no_action",
                        "coverage_gaps": gaps,
                        "control_snapshot_digest": plan.control_snapshot_digest,
                        "reason_category": reason_category,
                        "salary_runway_days": 3,
                        "salary_runway_silver": int(
                            dict(plan.resource_planning_snapshot.protected_resources).get(ResourceType.SILVER, 0)
                        ),
                    },
                    "started_at": now,
                },
            ),
            return_objects=False,
            assume_new=True,
        )
        payload = dict(cycle.payload or {})
        existing_gaps = list(payload.get("coverage_gaps") or [])
        payload["coverage_gaps"] = [*existing_gaps, *gaps]
        payload["last_reason_category"] = reason_category
        cycle.payload = payload
        _record_domain_snapshot(cycle)
        cycle.last_reason = normalized_reason
        retryable_reason = normalized_reason in {
            "candidate_domain_constraint",
            "insufficient_resource",
            "resource_snapshot_changed",
            "salary_runway_protected",
            "archetype_parallel_training_cap",
            "archetype_budget_silver",
            "archetype_budget_grain",
        }
        has_future_domain_completion = any(
            completion_at > now
            for _domain_name, completion_times in current_domain_availability
            for completion_at in completion_times
        )
        if retryable_reason or (normalized_reason == "no_eligible_candidate" and has_future_domain_completion):
            retry_at = plan.next_growth_at_after_no_action
            if retry_at is None or retry_at <= now:
                retry_at = cycle_retry_due_at(
                    cycle.interval_seed or cycle.cycle_id,
                    now=now,
                    reason=normalized_reason,
                )
            retry_history = list(payload.get("retry_history") or [])
            retry_history.append(
                {
                    "recorded_at": _datetime_payload(now),
                    "reason": normalized_reason,
                    "reason_category": reason_category,
                    "retry_at": _datetime_payload(retry_at),
                    "action_ordinal": int(cycle.action_ordinal),
                }
            )
            payload["retry_history"] = retry_history[-16:]
            cycle.payload = payload
            cycle.current_action_state = BotMaintenanceCycle.ActionState.READY
            cycle.last_action_completion_source = "retry_backoff"
            cycle.next_slot_due_at = retry_at
            cycle.next_decision_at = retry_at
            cycle.save(
                update_fields=[
                    "payload",
                    "current_action_state",
                    "last_action_completion_source",
                    "last_reason",
                    "next_slot_due_at",
                    "next_decision_at",
                    "updated_at",
                ]
            )
            return
        close_durable_cycle_locked(
            cycle,
            reason="candidate_exhausted",
            action_state=BotMaintenanceCycle.ActionState.NO_ACTION,
            completion_source=ACTION_COMPLETION_SOURCE_CANDIDATE_EXHAUSTED,
            extra_update_fields=("payload",),
        )
        _cap_policy2_cycle_restart_schedule(profile, now=now)
        return

    cycle, slot_ordinal = append_durable_cycle_action_locked(
        cycle,
        action_kind=(result.action_kind or "no_action"),
        business_key=(f"{result.action_kind or 'no_action'}:{result.sequence_before}:{result.reason or 'committed'}")[
            :128
        ],
        reason=(result.reason or result.action_kind or "committed"),
        persist=False,
    )
    attempt_operation_id = f"vp-cycle-{int(profile.id)}-{int(cycle.cycle_ordinal)}-{int(slot_ordinal)}"
    attempt_shadow_cost = {
        **dict(result.shadow_cost),
        "control_snapshot_digest": plan.control_snapshot_digest,
    }
    if plan.action_kind == InventoryAcquisitionActionSpec.action_kind and isinstance(
        plan.action_spec, InventoryAcquisitionActionSpec
    ):
        attempt_shadow_cost["inventory_batch"] = plan.action_spec.to_payload()
    if selected_assessment is not None:
        attempt_shadow_cost["resource_costs"] = dict(selected_assessment.resource_costs)
    attempt_shadow_cost["salary_runway_days"] = 3
    attempt_shadow_cost["salary_runway_silver"] = int(
        dict(plan.resource_planning_snapshot.protected_resources).get(ResourceType.SILVER, 0)
    )
    child_outcome = (
        BotMaintenanceAttempt.Outcome.APPLIED
        if result.outcome is MaintenanceOutcome.APPLIED
        else BotMaintenanceAttempt.Outcome.NO_ACTION
    )
    attempt_specs: list[dict[str, Any]] = [
        {
            "operation_id": attempt_operation_id,
            "trigger": CycleTrigger.SCHEDULED,
            "attempt_ordinal": 1,
            "outcome": child_outcome,
            "reason": result.reason,
            "cycle": cycle,
            "action_kind": result.action_kind,
            "action_ordinal_in_round": slot_ordinal,
            "receipt_operation_id": attempt_operation_id,
            "shadow_cost": attempt_shadow_cost,
            "started_at": now,
        }
    ]
    if plan.action_kind == InventoryAcquisitionActionSpec.action_kind and isinstance(
        plan.action_spec, InventoryAcquisitionActionSpec
    ):
        for draw_ordinal, color, item_key, weight in plan.action_spec.batch_draws:
            attempt_specs.append(
                {
                    "operation_id": _cycle_child_operation_id(
                        attempt_operation_id,
                        kind="inventory-draw",
                        ordinal=draw_ordinal,
                    ),
                    "trigger": CycleTrigger.SCHEDULED,
                    "attempt_ordinal": draw_ordinal,
                    "outcome": child_outcome,
                    "reason": result.reason,
                    "cycle": cycle,
                    "action_ordinal_in_round": slot_ordinal,
                    "receipt_operation_id": attempt_operation_id,
                    "shadow_cost": {
                        "kind": "inventory_draw",
                        "draw_ordinal": int(draw_ordinal),
                        "color": str(color),
                        "item_key": str(item_key),
                        "weight": float(weight),
                    },
                    "started_at": now,
                }
            )
    record_durable_attempts_locked(
        profile,
        attempts=tuple(attempt_specs),
        return_objects=False,
        assume_new=True,
    )
    _record_domain_snapshot(cycle)
    pending_domain_action = _pending_domain_action(slot_ordinal)
    if pending_domain_action is not None:
        payload = dict(cycle.payload or {})
        pending_actions = list(payload.get("pending_domain_actions") or [])
        pending_actions.append(pending_domain_action)
        payload["pending_domain_actions"] = pending_actions[-16:]
        cycle.payload = payload
    cycle.last_action_completion_source = completion_source_by_action.get(
        str(result.action_kind),
        ACTION_COMPLETION_SOURCE_MAINTENANCE_COMMIT,
    )
    cycle.current_action_state = (
        BotMaintenanceCycle.ActionState.SUBMITTED
        if str(result.action_kind) in completion_source_by_action
        else BotMaintenanceCycle.ActionState.COMPLETED
    )
    gaps = _coverage_gaps(cycle)
    if gaps:
        payload = dict(cycle.payload or {})
        existing_gaps = list(payload.get("coverage_gaps") or [])
        payload["coverage_gaps"] = [*existing_gaps, *gaps]
        cycle.payload = payload
    if int(cycle.action_ordinal) >= int(cycle.max_actions):
        close_durable_cycle_locked(
            cycle,
            reason="cycle_budget_exhausted",
            action_state=(
                BotMaintenanceCycle.ActionState.SUBMITTED
                if str(result.action_kind) in completion_source_by_action
                else BotMaintenanceCycle.ActionState.COMPLETED
            ),
            completion_source=cycle.last_action_completion_source,
            completed_at=now,
            extra_update_fields=(
                "action_ordinal",
                "high_cost_actions_used",
                "covered_action_kinds",
                "used_business_keys",
                "payload",
            ),
        )
        _cap_policy2_cycle_restart_schedule(profile, now=now)
        return
    next_due_at = next_ordinary_slot_due_at(
        cycle.interval_seed or cycle.cycle_id,
        completed_at=now,
        next_slot_ordinal=slot_ordinal + 1,
        interval_minutes=pacing_from_cycle_payload(cycle.payload).slot_interval_minutes,
    )
    cycle.next_slot_due_at = next_due_at
    cycle.next_decision_at = next_due_at
    cycle.save(
        update_fields=[
            "action_ordinal",
            "high_cost_actions_used",
            "covered_action_kinds",
            "used_business_keys",
            "last_reason",
            "current_action_state",
            "last_action_completion_source",
            "payload",
            "next_slot_due_at",
            "next_decision_at",
            "updated_at",
        ]
    )


def maintain_virtual_player_v2(
    profile_id: int,
    *,
    trigger: MaintenanceTrigger,
    operation_id: UUID | str | None = None,
    attempt_ordinal: int = 1,
    now: datetime | None = None,
    admin_requires_due: bool | None = None,
    admin_schedule_disposition: MaintenanceScheduleDisposition | None = None,
    minimum_guest_count: int | None = None,
    minimum_guest_level: int | None = None,
    guest_rarity_cap: str | None = None,
    max_guest_level_step: int | None = None,
    arena_growth_objective: ArenaGrowthObjective | None = None,
    _execution_request_digest: str | None = None,
    _execution_requested_at: datetime | None = None,
    _execution_request_digest_schema: int = 3,
    _frozen_control_snapshot_digest: str | None = None,
    _routing_snapshot: RuntimeRoutingSnapshot | None = None,
    _external_reconciliation_prechecked: bool = False,
    _planning_snapshot: _MaintenancePlanningSnapshot | None = None,
    _arena_excluded_training_guest_ids: tuple[int, ...] = (),
    _arena_member_id: int | None = None,
    _arena_round_ordinal: int | None = None,
    _arena_action_ordinal: int | None = None,
    _arena_slot_attempt_ordinal: int | None = None,
    _safety_attempt: MaintenanceAttempt | None = None,
    _safety_terminal_batch: list[tuple[MaintenanceAttempt, MaintenanceResult | MaintenanceAttemptResult]] | None = None,
) -> MaintenanceResult:
    """Plan and execute one V2 cycle without falling back to V1."""
    if isinstance(profile_id, bool) or not isinstance(profile_id, int) or profile_id < 1:
        raise ValueError("profile_id must be a positive integer")
    trigger_policy = maintenance_trigger_policy(
        trigger,
        admin_requires_due=admin_requires_due,
        admin_schedule_disposition=admin_schedule_disposition,
    )
    if _safety_attempt is None:
        safety_preflight = check_v2_development_write_preflight(now=(now or timezone.now()))
        if not safety_preflight.allowed:
            return _uncommitted_maintenance_result(
                profile_id=profile_id,
                trigger_policy=trigger_policy,
                outcome=MaintenanceOutcome.PAUSED,
                reason=safety_preflight.reason,
            )
        try:
            safety_attempt = start_maintenance_attempt(
                trigger=trigger_policy.trigger,
                operation_id=operation_id,
                attempt_ordinal=attempt_ordinal,
            )
        except (DatabaseError, SafetyProviderError) as exc:
            log_safety_metric_failure(
                operation="maintenance_attempt_started",
                exc=exc,
            )
            return _uncommitted_maintenance_result(
                profile_id=profile_id,
                trigger_policy=trigger_policy,
                outcome=MaintenanceOutcome.PAUSED,
                reason="safety_provider_unavailable",
            )
    else:
        safety_attempt = _safety_attempt
        if safety_attempt.trigger is not trigger_policy.trigger:
            raise V2MaintenanceError("prepared safety attempt trigger differs from maintenance trigger")
    policy_release = None if _planning_snapshot is None else _planning_snapshot.policy_release
    grain_template = None if _planning_snapshot is None else _planning_snapshot.grain_template
    grain_template_resolved = _planning_snapshot is not None
    receipt_context: _MaintenanceExecutionReceiptContext | None = None
    if _execution_request_digest is not None:
        if operation_id is None or _execution_requested_at is None:
            raise V2MaintenanceError("maintenance execution receipt requires operation_id and requested_at")
        if int(_execution_request_digest_schema) != 3:
            raise V2MaintenanceError("arena maintenance receipts require digest schema 3")
        receipt_context = _MaintenanceExecutionReceiptContext(
            operation_id=safety_attempt.operation_id,
            attempt_ordinal=safety_attempt.attempt_ordinal,
            request_digest=_execution_request_digest,
            requested_at=_execution_requested_at,
            request_digest_schema=int(_execution_request_digest_schema),
            safety_started_at=safety_attempt.started_at,
        )
    scheduled_cycle: BotMaintenanceCycle | None = None
    scheduled_cycle_slot_due = False
    scheduled_cycle_budget_state: ArchetypeBudgetState | None = None
    scheduled_cycle_pacing: ArchetypePacing | None = None
    if trigger_policy.trigger is MaintenanceTrigger.SCHEDULED:
        policy_version = (
            _planning_snapshot.profile.policy_version
            if _planning_snapshot is not None
            else BotProfile.objects.filter(pk=profile_id).values_list("policy_version", flat=True).first()
        )
        if int(policy_version or 0) == 2:
            scheduled_cycle = _open_policy2_scheduled_cycle(
                profile_id,
                now=(now or timezone.now()),
            )
            scheduled_cycle_slot_due = bool(
                scheduled_cycle.next_slot_due_at is not None
                and scheduled_cycle.next_slot_due_at <= (now or timezone.now())
            )
    if scheduled_cycle is not None and not scheduled_cycle_slot_due:
        result = _uncommitted_maintenance_result(
            profile_id=profile_id,
            trigger_policy=trigger_policy,
            outcome=MaintenanceOutcome.INELIGIBLE,
            reason="scheduled_cycle_slot_not_due",
        )
        _finish_or_defer_safety_attempt(
            safety_attempt,
            result=result,
            terminal_batch=_safety_terminal_batch,
        )
        return result
    candidate_exclusions: tuple[str, ...] = ()
    plan: MaintenancePlan | None = None
    try:
        if scheduled_cycle is not None:
            try:
                cycle_payload = scheduled_cycle.payload if isinstance(scheduled_cycle.payload, Mapping) else {}
                scheduled_cycle_budget_state = ArchetypeBudgetState.from_payload(cycle_payload.get("archetype_budget"))
                scheduled_cycle_pacing = pacing_from_cycle_payload(cycle_payload)
            except ArchetypePacingError as exc:
                raise _V2MaintenanceOutcomeError(
                    MaintenanceOutcome.PAUSED,
                    "cycle_budget_state_invalid",
                ) from exc
        while True:
            plan = build_virtual_player_v2_maintenance_plan(
                profile_id,
                trigger=trigger_policy.trigger,
                now=now,
                admin_requires_due=admin_requires_due,
                admin_schedule_disposition=admin_schedule_disposition,
                minimum_guest_count=minimum_guest_count,
                minimum_guest_level=minimum_guest_level,
                guest_rarity_cap=guest_rarity_cap,
                max_guest_level_step=max_guest_level_step,
                arena_growth_objective=arena_growth_objective,
                _arena_excluded_training_guest_ids=_arena_excluded_training_guest_ids,
                _cycle_covered_action_kinds=(
                    () if scheduled_cycle is None else tuple(scheduled_cycle.covered_action_kinds or [])
                ),
                _cycle_high_cost_actions_used=(
                    0 if scheduled_cycle is None else int(scheduled_cycle.high_cost_actions_used or 0)
                ),
                _cycle_budget_state=scheduled_cycle_budget_state,
                _cycle_pacing=scheduled_cycle_pacing,
                _candidate_exclusions=candidate_exclusions,
                _scheduled_cycle_slot_due=scheduled_cycle_slot_due,
                _routing_snapshot=_routing_snapshot,
                _external_reconciliation_prechecked=(_external_reconciliation_prechecked),
                _planning_snapshot=_planning_snapshot,
                _frozen_control_snapshot_digest=_frozen_control_snapshot_digest,
            )
            if receipt_context is not None:
                _persist_arena_training_assignment_from_plan(
                    plan,
                    operation_id=receipt_context.operation_id,
                    member_id=_arena_member_id,
                    round_ordinal=_arena_round_ordinal,
                    action_ordinal_in_round=_arena_action_ordinal,
                )
            try:
                result = (
                    (
                        execute_virtual_player_v2_maintenance_plan(
                            plan,
                            _routing_snapshot=_routing_snapshot,
                            _grain_template=grain_template,
                            _grain_template_resolved=grain_template_resolved,
                            _scheduled_cycle_id=(None if scheduled_cycle is None else scheduled_cycle.cycle_id),
                            _run_ordinary_preamble=(
                                scheduled_cycle is not None and int(scheduled_cycle.action_ordinal) == 0
                            ),
                        )
                        if policy_release is None
                        else execute_virtual_player_v2_maintenance_plan(
                            plan,
                            _policy_release=policy_release,
                            _routing_snapshot=_routing_snapshot,
                            _grain_template=grain_template,
                            _grain_template_resolved=grain_template_resolved,
                            _scheduled_cycle_id=(None if scheduled_cycle is None else scheduled_cycle.cycle_id),
                            _run_ordinary_preamble=(
                                scheduled_cycle is not None and int(scheduled_cycle.action_ordinal) == 0
                            ),
                        )
                    )
                    if receipt_context is None
                    else _execute_virtual_player_v2_maintenance_with_receipt(
                        plan,
                        context=receipt_context,
                        policy_release=policy_release,
                        routing_snapshot=_routing_snapshot,
                        grain_template=grain_template,
                        grain_template_resolved=grain_template_resolved,
                        scheduled_cycle_id=(None if scheduled_cycle is None else scheduled_cycle.cycle_id),
                        run_ordinary_preamble=(
                            scheduled_cycle is not None and int(scheduled_cycle.action_ordinal) == 0
                        ),
                        arena_member_id=_arena_member_id,
                        arena_round_ordinal=_arena_round_ordinal,
                        arena_action_ordinal=_arena_action_ordinal,
                        arena_slot_attempt_ordinal=_arena_slot_attempt_ordinal,
                    )
                )
            except _V2MaintenanceCandidateRejected as exc:
                if not (
                    scheduled_cycle is not None
                    and plan.policy_version == 2
                    and trigger_policy.trigger is MaintenanceTrigger.SCHEDULED
                ):
                    raise
                next_exclusions = [*candidate_exclusions, exc.business_key]
                if len(next_exclusions) >= 6:
                    # The next planning pass produces a committed NO_ACTION
                    # from the same frozen snapshot without trying a seventh
                    # candidate.  Keep the rejected identities in the digest
                    # so revalidation cannot silently revive one.
                    next_exclusions.extend(assessment.intent.business_key for assessment in plan.candidate_assessments)
                candidate_exclusions = tuple(dict.fromkeys(next_exclusions))
                continue
            break
    except _V2MaintenanceOutcomeError as exc:
        result = _uncommitted_maintenance_result(
            profile_id=profile_id,
            trigger_policy=trigger_policy,
            outcome=exc.outcome,
            reason=exc.reason,
        )
    except DatabaseError:
        _finish_or_defer_safety_attempt(
            safety_attempt,
            result=MaintenanceAttemptResult.COMMIT_UNCERTAIN,
            terminal_batch=_safety_terminal_batch,
        )
        raise
    except Exception:
        _finish_or_defer_safety_attempt(
            safety_attempt,
            result=MaintenanceAttemptResult.FAILED,
            terminal_batch=_safety_terminal_batch,
        )
        raise
    _finish_or_defer_safety_attempt(
        safety_attempt,
        result=result,
        terminal_batch=_safety_terminal_batch,
    )
    if (
        scheduled_cycle is not None
        and plan is not None
        and result.outcome in {MaintenanceOutcome.APPLIED, MaintenanceOutcome.NO_ACTION}
    ):
        _record_policy2_scheduled_cycle_result(
            scheduled_cycle.cycle_id,
            result,
            plan=plan,
            now=(now or timezone.now()),
        )
    elif scheduled_cycle is not None and result.outcome is MaintenanceOutcome.BUSY:
        _record_policy2_scheduled_cycle_retry(
            scheduled_cycle.cycle_id,
            result,
            now=(now or timezone.now()),
        )
    return result


def _growth_execution_request_digest(
    *,
    profile_id: int,
    requested_at: datetime,
    minimum_guest_count: int | None,
    minimum_guest_level: int | None,
    guest_rarity_cap: str | None,
    max_guest_level_step: int | None,
    arena_growth_objective: ArenaGrowthObjective | None,
    control_snapshot_digest: str | None = None,
    policy_checksum: str | None = None,
    schema_version: int = 3,
) -> str:
    if timezone.is_naive(requested_at):
        raise ValueError("arena growth requested_at must be timezone-aware")
    if schema_version != 3:
        raise ValueError("arena growth requires request digest schema 3")
    payload = {
        "schema_version": schema_version,
        "profile_id": int(profile_id),
        "requested_at": requested_at.astimezone(UTC).isoformat(),
        "minimum_guest_count": minimum_guest_count,
        "minimum_guest_level": minimum_guest_level,
        "guest_rarity_cap": guest_rarity_cap,
        "max_guest_level_step": max_guest_level_step,
    }
    if schema_version >= 2:
        payload["arena_growth_objective"] = (
            None if arena_growth_objective is None else arena_growth_objective.to_payload()
        )
        payload["control_snapshot_digest"] = control_snapshot_digest
    if schema_version >= 3:
        payload["policy_checksum"] = policy_checksum
    return sha256(canonical_json_bytes(payload)).hexdigest()


def _committed_growth_outcome(
    *,
    operation_id: str,
    profile_id: int,
    request_digest: str,
) -> AcceleratedGrowthOutcome | None:
    receipt = BotMaintenanceExecution.objects.filter(operation_id=operation_id).first()
    if receipt is None:
        return None
    if (
        int(receipt.profile_id) != int(profile_id)
        or receipt.request_digest != request_digest
        or receipt.trigger != BotMaintenanceExecution.Trigger.ARENA_ACCELERATION
    ):
        raise MaintenanceExecutionConflict("maintenance operation_id already belongs to a different request")
    if receipt.outcome == BotMaintenanceExecution.Outcome.APPLIED:
        growth_outcome = AcceleratedGrowthOutcome.GROWN
    elif receipt.outcome == BotMaintenanceExecution.Outcome.NO_ACTION:
        growth_outcome = AcceleratedGrowthOutcome.NO_ACTION
    else:
        raise MaintenanceExecutionConflict("maintenance execution receipt has an unsupported outcome")
    if receipt.safety_started_at is not None:
        # Rebuild the original structured result so the safety terminal event
        # uses the same canonical payload as the committed execution. Passing
        # only the enum outcome would drop fields such as ``reason`` and make
        # an otherwise idempotent replay look like an event-id conflict.
        result_shadow_cost = {
            str(key): int(value)
            for key, value in (receipt.shadow_cost or {}).items()
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        }
        target_guest_id = (receipt.shadow_cost or {}).get("target_guest_id")
        terminal_result = MaintenanceResult(
            outcome=MaintenanceOutcome(receipt.outcome),
            trigger=MaintenanceTrigger(receipt.trigger),
            profile_id=int(receipt.profile_id),
            sequence_before=int(receipt.maintenance_sequence_before),
            sequence_after=int(receipt.maintenance_sequence_after),
            schedule_disposition=MaintenanceScheduleDisposition(receipt.schedule_disposition),
            next_growth_at_before=receipt.next_growth_at_before,
            next_growth_at_after=receipt.next_growth_at_after,
            action_kind=str(receipt.action_kind or ""),
            reason=str(receipt.reason or ""),
            shadow_cost=result_shadow_cost,
            target_id=(
                int(target_guest_id)
                if isinstance(target_guest_id, int) and not isinstance(target_guest_id, bool)
                else None
            ),
        )
        attempt = MaintenanceAttempt(
            operation_id=receipt.operation_id,
            attempt_ordinal=int(receipt.attempt_ordinal),
            started_at=receipt.safety_started_at,
            trigger=MaintenanceTrigger.ARENA_ACCELERATION,
        )
        _finish_safety_attempt_best_effort(attempt, result=terminal_result)
    return growth_outcome


def _run_arena_v2_healing_sweep(
    profile_id: int,
    *,
    now: datetime | None,
    arena_member_id: int,
    arena_round_ordinal: int,
) -> None:
    """Run one non-budgeted arena preamble for a durable arena round.

    The healing sweep is deliberately keyed to the durable arena round, not
    to an action operation.  A retry, claim takeover, or another slot in the
    same round therefore replays the same parent/child audit instead of
    starting another roster-wide sweep.
    """

    round_key = f"arena-member-{int(arena_member_id)}-r{int(arena_round_ordinal)}-healing"
    sweep_operation_id = (
        round_key if len(round_key) <= 64 else f"arena-healing-{sha256(round_key.encode('utf-8')).hexdigest()[:51]}"
    )
    policy_version = BotProfile.objects.filter(pk=profile_id).values_list("policy_version", flat=True).first()
    if int(policy_version or 0) != 2:
        return
    run_arena_guest_healing_sweep(
        profile_id,
        operation_id=sweep_operation_id,
        now=now,
    )


def accelerate_virtual_player_growth(
    profile_id: int,
    *,
    now=None,
    minimum_guest_count: int | None = None,
    minimum_guest_level: int | None = None,
    guest_rarity_cap: str | None = None,
    max_guest_level_step: int | None = None,
    arena_growth_objective: ArenaGrowthObjective | None = None,
    operation_id: UUID | str | None = None,
    attempt_ordinal: int = 1,
    request_digest_schema: int = 3,
    _arena_member_id: int | None = None,
    _arena_round_ordinal: int | None = None,
    _arena_action_ordinal: int | None = None,
    _arena_slot_attempt_ordinal: int | None = None,
    _control_snapshot_digest: str | None = None,
    _expected_policy_checksum: str | None = None,
) -> AcceleratedGrowthOutcome:
    """Route one V2 arena-growth request through persisted maintenance mode."""
    (
        minimum_guest_count,
        minimum_guest_level,
        guest_rarity_cap,
        max_guest_level_step,
    ) = _growth_parameters_from_objective(
        arena_growth_objective,
        minimum_guest_count=minimum_guest_count,
        minimum_guest_level=minimum_guest_level,
        guest_rarity_cap=guest_rarity_cap,
        max_guest_level_step=max_guest_level_step,
    )
    if int(request_digest_schema) != 3:
        raise ValueError("arena growth requires request digest schema 3")
    try:
        normalized_rarity_cap = _normalize_recruitment_rarity_cap(guest_rarity_cap)
    except _V2MaintenanceOutcomeError as exc:
        if exc.outcome is not MaintenanceOutcome.PAUSED:
            raise
        try:
            routing = read_virtual_player_routing()
        except RuntimeRoutingError:
            return AcceleratedGrowthOutcome.PAUSED
        if routing.maintenance_mode is not MaintenanceMode.V2_ACTIVE:
            return AcceleratedGrowthOutcome.PAUSED
        result = maintain_virtual_player_v2(
            profile_id,
            trigger=MaintenanceTrigger.ARENA_ACCELERATION,
            operation_id=operation_id,
            attempt_ordinal=attempt_ordinal,
            now=now,
            minimum_guest_count=minimum_guest_count,
            minimum_guest_level=minimum_guest_level,
            guest_rarity_cap=guest_rarity_cap,
            max_guest_level_step=max_guest_level_step,
            arena_growth_objective=arena_growth_objective,
            _routing_snapshot=routing,
            _arena_member_id=_arena_member_id,
            _arena_round_ordinal=_arena_round_ordinal,
            _arena_action_ordinal=_arena_action_ordinal,
            _arena_slot_attempt_ordinal=_arena_slot_attempt_ordinal,
            _frozen_control_snapshot_digest=_control_snapshot_digest,
        )
        return _accelerated_growth_outcome_from_maintenance(result)
    guest_rarity_cap = normalized_rarity_cap
    if arena_growth_objective is not None and normalized_rarity_cap != arena_growth_objective.recruitment_rarity_cap:
        arena_growth_objective = replace(
            arena_growth_objective,
            recruitment_rarity_cap=normalized_rarity_cap,
        )
    execution_context: _MaintenanceExecutionReceiptContext | None = None
    resolved_now = now
    arena_excluded_training_guest_ids: tuple[int, ...] = ()
    if _arena_member_id is not None and _arena_round_ordinal is not None:
        assigned = ArenaReserveTrainingAssignment.objects.filter(
            member_id=int(_arena_member_id),
            round_ordinal=int(_arena_round_ordinal),
        ).exclude(status=ArenaReserveTrainingAssignment.Status.RELEASED)
        if _arena_action_ordinal is not None:
            assigned = assigned.exclude(
                status=ArenaReserveTrainingAssignment.Status.ASSIGNED,
                action_ordinal_in_round=int(_arena_action_ordinal),
            )
        arena_excluded_training_guest_ids = tuple(int(value) for value in assigned.values_list("guest_id", flat=True))
    if operation_id is not None:
        normalized_operation_id = normalize_maintenance_operation_id(operation_id)
        normalized_attempt_ordinal = normalize_maintenance_attempt_ordinal(attempt_ordinal)
        resolved_now = timezone.now() if now is None else now
        request_digest = _growth_execution_request_digest(
            profile_id=profile_id,
            requested_at=resolved_now,
            minimum_guest_count=minimum_guest_count,
            minimum_guest_level=minimum_guest_level,
            guest_rarity_cap=guest_rarity_cap,
            max_guest_level_step=max_guest_level_step,
            arena_growth_objective=(arena_growth_objective if int(request_digest_schema) >= 2 else None),
            control_snapshot_digest=(_control_snapshot_digest if int(request_digest_schema) >= 2 else None),
            policy_checksum=(_expected_policy_checksum if int(request_digest_schema) >= 3 else None),
            schema_version=int(request_digest_schema),
        )
        committed = _committed_growth_outcome(
            operation_id=normalized_operation_id,
            profile_id=profile_id,
            request_digest=request_digest,
        )
        if committed is not None:
            return committed
        execution_context = _MaintenanceExecutionReceiptContext(
            operation_id=normalized_operation_id,
            attempt_ordinal=normalized_attempt_ordinal,
            request_digest=request_digest,
            requested_at=resolved_now,
            request_digest_schema=int(request_digest_schema),
        )
    try:
        routing = read_virtual_player_routing()
    except RuntimeRoutingError:
        # A missing or malformed routing row is an operational pause, not a
        # profile-qualification failure. Keep the lease and let the next
        # scheduled scan retry after routing recovers.
        return AcceleratedGrowthOutcome.PAUSED
    if routing.maintenance_mode is MaintenanceMode.LEGACY_BEFORE_GATE:
        # Arena acceleration is V2-only.  A stale pre-gate routing row must
        # pause the member rather than dispatching the retired V1 writer.
        return AcceleratedGrowthOutcome.PAUSED
    if routing.maintenance_mode in {
        MaintenanceMode.V2_CUTOVER,
        MaintenanceMode.V2_PAUSED,
    }:
        return AcceleratedGrowthOutcome.PAUSED
    if _arena_member_id is not None and _arena_round_ordinal is not None:
        _run_arena_v2_healing_sweep(
            profile_id,
            now=resolved_now,
            arena_member_id=int(_arena_member_id),
            arena_round_ordinal=int(_arena_round_ordinal),
        )
    try:
        result = maintain_virtual_player_v2(
            profile_id,
            trigger=MaintenanceTrigger.ARENA_ACCELERATION,
            operation_id=(execution_context.operation_id if execution_context is not None else None),
            attempt_ordinal=(execution_context.attempt_ordinal if execution_context is not None else 1),
            now=resolved_now,
            minimum_guest_count=minimum_guest_count,
            minimum_guest_level=minimum_guest_level,
            guest_rarity_cap=guest_rarity_cap,
            max_guest_level_step=max_guest_level_step,
            arena_growth_objective=arena_growth_objective,
            _arena_excluded_training_guest_ids=arena_excluded_training_guest_ids,
            _arena_member_id=_arena_member_id,
            _arena_round_ordinal=_arena_round_ordinal,
            _arena_action_ordinal=_arena_action_ordinal,
            _arena_slot_attempt_ordinal=(
                max(1, int(_arena_slot_attempt_ordinal)) if _arena_slot_attempt_ordinal is not None else None
            ),
            _execution_request_digest=(execution_context.request_digest if execution_context is not None else None),
            _execution_requested_at=(execution_context.requested_at if execution_context is not None else None),
            _execution_request_digest_schema=(
                execution_context.request_digest_schema if execution_context is not None else 3
            ),
            _frozen_control_snapshot_digest=_control_snapshot_digest,
        )
    except DatabaseError:
        if execution_context is not None:
            committed = _committed_growth_outcome(
                operation_id=execution_context.operation_id,
                profile_id=profile_id,
                request_digest=execution_context.request_digest,
            )
            if committed is not None:
                return committed
        raise
    if execution_context is not None:
        committed = _committed_growth_outcome(
            operation_id=execution_context.operation_id,
            profile_id=profile_id,
            request_digest=execution_context.request_digest,
        )
        if committed is not None:
            return committed
    return _accelerated_growth_outcome_from_maintenance(result)


def _accelerated_growth_outcome_from_maintenance(
    result: MaintenanceResult,
) -> AcceleratedGrowthOutcome:
    return {
        MaintenanceOutcome.APPLIED: AcceleratedGrowthOutcome.GROWN,
        MaintenanceOutcome.NO_ACTION: AcceleratedGrowthOutcome.NO_ACTION,
        MaintenanceOutcome.BUSY: AcceleratedGrowthOutcome.BUSY,
        MaintenanceOutcome.PAUSED: AcceleratedGrowthOutcome.PAUSED,
        MaintenanceOutcome.INELIGIBLE: AcceleratedGrowthOutcome.INELIGIBLE,
    }[result.outcome]


@record_maintenance_stage(STAGE_PLANNING_SNAPSHOT_PRELOAD)
def _scheduled_planning_snapshots(
    profiles: tuple[BotProfile, ...],
    *,
    planned_at: datetime,
) -> dict[int, _MaintenancePlanningSnapshot]:
    manor_ids = tuple(int(profile.manor_id) for profile in profiles)
    guests_by_manor_mutable: dict[int, list[Guest]] = defaultdict(list)
    for guest in Guest.objects.filter(manor_id__in=manor_ids).select_related("template").order_by("manor_id", "id"):
        guests_by_manor_mutable[int(guest.manor_id)].append(guest)
    guests_by_manor = {manor_id: tuple(guests_by_manor_mutable.get(manor_id, ())) for manor_id in manor_ids}

    all_guest_ids = [int(guest.id) for guests in guests_by_manor.values() for guest in guests]
    guest_manor_by_id = {int(guest.id): manor_id for manor_id, guests in guests_by_manor.items() for guest in guests}
    guest_skills_mutable: dict[int, list[GuestSkill]] = defaultdict(list)
    if all_guest_ids:
        for guest_skill in (
            GuestSkill.objects.filter(guest_id__in=all_guest_ids)
            .select_related("skill")
            .order_by("guest_id", "skill_id")
        ):
            guest_skills_mutable[guest_manor_by_id[int(guest_skill.guest_id)]].append(guest_skill)
    gear_items_mutable: dict[int, list[GearItem]] = defaultdict(list)
    if all_guest_ids:
        for gear in (
            GearItem.objects.filter(
                manor_id__in=manor_ids,
                guest_id__in=all_guest_ids,
            )
            .select_related("template")
            .order_by("manor_id", "guest_id", "id")
        ):
            gear_items_mutable[int(gear.manor_id)].append(gear)
    paid_guest_ids = frozenset(
        bulk_check_salary_paid(
            all_guest_ids,
            timezone.localdate(planned_at),
        )
    )
    strengths = load_manor_strength_summaries(
        manor_ids=manor_ids,
        guests_by_manor=guests_by_manor,
    )
    buildings_by_manor_mutable: dict[int, list[Building]] = defaultdict(list)
    for building in (
        Building.objects.filter(manor_id__in=manor_ids)
        .select_related("building_type")
        .order_by("manor_id", "building_type__key", "id")
    ):
        buildings_by_manor_mutable[int(building.manor_id)].append(building)
    buildings_by_manor = {manor_id: tuple(buildings_by_manor_mutable.get(manor_id, ())) for manor_id in manor_ids}
    technologies_by_manor_mutable: dict[int, list[PlayerTechnology]] = defaultdict(list)
    for technology in PlayerTechnology.objects.filter(manor_id__in=manor_ids).order_by("manor_id", "tech_key", "id"):
        technologies_by_manor_mutable[int(technology.manor_id)].append(technology)
    technologies_by_manor = {manor_id: tuple(technologies_by_manor_mutable.get(manor_id, ())) for manor_id in manor_ids}
    technology_levels_by_manor = {
        manor_id: {str(technology.tech_key): int(technology.level) for technology in technologies_by_manor[manor_id]}
        for manor_id in manor_ids
    }
    manors = tuple(profile.manor for profile in profiles)
    production_bases = load_resource_production_bases(
        manors,
        guest_counts={manor_id: len(guests_by_manor[manor_id]) for manor_id in manor_ids},
        buildings_by_manor=buildings_by_manor,
        technology_levels=technology_levels_by_manor,
    )
    policy_releases = BotPolicyRelease.objects.in_bulk({int(profile.policy_version) for profile in profiles})

    troop_manor_ids = tuple(int(profile.manor_id) for profile in profiles if int(profile.manor.retainer_count or 0) > 0)
    troop_counts_mutable: dict[int, list[tuple[str, int]]] = defaultdict(list)
    if troop_manor_ids:
        for manor_id, troop_key, count in (
            PlayerTroop.objects.filter(manor_id__in=troop_manor_ids)
            .order_by("manor_id", "troop_template__key")
            .values_list("manor_id", "troop_template__key", "count")
        ):
            troop_counts_mutable[int(manor_id)].append((str(troop_key), int(count)))

    warehouse_items_mutable: dict[int, list[InventoryItem]] = defaultdict(list)
    medicine_items_mutable: dict[int, list[InventoryItem]] = defaultdict(list)
    if manor_ids:
        for item in (
            InventoryItem.objects.filter(
                manor_id__in=manor_ids,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            )
            .filter(Q(quantity__gt=0) | Q(template__key=GRAIN_ITEM_KEY))
            .select_related("template")
            .order_by("manor_id", "template__key", "id")
        ):
            manor_id = int(item.manor_id)
            warehouse_items_mutable[manor_id].append(item)
            if item.template.effect_type == ItemTemplate.EffectType.MEDICINE:
                medicine_items_mutable[manor_id].append(item)

    all_inventory_template_keys = tuple(
        dict.fromkeys(
            (
                GRAIN_ITEM_KEY,
                *(
                    key
                    for profile in profiles
                    for key in _inventory_template_query_keys(profile.inventory_template_keys)
                ),
            )
        )
    )
    inventory_templates_by_key = {
        str(template.key): template
        for template in ItemTemplate.objects.filter(key__in=all_inventory_template_keys).order_by("key")
    }
    all_warehouse_items = tuple(item for manor_id in manor_ids for item in warehouse_items_mutable.get(manor_id, ()))
    skills = _skills_for_warehouse_items(all_warehouse_items)

    # Policy 2 uses the same immutable projection pools for every profile in a
    # scheduled batch.  Load them once here so the later per-profile plan and
    # lock-time revalidation can remain deterministic without issuing the same
    # catalog reads repeatedly.
    virtual_pools = _load_virtual_projection_pools(planned_at=planned_at)

    snapshots: dict[int, _MaintenancePlanningSnapshot] = {}
    for profile in profiles:
        manor_id = int(profile.manor_id)
        guests = guests_by_manor[manor_id]
        guest_ids = {int(guest.id) for guest in guests}
        snapshots[int(profile.id)] = _MaintenancePlanningSnapshot(
            profile=profile,
            guests=guests,
            buildings=buildings_by_manor[manor_id],
            technologies=technologies_by_manor[manor_id],
            gear_items=tuple(gear_items_mutable.get(manor_id, ())),
            strength=strengths[manor_id],
            paid_guest_ids=frozenset(paid_guest_ids & guest_ids),
            troop_counts=tuple(troop_counts_mutable.get(manor_id, ())),
            medicine_items=(tuple(medicine_items_mutable.get(manor_id, ())) if _healable_guests(guests) else ()),
            guest_skills=tuple(guest_skills_mutable.get(manor_id, ())),
            skills=skills,
            warehouse_items=tuple(warehouse_items_mutable.get(manor_id, ())),
            inventory_templates=tuple(
                inventory_templates_by_key[key]
                for key in _inventory_template_query_keys(profile.inventory_template_keys)
                if key in inventory_templates_by_key
            ),
            production_basis=production_bases[manor_id],
            policy_release=policy_releases.get(int(profile.policy_version)),
            grain_template=inventory_templates_by_key.get(GRAIN_ITEM_KEY),
            virtual_skill_books=virtual_pools.skill_books,
            virtual_skills=virtual_pools.skills,
            virtual_gear_templates=virtual_pools.gear_templates,
            virtual_inventory_templates=virtual_pools.inventory_templates,
            rare_inventory_quantity_today=virtual_pools.rare_inventory_quantity_today,
        )
    return snapshots


def _scheduled_planning_snapshots_with_profile_isolation(
    profiles: tuple[BotProfile, ...],
    *,
    planned_at: datetime,
    safety_attempts: tuple[MaintenanceAttempt, ...],
    terminal_batch: list[tuple[MaintenanceAttempt, MaintenanceResult | MaintenanceAttemptResult]],
) -> dict[int, _MaintenancePlanningSnapshot]:
    """Keep one malformed profile from poisoning a scheduled batch."""

    expected_profile_ids = {int(profile.id) for profile in profiles}

    def finish_unresolved_attempts() -> None:
        terminal_operation_ids = {attempt.operation_id for attempt, _result in terminal_batch}
        terminal_batch.extend(
            (attempt, MaintenanceAttemptResult.FAILED)
            for attempt in safety_attempts
            if attempt.operation_id not in terminal_operation_ids
        )
        _finish_safety_attempts_best_effort(terminal_batch)

    def validate_snapshot_ids(
        snapshots: dict[int, _MaintenancePlanningSnapshot],
        *,
        expected_ids: set[int],
    ) -> None:
        if set(snapshots) != expected_ids:
            raise RuntimeError("scheduled maintenance planning snapshots do not match requested profiles")

    try:
        batch_snapshots = _scheduled_planning_snapshots(
            profiles,
            planned_at=planned_at,
        )
        validate_snapshot_ids(
            batch_snapshots,
            expected_ids=expected_profile_ids,
        )
        return batch_snapshots
    except (DatabaseError, SafetyProviderError):
        finish_unresolved_attempts()
        raise
    except _SCHEDULED_PLANNING_PROFILE_ERRORS as exc:
        logger.exception(
            "Virtual player V2 maintenance batch planning degraded to profile isolation",
            extra={
                "event": "virtual_player_v2_maintenance_batch_planning_degraded",
                "due_profile_count": len(profiles),
                "failure_code": type(exc).__name__,
            },
        )
    except Exception:
        logger.exception(
            "Virtual player V2 maintenance batch planning failed unexpectedly",
            extra={
                "event": "virtual_player_v2_maintenance_batch_planning_unexpected_failure",
                "due_profile_count": len(profiles),
            },
        )
        if len(profiles) == 1:
            # With no sibling entity to isolate, preserve the task-level
            # failure so infrastructure can retry the scan with full context.
            finish_unresolved_attempts()
            raise
        # A batch-level planner failure is not enough evidence that every
        # profile is broken.  Fall through to one-profile planning so healthy
        # rows can still make progress and only the poisoned row is quarantined.

    snapshots: dict[int, _MaintenancePlanningSnapshot] = {}
    for profile, safety_attempt in zip(profiles, safety_attempts, strict=True):
        profile_id = int(profile.id)
        try:
            profile_snapshots = _scheduled_planning_snapshots(
                (profile,),
                planned_at=planned_at,
            )
            validate_snapshot_ids(
                profile_snapshots,
                expected_ids={profile_id},
            )
        except (DatabaseError, SafetyProviderError):
            finish_unresolved_attempts()
            raise
        except _SCHEDULED_PLANNING_PROFILE_ERRORS as exc:
            logger.exception(
                "Virtual player V2 maintenance profile planning failed: profile_id=%s",
                profile_id,
                extra={
                    "event": "virtual_player_v2_maintenance_profile_planning_failed",
                    "profile_id": profile_id,
                    "failure_code": type(exc).__name__,
                },
            )
            record_recovery_failure(
                scope="profile",
                entity_key=str(profile_id),
                failure_code=classify_failure(exc),
                error=exc,
                operation_id=safety_attempt.operation_id,
                payload={"trigger": MaintenanceTrigger.SCHEDULED.value, "phase": "planning"},
            )
            terminal_batch.append((safety_attempt, MaintenanceAttemptResult.FAILED))
            continue
        except Exception as exc:
            logger.exception(
                "Virtual player V2 maintenance profile planning failed unexpectedly: profile_id=%s",
                profile_id,
                extra={
                    "event": "virtual_player_v2_maintenance_profile_planning_unexpected_failure",
                    "profile_id": profile_id,
                },
            )
            record_recovery_failure(
                scope="profile",
                entity_key=str(profile_id),
                failure_code=classify_failure(exc),
                error=exc,
                operation_id=safety_attempt.operation_id,
                payload={"trigger": MaintenanceTrigger.SCHEDULED.value, "phase": "planning"},
            )
            terminal_batch.append((safety_attempt, MaintenanceAttemptResult.FAILED))
            continue
        snapshots.update(profile_snapshots)
    return snapshots


def _retire_due_v2_profiles(
    profiles: tuple[BotProfile, ...],
    *,
    current_time: datetime,
) -> tuple[int, frozenset[int]]:
    """Apply ordinary V2 retirement without crossing an Arena lease boundary."""

    retired = 0
    retired_ids: set[int] = set()
    for profile in profiles:
        if retire_locked_virtual_player_if_unprotected(profile, now=current_time):
            retired += 1
            retired_ids.add(int(profile.id))
    return retired, frozenset(retired_ids)


@record_maintenance_stage(STAGE_SAFETY_TASK_WRAPUP)
def _maintain_due_virtual_players_v2(
    *,
    current_time: datetime,
    limit: int,
    routing: RuntimeRoutingSnapshot,
) -> int:
    batch_started_at = monotonic()
    limit = max(0, min(SCHEDULED_MAINTENANCE_DUE_SCAN_HARD_CAP, int(limit)))
    if limit <= 0:
        return 0
    if recovery_circuit_is_open(path="profile", now=current_time):
        logger.warning("Virtual player V2 profile maintenance circuit is open; preserving due profiles")
        return 0
    # Filter unsafe profiles before applying the batch limit.  Slicing first
    # lets a permanently quarantined head-of-line profile starve every later
    # due profile indefinitely.
    open_scheduled_cycle = (
        BotMaintenanceCycle.objects.filter(
            profile_id=OuterRef("pk"),
            trigger=BotMaintenanceCycle.Trigger.SCHEDULED,
            status=BotMaintenanceCycle.Status.OPEN,
        )
        .order_by("-cycle_ordinal", "-id")
        .values("next_slot_due_at")[:1]
    )
    profile_queryset = without_unresolved_external_reconciliations(
        BotProfile.objects.filter(
            engine_version=V2_MAINTENANCE_ENGINE_VERSION,
            policy_version=2,
            state__in=[BotProfile.State.ACTIVE, BotProfile.State.SLOWING],
            arena_virtual_reserve__isnull=True,
        ).annotate(
            scheduled_cycle_next_slot_due_at=Subquery(
                open_scheduled_cycle,
                output_field=DateTimeField(),
            ),
        )
    )
    recovery_needs_clear = BotMaintenanceRecovery.objects.filter(
        scope=BotMaintenanceRecovery.Scope.PROFILE,
        entity_key=Cast(OuterRef("pk"), output_field=CharField()),
    ).exclude(
        status=BotMaintenanceRecovery.Status.REQUEUED,
        failure_streak=0,
    )
    due_profile_queryset = exclude_blocked_profile_recoveries(
        profile_queryset.filter(
            Q(retire_at__lte=current_time)
            | Q(scheduled_cycle_next_slot_due_at__lte=current_time)
            | (Q(scheduled_cycle_next_slot_due_at__isnull=True) & Q(next_growth_at__lte=current_time)),
        ),
        now=current_time,
    )
    with record_maintenance_stage(STAGE_DUE_BACKLOG_SELECTION):
        due_summary = due_profile_queryset.aggregate(
            due_backlog_count=Count("pk"),
            region_count=Count("manor__region", distinct=True),
        )
        due_backlog_count = int(due_summary["due_backlog_count"] or 0)
        region_count = max(1, int(due_summary["region_count"] or 0))
        if due_backlog_count > SCHEDULED_MAINTENANCE_DUE_SCAN_HARD_CAP:
            logger.error(
                "Virtual player V2 due backlog exceeds the bounded selection contract; preserving all due profiles",
                extra={
                    "event": "virtual_player_v2_due_selection_overflow",
                    "due_backlog_count": int(due_backlog_count),
                    "selection_hard_cap": SCHEDULED_MAINTENANCE_DUE_SCAN_HARD_CAP,
                },
            )
            return 0
        per_region_limit = max(1, math.ceil(int(limit) / region_count))
        due_order = (
            Case(
                When(retire_at__lte=current_time, then=F("retire_at")),
                When(
                    scheduled_cycle_next_slot_due_at__isnull=False,
                    then=F("scheduled_cycle_next_slot_due_at"),
                ),
                default=F("next_growth_at"),
                output_field=DateTimeField(),
            ).asc(),
            F("last_planned_at").asc(nulls_first=True),
            F("id").asc(),
        )
        ordered_due_profiles = tuple(
            due_profile_queryset.annotate(
                maintenance_database_now=RawSQL(
                    database_utc_sql_expression(),
                    (),
                    output_field=DateTimeField(),
                ),
                maintenance_recovery_needs_clear=Exists(recovery_needs_clear),
            )
            .select_related("manor")
            .order_by(*due_order, "manor__region")[:SCHEDULED_MAINTENANCE_DUE_SCAN_HARD_CAP]
        )
        selected_by_region: dict[str, int] = defaultdict(int)
        selected_candidates: list[BotProfile] = []
        for profile in ordered_due_profiles:
            region = str(profile.manor.region)
            if selected_by_region[region] >= per_region_limit:
                continue
            selected_by_region[region] += 1
            selected_candidates.append(profile)
            if len(selected_candidates) >= int(limit):
                break
        candidates = tuple(selected_candidates)
        oldest_due_at = None
        if candidates:
            oldest_profile = candidates[0]
            scheduled_cycle_due_at = getattr(oldest_profile, "scheduled_cycle_next_slot_due_at", None)
            oldest_due_at = (
                oldest_profile.retire_at
                if oldest_profile.retire_at is not None and oldest_profile.retire_at <= current_time
                else (scheduled_cycle_due_at if scheduled_cycle_due_at is not None else oldest_profile.next_growth_at)
            )
        oldest_due_age_seconds = (
            max(0.0, (current_time - oldest_due_at).total_seconds()) if oldest_due_at is not None else 0.0
        )
    logger.info(
        "Virtual player V2 maintenance selected a bounded due batch",
        extra={
            "event": "virtual_player_v2_maintenance_batch_selected",
            "requested_limit": int(limit),
            "due_backlog_count": int(due_backlog_count),
            "region_count": int(region_count),
            "per_region_limit": int(per_region_limit),
            "selected_count": len(candidates),
            "oldest_due_at": oldest_due_at.isoformat() if oldest_due_at is not None else None,
            "oldest_due_age_seconds": oldest_due_age_seconds,
            "selection_duration_seconds": max(0.0, monotonic() - batch_started_at),
        },
    )
    retirement_profiles = tuple(
        profile for profile in candidates if profile.retire_at is not None and profile.retire_at <= current_time
    )
    retirement_ids = {int(profile.id) for profile in retirement_profiles}
    profiles = tuple(profile for profile in candidates if int(profile.id) not in retirement_ids)
    if not profiles and not retirement_profiles:
        logger.info(
            "Virtual player V2 maintenance found no executable due profiles",
            extra={
                "event": "virtual_player_v2_maintenance_batch_completed",
                "requested_limit": int(limit),
                "due_backlog_count": int(due_backlog_count),
                "selected_count": 0,
                "maintained_count": 0,
                "batch_duration_seconds": max(0.0, monotonic() - batch_started_at),
            },
        )
        return 0

    safety_preflight = check_v2_development_write_preflight(now=current_time)
    if not safety_preflight.allowed:
        logger.warning(
            "Virtual player V2 maintenance batch blocked by safety preflight: reason=%s due_profiles=%s",
            safety_preflight.reason,
            len(profiles) + len(retirement_profiles),
            extra={
                "event": "virtual_player_v2_maintenance_batch_blocked",
                "reason": safety_preflight.reason,
                "due_profile_count": len(profiles) + len(retirement_profiles),
                "checked_at": safety_preflight.checked_at,
                "monitor_heartbeat_at": safety_preflight.monitor_heartbeat_at,
            },
        )
        logger.info(
            "Virtual player V2 maintenance batch blocked by safety preflight",
            extra={
                "event": "virtual_player_v2_maintenance_batch_completed",
                "requested_limit": int(limit),
                "due_backlog_count": int(due_backlog_count),
                "selected_count": len(candidates),
                "maintained_count": 0,
                "batch_duration_seconds": max(0.0, monotonic() - batch_started_at),
                "blocked": True,
            },
        )
        return 0

    retired, _retired_ids = _retire_due_v2_profiles(
        retirement_profiles,
        current_time=current_time,
    )
    if not profiles:
        return retired

    try:
        database_clock = normalize_database_utc(getattr(candidates[0], "maintenance_database_now", None))
    except (DatabaseError, TypeError, ValueError):
        logger.exception("Virtual player V2 maintenance database clock annotation was invalid")
        raise

    try:
        safety_attempts = start_maintenance_attempts(
            trigger=MaintenanceTrigger.SCHEDULED,
            operation_ids=(None,) * len(profiles),
            retention_reference_at=database_clock,
        )
    except (DatabaseError, SafetyProviderError) as exc:
        log_safety_metric_failure(
            operation="maintenance_attempt_started_batch",
            exc=exc,
        )
        raise

    terminal_batch: list[tuple[MaintenanceAttempt, MaintenanceResult | MaintenanceAttemptResult]] = []
    planning_snapshots = _scheduled_planning_snapshots_with_profile_isolation(
        profiles,
        planned_at=current_time,
        safety_attempts=safety_attempts,
        terminal_batch=terminal_batch,
    )

    maintained = retired
    for profile, safety_attempt in zip(profiles, safety_attempts, strict=True):
        profile_id = int(profile.id)
        planning_snapshot = planning_snapshots.get(profile_id)
        if planning_snapshot is None:
            continue
        try:
            result = maintain_virtual_player_v2(
                profile_id,
                trigger=MaintenanceTrigger.SCHEDULED,
                now=current_time,
                _routing_snapshot=routing,
                _external_reconciliation_prechecked=True,
                _planning_snapshot=planning_snapshot,
                _safety_attempt=safety_attempt,
                _safety_terminal_batch=terminal_batch,
            )
        except DatabaseError as exc:
            record_recovery_failure(
                scope="profile",
                entity_key=str(profile_id),
                failure_code=classify_failure(exc, commit_uncertain=True),
                error=exc,
                operation_id=safety_attempt.operation_id,
                payload={"trigger": MaintenanceTrigger.SCHEDULED.value},
            )
            logger.exception(
                "Virtual player V2 maintenance database failure isolated: profile_id=%s",
                profile_id,
                extra={
                    "event": "virtual_player_v2_maintenance_profile_database_failure",
                    "profile_id": int(profile_id),
                    "failure_class": RecoveryFailureClass.COMMIT_UNCERTAIN.value,
                },
            )
            _finish_safety_attempts_best_effort(terminal_batch)
            raise
        except Exception as exc:
            record_recovery_failure(
                scope="profile",
                entity_key=str(profile_id),
                failure_code=classify_failure(exc),
                error=exc,
                operation_id=safety_attempt.operation_id,
                payload={"trigger": MaintenanceTrigger.SCHEDULED.value},
            )
            logger.exception(
                "Virtual player V2 maintenance failed: profile_id=%s",
                profile_id,
                extra={
                    "event": "virtual_player_v2_maintenance_failed",
                    "profile_id": int(profile_id),
                },
            )
            continue
        if result.outcome in {
            MaintenanceOutcome.APPLIED,
            MaintenanceOutcome.NO_ACTION,
        }:
            if bool(getattr(profile, "maintenance_recovery_needs_clear", False)):
                clear_recovery_failure(scope="profile", entity_key=str(profile_id), now=current_time)
            maintained += 1
    _finish_safety_attempts_best_effort(terminal_batch)
    logger.info(
        "Virtual player V2 maintenance batch completed",
        extra={
            "event": "virtual_player_v2_maintenance_batch_completed",
            "requested_limit": int(limit),
            "due_backlog_count": int(due_backlog_count),
            "selected_count": len(candidates),
            "maintained_count": int(maintained),
            "retired_count": int(retired),
            "batch_duration_seconds": max(0.0, monotonic() - batch_started_at),
            "oldest_due_age_seconds": oldest_due_age_seconds,
        },
    )
    return maintained


def maintain_due_virtual_players(*, now=None, limit: int = SCHEDULED_MAINTENANCE_DEFAULT_BATCH_SIZE) -> int:
    current_time = now or timezone.now()
    normalized_limit = max(0, min(SCHEDULED_MAINTENANCE_DUE_SCAN_HARD_CAP, int(limit)))
    if normalized_limit <= 0:
        return 0
    try:
        routing = read_virtual_player_routing()
    except RuntimeRoutingError:
        return 0
    if routing.maintenance_mode is MaintenanceMode.LEGACY_BEFORE_GATE:
        # V1 scheduled maintenance is retired.  A stale pre-gate routing row
        # must fail closed instead of silently dispatching the old writer.
        return 0
    if routing.maintenance_mode in {
        MaintenanceMode.V2_CUTOVER,
        MaintenanceMode.V2_PAUSED,
    }:
        return 0

    acquired, lock_key, lock_token = acquire_action_lock(
        "virtual_player",
        "scheduled_maintenance_batch",
        0,
        "v2",
        timeout_seconds=SCHEDULED_MAINTENANCE_BATCH_LOCK_TIMEOUT_SECONDS,
        logger=logger,
        log_context="Virtual player scheduled maintenance batch",
    )
    if not acquired:
        return 0
    try:
        return _maintain_due_virtual_players_v2(
            current_time=current_time,
            limit=normalized_limit,
            routing=routing,
        )
    finally:
        release_action_lock(
            lock_key,
            lock_token=lock_token,
            logger=logger,
            log_context="Virtual player scheduled maintenance batch",
        )


__all__ = [
    "MaintenancePlan",
    "SCHEDULED_MAINTENANCE_DEFAULT_BATCH_SIZE",
    "V2MaintenanceError",
    "accelerate_virtual_player_growth",
    "build_virtual_player_v2_maintenance_plan",
    "execute_virtual_player_v2_maintenance_plan",
    "maintain_due_virtual_players",
    "maintain_virtual_player_v2",
    "reactivate_locked_virtual_player_profile",
    "retire_locked_virtual_player_if_unprotected",
    "retire_virtual_player_if_unprotected",
]
