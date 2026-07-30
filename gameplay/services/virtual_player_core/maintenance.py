from __future__ import annotations

import logging
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID

from django.db import DatabaseError, connection, transaction
from django.db.models import Sum
from django.utils import timezone

from core.exceptions import (
    BuildingConcurrentUpgradeLimitError,
    BuildingMaxLevelError,
    BuildingUpgradingError,
    EquipmentError,
    EquipmentSlotFullError,
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
from gameplay.models import (
    BotMaintenanceExecution,
    BotPolicyRelease,
    BotProfile,
    Building,
    InventoryItem,
    ItemTemplate,
    Manor,
    PlayerTechnology,
    PlayerTroop,
    RaidRun,
    ResourceEvent,
    ResourceType,
    ScoutRecord,
)
from gameplay.services.arena.virtual_protection import is_virtual_profile_arena_protected
from gameplay.services.inventory.core import GRAIN_ITEM_KEY, add_item_to_inventory_locked
from gameplay.services.manor.core import (
    BuildingUpgradeQuote,
    BuildingUpgradeQuoteStaleError,
    apply_building_upgrade_locked,
    quote_building_upgrade,
)
from gameplay.services.manor.prestige import schedule_prestige_change_on_commit
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
)
from gameplay.services.virtual_player_state_policy import VIRTUAL_PROFILE_MAINTAINED_STATES
from guests.growth_engine import allocate_level_up_attributes, apply_training_completion
from guests.models import GearItem, Guest, GuestSkill, GuestStatus, Skill
from guests.services.equipment import equip_guest_from_inventory_locked
from guests.services.health import MedicineUseQuote, apply_medicine_item_for_guest_locked, quote_medicine_item_for_guest
from guests.services.salary import (
    SalaryBatchQuote,
    bulk_check_salary_paid,
    pay_all_salaries,
    pay_all_salaries_locked,
    quote_all_salaries,
)
from guests.services.skills import learn_guest_skill_locked
from guests.services.training import apply_training_locked, project_training_completion

from . import profile_store
from .bootstrap import growth_stage_cap_for_band as _growth_stage_cap_for_band
from .bootstrap import lifecycle_dates
from .calibration_runtime import ActiveCalibrationReference, load_active_calibration_reference
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
    InvalidStrengthBudgetError,
    MaintenanceOutcome,
    MaintenanceResult,
    MaintenanceScheduleDisposition,
    MaintenanceTrigger,
    MaintenanceTriggerPolicy,
    StrengthBudgetEntry,
    maintenance_trigger_policy,
    parse_strength_budget_entries,
    prune_strength_budget_entries,
)
from .economy import ForcedSettlementDecision, parse_forced_settlement_budget, plan_forced_settlement
from .inventory_budget import apply_inventory_daily_caps, inventory_daily_cap_limits
from .legacy.inventory import _replenish_inventory_stock
from .legacy.projection import apply_stable_troop_variation, bounded_approach, range_value
from .legacy.roster import (
    INITIAL_BOT_GUEST_LEVEL,
    _grant_configured_extra_skills,
    _grant_extra_template_skills,
    _project_buildings,
    _project_guests_and_gear,
    _project_resources,
    _project_technologies,
    _project_troops,
    _promote_one_virtual_guest_rarity,
    _reconcile_guest_gear,
)
from .maintenance_action_specs import (
    BuildingUpgradeActionSpec,
    EquipmentEquipActionSpec,
    InventoryAcquisitionActionSpec,
    MaintenanceActionSpec,
    MaintenanceActionSpecError,
    SkillLearningActionSpec,
    TechnologyUpgradeActionSpec,
    maintenance_action_spec_payload,
)
from .maintenance_candidates import (
    MaintenanceCandidateError,
    build_equipment_equip_candidates,
    build_inventory_acquisition_candidates,
    build_skill_learning_candidates,
)
from .maintenance_rules import (
    MaintenanceNoActionReason,
    MaintenanceRuleError,
    PrestigeBandGrowthPolicy,
    evaluate_controlled_action,
    next_normal_strength_check_at,
    parse_prestige_band_growth_policy,
)
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
    select_development_intent,
    select_guest_healing_candidate,
)
from .random_context import (
    RandomContext,
    UnsupportedRandomDomainError,
    UnsupportedRngVersionError,
    canonical_json_bytes,
)
from .reference_snapshots import CORE_BUILDING_KEYS, ReferenceSnapshotError
from .reference_snapshots import apply_persona_to_projection as _apply_persona_to_projection
from .reference_snapshots import build_strength_summary, load_manor_strength_summaries, load_manor_strength_summary
from .reference_snapshots import maintenance_projection_from_real_players as _maintenance_projection_from_real_players
from .reference_snapshots import policy_starter_snapshot, select_policy_reference
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
)
from .strategy import BotDevelopmentPlan, DevelopmentPlanError, development_plan_catalog_v1, parse_development_plan

logger = logging.getLogger(__name__)

LEGACY_MAINTENANCE_ENGINE_VERSION = 1
V2_MAINTENANCE_ENGINE_VERSION = 2
SCHEDULED_MAINTENANCE_BATCH_LOCK_TIMEOUT_SECONDS = 180


class V2MaintenanceError(ValueError):
    pass


class MaintenanceExecutionConflict(V2MaintenanceError):
    pass


class InventoryAcquisitionUnavailable(V2MaintenanceError):
    pass


class _V2MaintenanceOutcomeError(V2MaintenanceError):
    def __init__(self, outcome: MaintenanceOutcome, reason: str) -> None:
        super().__init__(reason)
        self.outcome = MaintenanceOutcome(outcome)
        self.reason = str(reason)


@dataclass(frozen=True, slots=True)
class _MaintenanceExecutionReceiptContext:
    operation_id: str
    attempt_ordinal: int
    request_digest: str
    requested_at: datetime
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
    reference_selection: ReferenceSelection
    target_reference_selection: ReferenceSelection | None
    strength_before: StrengthSummary
    strength_budget_entries_before: tuple[StrengthBudgetEntry, ...]
    resource_production_deltas: tuple[tuple[str, int], ...]
    forced_settlement_decision: ForcedSettlementDecision
    salary_quote: SalaryBatchQuote
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
    calibration_route: CalibrationRoute | None
    target_calibration_route: CalibrationRoute | None
    minimum_guest_count: int | None
    minimum_guest_level: int | None
    guest_rarity_cap: str | None
    max_guest_level_step: int | None

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
        if self.guest_rarity_cap is not None and not isinstance(self.guest_rarity_cap, str):
            raise V2MaintenanceError("guest_rarity_cap must be a string or None")

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


def _next_growth_time(now, profile: BotProfile, rng: random.Random, config: dict[str, Any]):
    lifecycle = config.get("lifecycle") or {}
    hours = range_value(rng, lifecycle.get("next_growth_hours"), default=(2, 18))
    if profile.state == BotProfile.State.SLOWING:
        hours *= 2
    return now + timedelta(hours=hours, minutes=rng.randint(0, 59))


def _sync_profile_prestige_band(profile: BotProfile, *, config: dict[str, Any]) -> None:
    current_band = prestige_band_for_value(int(profile.manor.prestige or 0), config)
    if not current_band or current_band == profile.current_prestige_band:
        return

    profile_store.set_current_prestige_band(profile, prestige_band=current_band)


def _pay_maintained_bot_salaries(profile: BotProfile, *, now) -> None:
    manor = profile.manor
    try:
        pay_all_salaries(manor, for_date=timezone.localdate(now))
    except (NoGuestsError, SalaryAlreadyPaidError):
        pass
    except InsufficientResourceError:
        logger.info(
            "Virtual player could not cover guest salaries: manor_id=%s state=%s",
            manor.id,
            profile.state,
            extra={
                "event": "virtual_player_salary_unpaid",
                "manor_id": manor.id,
                "state": profile.state,
            },
        )


def _maintain_active_profile(
    profile: BotProfile,
    *,
    now,
    config: dict[str, Any],
    minimum_guest_count: int | None = None,
    minimum_guest_level: int | None = None,
    guest_rarity_cap: str | None = None,
    max_guest_level_step: int | None = None,
) -> None:
    rng = random.Random(profile.growth_seed + profile.growth_stage)
    manor = profile.manor
    before_building_level = max(1, int(profile.growth_stage))
    before_guest_level = max([int(level) for level in manor.guests.values_list("level", flat=True)] or [0])
    before_troop_count = int(manor.troops.aggregate(total=Sum("count"))["total"] or 0)
    before_prestige = int(manor.prestige or 0)
    stage_cap = _growth_stage_cap_for_band(profile_target_prestige_band(profile), config)
    projection = _maintenance_projection_from_real_players(profile, rng=rng, config=config)
    if projection is not None:
        projection = _apply_persona_to_projection(
            projection,
            archetype=str(profile.archetype),
            config=config,
            growth_seed=int(profile.growth_seed),
        )
    growth = config.get("growth") or {}
    catch_up_ratio = max(0.0, min(1.0, float(growth.get("catch_up_ratio") or 0.25)))
    if profile.state == BotProfile.State.SLOWING:
        catch_up_ratio *= max(0.0, min(1.0, float(growth.get("slowing_ratio_multiplier") or 0.5)))
    current_building_level = max(1, int(profile.growth_stage))
    projected_building_level = int(projection.building_level) if projection is not None else current_building_level + 1
    target_building_level = min(
        stage_cap,
        bounded_approach(
            current_building_level,
            max(current_building_level, projected_building_level),
            ratio=catch_up_ratio,
            min_step=1,
            max_step=max(1, int(growth.get("max_building_step") or 2)),
        ),
    )
    current_guest_count = manor.guests.count()
    target_guest_count = current_guest_count
    projected_guest_level = max([int(level) for level in manor.guests.values_list("level", flat=True)] or [1])
    if projection is not None:
        target_guest_count = max(current_guest_count, int(projection.guest_count))
        projected_guest_level = max(projected_guest_level, int(projection.guest_level))
    if minimum_guest_count is not None:
        target_guest_count = max(target_guest_count, max(0, int(minimum_guest_count)))
    if minimum_guest_level is not None:
        projected_guest_level = max(projected_guest_level, max(1, int(minimum_guest_level)))
    quantity_phase = current_guest_count < target_guest_count

    _project_buildings(manor, level=target_building_level)
    _project_resources(manor, archetype=profile.archetype, rng=rng, config=config)
    current_prestige = int(manor.prestige or 0)
    projected_prestige = int(projection.prestige) if projection is not None else target_building_level * 250
    manor.prestige = bounded_approach(
        current_prestige,
        max(current_prestige, projected_prestige),
        ratio=catch_up_ratio,
        min_step=1,
        max_step=max(1, int(growth.get("max_prestige_step") or 500)),
    )
    manor.resource_updated_at = now
    manor.save(
        update_fields=[
            "silver_capacity",
            "grain_capacity",
            "silver",
            "grain",
            "prestige",
            "resource_updated_at",
        ]
    )
    schedule_prestige_change_on_commit(
        manor=manor,
        before_prestige=before_prestige,
        after_prestige=int(manor.prestige),
    )
    _sync_profile_prestige_band(profile, config=config)
    _project_technologies(manor, level=max(1, target_building_level // 2), config=config)
    missing_guests = max(0, target_guest_count - manor.guests.count())
    if missing_guests:
        _project_guests_and_gear(
            manor,
            count=missing_guests,
            level=INITIAL_BOT_GUEST_LEVEL,
            rng=rng,
            config=config,
            archetype=str(profile.archetype),
            growth_stage=target_building_level,
            quality_enabled=False,
        )
    if not quantity_phase:
        _promote_one_virtual_guest_rarity(
            manor,
            growth_stage=target_building_level,
            rng=rng,
            config=config,
            guest_rarity_cap=guest_rarity_cap,
        )
        guest_level_step = (
            max(1, int(growth.get("max_guest_level_step") or 3))
            if max_guest_level_step is None
            else max(1, int(max_guest_level_step))
        )
        for guest in manor.guests.select_related("template").order_by("id"):
            target_level = bounded_approach(
                int(guest.level),
                max(int(guest.level), projected_guest_level),
                ratio=catch_up_ratio,
                min_step=1,
                max_step=guest_level_step,
            )
            if guest.level < target_level:
                levels_gained = target_level - int(guest.level)
                apply_training_completion(
                    guest,
                    levels_gained=levels_gained,
                    allocate_level_up_attributes_func=lambda current_guest, levels, _rng: allocate_level_up_attributes(
                        current_guest,
                        levels,
                        rng,
                    ),
                )
                guest.save(
                    update_fields=[
                        "level",
                        "force",
                        "intellect",
                        "defense_stat",
                        "agility",
                        "attribute_points",
                        "experience",
                        "current_hp",
                    ]
                )
            template_skills_added = _grant_extra_template_skills(guest, limit=1)
            if template_skills_added == 0:
                _grant_configured_extra_skills(
                    guest,
                    growth_stage=target_building_level,
                    rng=rng,
                    config=config,
                    max_new_skills=1,
                )
            _reconcile_guest_gear(
                guest,
                growth_stage=target_building_level,
                rng=rng,
                config=config,
                max_changes=1,
            )
    after_guest_level = max([int(level) for level in manor.guests.values_list("level", flat=True)] or [0])
    _pay_maintained_bot_salaries(profile, now=now)
    current_troop_count = int(manor.troops.aggregate(total=Sum("count"))["total"] or 0)
    projected_troop_count = (
        int(projection.troop_count)
        if projection is not None
        else apply_stable_troop_variation(target_building_level * 80, int(profile.growth_seed))
    )
    target_troop_count = bounded_approach(
        current_troop_count,
        max(0, projected_troop_count),
        ratio=catch_up_ratio,
        min_step=1,
        max_step=max(50, target_building_level * 80),
    )
    _project_troops(manor, count=max(0, target_troop_count), config=config)
    _replenish_inventory_stock(
        profile,
        manor,
        level=max(1, target_building_level),
        rng=rng,
        config=config,
        archetype=str(profile.archetype),
        growth_stage=target_building_level,
        prestige=int(manor.prestige or 0),
        now=now,
    )

    next_growth_at = _next_growth_time(now, profile, rng, config)
    profile_store.record_maintenance_growth(
        profile,
        growth_stage=target_building_level,
        next_growth_at=next_growth_at,
        last_planned_at=now,
    )
    logger.info(
        "Virtual player maintained: manor_id=%s region=%s archetype=%s building=%s->%s prestige=%s->%s",
        manor.id,
        manor.region,
        profile.archetype,
        before_building_level,
        target_building_level,
        before_prestige,
        manor.prestige,
        extra={
            "event": "virtual_player_maintained",
            "manor_id": manor.id,
            "region": manor.region,
            "archetype": profile.archetype,
            "state": profile.state,
            "target_prestige_band": profile_target_prestige_band(profile),
            "current_prestige_band": profile.current_prestige_band,
            "before_building_level": before_building_level,
            "after_building_level": target_building_level,
            "before_guest_level": before_guest_level,
            "after_guest_level": after_guest_level,
            "guest_growth_phase": "quantity" if quantity_phase else "quality",
            "before_troop_count": before_troop_count,
            "after_troop_count": target_troop_count,
            "before_prestige": before_prestige,
            "after_prestige": int(manor.prestige or 0),
        },
    )


def _datetime_payload(value: datetime | None) -> str | None:
    if value is None:
        return None
    if timezone.is_naive(value):
        raise V2MaintenanceError("maintenance timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


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
    reference_selection: ReferenceSelection,
    target_reference_selection: ReferenceSelection | None,
    strength_before: StrengthSummary,
    budget_entries: tuple[StrengthBudgetEntry, ...],
    resource_production_deltas: tuple[tuple[str, int], ...],
    forced_settlement_decision: ForcedSettlementDecision,
    salary_quote: SalaryBatchQuote,
    intent: DevelopmentIntent | None,
    action_kind: str,
    target_id: int | None,
    training_levels: int,
    rng_seed: int | None,
    troop_recruitment_quote: TroopRecruitmentQuote | None,
    medicine_quote: MedicineUseQuote | None,
    action_spec: MaintenanceActionSpec | None,
    gear_items: tuple[GearItem, ...],
    warehouse_items: tuple[InventoryItem, ...],
    troop_counts: tuple[tuple[str, int], ...],
    calibration_route: CalibrationRoute | None,
    target_calibration_route: CalibrationRoute | None,
    minimum_guest_count: int | None,
    minimum_guest_level: int | None,
    guest_rarity_cap: str | None,
    max_guest_level_step: int | None,
    next_growth_at_after: datetime | None,
    next_growth_at_after_no_action: datetime | None,
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
        "calibration_route": (None if calibration_route is None else calibration_route.to_payload()),
        "target_calibration_route": (
            None if target_calibration_route is None else target_calibration_route.to_payload()
        ),
        "development_plan": development_plan.to_payload(),
        "manor": _manor_precondition_payload(manor),
        "mandatory_settlement": {
            "forced_resource": _forced_settlement_payload(forced_settlement_decision),
            "resource_production_deltas": dict(resource_production_deltas),
            "salary": _salary_quote_payload(salary_quote),
        },
        "planner_constraints": {
            "guest_rarity_cap": guest_rarity_cap,
            "max_guest_level_step": max_guest_level_step,
            "minimum_guest_count": minimum_guest_count,
            "minimum_guest_level": minimum_guest_level,
        },
        "planned_at": _datetime_payload(planned_at),
        "profile": _profile_precondition_payload(profile),
        "reference": {
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
) -> int:
    return calculate_guest_arena_power(
        force=force,
        intellect=intellect,
        defense=defense,
        hp_bonus=int(guest.hp_bonus),
        archetype=str(guest.template.archetype),
        base_hp=int(guest.template.base_hp),
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


def _active_calibration_reference(
    *,
    routing: RuntimeRoutingSnapshot,
    config: VirtualPlayerV2Config,
    profile: BotProfile,
    prestige_band: str,
) -> tuple[CalibrationRoute | None, ActiveCalibrationReference | None]:
    matches = tuple(
        route
        for route in routing.calibration_routes
        if route.policy_version == int(profile.policy_version) and route.prestige_band == prestige_band
    )
    if len(matches) > 1:
        raise V2MaintenanceError("multiple calibration routes match the maintenance profile")
    route = matches[0] if matches else None
    if route is None:
        return None, None
    calibration = load_active_calibration_reference(
        policy_version=int(profile.policy_version),
        policy_checksum=str(profile.policy_checksum),
        prestige_band=prestige_band,
        config=config,
        routing=routing,
        required_route=route,
    )
    if calibration is None:
        raise V2MaintenanceError("active calibration route failed maintenance revalidation")
    return route, calibration


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
) -> tuple[int, ReferenceSelection, CalibrationRoute | None]:
    band = next(
        (candidate for candidate in config.bands if candidate.name == prestige_band),
        None,
    )
    if band is None:
        raise V2MaintenanceError(f"maintenance reference uses an unknown prestige band: {prestige_band}")
    snapshot_version, _snapshot = policy_starter_snapshot(
        release.payload,
        prestige_band=band.name,
    )
    calibration_route, calibration = _active_calibration_reference(
        routing=routing,
        config=config,
        profile=profile,
        prestige_band=band.name,
    )
    _snapshot, _starter_strength, selection = select_policy_reference(
        policy_payload=release.payload,
        context=context,
        region=region,
        prestige_band=band.name,
        band_lower_inclusive=band.lower_inclusive,
        band_upper_exclusive=band.upper_exclusive,
        now=now,
        calibrated_candidates=(None if calibration is None else calibration.candidates),
        calibrated_sample_count=(None if calibration is None else calibration.profile_count),
    )
    return snapshot_version, selection, calibration_route


def _resolve_maintenance_policy(
    *,
    profile: BotProfile,
    manor: Manor,
    routing: RuntimeRoutingSnapshot,
    context: RandomContext,
    now: datetime,
    policy_release: BotPolicyRelease | None = None,
) -> tuple[
    BotDevelopmentPlan,
    PrestigeBandGrowthPolicy,
    int,
    ReferenceSelection,
    CalibrationRoute | None,
    VirtualPlayerV2Config,
    BotPolicyRelease,
]:
    config = load_virtual_player_v2_config()
    if config is None:
        raise V2MaintenanceError("bot_development_v2 is not configured")
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
    if int(release.payload.get("max_development_actions") or 0) != 1:
        raise V2MaintenanceError("the first V2 maintenance slice requires max_development_actions=1")
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
    snapshot_version, reference_selection, calibration_route = _maintenance_reference_for_band(
        config=config,
        release=release,
        profile=profile,
        routing=routing,
        context=context,
        region=str(manor.region),
        prestige_band=band.name,
        now=now,
    )
    return (
        development_plan,
        growth_policy,
        snapshot_version,
        reference_selection,
        calibration_route,
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
            quantity__gt=0,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .select_related("template")
        .order_by("template__key", "id")
    )


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
    return tuple(dict.fromkeys(entry.strip() for entry in value if isinstance(entry, str) and entry.strip()))


def _inventory_templates_for_profile(profile: BotProfile) -> tuple[ItemTemplate, ...]:
    keys = _inventory_template_query_keys(profile.inventory_template_keys)
    if not keys:
        return ()
    return tuple(ItemTemplate.objects.filter(key__in=keys).order_by("key"))


def _apply_inventory_acquisition_locked(
    manor: Manor,
    spec: InventoryAcquisitionActionSpec,
    *,
    now: datetime,
) -> InventoryItem:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("_apply_inventory_acquisition_locked must run inside transaction.atomic()")
    template = ItemTemplate.objects.filter(
        pk=spec.item_template_id,
        key=spec.item_key,
        tradeable=True,
    ).first()
    if template is None or spec.item_key == "grain":
        raise InventoryAcquisitionUnavailable("inventory acquisition template is no longer eligible")
    if (
        inventory_daily_cap_limits(
            template,
            config=load_virtual_player_config(),
        )
        != spec.daily_caps
    ):
        raise InventoryAcquisitionUnavailable("inventory acquisition cap inputs changed")
    existing = (
        InventoryItem.objects.select_for_update()
        .filter(
            manor=manor,
            template=template,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .first()
    )
    if existing is not None and int(existing.quantity or 0) > 0:
        raise InventoryAcquisitionUnavailable("inventory acquisition target is already stocked")
    allowed = apply_inventory_daily_caps(
        template,
        quantity=1,
        config=load_virtual_player_config(),
        now=now,
    )
    if allowed != 1:
        raise InventoryAcquisitionUnavailable("inventory acquisition daily cap is exhausted")
    return add_item_to_inventory_locked(manor, spec.item_key, 1)


def _guest_investment_tiers(guests: tuple[Guest, ...]) -> dict[int, str]:
    if not guests:
        return {}
    power_by_id = {
        int(guest.id): _guest_arena_power(
            guest,
            force=int(guest.force),
            intellect=int(guest.intellect),
            defense=int(guest.defense_stat),
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
    guests: tuple[Guest, ...] | None = None,
) -> tuple[
    tuple[DevelopmentIntent, ...],
    dict[str, tuple[Guest, int, int]],
]:
    guest_count = int(strength_before.components.get("guest_count", 0))
    if minimum_guest_count is not None and guest_count < minimum_guest_count:
        return (), {}
    candidate_guests = (
        [
            guest
            for guest in guests
            if guest.status == GuestStatus.IDLE
            and guest.training_complete_at is None
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
    metadata: dict[str, tuple[Guest, int, int]] = {}
    for guest in candidate_guests:
        current_level = int(guest.level)
        if minimum_guest_level is not None:
            target_gap = max(0, minimum_guest_level - current_level)
            if target_gap == 0:
                continue
        else:
            target_gap = 1
        levels = min(
            target_gap,
            max_guest_level_step if max_guest_level_step is not None else 1,
        )
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
            )
        except (
            GuestMaxLevelError,
            GuestNotIdleError,
            GuestTrainingInProgressError,
        ):
            continue
        power_before = power_before_by_id[int(guest.id)]
        power_after = _guest_arena_power(
            guest,
            force=completion.force,
            intellect=completion.intellect,
            defense=completion.defense_stat,
        )
        rank = investment_rank[int(guest.id)]
        investment_weight = investment_weights[min(rank, len(investment_weights) - 1)]
        role_importance = 1.0 + (
            development_plan.roster_focus
            if str(guest.template.archetype) in development_plan.preferred_guest_archetypes
            else 0.0
        )
        normalized_cost = max(
            1,
            sum(int(value) for value in completion.quote.resource_cost.values()),
        )
        utility_score = (
            investment_weight
            * max(1, target_gap)
            * role_importance
            * max(1, power_after - power_before)
            / normalized_cost
        )
        intent = project_training_development_intent(
            guest_id=int(guest.id),
            prestige_band=prestige_band,
            strength_before=strength_before,
            guest_level_after=completion.level,
            guest_arena_power_before=power_before,
            guest_arena_power_after=power_after,
            utility_score=utility_score,
        )
        candidates.append(intent)
        metadata[intent.business_key] = (guest, levels, rng_seed)
    return tuple(candidates), metadata


def _building_upgrade_quotes(
    *,
    manor: Manor,
    development_plan: BotDevelopmentPlan,
    buildings: tuple[Building, ...] | None = None,
    technology_levels: dict[str, int] | None = None,
) -> tuple[BuildingUpgradeQuote, ...]:
    building_snapshot = (
        tuple(
            Building.objects.filter(
                manor_id=manor.id,
                building_type__key__in=development_plan.building_focuses,
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
        if str(building.building_type.key) in development_plan.building_focuses
    }
    quotes: list[BuildingUpgradeQuote] = []
    for building_key in development_plan.building_focuses:
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
) -> tuple[TechnologyUpgradeQuote, ...]:
    quotes: list[TechnologyUpgradeQuote] = []
    for technology_key in development_plan.technology_focuses:
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
) -> datetime | None:
    if trigger_policy.schedule_disposition is MaintenanceScheduleDisposition.PRESERVE_NORMAL_SCHEDULE:
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
    planning_snapshot: _MaintenancePlanningSnapshot | None = None,
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
    if not trigger_policy.is_due(
        next_growth_at=profile.next_growth_at,
        now=planned_at,
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

    context = _maintenance_context(profile)
    (
        development_plan,
        growth_policy,
        reference_snapshot_version,
        reference_selection,
        calibration_route,
        v2_config,
        resolved_policy_release,
    ) = _resolve_maintenance_policy(
        profile=profile,
        manor=manor,
        routing=routing,
        context=context,
        now=planned_at,
        policy_release=(None if planning_snapshot is None else planning_snapshot.policy_release),
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
    budget_entries = parse_strength_budget_entries(
        profile.strength_budget_entries,
        now=planned_at,
    )
    resource_production_deltas = tuple(
        sorted(
            (str(resource), int(delta))
            for resource, delta in preview_resource_production(
                manor,
                now=planned_at,
                production_basis=production_basis,
            ).items()
            if int(delta) != 0
        )
    )
    production_delta_by_resource = dict(resource_production_deltas)
    forced_settlement_decision = plan_forced_settlement(
        parse_forced_settlement_budget(profile.forced_settlement_daily_budget),
        now=planned_at,
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
    salary_quote = quote_all_salaries(
        manor,
        for_date=timezone.localdate(planned_at),
        guests=guests,
        paid_guest_ids=paid_guest_ids,
    )
    healing_intent, healing_guest, medicine_quote = _guest_healing_candidate(
        manor=manor,
        prestige_band=str(profile.current_prestige_band),
        strength_before=strength_before,
        context=context,
        guests=guests,
        medicine_items=medicine_items,
    )
    training_candidates, candidate_metadata = _training_candidates(
        manor=manor,
        prestige_band=str(profile.current_prestige_band),
        strength_before=strength_before,
        context=context,
        development_plan=development_plan,
        minimum_guest_count=minimum_guest_count,
        minimum_guest_level=minimum_guest_level,
        max_guest_level_step=max_guest_level_step,
        guests=guests,
    )
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
    maintenance_config = load_virtual_player_config()
    equipment_candidates, equipment_specs = build_equipment_equip_candidates(
        manor_id=int(manor.id),
        prestige_band=str(profile.current_prestige_band),
        strength_before=strength_before,
        development_plan=development_plan,
        growth_stage=int(profile.growth_stage),
        config=maintenance_config,
        guests=guests,
        gear_items=gear_items,
        warehouse_items=warehouse_items,
    )
    building_candidates, building_specs = build_building_upgrade_candidates(
        manor=manor,
        prestige_band=str(profile.current_prestige_band),
        strength_before=strength_before,
        development_plan=development_plan,
        quotes=_building_upgrade_quotes(
            manor=manor,
            development_plan=development_plan,
            buildings=buildings,
            technology_levels=technology_levels,
        ),
        prestige_band_for=lambda prestige: prestige_band_for_value(
            prestige,
            maintenance_config,
        ),
    )
    technology_candidates, technology_specs = build_technology_upgrade_candidates(
        manor=manor,
        prestige_band=str(profile.current_prestige_band),
        strength_before=strength_before,
        development_plan=development_plan,
        quotes=_technology_upgrade_quotes(
            manor=manor,
            development_plan=development_plan,
            technologies=technologies,
        ),
        prestige_band_for=lambda prestige: prestige_band_for_value(
            prestige,
            maintenance_config,
        ),
    )
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
    troop_candidates, troop_quotes = _troop_recruitment_candidates(
        manor=manor,
        prestige_band=str(profile.current_prestige_band),
        strength_before=strength_before,
        development_plan=development_plan,
        troop_counts=troop_counts,
    )
    intent = healing_intent or select_development_intent(
        (
            *training_candidates,
            *building_candidates,
            *technology_candidates,
            *equipment_candidates,
            *skill_candidates,
            *troop_candidates,
            *inventory_candidates,
        ),
        context=context,
        optimization_bias=development_plan.optimization_bias,
    )
    action_kind = ""
    target_id = None
    training_levels = 0
    rng_seed = None
    target_guest = None
    troop_recruitment_quote = None
    action_spec = None
    typed_action_specs = {
        **building_specs,
        **technology_specs,
        **equipment_specs,
        **skill_specs,
        **inventory_specs,
    }
    if intent is not None:
        action_kind = intent.action_kind
        if action_kind == "guest_healing":
            assert healing_guest is not None
            assert medicine_quote is not None
            target_guest = healing_guest
            target_id = int(target_guest.id)
        elif action_kind == "training":
            target_guest, training_levels, rng_seed = candidate_metadata[intent.business_key]
            target_id = int(target_guest.id)
        elif action_kind == "troop_recruitment":
            troop_recruitment_quote = troop_quotes[intent.business_key]
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
    target_reference_selection = None
    target_calibration_route = None
    transition_distance = 0
    if intent is not None:
        transition_distance = abs(
            PRESTIGE_BANDS.index(intent.source_prestige_band) - PRESTIGE_BANDS.index(intent.target_prestige_band)
        )
    if intent is not None and transition_distance == 1:
        (
            target_snapshot_version,
            target_reference_selection,
            target_calibration_route,
        ) = _maintenance_reference_for_band(
            config=v2_config,
            release=resolved_policy_release,
            profile=profile,
            routing=routing,
            context=context,
            region=str(manor.region),
            prestige_band=intent.target_prestige_band,
            now=planned_at,
        )
        if target_snapshot_version != reference_snapshot_version:
            raise V2MaintenanceError("source and target maintenance references use different snapshot versions")

    next_growth_at_after_no_action = _next_v2_growth_at(
        profile=profile,
        trigger_policy=trigger_policy,
        growth_policy=growth_policy,
        context=context,
        prestige_band=str(profile.current_prestige_band),
        now=planned_at,
    )
    next_growth_at_after = next_growth_at_after_no_action
    if intent is not None and action_kind != "guest_healing":
        planning_decision = evaluate_controlled_action(
            policy=growth_policy,
            intent=intent,
            now=planned_at,
            last_strength_increase_at=profile.last_strength_increase_at,
            budget_entries=budget_entries,
            policy_version=int(profile.policy_version),
            source_sample_count=reference_selection.local_sample_count,
            source_strength_cap=reference_selection.cap,
            target_sample_count=(
                None if target_reference_selection is None else target_reference_selection.local_sample_count
            ),
            target_strength_cap=(None if target_reference_selection is None else target_reference_selection.cap),
        )
        if planning_decision.allowed and intent.target_prestige_band != str(profile.current_prestige_band):
            next_growth_at_after = _next_v2_growth_at(
                profile=profile,
                trigger_policy=trigger_policy,
                growth_policy=growth_policy,
                context=context,
                prestige_band=intent.target_prestige_band,
                now=planned_at,
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
        reference_selection=reference_selection,
        target_reference_selection=target_reference_selection,
        strength_before=strength_before,
        budget_entries=budget_entries,
        resource_production_deltas=resource_production_deltas,
        forced_settlement_decision=forced_settlement_decision,
        salary_quote=salary_quote,
        intent=intent,
        action_kind=action_kind,
        target_id=target_id,
        training_levels=training_levels,
        rng_seed=rng_seed,
        troop_recruitment_quote=troop_recruitment_quote,
        medicine_quote=medicine_quote,
        action_spec=action_spec,
        gear_items=gear_items,
        warehouse_items=warehouse_items,
        troop_counts=troop_counts,
        calibration_route=calibration_route,
        target_calibration_route=target_calibration_route,
        minimum_guest_count=minimum_guest_count,
        minimum_guest_level=minimum_guest_level,
        guest_rarity_cap=guest_rarity_cap,
        max_guest_level_step=max_guest_level_step,
        next_growth_at_after=next_growth_at_after,
        next_growth_at_after_no_action=next_growth_at_after_no_action,
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
        reference_selection=reference_selection,
        target_reference_selection=target_reference_selection,
        strength_before=strength_before,
        strength_budget_entries_before=budget_entries,
        resource_production_deltas=resource_production_deltas,
        forced_settlement_decision=forced_settlement_decision,
        salary_quote=salary_quote,
        last_strength_increase_at_before=profile.last_strength_increase_at,
        next_growth_at_before=profile.next_growth_at,
        next_growth_at_after=next_growth_at_after,
        next_growth_at_after_no_action=next_growth_at_after_no_action,
        action_kind=action_kind,
        target_id=target_id,
        training_levels=training_levels,
        rng_seed=rng_seed,
        troop_recruitment_quote=troop_recruitment_quote,
        medicine_quote=medicine_quote,
        action_spec=action_spec,
        precondition_digest=precondition_digest,
        intent=intent,
        calibration_route=calibration_route,
        target_calibration_route=target_calibration_route,
        minimum_guest_count=minimum_guest_count,
        minimum_guest_level=minimum_guest_level,
        guest_rarity_cap=guest_rarity_cap,
        max_guest_level_step=max_guest_level_step,
    )


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
    _routing_snapshot: RuntimeRoutingSnapshot | None = None,
    _external_reconciliation_prechecked: bool = False,
    _planning_snapshot: _MaintenancePlanningSnapshot | None = None,
) -> MaintenancePlan:
    """Build a deterministic, read-only V2 maintenance plan."""
    if isinstance(profile_id, bool) or not isinstance(profile_id, int) or profile_id < 1:
        raise ValueError("profile_id must be a positive integer")
    planned_at = now or timezone.now()
    if timezone.is_naive(planned_at):
        raise ValueError("now must be timezone-aware")
    normalized_minimum_count = _normalize_optional_non_negative_int(
        minimum_guest_count,
        field="minimum_guest_count",
    )
    normalized_minimum_level = _normalize_optional_positive_int(
        minimum_guest_level,
        field="minimum_guest_level",
    )
    normalized_max_step = _normalize_optional_positive_int(
        max_guest_level_step,
        field="max_guest_level_step",
    )
    if guest_rarity_cap is not None and not isinstance(guest_rarity_cap, str):
        raise ValueError("guest_rarity_cap must be a string or None")
    trigger_policy = maintenance_trigger_policy(
        trigger,
        admin_requires_due=admin_requires_due,
        admin_schedule_disposition=admin_schedule_disposition,
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
            guest_rarity_cap=guest_rarity_cap,
            max_guest_level_step=normalized_max_step,
            planning_snapshot=_planning_snapshot,
        )
    except _V2MaintenanceOutcomeError:
        raise
    except (
        DevelopmentPlanError,
        InvalidStrengthBudgetError,
        MaintenanceActionSpecError,
        MaintenanceCandidateError,
        MaintenanceUpgradeCandidateError,
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


def _raise_if_external_reconciliation_unresolved(profile_id: int) -> None:
    if profile_id in unresolved_external_reconciliation_profile_ids({profile_id}):
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.PAUSED,
            "external_reconciliation_unresolved",
        )


def _assert_locked_profile_matches_plan(
    profile: BotProfile,
    plan: MaintenancePlan,
) -> None:
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
    if not plan.trigger_policy.is_due(
        next_growth_at=profile.next_growth_at,
        now=plan.planned_at,
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


@transaction.atomic
def execute_virtual_player_v2_maintenance_plan(
    plan: MaintenancePlan,
    *,
    _policy_release: BotPolicyRelease | None = None,
    _routing_snapshot: RuntimeRoutingSnapshot | None = None,
    _grain_template: ItemTemplate | None = None,
    _grain_template_resolved: bool = False,
) -> MaintenanceResult:
    """Revalidate and atomically execute one frozen V2 maintenance plan."""
    if not isinstance(plan, MaintenancePlan):
        raise V2MaintenanceError("plan must be a MaintenancePlan")
    try:
        profile = profile_store.lock_maintained_profile(
            plan.profile_id,
            nowait=True,
            expected_v2_routing=_routing_snapshot,
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
    _assert_locked_profile_matches_plan(profile, plan)
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

    manor = Manor.objects.select_for_update().filter(pk=plan.manor_id).first()
    if manor is None or int(profile.manor_id) != int(manor.id):
        raise _V2MaintenanceOutcomeError(
            MaintenanceOutcome.INELIGIBLE,
            "profile_manor_ineligible",
        )
    _raise_if_external_reconciliation_unresolved(int(profile.id))
    locked_guest = None
    if plan.target_id is not None and plan.action_kind != "guest_healing":
        locked_guest = (
            Guest.objects.select_for_update()
            .select_related("template")
            .filter(pk=plan.target_id, manor_id=manor.id)
            .first()
        )
        if locked_guest is None:
            raise _V2MaintenanceOutcomeError(
                MaintenanceOutcome.BUSY,
                "maintenance_target_changed",
            )

    revalidation_guest_query = Guest.objects.filter(manor_id=manor.id).select_related("template")
    if locked_guest is not None:
        revalidation_guest_query = revalidation_guest_query.exclude(pk=locked_guest.pk)
    if connection.features.has_select_for_update_of:
        revalidation_guest_query = revalidation_guest_query.select_for_update(of=("self",))
    else:
        revalidation_guest_query = revalidation_guest_query.select_for_update()
    revalidation_guests = tuple(
        sorted(
            (*revalidation_guest_query, *((locked_guest,) if locked_guest else ())),
            key=lambda guest: int(guest.id),
        )
    )
    revalidation_buildings = tuple(
        Building.objects.filter(manor_id=manor.id).select_related("building_type").order_by("building_type__key", "id")
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
    paid_guest_ids = frozenset(
        bulk_check_salary_paid(
            [int(guest.id) for guest in revalidation_guests],
            timezone.localdate(plan.planned_at),
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
        inventory_templates=_inventory_templates_for_profile(profile),
        production_basis=production_basis,
        policy_release=_policy_release,
    )

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
            planning_snapshot=revalidation_snapshot,
        )
    except _V2MaintenanceOutcomeError:
        raise
    except (
        DevelopmentPlanError,
        InvalidStrengthBudgetError,
        MaintenanceActionSpecError,
        MaintenanceCandidateError,
        MaintenanceUpgradeCandidateError,
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

    _apply_due_resource_production_settlement_locked(
        profile,
        manor,
        now=plan.planned_at,
        expected_production_deltas=plan.resource_production_deltas,
        expected_decision=plan.forced_settlement_decision,
        production_basis=production_basis,
    )
    salary_blocks_development = False
    if plan.salary_quote.unpaid_guest_ids:
        try:
            salary_result = pay_all_salaries_locked(
                manor,
                for_date=plan.salary_quote.for_date,
                _quote=revalidated.salary_quote,
                _locked_guests=revalidation_guests,
            )
        except InsufficientResourceError:
            salary_blocks_development = True
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

    budget_entries_after = prune_strength_budget_entries(
        plan.strength_budget_entries_before,
        now=plan.planned_at,
    )
    last_strength_increase_at_after = plan.last_strength_increase_at_before
    outcome = MaintenanceOutcome.NO_ACTION
    action_kind = ""
    reason = MaintenanceNoActionReason.DOMAIN_CONSTRAINT.value
    if plan.intent is not None and not salary_blocks_development:
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
            except (
                GuestFullHpError,
                GuestItemConfigurationError,
                GuestItemOwnershipError,
                GuestNotIdleError,
                GuestOwnershipError,
                InsufficientStockError,
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
                            recruit_troops_locked(
                                manor,
                                plan.troop_recruitment_quote,
                                now=plan.planned_at,
                            )
                            final_troop_total += int(plan.troop_recruitment_quote.quantity)
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
                                "training": "training",
                                "troop_recruitment": "troop recruitment",
                                SkillLearningActionSpec.action_kind: "skill learning",
                                InventoryAcquisitionActionSpec.action_kind: ("inventory acquisition"),
                                TechnologyUpgradeActionSpec.action_kind: ("technology upgrade"),
                            }[plan.action_kind]
                            raise V2MaintenanceError(
                                f"committed {action_label} strength differs from its frozen intent"
                            )
                except (
                    BuildingConcurrentUpgradeLimitError,
                    BuildingMaxLevelError,
                    BuildingUpgradeQuoteStaleError,
                    BuildingUpgradingError,
                    EquipmentError,
                    EquipmentSlotFullError,
                    GuestMaxLevelError,
                    GuestItemConfigurationError,
                    GuestItemOwnershipError,
                    GuestNotIdleError,
                    GuestNotRequirementError,
                    GuestSkillAlreadyLearnedError,
                    GuestTrainingInProgressError,
                    InsufficientResourceError,
                    InsufficientStockError,
                    InventoryAcquisitionUnavailable,
                    ItemNotFoundError,
                    SkillSlotFullError,
                    TechnologyConcurrentUpgradeLimitError,
                    TechnologyMaxLevelError,
                    TechnologyNotFoundError,
                    TechnologyUpgradeInProgressError,
                    TechnologyUpgradeQuoteStaleError,
                    TroopRecruitmentError,
                ):
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
            },
        )

    transaction.on_commit(_log_committed_maintenance)
    return result


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
) -> MaintenanceResult:
    result = (
        execute_virtual_player_v2_maintenance_plan(
            plan,
            _routing_snapshot=routing_snapshot,
            _grain_template=grain_template,
            _grain_template_resolved=grain_template_resolved,
        )
        if policy_release is None
        else execute_virtual_player_v2_maintenance_plan(
            plan,
            _policy_release=policy_release,
            _routing_snapshot=routing_snapshot,
            _grain_template=grain_template,
            _grain_template_resolved=grain_template_resolved,
        )
    )
    if result.outcome not in {
        MaintenanceOutcome.APPLIED,
        MaintenanceOutcome.NO_ACTION,
    }:
        return result
    if result.next_growth_at_before is None or result.next_growth_at_after is None:
        raise V2MaintenanceError("committed maintenance receipt requires a complete growth schedule")
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
    )
    return result


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
    _execution_request_digest: str | None = None,
    _execution_requested_at: datetime | None = None,
    _routing_snapshot: RuntimeRoutingSnapshot | None = None,
    _external_reconciliation_prechecked: bool = False,
    _planning_snapshot: _MaintenancePlanningSnapshot | None = None,
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
        safety_preflight = check_v2_development_write_preflight()
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
        receipt_context = _MaintenanceExecutionReceiptContext(
            operation_id=safety_attempt.operation_id,
            attempt_ordinal=safety_attempt.attempt_ordinal,
            request_digest=_execution_request_digest,
            requested_at=_execution_requested_at,
            safety_started_at=safety_attempt.started_at,
        )
    try:
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
            _routing_snapshot=_routing_snapshot,
            _external_reconciliation_prechecked=(_external_reconciliation_prechecked),
            _planning_snapshot=_planning_snapshot,
        )
        result = (
            (
                execute_virtual_player_v2_maintenance_plan(
                    plan,
                    _routing_snapshot=_routing_snapshot,
                    _grain_template=grain_template,
                    _grain_template_resolved=grain_template_resolved,
                )
                if policy_release is None
                else execute_virtual_player_v2_maintenance_plan(
                    plan,
                    _policy_release=policy_release,
                    _routing_snapshot=_routing_snapshot,
                    _grain_template=grain_template,
                    _grain_template_resolved=grain_template_resolved,
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
            )
        )
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
    return result


def _growth_execution_request_digest(
    *,
    profile_id: int,
    requested_at: datetime,
    minimum_guest_count: int | None,
    minimum_guest_level: int | None,
    guest_rarity_cap: str | None,
    max_guest_level_step: int | None,
) -> str:
    if timezone.is_naive(requested_at):
        raise ValueError("arena growth requested_at must be timezone-aware")
    payload = {
        "schema_version": 1,
        "profile_id": int(profile_id),
        "requested_at": requested_at.astimezone(UTC).isoformat(),
        "minimum_guest_count": minimum_guest_count,
        "minimum_guest_level": minimum_guest_level,
        "guest_rarity_cap": guest_rarity_cap,
        "max_guest_level_step": max_guest_level_step,
    }
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
        terminal_result = MaintenanceAttemptResult.APPLIED
        growth_outcome = AcceleratedGrowthOutcome.GROWN
    elif receipt.outcome == BotMaintenanceExecution.Outcome.NO_ACTION:
        terminal_result = MaintenanceAttemptResult.NO_ACTION
        growth_outcome = AcceleratedGrowthOutcome.NO_ACTION
    else:
        raise MaintenanceExecutionConflict("maintenance execution receipt has an unsupported outcome")
    if receipt.safety_started_at is not None:
        attempt = MaintenanceAttempt(
            operation_id=receipt.operation_id,
            attempt_ordinal=int(receipt.attempt_ordinal),
            started_at=receipt.safety_started_at,
            trigger=MaintenanceTrigger.ARENA_ACCELERATION,
        )
        _finish_safety_attempt_best_effort(attempt, result=terminal_result)
    return growth_outcome


@transaction.atomic
def _accelerate_virtual_player_growth_v1(
    profile_id: int,
    *,
    now=None,
    minimum_guest_count: int | None = None,
    minimum_guest_level: int | None = None,
    guest_rarity_cap: str | None = None,
    max_guest_level_step: int | None = None,
    _execution_context: _MaintenanceExecutionReceiptContext | None = None,
) -> AcceleratedGrowthOutcome:
    current_time = now or timezone.now()
    profile = (
        BotProfile.objects.select_for_update(skip_locked=True)
        .select_related("manor")
        .filter(
            pk=profile_id,
            engine_version=LEGACY_MAINTENANCE_ENGINE_VERSION,
            state__in=[BotProfile.State.ACTIVE, BotProfile.State.SLOWING],
        )
        .first()
    )
    if profile is None:
        state = (
            BotProfile.objects.filter(
                pk=profile_id,
                engine_version=LEGACY_MAINTENANCE_ENGINE_VERSION,
            )
            .values_list("state", flat=True)
            .first()
        )
        if state in [BotProfile.State.ACTIVE, BotProfile.State.SLOWING]:
            return AcceleratedGrowthOutcome.BUSY
        return AcceleratedGrowthOutcome.INELIGIBLE

    original_next_growth_at = profile.next_growth_at
    sequence_before = int(profile.maintenance_sequence)
    _maintain_active_profile(
        profile,
        now=current_time,
        config=load_virtual_player_config(),
        minimum_guest_count=minimum_guest_count,
        minimum_guest_level=minimum_guest_level,
        guest_rarity_cap=guest_rarity_cap,
        max_guest_level_step=max_guest_level_step,
    )
    profile.refresh_from_db(fields=["next_growth_at"])
    if original_next_growth_at != profile.next_growth_at:
        profile_store.set_next_growth_at(profile, next_growth_at=original_next_growth_at)
    if _execution_context is not None:
        _create_maintenance_execution_receipt(
            _execution_context,
            profile_id=int(profile.id),
            trigger=MaintenanceTrigger.ARENA_ACCELERATION,
            outcome=MaintenanceOutcome.APPLIED,
            schedule_disposition=(MaintenanceScheduleDisposition.PRESERVE_NORMAL_SCHEDULE),
            sequence_before=sequence_before,
            sequence_after=int(profile.maintenance_sequence),
            next_growth_at_before=original_next_growth_at,
            next_growth_at_after=original_next_growth_at,
            action_kind="legacy_growth",
        )
    return AcceleratedGrowthOutcome.GROWN


def accelerate_virtual_player_growth(
    profile_id: int,
    *,
    now=None,
    minimum_guest_count: int | None = None,
    minimum_guest_level: int | None = None,
    guest_rarity_cap: str | None = None,
    max_guest_level_step: int | None = None,
    operation_id: UUID | str | None = None,
    attempt_ordinal: int = 1,
) -> AcceleratedGrowthOutcome:
    """Compatibility facade routed by the persisted maintenance mode."""
    execution_context: _MaintenanceExecutionReceiptContext | None = None
    resolved_now = now
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
        )
    try:
        routing = read_virtual_player_routing()
    except RuntimeRoutingError:
        engine_version = BotProfile.objects.filter(pk=profile_id).values_list("engine_version", flat=True).first()
        if engine_version == V2_MAINTENANCE_ENGINE_VERSION:
            return AcceleratedGrowthOutcome.INELIGIBLE
        return AcceleratedGrowthOutcome.PAUSED
    if routing.maintenance_mode is MaintenanceMode.LEGACY_BEFORE_GATE:
        try:
            outcome = _accelerate_virtual_player_growth_v1(
                profile_id,
                now=resolved_now,
                minimum_guest_count=minimum_guest_count,
                minimum_guest_level=minimum_guest_level,
                guest_rarity_cap=guest_rarity_cap,
                max_guest_level_step=max_guest_level_step,
                _execution_context=execution_context,
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
        return outcome
    if routing.maintenance_mode in {
        MaintenanceMode.V2_CUTOVER,
        MaintenanceMode.V2_PAUSED,
    }:
        return AcceleratedGrowthOutcome.PAUSED
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
            _execution_request_digest=(execution_context.request_digest if execution_context is not None else None),
            _execution_requested_at=(execution_context.requested_at if execution_context is not None else None),
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
    return {
        MaintenanceOutcome.APPLIED: AcceleratedGrowthOutcome.GROWN,
        MaintenanceOutcome.NO_ACTION: AcceleratedGrowthOutcome.NO_ACTION,
        MaintenanceOutcome.BUSY: AcceleratedGrowthOutcome.BUSY,
        MaintenanceOutcome.PAUSED: AcceleratedGrowthOutcome.PAUSED,
        MaintenanceOutcome.INELIGIBLE: AcceleratedGrowthOutcome.INELIGIBLE,
    }[result.outcome]


def _loot_resource_total(loot_resources: Any) -> int:
    if not isinstance(loot_resources, dict):
        return 0
    total = 0
    for amount in loot_resources.values():
        try:
            total += max(0, int(amount or 0))
        except (TypeError, ValueError):
            continue
    return total


def _is_resource_empty(manor: Manor) -> bool:
    return int(manor.silver or 0) <= 0 and int(manor.grain or 0) <= 0


def _maintenance_cycle_started_at(profile: BotProfile):
    return profile.maintenance_started_at or profile.created_at


def _has_repeated_empty_raids(profile: BotProfile, *, now, config: dict[str, Any]) -> bool:
    lifecycle = config.get("lifecycle") or {}
    threshold = int(lifecycle.get("empty_hit_stale_threshold") or 0)
    if threshold <= 0 or not _is_resource_empty(profile.manor):
        return False
    window_hours = int(lifecycle.get("empty_hit_window_hours") or 24)
    since = now - timedelta(hours=max(1, window_hours))
    since = max(since, _maintenance_cycle_started_at(profile))
    recent_loot = (
        RaidRun.objects.filter(
            defender=profile.manor,
            is_attacker_victory=True,
            started_at__gte=since,
        )
        .order_by("-started_at")
        .values_list("loot_resources", flat=True)[:threshold]
    )
    empty_hits = sum(1 for loot_resources in recent_loot if _loot_resource_total(loot_resources) <= 0)
    return empty_hits >= threshold


def _has_long_no_interaction(profile: BotProfile, *, now, config: dict[str, Any]) -> bool:
    lifecycle = config.get("lifecycle") or {}
    days = int(lifecycle.get("stale_no_interaction_days") or 0)
    if days <= 0:
        return False
    cutoff = now - timedelta(days=days)
    maintenance_started_at = _maintenance_cycle_started_at(profile)
    if maintenance_started_at > cutoff:
        return False
    since = max(cutoff, maintenance_started_at)
    return not (
        RaidRun.objects.filter(defender=profile.manor, started_at__gte=since).exists()
        or ScoutRecord.objects.filter(defender=profile.manor, started_at__gte=since).exists()
    )


def _mark_profile_retired(profile: BotProfile, *, now) -> bool:
    return retire_locked_virtual_player_if_unprotected(profile, now=now)


def _maintain_profile(profile: BotProfile, *, now, config: dict[str, Any]) -> None:
    _sync_profile_prestige_band(profile, config=config)

    if _has_repeated_empty_raids(profile, now=now, config=config) or _has_long_no_interaction(
        profile, now=now, config=config
    ):
        _mark_profile_retired(profile, now=now)
        return

    if profile.retire_at <= now:
        _mark_profile_retired(profile, now=now)
        return

    if profile.state == BotProfile.State.ABANDONED:
        next_growth_at = _next_growth_time(now, profile, random.Random(profile.growth_seed), config)
        profile_store.set_next_growth_at(profile, next_growth_at=next_growth_at)
        return

    if profile.abandon_at <= now:
        next_growth_at = _next_growth_time(now, profile, random.Random(profile.growth_seed), config)
        profile_store.transition_profile(
            profile,
            state=BotProfile.State.ABANDONED,
            next_growth_at=next_growth_at,
        )
        return

    maintenance_started_at = _maintenance_cycle_started_at(profile)
    active_duration = max(timedelta(days=1), profile.abandon_at - maintenance_started_at)
    slowing_at = profile.abandon_at - max(timedelta(days=1), active_duration * 0.2)
    if profile.state == BotProfile.State.ACTIVE and slowing_at <= now:
        next_growth_at = _next_growth_time(now, profile, random.Random(profile.growth_seed), config)
        profile_store.transition_profile(
            profile,
            state=BotProfile.State.SLOWING,
            next_growth_at=next_growth_at,
            last_planned_at=now,
        )
        _pay_maintained_bot_salaries(profile, now=now)
        return

    if profile.archetype == BotProfile.Archetype.ABANDONED:
        next_growth_at = _next_growth_time(now, profile, random.Random(profile.growth_seed), config)
        profile_store.set_next_growth_at(profile, next_growth_at=next_growth_at)
        return

    _maintain_active_profile(profile, now=now, config=config)


def _maintain_due_virtual_players_v1(*, now=None, limit: int = 100) -> int:
    now = now or timezone.now()
    config = load_virtual_player_config()
    if not bool(config.get("enabled", True)):
        return 0
    profile_ids = list(
        BotProfile.objects.filter(
            engine_version=LEGACY_MAINTENANCE_ENGINE_VERSION,
            state__in=VIRTUAL_PROFILE_MAINTAINED_STATES,
        )
        .filter(next_growth_at__lte=now)
        .order_by("next_growth_at", "id")[: max(0, int(limit))]
        .values_list("id", flat=True)
    )
    maintained = 0
    for profile_id in profile_ids:
        with transaction.atomic():
            profile = (
                BotProfile.objects.select_for_update(skip_locked=True)
                .select_related("manor")
                .filter(
                    id=profile_id,
                    engine_version=LEGACY_MAINTENANCE_ENGINE_VERSION,
                    state__in=VIRTUAL_PROFILE_MAINTAINED_STATES,
                    next_growth_at__lte=now,
                )
                .first()
            )
            if profile is None:
                continue
            _maintain_profile(profile, now=now, config=config)
            maintained += 1
    return maintained


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
                quantity__gt=0,
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            )
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
        )
    return snapshots


def _maintain_due_virtual_players_v2(
    *,
    current_time: datetime,
    limit: int,
    routing: RuntimeRoutingSnapshot,
) -> int:
    profiles = tuple(
        BotProfile.objects.filter(
            engine_version=V2_MAINTENANCE_ENGINE_VERSION,
            state__in=[BotProfile.State.ACTIVE, BotProfile.State.SLOWING],
            next_growth_at__lte=current_time,
        )
        .select_related("manor")
        .order_by("next_growth_at", "id")[:limit]
    )
    unresolved = unresolved_external_reconciliation_profile_ids(tuple(int(profile.id) for profile in profiles))
    profiles = tuple(profile for profile in profiles if int(profile.id) not in unresolved)
    if not profiles:
        return 0

    safety_preflight = check_v2_development_write_preflight()
    if not safety_preflight.allowed:
        logger.warning(
            "Virtual player V2 maintenance batch blocked by safety preflight: reason=%s due_profiles=%s",
            safety_preflight.reason,
            len(profiles),
            extra={
                "event": "virtual_player_v2_maintenance_batch_blocked",
                "reason": safety_preflight.reason,
                "due_profile_count": len(profiles),
                "checked_at": safety_preflight.checked_at,
                "monitor_heartbeat_at": safety_preflight.monitor_heartbeat_at,
            },
        )
        return 0
    try:
        safety_attempts = start_maintenance_attempts(
            trigger=MaintenanceTrigger.SCHEDULED,
            operation_ids=(None,) * len(profiles),
        )
    except (DatabaseError, SafetyProviderError) as exc:
        log_safety_metric_failure(
            operation="maintenance_attempt_started_batch",
            exc=exc,
        )
        return 0

    terminal_batch: list[tuple[MaintenanceAttempt, MaintenanceResult | MaintenanceAttemptResult]] = []
    try:
        planning_snapshots = _scheduled_planning_snapshots(
            profiles,
            planned_at=current_time,
        )
    except Exception:
        logger.exception(
            "Virtual player V2 maintenance batch planning failed",
            extra={"event": "virtual_player_v2_maintenance_batch_planning_failed"},
        )
        terminal_batch.extend((attempt, MaintenanceAttemptResult.FAILED) for attempt in safety_attempts)
        _finish_safety_attempts_best_effort(terminal_batch)
        return 0

    maintained = 0
    for profile, safety_attempt in zip(profiles, safety_attempts, strict=True):
        profile_id = int(profile.id)
        try:
            result = maintain_virtual_player_v2(
                profile_id,
                trigger=MaintenanceTrigger.SCHEDULED,
                now=current_time,
                _routing_snapshot=routing,
                _external_reconciliation_prechecked=True,
                _planning_snapshot=planning_snapshots[profile_id],
                _safety_attempt=safety_attempt,
                _safety_terminal_batch=terminal_batch,
            )
        except Exception:
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
            maintained += 1
    _finish_safety_attempts_best_effort(terminal_batch)
    return maintained


def maintain_due_virtual_players(*, now=None, limit: int = 100) -> int:
    current_time = now or timezone.now()
    normalized_limit = max(0, int(limit))
    if normalized_limit <= 0:
        return 0
    try:
        routing = read_virtual_player_routing()
    except RuntimeRoutingError:
        return 0
    if routing.maintenance_mode is MaintenanceMode.LEGACY_BEFORE_GATE:
        return _maintain_due_virtual_players_v1(
            now=current_time,
            limit=normalized_limit,
        )
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
