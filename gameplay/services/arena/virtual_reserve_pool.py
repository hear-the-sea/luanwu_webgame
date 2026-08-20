from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Any, cast
from uuid import UUID, uuid4

from django.db import DatabaseError, transaction
from django.db.models import Count, F, Prefetch, Q, Window
from django.db.models.functions import RowNumber
from django.utils import timezone

from gameplay.constants import BUILDING_MAX_LEVELS, BuildingKeys
from gameplay.models import (
    ArenaReserveTrainingAssignment,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
    BotMaintenanceAttempt,
    BotMaintenanceExecution,
    BotMaintenanceRecovery,
    BotProfile,
)
from gameplay.models.arena_virtual import ARENA_RESERVE_MEMBER_LEASE_AGE
from gameplay.services.arena.snapshots import build_entry_guest_snapshot
from gameplay.services.virtual_player_core.contracts import (
    AcceleratedGrowthOutcome,
    ArenaGrowthObjective,
    PopulationMutationStatus,
)
from gameplay.services.virtual_player_core.growth_control import growth_control_digest_for_route
from gameplay.services.virtual_player_core.maintenance import V2MaintenanceError, accelerate_virtual_player_growth
from gameplay.services.virtual_player_core.maintenance_arena_projection import ArenaSelectedPowerProjectionError
from gameplay.services.virtual_player_core.maintenance_cycle import CycleTrigger, record_durable_attempt
from gameplay.services.virtual_player_core.population_runtime import (
    get_virtual_player_capacity,
    reactivate_retired_virtual_player_with_capacity,
    reactivate_virtual_player_profile,
)
from gameplay.services.virtual_player_core.recovery import (
    RecoveryFailureClass,
    classify_failure,
    clear_recovery_failure,
    record_recovery_failure,
    recovery_circuit_is_open,
    recovery_is_blocked,
)
from gameplay.services.virtual_player_core.runtime_assessment import assess_virtual_player_runtime
from gameplay.services.virtual_player_core.safety_provider import SafetyProviderError
from gameplay.services.virtual_player_core.selectors import profile_target_prestige_band, target_band_filter
from gameplay.services.virtual_player_state_policy import (
    VIRTUAL_PROFILE_ARENA_ELIGIBLE_STATES,
    is_virtual_profile_arena_eligible,
)
from guests.models import Guest, GuestRarity, GuestStatus, GuestTemplate
from guests.rarity import GUEST_RARITY_ORDER

from .coop_rules import load_arena_coop_rules
from .rules import load_arena_rules
from .virtual_lineups import (
    MAX_LINEUP_POWER_PERCENT,
    MIN_LINEUP_POWER_PERCENT,
    BotLineupEvaluation,
    InvalidVirtualLineupSnapshot,
    LineupSelectionContext,
    evaluate_lineup_snapshots,
)
from .virtual_protection import is_virtual_profile_arena_match_eligible, with_arena_reconciliation_state
from .virtual_reserve_growth_budget import (
    ARENA_GROWTH_BUDGET_MAX_ATTEMPTS,
    ARENA_GROWTH_BUDGET_WINDOW,
    ARENA_GROWTH_MAX_SLOT_ATTEMPTS,
    ArenaGrowthAttemptBudgetExceeded,
    ArenaGrowthAttemptOutcome,
    ArenaGrowthBudgetError,
    InvalidArenaGrowthBudgetError,
    actual_arena_growth_attempt_count,
    applied_arena_growth_attempt_count,
    cancel_arena_growth_attempt,
    finalize_arena_growth_attempt,
    parse_arena_growth_budget_entries,
    prune_arena_growth_budget_entries,
    reserve_arena_growth_attempt,
    selected_growth_bps,
    serialize_arena_growth_budget_entries,
)
from .virtual_reserve_observability import log_demand_event
from .virtual_reserve_policy import (
    RESERVE_ADMISSION_PROBE_COOLDOWN,
    RESERVE_ADMISSION_STALL_AGE,
    RESERVE_ADMISSION_STALL_FAILURES,
    ReserveAdmissionAssessment,
    assess_reserve_admission,
    reserve_admission_attempt_high_water,
    reserve_materialization_needed,
    reserve_warm_target,
    virtual_roster_target_count,
)
from .virtual_reserve_read_models import ArenaReserveCandidateContext as _ArenaReserveCandidateContext
from .virtual_reserve_read_models import occupied_arena_manor_ids as _occupied_arena_manor_ids
from .virtual_reserve_read_models import supply_band_priority_for_demand as _supply_band_priority_for_demand
from .virtual_reserve_read_models import target_population_cell_for_demand as _target_population_cell_for_demand
from .virtual_reserve_references import reference_snapshots_for_demand
from .virtual_reserve_training_policy import demand_supply_prestige_band_priority, demand_uses_arena_training_policy

logger = logging.getLogger(__name__)


def _record_arena_growth_attempt_audit(
    claim: ArenaVirtualGrowthClaim,
    *,
    growth_outcome: AcceleratedGrowthOutcome,
    reason: str = "",
) -> None:
    """Persist one durable per-slot attempt, including BUSY and NO_ACTION."""

    outcome = {
        AcceleratedGrowthOutcome.BUSY: BotMaintenanceAttempt.Outcome.BUSY,
        AcceleratedGrowthOutcome.NO_ACTION: BotMaintenanceAttempt.Outcome.NO_ACTION,
        AcceleratedGrowthOutcome.PAUSED: BotMaintenanceAttempt.Outcome.NO_ACTION,
        AcceleratedGrowthOutcome.GROWN: BotMaintenanceAttempt.Outcome.APPLIED,
        AcceleratedGrowthOutcome.INELIGIBLE: BotMaintenanceAttempt.Outcome.NO_ACTION,
    }.get(growth_outcome)
    if outcome is None:
        raise ValueError(f"unsupported arena growth outcome: {growth_outcome!r}")
    normalized_reason = str(
        reason or ("arena_ineligible" if growth_outcome is AcceleratedGrowthOutcome.INELIGIBLE else "")
    )
    operation_digest = sha256(f"{claim.operation_id}:{claim.attempt_ordinal}".encode("utf-8")).hexdigest()[:20]
    operation_id = f"arena-attempt-{int(claim.member_id)}-{operation_digest}"[:64]
    receipt = BotMaintenanceExecution.objects.filter(operation_id=claim.operation_id).first()
    profile = BotProfile.objects.get(pk=int(claim.profile_id))
    shadow_cost = dict(receipt.shadow_cost or {}) if receipt is not None else {}
    shadow_cost.update(
        {
            "arena_member_id": int(claim.member_id),
            "round_ordinal": int(claim.round_ordinal),
            "action_ordinal_in_round": int(claim.action_ordinal_in_round),
            "slot_attempt_ordinal": int(claim.slot_attempt_ordinal),
            "execution_attempt_ordinal": int(claim.attempt_ordinal),
            "control_snapshot_digest": str(claim.control_snapshot_digest or ""),
        }
    )
    record_durable_attempt(
        profile,
        operation_id=operation_id,
        trigger=CycleTrigger.ARENA_ACCELERATION,
        archetype=str(profile.archetype),
        action_kind=str(receipt.action_kind or "arena_growth") if receipt is not None else "arena_growth",
        round_ordinal=int(claim.round_ordinal),
        action_ordinal_in_round=int(claim.action_ordinal_in_round),
        attempt_ordinal=max(1, min(5, int(claim.slot_attempt_ordinal))),
        outcome=outcome,
        reason=normalized_reason,
        receipt_operation_id=claim.operation_id,
        shadow_cost=shadow_cost,
        started_at=claim.claimed_at,
    )


ARENA_SLOTS_PER_ROUND = 8
MAX_RESERVE_MEMBER_LEASE_AGE = ARENA_RESERVE_MEMBER_LEASE_AGE
EXHAUSTED_LEASE_GRACE = timedelta(minutes=30)
# Arena replenishment training may advance a guest by up to ten levels in one
# direct, subsidized action.  The formal training quote still clamps the
# actual increment at the guest max level and the candidate event cap.
ARENA_MAX_GUEST_LEVEL_STEP = 10
PRE_FILL_GROWTH_INTERVAL = timedelta(hours=1)
POST_FILL_GROWTH_INTERVAL = timedelta(minutes=15)
GROWTH_CLAIM_LEASE = timedelta(minutes=5)
GROWTH_RETRY_MAX_DELAY = timedelta(hours=1)
ARENA_REARM_JITTER_MAX = timedelta(seconds=45)
ARENA_GROWTH_MAX_MEMBERS_PER_DEMAND = 8
RESERVE_LEASE_RESUME_GRACE = GROWTH_RETRY_MAX_DELAY
BUSY_RETRY_INITIAL_DELAY = timedelta(minutes=5)
DEMAND_RETRY_INITIAL = timedelta(minutes=5)
DEMAND_RETRY_MAX = timedelta(hours=1)
_GUEST_RARITY_RANK = {rarity.value: index for index, rarity in enumerate(GuestRarity)}
_ADMISSION_STALL_EXPLICIT_REASONS = frozenset(
    {
        "arena_strength_envelope_mismatch",
        "arena_strength_segment_mismatch",
        "arena_training_policy_unavailable",
        "arena_attempt_budget_exhausted",
        "domain_constraint",
        "insufficient_resource",
        "salary_runway_protected",
        "target_unreachable_by_cap",
    }
)
_GROWTH_BUSINESS_ERROR_REASON = "growth_business_error"
_GROWTH_BUSINESS_ERROR_LEASE_REASON = "growth_business_error_lease_deadline"
_ARENA_GROWTH_MEMBER_BUSINESS_ERRORS = (
    ArenaGrowthBudgetError,
    ArenaSelectedPowerProjectionError,
    InvalidVirtualLineupSnapshot,
    V2MaintenanceError,
)


@dataclass(frozen=True)
class ReserveReplenishmentResult:
    ready_count: int
    training_count: int
    recovered_abandoned: int
    recovered_retired: int
    creation_needed: int
    warm_target_count: int = 0


def _demand_retry_delay(failure_count: int) -> timedelta:
    exponent = min(max(0, int(failure_count) - 1), 6)
    return min(DEMAND_RETRY_MAX, DEMAND_RETRY_INITIAL * (2**exponent))


def record_demand_failure_locked(
    demand: ArenaVirtualDemand,
    *,
    reason: str,
    now,
) -> None:
    """Persist one bounded backoff per demand failure episode.

    A due-growth scan can terminate several members for the same deterministic
    reason under the same demand lock.  Those rows are one supply failure, not
    an exponential sequence of independent demand failures.  Keep the first
    retry deadline until it expires; a different reason or a later retry
    window still advances the normal backoff.
    """
    normalized_reason = str(reason)[:64]
    if (
        str(demand.last_failure_reason or "") == normalized_reason
        and demand.next_retry_at is not None
        and demand.next_retry_at > now
    ):
        return
    failure_count = min(255, int(demand.consecutive_failure_count) + 1)
    demand.consecutive_failure_count = failure_count
    demand.last_failure_reason = normalized_reason
    demand.last_checked_at = now
    demand.next_retry_at = now + _demand_retry_delay(failure_count)
    demand.save(
        update_fields=[
            "consecutive_failure_count",
            "last_failure_reason",
            "last_checked_at",
            "next_retry_at",
            "updated_at",
        ]
    )


def record_demand_progress_locked(
    demand: ArenaVirtualDemand,
    *,
    now,
) -> None:
    """Clear backoff after any successful reserve progress in the locked demand."""
    demand.consecutive_failure_count = 0
    demand.last_failure_reason = ""
    demand.last_checked_at = now
    demand.last_progress_at = now
    demand.next_retry_at = None
    demand.admission_paused_at = None
    demand.admission_pause_reason = ""
    demand.admission_probe_target_ordinal = None
    demand.save(
        update_fields=[
            "consecutive_failure_count",
            "last_failure_reason",
            "last_checked_at",
            "last_progress_at",
            "next_retry_at",
            "admission_paused_at",
            "admission_pause_reason",
            "admission_probe_target_ordinal",
            "updated_at",
        ]
    )


@dataclass(frozen=True)
class ArenaVirtualGrowthTarget:
    critical_guest_count: int
    preferred_guest_count: int
    minimum_guest_level: int
    recruitment_rarity_cap: str | None
    selected_power_lower_bound: int
    selected_power_upper_bound: int

    @property
    def minimum_guest_count(self) -> int:
        return self.critical_guest_count

    @property
    def guest_rarity_cap(self) -> str | None:
        return self.recruitment_rarity_cap

    def objective(
        self,
        *,
        selected_power_before: int,
        max_guest_level_step: int,
        target_team_power: int,
        lineup_mode: str,
        lineup_event_id: int,
        lineup_max_size: int,
    ) -> ArenaGrowthObjective:
        return ArenaGrowthObjective(
            critical_guest_count=self.critical_guest_count,
            preferred_guest_count=self.preferred_guest_count,
            selected_power_lower_bound=self.selected_power_lower_bound,
            selected_power_upper_bound=self.selected_power_upper_bound,
            selected_power_before=selected_power_before,
            target_team_power=target_team_power,
            lineup_mode=lineup_mode,
            lineup_event_id=lineup_event_id,
            lineup_max_size=lineup_max_size,
            minimum_guest_level=self.minimum_guest_level,
            recruitment_rarity_cap=self.recruitment_rarity_cap,
            max_guest_level_step=max_guest_level_step,
        )


@dataclass(frozen=True, slots=True)
class ArenaVirtualGrowthClaim:
    member_id: int
    demand_id: int
    profile_id: int
    claim_token: UUID
    claimed_at: datetime
    claim_expires_at: datetime
    operation_id: str
    attempt_ordinal: int
    request_digest_schema: int
    control_snapshot_digest: str
    policy_checksum: str
    requested_at: datetime
    demand_version: int
    member_version: int
    power_before: int
    eligible_guest_count_before: int
    target_team_power: int
    lineup_mode: str
    lineup_event_id: int
    lineup_max_size: int
    round_ordinal: int
    action_ordinal_in_round: int
    target: ArenaVirtualGrowthTarget
    slot_attempt_ordinal: int = 1


@dataclass(frozen=True, slots=True)
class _ArenaReachabilityAssessment:
    reachable: bool
    max_selected_power: int | None = None
    reason: str = ""


class ArenaReserveCandidateDisposition(StrEnum):
    """The single candidate classification shared by planning and leasing."""

    READY = "ready"
    TRAINING = "training"
    EXHAUSTED = "exhausted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ArenaReserveCandidateAssessment:
    disposition: ArenaReserveCandidateDisposition
    evaluation: BotLineupEvaluation
    roster_target_count: int
    reachability_reason: str = ""
    max_selected_power: int | None = None


class _CandidateLeaseRejected(RuntimeError):
    pass


def _profile_lineup_snapshots(profile: BotProfile) -> list[dict]:
    prefetched_guests = getattr(profile.manor, "arena_idle_guests", None)
    guests = (
        list(prefetched_guests)
        if prefetched_guests is not None
        else list(profile.manor.guests.filter(status=GuestStatus.IDLE).select_related("template").order_by("id"))
    )
    return [build_entry_guest_snapshot(guest) for guest in guests]


def _eligible_guest_count_for_profile(profile: BotProfile) -> int:
    prefetched_guests = getattr(profile.manor, "arena_idle_guests", None)
    if prefetched_guests is not None:
        return len(prefetched_guests)
    guests = profile.manor.guests
    if hasattr(guests, "filter"):
        return guests.filter(status=GuestStatus.IDLE).count()
    return int(guests.count())


def _current_guest_count_for_profile(profile: BotProfile) -> int:
    prefetched_guests = getattr(profile.manor, "arena_all_guests", None)
    if prefetched_guests is not None:
        return len(prefetched_guests)
    return int(profile.manor.guests.count())


def evaluate_bot_lineup(
    profile: BotProfile,
    *,
    mode: str,
    event_id: int,
    target_guest_count: int,
    target_team_power: int,
    max_lineup_size: int | None = None,
    preferred_guest_count: int | None = None,
) -> BotLineupEvaluation:
    if max_lineup_size is None:
        if str(mode) == "tournament":
            max_lineup_size = int(load_arena_rules()["registration"]["max_guests_per_entry"])
        else:
            max_lineup_size = int(load_arena_coop_rules()["registration"]["guest_limit_per_entry"])
    return evaluate_lineup_snapshots(
        _profile_lineup_snapshots(profile),
        context=LineupSelectionContext(
            mode=str(mode),
            event_id=int(event_id),
            profile_id=int(profile.id),
            max_lineup_size=max_lineup_size,
        ),
        target_guest_count=int(target_guest_count),
        target_team_power=int(target_team_power),
        preferred_guest_count=preferred_guest_count,
    )


def _max_lineup_size_for_demand(demand: ArenaVirtualDemand) -> int:
    if demand.tournament_id is not None:
        return max(1, int(load_arena_rules()["registration"]["max_guests_per_entry"]))
    event = demand.coop_event
    if event is not None:
        return max(1, int(event.guest_limit_per_entry))
    return max(1, int(load_arena_coop_rules()["registration"]["guest_limit_per_entry"]))


def release_virtual_reserve_member_for_manor(manor_id: int) -> int:
    deleted, _details = ArenaVirtualReserveMember.objects.filter(
        profile__manor_id=int(manor_id),
        growth_claim_token__isnull=True,
    ).delete()
    return int(deleted)


def release_virtual_reserve_members_for_demand(demand: ArenaVirtualDemand) -> int:
    deleted, _details = ArenaVirtualReserveMember.objects.filter(
        demand=demand,
        growth_claim_token__isnull=True,
    ).delete()
    return int(deleted)


def release_expired_exhausted_virtual_reserve_members(*, now=None, limit: int = 100) -> int:
    """Release only terminal-demand exhausted leases so they cannot re-enter the same demand."""
    current_time = now or timezone.now()
    cutoff = current_time - EXHAUSTED_LEASE_GRACE
    candidate_ids = list(
        ArenaVirtualReserveMember.objects.filter(
            state=ArenaVirtualReserveMember.State.EXHAUSTED,
            growth_claim_token__isnull=True,
            demand__status__in=[
                ArenaVirtualDemand.Status.SATISFIED,
                ArenaVirtualDemand.Status.BLOCKED,
                ArenaVirtualDemand.Status.CLOSED,
            ],
        )
        .filter(
            Q(last_checked_at__lte=cutoff) | Q(last_checked_at__isnull=True, created_at__lte=cutoff),
        )
        .order_by("last_checked_at", "id")
        .values_list("id", flat=True)[: max(0, int(limit))]
    )
    if not candidate_ids:
        return 0
    deleted, _details = ArenaVirtualReserveMember.objects.filter(
        id__in=candidate_ids,
        state=ArenaVirtualReserveMember.State.EXHAUSTED,
        growth_claim_token__isnull=True,
        demand__status__in=[
            ArenaVirtualDemand.Status.SATISFIED,
            ArenaVirtualDemand.Status.BLOCKED,
            ArenaVirtualDemand.Status.CLOSED,
        ],
    ).delete()
    released = int(deleted)
    if released:
        logger.info(
            "arena virtual exhausted reserve leases released",
            extra={
                "event": "arena_virtual_exhausted_leases_released",
                "released_count": released,
                "grace_period_seconds": int(EXHAUSTED_LEASE_GRACE.total_seconds()),
            },
        )
    return released


def _release_expired_terminal_virtual_reserve_claims(*, now, limit: int) -> int:
    candidate_ids = list(
        ArenaVirtualReserveMember.objects.filter(
            demand__status__in=[
                ArenaVirtualDemand.Status.SATISFIED,
                ArenaVirtualDemand.Status.BLOCKED,
                ArenaVirtualDemand.Status.CLOSED,
            ],
            growth_claim_token__isnull=False,
            growth_claim_expires_at__lte=now,
        )
        .order_by("growth_claim_expires_at", "id")
        .values_list("id", flat=True)[: max(0, int(limit))]
    )
    if not candidate_ids:
        return 0
    deleted, _details = ArenaVirtualReserveMember.objects.filter(
        id__in=candidate_ids,
        growth_claim_token__isnull=False,
        growth_claim_expires_at__lte=now,
        demand__status__in=[
            ArenaVirtualDemand.Status.SATISFIED,
            ArenaVirtualDemand.Status.BLOCKED,
            ArenaVirtualDemand.Status.CLOSED,
        ],
    ).delete()
    released = int(deleted)
    if released:
        logger.info(
            "arena virtual expired terminal reserve claims released",
            extra={
                "event": "arena_virtual_terminal_claims_released",
                "released_count": released,
            },
        )
    return released


def _roster_target_for_member(
    demand: ArenaVirtualDemand,
    *,
    profile_id: int,
    current_guest_count: int,
) -> int:
    mode = "tournament" if demand.tournament_id is not None else "coop"
    event_id = int(demand.tournament_id or demand.coop_event_id or 0)
    max_lineup_size = max(1, _max_lineup_size_for_demand(demand))
    reference_guest_count = min(max_lineup_size, max(1, int(demand.target_guest_count)))
    generated_target = virtual_roster_target_count(
        reference_guest_count=reference_guest_count,
        max_lineup_size=max_lineup_size,
        mode=mode,
        event_id=event_id,
        profile_id=int(profile_id),
    )
    return max(
        reference_guest_count,
        min(max_lineup_size, max(int(current_guest_count), generated_target)),
    )


def _ensure_roster_targets_for_demand(demand: ArenaVirtualDemand) -> int:
    """Initialize the persisted roster target without forcing a full re-evaluation."""

    members = list(
        demand.reserve_members.filter(roster_target_count__isnull=True)
        .annotate(current_guest_count=Count("profile__manor__guests"))
        .order_by("id")
    )
    if not members:
        return 0
    updated_at = timezone.now()
    for member in members:
        member.roster_target_count = _roster_target_for_member(
            demand,
            profile_id=int(member.profile_id),
            current_guest_count=int(member.current_guest_count),
        )
        member.updated_at = updated_at
    ArenaVirtualReserveMember.objects.bulk_update(
        members,
        ["roster_target_count", "updated_at"],
        batch_size=100,
    )
    return len(members)


def _growth_target_for_demand(
    demand: ArenaVirtualDemand,
    *,
    roster_target_count: int | None = None,
    reference_snapshots_override: Sequence[dict[str, Any]] | None = None,
) -> ArenaVirtualGrowthTarget:
    snapshots = (
        list(reference_snapshots_override)
        if reference_snapshots_override is not None
        else reference_snapshots_for_demand(demand)
    )
    guest_levels: list[int] = []
    guest_rarities: list[str] = []
    for snapshot in snapshots:
        try:
            guest_levels.append(max(1, int(snapshot.get("level") or 1)))
        except (TypeError, ValueError):
            guest_levels.append(1)
        rarity = str(snapshot.get("rarity") or "")
        if rarity in _GUEST_RARITY_RANK:
            guest_rarities.append(rarity)
    rarity_cap = max(guest_rarities, key=_GUEST_RARITY_RANK.__getitem__) if guest_rarities else None
    # The event reference count is the hard admission target. The persisted
    # roster target remains a lineup-size preference and must not force an
    # emergency growth cycle to recruit bench guests before improving power.
    minimum_guest_count = max(0, int(demand.target_guest_count))
    preferred_guest_count = max(
        minimum_guest_count,
        int(roster_target_count or minimum_guest_count),
    )
    return ArenaVirtualGrowthTarget(
        critical_guest_count=minimum_guest_count,
        preferred_guest_count=preferred_guest_count,
        minimum_guest_level=max(guest_levels, default=1),
        recruitment_rarity_cap=rarity_cap,
        selected_power_lower_bound=(int(demand.target_team_power) * MIN_LINEUP_POWER_PERCENT + 99) // 100,
        selected_power_upper_bound=(int(demand.target_team_power) * MAX_LINEUP_POWER_PERCENT) // 100,
    )


def _arena_growth_reachability(
    *,
    demand: ArenaVirtualDemand,
    profile: BotProfile,
    selected_power: int,
    growth_target: ArenaVirtualGrowthTarget | None = None,
    growth_execution_attempt_count: int | None = None,
    growth_round_training_guest_ids: Collection[int] | None = None,
    available_growth_candidate_count: int | None = None,
) -> _ArenaReachabilityAssessment:
    supply_band_priority = demand_supply_prestige_band_priority(demand)
    if supply_band_priority is not None and profile_target_prestige_band(profile) not in supply_band_priority:
        return _ArenaReachabilityAssessment(
            False,
            max_selected_power=selected_power,
            reason="arena_strength_segment_mismatch",
        )
    if demand_uses_arena_training_policy(demand) and supply_band_priority is None:
        return _ArenaReachabilityAssessment(
            False,
            max_selected_power=selected_power,
            reason="arena_strength_envelope_mismatch",
        )
    target = growth_target or _growth_target_for_demand(
        demand,
        roster_target_count=_roster_target_for_member(
            demand,
            profile_id=int(profile.id),
            current_guest_count=_current_guest_count_for_profile(profile),
        ),
    )
    current_guest_count = _current_guest_count_for_profile(profile)
    eligible_guest_count = _eligible_guest_count_for_profile(profile)
    if hasattr(demand, "tournament_id") or hasattr(demand, "coop_event_id"):
        maximum_lineup_size = _max_lineup_size_for_demand(demand)
    else:
        maximum_lineup_size = target.critical_guest_count
    if target.critical_guest_count > maximum_lineup_size:
        return _ArenaReachabilityAssessment(
            False,
            max_selected_power=selected_power,
            reason="target_unreachable_by_cap",
        )

    # The 24-hour execution budget is a retry/backpressure window, not a
    # lifetime growth cap. Once it is full, the member remains reachable and
    # the budget layer supplies the next retry timestamp.

    missing_guests = max(0, target.critical_guest_count - current_guest_count)
    if missing_guests:
        remaining_capacity = max(0, int(profile.manor.guest_capacity) - current_guest_count)
        if remaining_capacity < missing_guests:
            current_juxian_level = profile.manor.get_building_level(BuildingKeys.JUXIAN_ZHUANG)
            max_juxian_level = BUILDING_MAX_LEVELS.get(BuildingKeys.JUXIAN_ZHUANG)
            if max_juxian_level is not None and current_juxian_level >= max_juxian_level:
                return _ArenaReachabilityAssessment(
                    False,
                    max_selected_power=selected_power,
                    reason="target_unreachable_by_cap",
                )
            # Arena replenishment can provision the missing capacity through
            # the dedicated instant Juxianzhuang action.
        if int(profile.engine_version) == 2:
            if available_growth_candidate_count is not None:
                candidate_upper_bound = max(0, int(available_growth_candidate_count))
            else:
                allowed_rarities: tuple[str, ...] = tuple(GUEST_RARITY_ORDER)
                if target.recruitment_rarity_cap is not None:
                    try:
                        cap_index = GUEST_RARITY_ORDER.index(target.recruitment_rarity_cap)
                    except ValueError:
                        return _ArenaReachabilityAssessment(
                            False,
                            max_selected_power=selected_power,
                            reason="target_unreachable_by_cap",
                        )
                    allowed_rarities = GUEST_RARITY_ORDER[: cap_index + 1]
                recruitable = GuestTemplate.objects.filter(
                    recruitable=True,
                    is_hermit=False,
                    rarity__in=allowed_rarities,
                )
                repeatable_exists = recruitable.filter(
                    rarity__in=(GuestRarity.BLACK, GuestRarity.GRAY),
                ).exists()
                candidate_upper_bound = None
                if not repeatable_exists:
                    prefetched_guests = getattr(profile.manor, "arena_all_guests", None)
                    owned_template_ids = (
                        [int(guest.template_id) for guest in prefetched_guests]
                        if prefetched_guests is not None
                        else profile.manor.guests.values_list("template_id", flat=True)
                    )
                    candidate_upper_bound = recruitable.exclude(id__in=owned_template_ids).count()
            if candidate_upper_bound is not None and candidate_upper_bound < missing_guests:
                return _ArenaReachabilityAssessment(
                    False,
                    max_selected_power=selected_power,
                    reason="target_unreachable_by_cap",
                )
        # Recruitment is separately bounded; it does not participate in a
        # strength-growth cap.
        return _ArenaReachabilityAssessment(True)

    # A sufficient total roster may still be temporarily unavailable because
    # guests are injured, training, or working. Healing/completion can recover
    # that eligibility.
    assigned_guest_ids: set[int] = set()
    for value in growth_round_training_guest_ids or ():
        if isinstance(value, bool):
            continue
        try:
            normalized_guest_id = int(value)
        except (TypeError, ValueError):
            continue
        if normalized_guest_id > 0:
            assigned_guest_ids.add(normalized_guest_id)
    available_eligible_guest_count = eligible_guest_count
    if assigned_guest_ids:
        prefetched_guests = getattr(profile.manor, "arena_idle_guests", None)
        if prefetched_guests is not None:
            available_eligible_guest_count = sum(int(guest.id) not in assigned_guest_ids for guest in prefetched_guests)
        else:
            guests = cast(Any, getattr(profile.manor, "guests", None))
            if hasattr(guests, "filter"):
                available_eligible_guest_count = (
                    guests.filter(status=GuestStatus.IDLE)
                    .exclude(
                        pk__in=assigned_guest_ids,
                    )
                    .count()
                )
    if available_eligible_guest_count < target.critical_guest_count:
        # Injury/training/this-round assignment is a recheck condition, not a
        # terminal reachability verdict.  Keep the member recoverable while
        # hard lifecycle and execution budgets above remain authoritative.
        return _ArenaReachabilityAssessment(True)
    if selected_power >= target.selected_power_lower_bound:
        return _ArenaReachabilityAssessment(True, max_selected_power=selected_power)
    # No strength ceiling is applied after the roster is legal. The event
    # target remains reachable; actual actions still pass arena lineup and
    # domain validation at execution time.
    return _ArenaReachabilityAssessment(True)


def _demand_fill_at(demand: ArenaVirtualDemand):
    event = demand.tournament if demand.tournament_id is not None else demand.coop_event
    return event.virtual_fill_at if event is not None else None


def _demand_fill_is_due(demand: ArenaVirtualDemand, *, now) -> bool:
    fill_at = _demand_fill_at(demand)
    return fill_at is not None and fill_at <= now


def _growth_interval_for_demand(demand: ArenaVirtualDemand, *, now) -> timedelta:
    return POST_FILL_GROWTH_INTERVAL if _demand_fill_is_due(demand, now=now) else PRE_FILL_GROWTH_INTERVAL


def _arena_rearm_jitter(*, member_id: int, round_ordinal: int, action_ordinal: int) -> timedelta:
    """Return a stable, bounded positive delay for the next arena round."""

    digest = sha256(f"arena-rearm:{int(member_id)}:{int(round_ordinal)}:{int(action_ordinal)}".encode("utf-8")).digest()
    seconds = int.from_bytes(digest[:4], "big") % (int(ARENA_REARM_JITTER_MAX.total_seconds()) + 1)
    return timedelta(seconds=seconds)


def _reserve_growth_engine_version(*, now: datetime | None = None) -> int | None:
    assessment = assess_virtual_player_runtime()
    if not assessment.routing_available:
        # A logically unavailable routing snapshot cannot authorize growth.
        # Preserve the member's remaining lease so a repaired routing row can
        # resume the same training work instead of expiring it during outage.
        pause_virtual_reserve_member_leases(now=now)
        logger.warning(
            "arena virtual reserve growth paused because runtime routing is unavailable",
            extra={
                "event": "arena_virtual_growth_routing_unavailable",
                "failure_reason": assessment.reason,
            },
        )
        return None
    if assessment.growth_allowed:
        # A worker restart can miss the routing resume callback; the next
        # durable growth scan repairs any remaining pause markers.
        resume_virtual_reserve_member_leases(now=now)
    else:
        # Likewise, a scheduled scan is sufficient to freeze a member when a
        # safety pause was written by an older process version.
        pause_virtual_reserve_member_leases(now=now)
    if not assessment.growth_allowed:
        logger.info(
            "arena virtual reserve growth deferred while maintenance routing is paused",
            extra={
                "event": "arena_virtual_growth_routing_paused",
                "maintenance_mode": (
                    assessment.maintenance_mode.value if assessment.maintenance_mode is not None else ""
                ),
                "failure_reason": assessment.reason,
            },
        )
        return None
    return assessment.growth_engine_version


def _reserve_creation_target(demand: ArenaVirtualDemand) -> int:
    """Return the persisted active target, with a safe fallback for old rows."""
    persisted_target = int(demand.warm_target_count or 0)
    if persisted_target > 0 or int(demand.missing_entry_count or 0) == 0:
        return persisted_target
    return reserve_warm_target(
        missing=demand.missing_entry_count,
        reserve_target=demand.reserve_target_count,
    )


def _reserve_attempt_count(demand: ArenaVirtualDemand) -> int:
    """Read the canonical monotonic admission high-water mark."""

    leased_attempts = demand.reserve_members.count()
    return reserve_admission_attempt_high_water(
        leased_attempts=leased_attempts,
        admission_attempt_high_water=int(demand.admission_attempt_high_water or 0),
    )


def _synchronize_reserve_attempt_count_locked(demand: ArenaVirtualDemand) -> int:
    attempt_count = _reserve_attempt_count(demand)
    update_fields: list[str] = []
    if int(demand.admission_attempt_high_water or 0) != attempt_count:
        demand.admission_attempt_high_water = attempt_count
        update_fields.append("admission_attempt_high_water")
    if demand.admission_probe_target_ordinal is not None and int(demand.admission_probe_target_ordinal) < attempt_count:
        demand.admission_probe_target_ordinal = None
        update_fields.append("admission_probe_target_ordinal")
    if update_fields:
        demand.save(update_fields=[*update_fields, "updated_at"])
    return attempt_count


def _next_reserve_attempt_is_allowed_locked(
    demand: ArenaVirtualDemand,
    *,
    attempt_count: int,
    bypass_admission_guard: bool = False,
) -> bool:
    if attempt_count >= int(demand.max_reserve_target_count):
        return False
    pause_reason = str(demand.admission_pause_reason or "")
    if not pause_reason or bypass_admission_guard:
        return True
    return bool(
        pause_reason == "no_effective_progress"
        and demand.admission_probe_target_ordinal is not None
        and int(demand.admission_probe_target_ordinal) == attempt_count + 1
    )


def _consume_reserve_attempt_locked(
    demand: ArenaVirtualDemand,
    *,
    now: datetime,
    bypass_admission_guard: bool = False,
) -> bool:
    attempt_count = _synchronize_reserve_attempt_count_locked(demand)
    if not _next_reserve_attempt_is_allowed_locked(
        demand,
        attempt_count=attempt_count,
        bypass_admission_guard=bypass_admission_guard,
    ):
        return False
    demand.admission_attempt_high_water = attempt_count + 1
    update_fields = ["admission_attempt_high_water"]
    if demand.admission_pause_reason:
        demand.admission_paused_at = now
        update_fields.append("admission_paused_at")
    demand.save(
        update_fields=[
            *update_fields,
            "updated_at",
        ]
    )
    return True


def _admission_stall_reason_is_explicit(reason: str) -> bool:
    normalized = str(reason).strip()
    return bool(
        normalized in _ADMISSION_STALL_EXPLICIT_REASONS
        or normalized.endswith("_cap")
        or normalized.endswith("_cap_reached")
        or normalized.endswith("_cap_retry_limit")
        or normalized.endswith("_round_limit")
    )


def _demand_stalled_without_explained_constraint(
    demand: ArenaVirtualDemand,
    *,
    member_retry_reasons: Counter[str],
    attempt_high_water: int,
    now: datetime,
) -> bool:
    if attempt_high_water <= 0 or int(demand.consecutive_failure_count) < RESERVE_ADMISSION_STALL_FAILURES:
        return False
    activity_candidates = [
        value
        for value in (
            demand.last_progress_at,
            demand.last_input_change_at,
            demand.created_at,
        )
        if value is not None
    ]
    if not activity_candidates or now - max(activity_candidates) < RESERVE_ADMISSION_STALL_AGE:
        return False
    if member_retry_reasons:
        primary_reason = min(
            member_retry_reasons,
            key=lambda reason: (-member_retry_reasons[reason], reason),
        )
    else:
        primary_reason = str(demand.last_failure_reason or "")
    return not _admission_stall_reason_is_explicit(primary_reason)


def _refresh_admission_guard_locked(
    demand: ArenaVirtualDemand,
    *,
    now: datetime,
) -> ReserveAdmissionAssessment:
    member_rows = tuple(demand.reserve_members.values_list("state", "growth_retry_reason"))
    ready_count = sum(1 for state, _reason in member_rows if state == ArenaVirtualReserveMember.State.READY)
    training_count = sum(1 for state, _reason in member_rows if state == ArenaVirtualReserveMember.State.TRAINING)
    retry_reasons = Counter(str(reason) for _state, reason in member_rows if reason)
    attempt_high_water = reserve_admission_attempt_high_water(
        leased_attempts=len(member_rows),
        admission_attempt_high_water=int(demand.admission_attempt_high_water or 0),
    )
    assessment = assess_reserve_admission(
        warm_target=int(demand.warm_target_count or 0),
        ready_count=ready_count,
        training_count=training_count,
        leased_attempts=len(member_rows),
        admission_attempt_high_water=int(demand.admission_attempt_high_water or 0),
        replacement_target=int(demand.max_reserve_target_count or 0),
        stalled_without_explained_constraint=_demand_stalled_without_explained_constraint(
            demand,
            member_retry_reasons=retry_reasons,
            attempt_high_water=attempt_high_water,
            now=now,
        ),
        active_pause_reason=str(demand.admission_pause_reason or ""),
        admission_probe_target_ordinal=demand.admission_probe_target_ordinal,
    )
    if not assessment.admission_guard_active:
        return assessment

    if demand.admission_pause_reason:
        probe_consumed = bool(
            demand.admission_probe_target_ordinal is not None
            and int(demand.admission_probe_target_ordinal) <= attempt_high_water
        )
        probe_due = bool(
            demand.admission_pause_reason == "no_effective_progress"
            and demand.admission_paused_at is not None
            and now >= demand.admission_paused_at + RESERVE_ADMISSION_PROBE_COOLDOWN
            and (demand.admission_probe_target_ordinal is None or probe_consumed)
            and assessment.raw_materialization_needed > 0
            and attempt_high_water < int(demand.max_reserve_target_count or 0)
        )
        if not probe_due:
            return assessment

        demand.admission_paused_at = now
        demand.admission_probe_target_ordinal = attempt_high_water + 1
        demand.save(
            update_fields=[
                "admission_paused_at",
                "admission_probe_target_ordinal",
                "updated_at",
            ]
        )
        log_demand_event(
            "arena_virtual_admission_probe_opened",
            demand,
            message="arena virtual admission guard opened one bounded probe",
            probe_target_ordinal=int(demand.admission_probe_target_ordinal),
            attempt_count=attempt_high_water,
            replacement_target_count=int(demand.max_reserve_target_count),
        )
        return assess_reserve_admission(
            warm_target=int(demand.warm_target_count or 0),
            ready_count=ready_count,
            training_count=training_count,
            leased_attempts=len(member_rows),
            admission_attempt_high_water=int(demand.admission_attempt_high_water or 0),
            replacement_target=int(demand.max_reserve_target_count or 0),
            active_pause_reason=str(demand.admission_pause_reason or ""),
            admission_probe_target_ordinal=demand.admission_probe_target_ordinal,
        )

    pause_reason = assessment.guard_reasons[0]
    demand.admission_paused_at = now
    demand.admission_pause_reason = pause_reason
    demand.admission_probe_target_ordinal = None
    demand.save(
        update_fields=[
            "admission_paused_at",
            "admission_pause_reason",
            "admission_probe_target_ordinal",
            "updated_at",
        ]
    )
    log_demand_event(
        "arena_virtual_admission_paused",
        demand,
        message="arena virtual admission paused by its demand guard",
        level=logging.WARNING,
        failure_reason=pause_reason,
        raw_materialization_needed=assessment.raw_materialization_needed,
        attempt_count=assessment.attempt_high_water,
        replacement_target_count=int(demand.max_reserve_target_count),
        retry_reason_distribution=dict(sorted(retry_reasons.items())),
    )
    return assess_reserve_admission(
        warm_target=int(demand.warm_target_count or 0),
        ready_count=ready_count,
        training_count=training_count,
        leased_attempts=len(member_rows),
        admission_attempt_high_water=int(demand.admission_attempt_high_water or 0),
        replacement_target=int(demand.max_reserve_target_count or 0),
        active_pause_reason=pause_reason,
        admission_probe_target_ordinal=None,
    )


def _block_replacement_budget_exhausted(
    demand: ArenaVirtualDemand,
    *,
    now: datetime,
) -> None:
    attempt_count = _synchronize_reserve_attempt_count_locked(demand)
    exhausted_reasons = tuple(
        demand.reserve_members.filter(
            state=ArenaVirtualReserveMember.State.EXHAUSTED,
        ).values_list("growth_retry_reason", flat=True)
    )
    failure_reason = (
        "target_unreachable_by_cap"
        if exhausted_reasons and all(reason == "target_unreachable_by_cap" for reason in exhausted_reasons)
        else "replacement_budget_exhausted"
    )
    demand.status = ArenaVirtualDemand.Status.BLOCKED
    demand.reserve_target_count = 0
    demand.warm_target_count = 0
    demand.next_retry_at = None
    demand.consecutive_failure_count = min(255, int(demand.consecutive_failure_count) + 1)
    demand.last_failure_reason = failure_reason
    demand.last_checked_at = now
    demand.admission_paused_at = None
    demand.admission_pause_reason = ""
    demand.admission_probe_target_ordinal = None
    demand.save(
        update_fields=[
            "status",
            "reserve_target_count",
            "warm_target_count",
            "next_retry_at",
            "consecutive_failure_count",
            "last_failure_reason",
            "last_checked_at",
            "admission_paused_at",
            "admission_pause_reason",
            "admission_probe_target_ordinal",
            "updated_at",
        ]
    )
    released_member_count = release_virtual_reserve_members_for_demand(demand)
    log_demand_event(
        "arena_virtual_demand_blocked",
        demand,
        message="arena virtual demand exhausted its replacement budget",
        level=logging.WARNING,
        failure_reason=failure_reason,
        attempt_count=attempt_count,
        replacement_target_count=int(demand.max_reserve_target_count),
        released_member_count=int(released_member_count),
    )


@transaction.atomic
def pause_virtual_reserve_member_leases(*, now: datetime | None = None) -> int:
    """Freeze active TRAINING lease clocks while maintenance cannot run.

    The routing transition invokes this in its own transaction.  Keeping the
    marker on the member makes a repeated pause harmless and lets a later
    active scan repair a missed transition without having to infer pause
    duration from routing history.
    """

    paused_at = now or timezone.now()
    return int(
        ArenaVirtualReserveMember.objects.filter(
            state=ArenaVirtualReserveMember.State.TRAINING,
            lease_paused_at__isnull=True,
        ).update(
            lease_paused_at=paused_at,
            updated_at=paused_at,
        )
    )


@transaction.atomic
def resume_virtual_reserve_member_leases(*, now: datetime | None = None) -> int:
    """Translate every paused TRAINING deadline by its exact paused duration.

    Rows are locked individually because portable Django SQL cannot add a
    per-row datetime interval on every supported database backend.  Clearing
    the marker in the same save makes duplicate resume calls idempotent.
    """

    resumed_at = now or timezone.now()
    members = list(
        ArenaVirtualReserveMember.objects.select_for_update()
        .filter(
            state=ArenaVirtualReserveMember.State.TRAINING,
            lease_paused_at__isnull=False,
        )
        .only("id", "created_at", "lease_expires_at", "lease_paused_at")
        .order_by("id")
    )
    for member in members:
        paused_at = member.lease_paused_at
        assert paused_at is not None
        pause_duration = max(timedelta(), resumed_at - paused_at)
        deadline = member.lease_expires_at or (member.created_at + MAX_RESERVE_MEMBER_LEASE_AGE)
        member.lease_expires_at = deadline + pause_duration
        member.lease_paused_at = None
        member.save(update_fields=["lease_expires_at", "lease_paused_at", "updated_at"])
    return len(members)


def _no_action_lease_deadline(
    member: ArenaVirtualReserveMember,
    *,
    now: datetime | None = None,
) -> datetime:
    """Return a wall-clock deadline that does not charge an active pause."""

    deadline = member.lease_expires_at or (member.created_at + MAX_RESERVE_MEMBER_LEASE_AGE)
    paused_at = member.lease_paused_at
    if paused_at is None or now is None:
        return deadline
    return deadline + max(timedelta(), now - paused_at)


def _no_action_lease_expired(member: ArenaVirtualReserveMember, *, now) -> bool:
    return now >= _no_action_lease_deadline(member, now=now)


def _clear_growth_retry(member: ArenaVirtualReserveMember) -> None:
    member.growth_retry_streak = 0
    member.growth_retry_reason = ""


def _schedule_growth_retry(
    member: ArenaVirtualReserveMember,
    *,
    handled_at: datetime,
    reason: str,
    initial_delay: timedelta,
) -> datetime:
    normalized_reason = str(reason)[:64]
    previous_reason = str(member.growth_retry_reason or "")
    streak = int(member.growth_retry_streak) + 1 if previous_reason == normalized_reason else 1
    streak = min(255, streak)
    member.growth_retry_streak = streak
    member.growth_retry_reason = normalized_reason
    exponent = min(streak - 1, 6)
    delay = min(GROWTH_RETRY_MAX_DELAY, initial_delay * (2**exponent))
    return handled_at + delay


def _growth_retry_reason(
    *,
    operation_id: str,
    growth_outcome: AcceleratedGrowthOutcome,
) -> str:
    if growth_outcome is not AcceleratedGrowthOutcome.NO_ACTION:
        return {
            AcceleratedGrowthOutcome.BUSY: "profile_busy",
            AcceleratedGrowthOutcome.PAUSED: "growth_paused",
        }.get(growth_outcome, "")
    reason = BotMaintenanceExecution.objects.filter(operation_id=operation_id).values_list("reason", flat=True).first()
    return str(reason or "growth_no_action")[:64]


def reevaluate_existing_members(
    demand: ArenaVirtualDemand,
    *,
    now,
    preserve_training: bool = False,
    target_cell: tuple[str, str] | None = None,
) -> None:
    if demand.tournament_id is None and demand.coop_event_id is None:
        return

    policy_scoped_supply = demand_uses_arena_training_policy(demand)
    resolved_target_cell = (
        target_cell
        if target_cell is not None
        else (_target_population_cell_for_demand(demand) if policy_scoped_supply else None)
    )
    supply_band_priority = _supply_band_priority_for_demand(demand, target_cell=resolved_target_cell)
    members = list(
        demand.reserve_members.select_for_update().select_related("profile", "profile__manor").order_by("id")
    )
    for member in members:
        if member.growth_claim_token is not None:
            continue
        if preserve_training and member.state == ArenaVirtualReserveMember.State.TRAINING:
            continue
        if policy_scoped_supply and (
            resolved_target_cell is None
            or supply_band_priority is None
            or not _profile_matches_population_supply(
                member.profile,
                target_region=resolved_target_cell[0],
                allowed_bands=supply_band_priority,
            )
        ):
            member.delete()
            continue
        if not is_virtual_profile_arena_match_eligible(
            member.profile, now=now
        ) or not is_virtual_profile_arena_eligible(member.profile):
            member.delete()
            continue
        previous_state = member.state
        previous_version = int(member.evaluated_version)
        previous_power = int(member.current_lineup_power)
        demand_version_changed = previous_version != int(demand.version)
        member.evaluated_version = demand.version
        member.last_checked_at = now
        if member.roster_target_count is None:
            member.roster_target_count = _roster_target_for_member(
                demand,
                profile_id=int(member.profile_id),
                current_guest_count=member.profile.manor.guests.count(),
            )
        else:
            member.roster_target_count = max(
                int(member.roster_target_count),
                int(demand.target_guest_count),
            )
        permanent_terminal_reason = member.growth_retry_reason == "invalid_growth_budget"
        version_scoped_terminal_reason = member.growth_retry_reason in {
            "growth_busy_lease_deadline",
        }
        age_terminal = _no_action_lease_expired(member, now=now) and not (
            member.growth_retry_reason == "target_unreachable_by_cap" and demand_version_changed
        )
        if member.state == ArenaVirtualReserveMember.State.EXHAUSTED and (
            permanent_terminal_reason or age_terminal or (version_scoped_terminal_reason and not demand_version_changed)
        ):
            member.lease_paused_at = None
            if not member.growth_retry_reason:
                member.growth_retry_reason = "no_action_lease_deadline"
            member.save(
                update_fields=[
                    "evaluated_version",
                    "roster_target_count",
                    "last_checked_at",
                    "growth_retry_reason",
                    "lease_paused_at",
                    "updated_at",
                ]
            )
            continue

        evaluation = _evaluate_profile_for_demand(
            demand,
            member.profile,
            preferred_guest_count=int(member.roster_target_count),
        )
        member.current_lineup_power = evaluation.selected_power
        if evaluation.is_ready:
            member.state = ArenaVirtualReserveMember.State.READY
            member.next_acceleration_at = None
            member.lease_paused_at = None
        elif evaluation.snapshots:
            member.state = ArenaVirtualReserveMember.State.TRAINING
            member.next_acceleration_at = now if demand_version_changed else (member.next_acceleration_at or now)
            if (
                _demand_fill_is_due(demand, now=now)
                and not member.growth_retry_reason
                and member.next_acceleration_at > now + POST_FILL_GROWTH_INTERVAL
            ):
                member.next_acceleration_at = now
        else:
            member.delete()
            continue
        if (
            evaluation.is_ready
            or previous_state != member.state
            or demand_version_changed
            or previous_power != int(evaluation.selected_power)
        ) and member.state != ArenaVirtualReserveMember.State.EXHAUSTED:
            _clear_growth_retry(member)
        member.save(
            update_fields=[
                "state",
                "evaluated_version",
                "current_lineup_power",
                "roster_target_count",
                "next_acceleration_at",
                "last_checked_at",
                "growth_retry_streak",
                "growth_retry_reason",
                "lease_paused_at",
                "updated_at",
            ]
        )
        ready_lower_bound = (int(demand.target_team_power) * MIN_LINEUP_POWER_PERCENT + 99) // 100
        gap_before = max(0, ready_lower_bound - previous_power)
        gap_after = max(0, ready_lower_bound - int(evaluation.selected_power))
        if (previous_state != ArenaVirtualReserveMember.State.READY and evaluation.is_ready) or gap_after < gap_before:
            record_demand_progress_locked(demand, now=now)


def _profile_matches_population_supply(
    profile: BotProfile,
    *,
    target_region: str,
    allowed_bands: Collection[str],
) -> bool:
    return str(profile.manor.region) == target_region and profile_target_prestige_band(profile) in allowed_bands


def _candidate_queryset(
    states: Collection[str],
    *,
    engine_version: int = 2,
    target_cell: tuple[str, str] | None = None,
    occupied_manor_ids: Collection[int] | None = None,
):
    resolved_occupied_manor_ids = (
        {int(manor_id) for manor_id in occupied_manor_ids}
        if occupied_manor_ids is not None
        else _occupied_arena_manor_ids()
    )
    queryset = (
        BotProfile.objects.filter(
            state__in=list(states),
            engine_version=int(engine_version),
            arena_virtual_reserve__isnull=True,
        )
        .exclude(manor_id__in=resolved_occupied_manor_ids)
        .select_related("manor")
        .prefetch_related(*_arena_candidate_guest_prefetches())
        .order_by("id")
    )
    if int(engine_version) == 2:
        queryset = queryset.filter(policy_version=2)
    if target_cell is not None:
        target_region, target_band = target_cell
        queryset = queryset.filter(
            target_band_filter(target_band),
            manor__region=target_region,
        )
    return with_arena_reconciliation_state(queryset)


def _arena_candidate_guest_prefetches() -> tuple[Prefetch, Prefetch]:
    return (
        Prefetch(
            "manor__guests",
            queryset=Guest.objects.filter(status=GuestStatus.IDLE).select_related("template").order_by("id"),
            to_attr="arena_idle_guests",
        ),
        Prefetch(
            "manor__guests",
            queryset=Guest.objects.only("id", "manor_id", "template_id").order_by("id"),
            to_attr="arena_all_guests",
        ),
    )


def _evaluate_profile_for_demand(
    demand: ArenaVirtualDemand,
    profile: BotProfile,
    *,
    preferred_guest_count: int | None = None,
):
    mode = "tournament" if demand.tournament_id is not None else "coop"
    event_id = demand.tournament_id or demand.coop_event_id
    if preferred_guest_count is None:
        preferred_guest_count = _roster_target_for_member(
            demand,
            profile_id=int(profile.id),
            current_guest_count=_current_guest_count_for_profile(profile),
        )
    return evaluate_bot_lineup(
        profile,
        mode=mode,
        event_id=int(event_id or 0),
        target_guest_count=demand.target_guest_count,
        target_team_power=demand.target_team_power,
        max_lineup_size=_max_lineup_size_for_demand(demand),
        preferred_guest_count=preferred_guest_count,
    )


def assess_arena_reserve_candidate(
    demand: ArenaVirtualDemand,
    profile: BotProfile,
    *,
    candidate_context: _ArenaReserveCandidateContext | None = None,
    reference_snapshots_override: Sequence[dict[str, Any]] | None = None,
) -> ArenaReserveCandidateAssessment:
    """Classify a profile exactly as the reserve writer will before leasing it.

    Population handoff is necessarily an optimistic read, so the lease path
    re-runs this function while holding its profile row lock. Keeping the
    lineup and reachability rules here prevents the planner from counting a
    profile that the writer would immediately reject or exhaust.
    """

    evaluation = _evaluate_profile_for_demand(demand, profile)
    current_guest_count = _current_guest_count_for_profile(profile)
    roster_target_count = _roster_target_for_member(
        demand,
        profile_id=int(profile.id),
        current_guest_count=current_guest_count,
    )
    if evaluation.is_ready:
        return ArenaReserveCandidateAssessment(
            disposition=ArenaReserveCandidateDisposition.READY,
            evaluation=evaluation,
            roster_target_count=roster_target_count,
        )
    if not evaluation.snapshots:
        return ArenaReserveCandidateAssessment(
            disposition=ArenaReserveCandidateDisposition.REJECTED,
            evaluation=evaluation,
            roster_target_count=roster_target_count,
            reachability_reason="no_valid_lineup",
        )

    if candidate_context is not None and reference_snapshots_override is not None:
        raise ValueError("candidate_context and reference_snapshots_override are mutually exclusive")
    if candidate_context is not None:
        reference_snapshots_override = candidate_context.reference_snapshots_for(demand)
    growth_target = _growth_target_for_demand(
        demand,
        roster_target_count=roster_target_count,
        reference_snapshots_override=reference_snapshots_override,
    )
    reachability = _arena_growth_reachability(
        demand=demand,
        profile=profile,
        selected_power=int(evaluation.selected_power),
        growth_target=growth_target,
    )
    if not reachability.reachable:
        return ArenaReserveCandidateAssessment(
            disposition=ArenaReserveCandidateDisposition.EXHAUSTED,
            evaluation=evaluation,
            roster_target_count=roster_target_count,
            reachability_reason=reachability.reason,
            max_selected_power=reachability.max_selected_power,
        )
    return ArenaReserveCandidateAssessment(
        disposition=ArenaReserveCandidateDisposition.TRAINING,
        evaluation=evaluation,
        roster_target_count=roster_target_count,
        max_selected_power=reachability.max_selected_power,
    )


def _trim_surplus_members(
    demand: ArenaVirtualDemand,
    *,
    preserve_training: bool = False,
) -> None:
    if preserve_training:
        # During an operational runtime pause, freeze the active reserve. A
        # partial trim could delete READY members while protected TRAINING
        # members remain, making the demand less fillable after recovery.
        return
    members = list(demand.reserve_members.exclude(state=ArenaVirtualReserveMember.State.EXHAUSTED).order_by("id"))
    surplus = len(members) - _reserve_creation_target(demand)
    if surplus <= 0:
        return
    removable = sorted(
        [
            member
            for member in members
            if member.growth_claim_token is None
            and not (preserve_training and member.state == ArenaVirtualReserveMember.State.TRAINING)
        ],
        key=lambda member: (
            member.state == ArenaVirtualReserveMember.State.READY,
            member.current_lineup_power,
            -member.id,
        ),
    )
    ArenaVirtualReserveMember.objects.filter(id__in=[member.id for member in removable[:surplus]]).delete()


def _lease_candidate(
    *,
    demand: ArenaVirtualDemand,
    profile_id: int,
    allowed_states: Collection[str],
    member_state: str,
    engine_version: int = 2,
    target_cell: tuple[str, str] | None = None,
    now,
    supply_band_priority: Sequence[str] | None = None,
    recover: bool = False,
) -> ArenaVirtualReserveMember | None:
    try:
        with transaction.atomic():
            resolved_target_cell = target_cell or _target_population_cell_for_demand(demand)
            if resolved_target_cell is None:
                raise _CandidateLeaseRejected
            resolved_supply_band_priority = (
                tuple(supply_band_priority)
                if supply_band_priority is not None
                else _supply_band_priority_for_demand(
                    demand,
                    target_cell=resolved_target_cell,
                )
            )
            if not resolved_supply_band_priority:
                raise _CandidateLeaseRejected
            previous_state = next(iter(allowed_states), "")
            profile: BotProfile | None
            if recover and BotProfile.State.RETIRED in allowed_states:
                mutation = reactivate_retired_virtual_player_with_capacity(
                    profile_id,
                    now=now,
                )
                if mutation.status is not PopulationMutationStatus.REACTIVATED:
                    return None
                profile = mutation.profile
            elif recover and BotProfile.State.ABANDONED in allowed_states:
                profile = reactivate_virtual_player_profile(profile_id, now=now)
            else:
                profile_queryset = (
                    BotProfile.objects.select_for_update(skip_locked=True)
                    .filter(
                        pk=profile_id,
                        state__in=list(allowed_states),
                        engine_version=int(engine_version),
                        arena_virtual_reserve__isnull=True,
                    )
                    .exclude(manor_id__in=_occupied_arena_manor_ids())
                    .select_related("manor")
                )
                if int(engine_version) == 2:
                    profile_queryset = profile_queryset.filter(policy_version=2)
                profile_queryset = profile_queryset.prefetch_related(*_arena_candidate_guest_prefetches())
                profile = with_arena_reconciliation_state(profile_queryset).first()
            if profile is None:
                return None
            if int(profile.engine_version) != int(engine_version):
                raise _CandidateLeaseRejected
            if not _profile_matches_population_supply(
                profile,
                target_region=resolved_target_cell[0],
                allowed_bands=resolved_supply_band_priority,
            ):
                raise _CandidateLeaseRejected
            if not is_virtual_profile_arena_match_eligible(profile, now=now):
                raise _CandidateLeaseRejected
            if (
                ArenaVirtualReserveMember.objects.filter(profile_id=profile.id).exists()
                or profile.manor_id in _occupied_arena_manor_ids()
            ):
                raise _CandidateLeaseRejected
            assessment = assess_arena_reserve_candidate(demand, profile)
            if member_state == ArenaVirtualReserveMember.State.READY and (
                assessment.disposition is not ArenaReserveCandidateDisposition.READY
            ):
                raise _CandidateLeaseRejected
            if member_state == ArenaVirtualReserveMember.State.TRAINING and assessment.disposition not in {
                ArenaReserveCandidateDisposition.TRAINING,
                # A candidate can become unreachable between the planning read
                # and this locked recheck. Preserve the attempt accounting by
                # recording that terminal result instead of silently dropping
                # the admission.
                ArenaReserveCandidateDisposition.EXHAUSTED,
            }:
                raise _CandidateLeaseRejected
            evaluation = assessment.evaluation
            roster_target_count = assessment.roster_target_count
            reachability = (
                None
                if assessment.disposition is not ArenaReserveCandidateDisposition.EXHAUSTED
                else _ArenaReachabilityAssessment(
                    reachable=False,
                    max_selected_power=assessment.max_selected_power,
                    reason=assessment.reachability_reason,
                )
            )
            if not _consume_reserve_attempt_locked(
                demand,
                now=now,
                # An already active profile is a handoff, not a new
                # materialization.  It must still respect the hard
                # replacement budget, but a stalled creation guard must not
                # strand usable supply in the same population cell.
                bypass_admission_guard=not recover,
            ):
                raise _CandidateLeaseRejected
            if reachability is not None and not reachability.reachable:
                ArenaVirtualReserveMember.objects.create(
                    demand=demand,
                    profile=profile,
                    state=ArenaVirtualReserveMember.State.EXHAUSTED,
                    evaluated_version=demand.version,
                    current_lineup_power=evaluation.selected_power,
                    roster_target_count=roster_target_count,
                    next_acceleration_at=None,
                    last_checked_at=now,
                    growth_retry_reason=reachability.reason,
                )
                log_demand_event(
                    "arena_virtual_profile_exhausted",
                    demand,
                    message="arena virtual profile rejected by reachability preflight",
                    level=logging.WARNING,
                    failure_reason=reachability.reason,
                    profile_id=int(profile.id),
                    power_before=int(evaluation.selected_power),
                    max_selected_power=reachability.max_selected_power,
                    growth_rounds=0,
                    member_state=ArenaVirtualReserveMember.State.EXHAUSTED,
                )
                return None
            member = ArenaVirtualReserveMember.objects.create(
                demand=demand,
                profile=profile,
                state=member_state,
                evaluated_version=demand.version,
                current_lineup_power=evaluation.selected_power,
                roster_target_count=roster_target_count,
                next_acceleration_at=(now if member_state == ArenaVirtualReserveMember.State.TRAINING else None),
                last_checked_at=now,
            )
            if recover:
                log_demand_event(
                    "arena_virtual_profile_recovered",
                    demand,
                    message="arena virtual profile recovered",
                    profile_id=int(profile.id),
                    previous_state=str(previous_state),
                    current_state=str(profile.state),
                    member_state=str(member.state),
                )
            return member
    except _CandidateLeaseRejected:
        return None


@transaction.atomic
def replenish_virtual_reserve(demand_id: int, *, now=None) -> ReserveReplenishmentResult:
    current_time = now or timezone.now()
    runtime_assessment = assess_virtual_player_runtime()
    growth_admission_allowed = runtime_assessment.training_admission_allowed
    population_mutation_allowed = runtime_assessment.population_mutation_allowed
    candidate_engine_version = runtime_assessment.reserve_engine_version
    demand = (
        ArenaVirtualDemand.objects.select_for_update()
        .filter(pk=demand_id, status=ArenaVirtualDemand.Status.ACTIVE)
        .first()
    )
    if demand is None:
        return ReserveReplenishmentResult(0, 0, 0, 0, 0)
    if not runtime_assessment.routing_available:
        # A missing or invalid routing snapshot is a safety pause too.  Do
        # not charge that unavailable interval to existing TRAINING members.
        pause_virtual_reserve_member_leases(now=current_time)
        ready_count = demand.reserve_members.filter(state=ArenaVirtualReserveMember.State.READY).count()
        training_count = demand.reserve_members.filter(state=ArenaVirtualReserveMember.State.TRAINING).count()
        log_demand_event(
            "arena_virtual_reserve_deferred",
            demand,
            message="arena virtual reserve deferred while runtime routing is unavailable",
            failure_reason="routing_unavailable",
            ready_count=int(ready_count),
            training_count=int(training_count),
        )
        return ReserveReplenishmentResult(
            ready_count=ready_count,
            training_count=training_count,
            recovered_abandoned=0,
            recovered_retired=0,
            creation_needed=0,
            warm_target_count=_reserve_creation_target(demand),
        )
    if runtime_assessment.growth_allowed:
        # Repair a missed resume edge (for example after a worker restart).
        resume_virtual_reserve_member_leases(now=current_time)
    else:
        # Freeze the effective lease clock before preserving TRAINING members.
        pause_virtual_reserve_member_leases(now=current_time)
    if candidate_engine_version is None:
        return ReserveReplenishmentResult(0, 0, 0, 0, 0)
    _synchronize_reserve_attempt_count_locked(demand)
    _ensure_roster_targets_for_demand(demand)
    admission_assessment = _refresh_admission_guard_locked(demand, now=current_time)
    if demand.next_retry_at is not None and demand.next_retry_at > current_time + timedelta(seconds=1):
        return ReserveReplenishmentResult(0, 0, 0, 0, 0)

    candidate_context = _ArenaReserveCandidateContext()
    target_cell = candidate_context.target_population_cell_for(demand)
    reevaluate_existing_members(
        demand,
        now=current_time,
        preserve_training=not growth_admission_allowed,
        target_cell=target_cell,
    )
    _trim_surplus_members(
        demand,
        preserve_training=not growth_admission_allowed,
    )
    ready_count = demand.reserve_members.filter(state=ArenaVirtualReserveMember.State.READY).count()
    training_count = demand.reserve_members.filter(state=ArenaVirtualReserveMember.State.TRAINING).count()
    attempt_count = _reserve_attempt_count(demand)
    creation_target = _reserve_creation_target(demand)
    slots_needed = reserve_materialization_needed(
        warm_target=creation_target,
        ready_count=ready_count,
        training_count=training_count,
        attempt_count=attempt_count,
        replacement_target=int(demand.max_reserve_target_count),
    )
    recovered_abandoned = 0
    recovered_retired = 0
    made_ready_progress = False
    training_candidates: list[tuple[int, int, int]] = []
    supply_band_priority = _supply_band_priority_for_demand(demand, target_cell=target_cell)
    occupied_manor_ids = (
        candidate_context.occupied_manor_ids() if target_cell is not None and supply_band_priority is not None else None
    )

    if target_cell is not None and supply_band_priority is not None:
        target_region = target_cell[0]
        for priority_index, target_band in enumerate(supply_band_priority):
            if slots_needed <= 0:
                break
            active_candidates = _candidate_queryset(
                VIRTUAL_PROFILE_ARENA_ELIGIBLE_STATES,
                engine_version=candidate_engine_version,
                target_cell=(target_region, target_band),
                occupied_manor_ids=occupied_manor_ids,
            )
            for profile in active_candidates.iterator(chunk_size=100):
                if slots_needed <= 0:
                    break
                if not is_virtual_profile_arena_match_eligible(profile, now=current_time):
                    continue
                assessment = assess_arena_reserve_candidate(
                    demand,
                    profile,
                    candidate_context=candidate_context,
                )
                if assessment.disposition is ArenaReserveCandidateDisposition.READY:
                    member = _lease_candidate(
                        demand=demand,
                        profile_id=profile.id,
                        allowed_states=VIRTUAL_PROFILE_ARENA_ELIGIBLE_STATES,
                        member_state=ArenaVirtualReserveMember.State.READY,
                        engine_version=candidate_engine_version,
                        target_cell=target_cell,
                        supply_band_priority=supply_band_priority,
                        now=current_time,
                    )
                    if member is not None:
                        made_ready_progress = True
                        slots_needed -= 1
                elif growth_admission_allowed and assessment.disposition is ArenaReserveCandidateDisposition.TRAINING:
                    training_candidates.append((priority_index, assessment.evaluation.selected_power, profile.id))

    # Re-read after direct handoff.  A stalled demand may still hand off
    # existing ACTIVE profiles, but reactivation is limited to the persisted
    # admission allowance (zero normally, one during a cooldown probe).
    admission_assessment = _refresh_admission_guard_locked(demand, now=current_time)
    materialization_attempt_limit = int(admission_assessment.admitted_materialization_needed)
    materialization_attempts_used = 0

    if (
        slots_needed > 0
        and materialization_attempt_limit > 0
        and population_mutation_allowed
        and target_cell is not None
        and supply_band_priority is not None
    ):
        target_region = target_cell[0]
        for target_band in supply_band_priority:
            abandoned_candidates = _candidate_queryset(
                [BotProfile.State.ABANDONED],
                engine_version=candidate_engine_version,
                target_cell=(target_region, target_band),
                occupied_manor_ids=occupied_manor_ids,
            )
            for profile in abandoned_candidates.iterator(chunk_size=100):
                if slots_needed <= 0 or materialization_attempts_used >= materialization_attempt_limit:
                    break
                if not is_virtual_profile_arena_match_eligible(profile, now=current_time):
                    continue
                if (
                    assess_arena_reserve_candidate(
                        demand,
                        profile,
                        candidate_context=candidate_context,
                    ).disposition
                    is not ArenaReserveCandidateDisposition.READY
                ):
                    continue
                attempt_before = _reserve_attempt_count(demand)
                member = _lease_candidate(
                    demand=demand,
                    profile_id=profile.id,
                    allowed_states=[BotProfile.State.ABANDONED],
                    member_state=ArenaVirtualReserveMember.State.READY,
                    engine_version=candidate_engine_version,
                    target_cell=target_cell,
                    supply_band_priority=supply_band_priority,
                    now=current_time,
                    recover=True,
                )
                materialization_attempts_used += max(0, _reserve_attempt_count(demand) - attempt_before)
                if member is not None:
                    made_ready_progress = True
                    recovered_abandoned += 1
                    slots_needed -= 1
            if slots_needed <= 0 or materialization_attempts_used >= materialization_attempt_limit:
                break

    if (
        slots_needed > 0
        and materialization_attempt_limit > materialization_attempts_used
        and population_mutation_allowed
        and target_cell is not None
        and supply_band_priority is not None
    ):
        hard_cap, maintained_count = get_virtual_player_capacity(now=current_time)
        target_region = target_cell[0]
        for target_band in supply_band_priority:
            retired_candidates = _candidate_queryset(
                [BotProfile.State.RETIRED],
                engine_version=candidate_engine_version,
                target_cell=(target_region, target_band),
                occupied_manor_ids=occupied_manor_ids,
            )
            for profile in retired_candidates.iterator(chunk_size=100):
                if (
                    slots_needed <= 0
                    or materialization_attempts_used >= materialization_attempt_limit
                    or (hard_cap > 0 and maintained_count >= hard_cap)
                ):
                    break
                if not is_virtual_profile_arena_match_eligible(profile, now=current_time):
                    continue
                if (
                    assess_arena_reserve_candidate(
                        demand,
                        profile,
                        candidate_context=candidate_context,
                    ).disposition
                    is not ArenaReserveCandidateDisposition.READY
                ):
                    continue
                attempt_before = _reserve_attempt_count(demand)
                member = _lease_candidate(
                    demand=demand,
                    profile_id=profile.id,
                    allowed_states=[BotProfile.State.RETIRED],
                    member_state=ArenaVirtualReserveMember.State.READY,
                    engine_version=candidate_engine_version,
                    target_cell=target_cell,
                    supply_band_priority=supply_band_priority,
                    now=current_time,
                    recover=True,
                )
                materialization_attempts_used += max(0, _reserve_attempt_count(demand) - attempt_before)
                if member is not None:
                    made_ready_progress = True
                    recovered_retired += 1
                    maintained_count += 1
                    slots_needed -= 1
            if (
                slots_needed <= 0
                or materialization_attempts_used >= materialization_attempt_limit
                or (hard_cap > 0 and maintained_count >= hard_cap)
            ):
                break

    if growth_admission_allowed:
        for _priority_index, _power, profile_id in sorted(
            training_candidates,
            key=lambda row: (row[0], -row[1], row[2]),
        ):
            if slots_needed <= 0:
                break
            member = _lease_candidate(
                demand=demand,
                profile_id=profile_id,
                allowed_states=VIRTUAL_PROFILE_ARENA_ELIGIBLE_STATES,
                member_state=ArenaVirtualReserveMember.State.TRAINING,
                engine_version=candidate_engine_version,
                target_cell=target_cell,
                supply_band_priority=supply_band_priority,
                now=current_time,
            )
            if member is not None:
                slots_needed -= 1

    if made_ready_progress:
        record_demand_progress_locked(demand, now=current_time)

    ready_count = demand.reserve_members.filter(state=ArenaVirtualReserveMember.State.READY).count()
    training_count = demand.reserve_members.filter(state=ArenaVirtualReserveMember.State.TRAINING).count()
    attempt_count = _reserve_attempt_count(demand)
    raw_creation_needed = reserve_materialization_needed(
        warm_target=creation_target,
        ready_count=ready_count,
        training_count=training_count,
        attempt_count=attempt_count,
        replacement_target=int(demand.max_reserve_target_count),
    )
    admission_assessment = _refresh_admission_guard_locked(demand, now=current_time)
    creation_needed = (
        0
        if not population_mutation_allowed
        else min(raw_creation_needed, admission_assessment.admitted_materialization_needed)
    )
    blocked = (
        raw_creation_needed == 0
        and training_count == 0
        and ready_count < int(demand.missing_entry_count)
        and attempt_count >= int(demand.max_reserve_target_count)
    )
    if blocked:
        _block_replacement_budget_exhausted(demand, now=current_time)
        ready_count = 0
        training_count = 0
        creation_target = 0
    result = ReserveReplenishmentResult(
        ready_count=ready_count,
        training_count=training_count,
        recovered_abandoned=recovered_abandoned,
        recovered_retired=recovered_retired,
        creation_needed=creation_needed,
        warm_target_count=creation_target,
    )
    log_demand_event(
        "arena_virtual_reserve_replenished",
        demand,
        message="arena virtual reserve replenished",
        recovered_abandoned=int(recovered_abandoned),
        recovered_retired=int(recovered_retired),
        creation_needed=int(creation_needed),
        raw_creation_needed=int(raw_creation_needed),
        admission_guard_active=admission_assessment.admission_guard_active,
        admission_guard_reasons=admission_assessment.guard_reasons,
        runtime_reason=runtime_assessment.reason,
        growth_admission_allowed=growth_admission_allowed,
        population_mutation_allowed=population_mutation_allowed,
        candidate_engine_version=int(candidate_engine_version),
        warm_target_count=int(creation_target),
        attempt_count=int(attempt_count),
        replacement_target_count=int(demand.max_reserve_target_count),
    )
    return result


def _evaluate_member(member: ArenaVirtualReserveMember):
    return _evaluate_profile_for_demand(member.demand, member.profile)


_GROWTH_CLAIM_FIELDS = (
    "growth_operation_id",
    "growth_attempt_ordinal",
    "growth_request_digest_schema",
    "growth_control_snapshot_digest",
    "growth_policy_checksum",
    "growth_claim_token",
    "growth_claimed_at",
    "growth_claim_expires_at",
    "growth_requested_at",
    "growth_demand_version",
    "growth_member_version",
    "growth_power_before",
    "growth_eligible_guest_count_before",
    "growth_minimum_guest_count",
    "growth_minimum_guest_level",
    "growth_guest_rarity_cap",
    "growth_objective_payload",
    "growth_rounds_started",
    "growth_applied_action_count",
    "growth_action_ordinal_in_round",
    "growth_slot_attempt_ordinal",
    "growth_execution_attempt_count",
    "growth_round_training_guest_ids",
    "growth_round_id",
)


def _growth_claim_from_member(
    member: ArenaVirtualReserveMember,
) -> ArenaVirtualGrowthClaim:
    if (
        any(
            value is None
            for value in (
                member.growth_claim_token,
                member.growth_claimed_at,
                member.growth_claim_expires_at,
                member.growth_requested_at,
                member.growth_demand_version,
                member.growth_member_version,
                member.growth_power_before,
                member.growth_eligible_guest_count_before,
                member.growth_minimum_guest_count,
                member.growth_minimum_guest_level,
            )
        )
        or not member.growth_operation_id
    ):
        raise RuntimeError("arena virtual growth claim is incomplete")
    if int(member.growth_request_digest_schema) != 3:
        raise RuntimeError("arena virtual growth claim digest schema is invalid")
    assert member.growth_claim_token is not None
    assert member.growth_claimed_at is not None
    assert member.growth_claim_expires_at is not None
    assert member.growth_requested_at is not None
    assert member.growth_demand_version is not None
    assert member.growth_member_version is not None
    assert member.growth_power_before is not None
    assert member.growth_eligible_guest_count_before is not None
    assert member.growth_minimum_guest_count is not None
    assert member.growth_minimum_guest_level is not None
    if not member.growth_objective_payload:
        raise RuntimeError("arena virtual growth claim objective snapshot is missing")
    objective = ArenaGrowthObjective.from_payload(member.growth_objective_payload)
    return ArenaVirtualGrowthClaim(
        member_id=int(member.id),
        demand_id=int(member.demand_id),
        profile_id=int(member.profile_id),
        claim_token=member.growth_claim_token,
        claimed_at=member.growth_claimed_at,
        claim_expires_at=member.growth_claim_expires_at,
        operation_id=str(member.growth_operation_id),
        attempt_ordinal=int(member.growth_attempt_ordinal),
        request_digest_schema=int(member.growth_request_digest_schema),
        control_snapshot_digest=str(member.growth_control_snapshot_digest or ""),
        policy_checksum=str(member.growth_policy_checksum or ""),
        requested_at=member.growth_requested_at,
        demand_version=int(member.growth_demand_version),
        member_version=int(member.growth_member_version),
        power_before=int(member.growth_power_before),
        eligible_guest_count_before=int(member.growth_eligible_guest_count_before),
        target_team_power=objective.target_team_power,
        lineup_mode=objective.lineup_mode,
        lineup_event_id=objective.lineup_event_id,
        lineup_max_size=objective.lineup_max_size,
        round_ordinal=max(1, int(member.growth_rounds_started or 1)),
        action_ordinal_in_round=max(1, int(member.growth_action_ordinal_in_round or 1)),
        target=ArenaVirtualGrowthTarget(
            critical_guest_count=objective.critical_guest_count,
            preferred_guest_count=objective.preferred_guest_count,
            minimum_guest_level=objective.minimum_guest_level,
            recruitment_rarity_cap=objective.recruitment_rarity_cap,
            selected_power_lower_bound=objective.selected_power_lower_bound,
            selected_power_upper_bound=objective.selected_power_upper_bound,
        ),
        slot_attempt_ordinal=max(1, int(member.growth_slot_attempt_ordinal) + 1),
    )


def _growth_operation_has_receipt(member: ArenaVirtualReserveMember) -> bool:
    """Return whether the member's single in-flight operation has committed."""

    operation_id = str(member.growth_operation_id or "").strip()
    return bool(operation_id and BotMaintenanceExecution.objects.filter(operation_id=operation_id).exists())


def _growth_member_operation_has_receipt(member_id: int) -> bool:
    operation_id = (
        ArenaVirtualReserveMember.objects.filter(pk=int(member_id))
        .values_list("growth_operation_id", flat=True)
        .first()
    )
    return bool(
        str(operation_id or "").strip()
        and BotMaintenanceExecution.objects.filter(operation_id=str(operation_id)).exists()
    )


def _clear_growth_claim(member: ArenaVirtualReserveMember) -> None:
    member.growth_operation_id = ""
    member.growth_attempt_ordinal = 0
    # Schema 3 describes an in-flight claim.  Once the claim is cleared the
    # row must return to the unclaimed schema accepted by the database guard.
    member.growth_request_digest_schema = 2
    member.growth_control_snapshot_digest = ""
    member.growth_policy_checksum = ""
    member.growth_claim_token = None
    member.growth_claimed_at = None
    member.growth_claim_expires_at = None
    member.growth_requested_at = None
    member.growth_demand_version = None
    member.growth_member_version = None
    member.growth_power_before = None
    member.growth_eligible_guest_count_before = None
    member.growth_minimum_guest_count = None
    member.growth_minimum_guest_level = None
    member.growth_guest_rarity_cap = ""
    member.growth_objective_payload = {}


def _finalize_growth_budget_attempt(
    member: ArenaVirtualReserveMember,
    claim: ArenaVirtualGrowthClaim,
    *,
    outcome: ArenaGrowthAttemptOutcome,
    effective_progress: bool = False,
    selected_power_after: int | None = None,
) -> None:
    entries = parse_arena_growth_budget_entries(
        member.arena_growth_budget_entries,
        now=claim.claimed_at,
    )
    growth_bps = 0
    if selected_power_after is not None:
        growth_bps = selected_growth_bps(
            selected_power_before=claim.power_before,
            selected_power_after=selected_power_after,
            ready_lower_bound=claim.target.selected_power_lower_bound,
        )
    member.arena_growth_budget_entries = serialize_arena_growth_budget_entries(
        finalize_arena_growth_attempt(
            entries,
            attempt_id=str(claim.claim_token),
            outcome=outcome,
            effective_progress=effective_progress,
            selected_growth_bps=growth_bps,
        )
    )
    entries_after = parse_arena_growth_budget_entries(
        member.arena_growth_budget_entries,
        now=claim.claimed_at,
    )
    member.growth_execution_attempt_count = actual_arena_growth_attempt_count(entries_after)


def _record_growth_slot_result(
    member: ArenaVirtualReserveMember,
    *,
    outcome: ArenaGrowthAttemptOutcome,
    retry_reason: str = "",
) -> None:
    """Update slot retry accounting without changing lifecycle success count."""

    if outcome is ArenaGrowthAttemptOutcome.PENDING:
        return
    if outcome is ArenaGrowthAttemptOutcome.BUSY:
        member.growth_slot_attempt_ordinal = min(
            ARENA_GROWTH_MAX_SLOT_ATTEMPTS,
            int(member.growth_slot_attempt_ordinal) + 1,
        )
        return
    if outcome is ArenaGrowthAttemptOutcome.NO_ACTION and str(retry_reason) in {
        "profile_busy",
        "resource_snapshot_changed",
        "growth_business_error",
    }:
        member.growth_slot_attempt_ordinal = min(
            ARENA_GROWTH_MAX_SLOT_ATTEMPTS,
            int(member.growth_slot_attempt_ordinal) + 1,
        )
        return
    # APPLIED and deterministic NO_ACTION close the slot.  The next claim can
    # allocate the following ordered slot in the same active round.
    member.growth_slot_attempt_ordinal = 0


def _record_training_assignment_from_receipt(
    member: ArenaVirtualReserveMember,
    claim: ArenaVirtualGrowthClaim,
    *,
    status: str,
    reason: str = "",
) -> None:
    """Materialize the same-round training identity after a committed receipt."""

    receipt = BotMaintenanceExecution.objects.filter(operation_id=claim.operation_id).first()
    if receipt is None or str(receipt.action_kind) != "training":
        return
    raw_target = (receipt.shadow_cost or {}).get("target_guest_id")
    if isinstance(raw_target, bool) or not isinstance(raw_target, int) or raw_target < 1:
        return
    if not Guest.objects.filter(pk=raw_target, manor_id=member.profile.manor_id).exists():
        return
    assignment, created = ArenaReserveTrainingAssignment.objects.get_or_create(
        member=member,
        round_ordinal=claim.round_ordinal,
        guest_id=raw_target,
        defaults={
            "action_ordinal_in_round": claim.action_ordinal_in_round,
            "operation_id": claim.operation_id,
            "status": status,
            "reason": str(reason or "")[:64],
        },
    )
    if not created:
        if assignment.action_ordinal_in_round != claim.action_ordinal_in_round:
            raise RuntimeError("arena training guest was assigned to multiple slots in one round")
        assignment.status = status
        assignment.reason = str(reason or "")[:64]
        assignment.save(update_fields=["status", "reason", "updated_at"])
    ids = [int(value) for value in (member.growth_round_training_guest_ids or []) if int(value) > 0]
    if raw_target not in ids:
        member.growth_round_training_guest_ids = [*ids, raw_target]


@transaction.atomic
def _claim_due_virtual_reserve_growth(
    *,
    member_id: int,
    demand_id: int,
    now: datetime,
    growth_targets: dict[tuple[int, int, int], ArenaVirtualGrowthTarget],
) -> ArenaVirtualGrowthClaim | None:
    demand = (
        ArenaVirtualDemand.objects.select_for_update()
        .select_related("tournament", "coop_event")
        .filter(pk=demand_id)
        .first()
    )
    if demand is None:
        return None
    member = (
        ArenaVirtualReserveMember.objects.select_for_update(skip_locked=True)
        .filter(
            pk=member_id,
            demand_id=demand.id,
            state=ArenaVirtualReserveMember.State.TRAINING,
        )
        .select_related("profile", "profile__manor")
        .first()
    )
    if member is None:
        return None

    has_existing_claim = member.growth_claim_token is not None
    if recovery_circuit_is_open(path="arena_member", now=now):
        if not (has_existing_claim and _growth_operation_has_receipt(member)):
            return None
    if demand.status != ArenaVirtualDemand.Status.ACTIVE:
        if has_existing_claim:
            member.delete()
        return None
    if member.lease_paused_at is not None:
        # A worker may observe an expired claim immediately after routing
        # pauses.  Leave it fenced until the routing transaction translates
        # the member deadline on resume.
        return None
    recovery = BotMaintenanceRecovery.objects.filter(
        scope=BotMaintenanceRecovery.Scope.ARENA_MEMBER,
        entity_key=f"member:{int(member.id)}",
    ).first()
    recovery_has_receipt = bool(
        recovery is not None
        and recovery.failure_code == RecoveryFailureClass.COMMIT_UNCERTAIN
        and _growth_operation_has_receipt(member)
    )
    if (
        recovery is not None
        and recovery.status != BotMaintenanceRecovery.Status.REQUEUED
        and not recovery_has_receipt
        and recovery_is_blocked(
            scope=BotMaintenanceRecovery.Scope.ARENA_MEMBER,
            entity_key=f"member:{int(member.id)}",
            now=now,
        )
    ):
        return None
    if has_existing_claim:
        if (
            recovery is not None
            and recovery.failure_code == RecoveryFailureClass.COMMIT_UNCERTAIN
            and recovery.last_operation_id == str(member.growth_operation_id)
            and str((recovery.payload or {}).get("phase") or "") in {"execute", "finalize", "dispatch"}
            and recovery.status != BotMaintenanceRecovery.Status.REQUEUED
            and not _growth_operation_has_receipt(member)
        ):
            # A worker may not submit a second operation while the first
            # operation's commit result is still unknown.  Receipt reconcile
            # or the formal requeue service must explicitly release this
            # fence.
            return None
        if member.growth_claim_expires_at is not None and member.growth_claim_expires_at > now:
            return None
        # A stale claim may be fenced and replaced only while the member's
        # absolute arena lease is still alive.  Never let claim takeover
        # extend that lease; the operation is now a commit-uncertain recovery
        # item until receipt reconciliation decides whether it was submitted.
        if _no_action_lease_expired(member, now=now):
            record_recovery_failure(
                scope="arena_member",
                entity_key=f"member:{int(member.id)}",
                failure_code=RecoveryFailureClass.COMMIT_UNCERTAIN,
                operation_id=str(member.growth_operation_id),
                payload={
                    "demand_id": int(demand.id),
                    "profile_id": int(member.profile_id),
                    "phase": "claim_takeover",
                    "reason": "member_lease_expired",
                },
                now=now,
            )
            return None
    else:
        if member.next_acceleration_at is None or member.next_acceleration_at > now:
            return None
        if int(member.evaluated_version) != int(demand.version):
            return None

    if not has_existing_claim:
        evaluation = _evaluate_profile_for_demand(demand, member.profile)
        member.current_lineup_power = int(evaluation.selected_power)
        member.last_checked_at = now
        if evaluation.is_ready:
            member.state = ArenaVirtualReserveMember.State.READY
            member.next_acceleration_at = None
            member.lease_paused_at = None
            _clear_growth_retry(member)
            member.save(
                update_fields=[
                    "state",
                    "current_lineup_power",
                    "next_acceleration_at",
                    "last_checked_at",
                    "growth_retry_streak",
                    "growth_retry_reason",
                    "lease_paused_at",
                    "updated_at",
                ]
            )
            record_demand_progress_locked(demand, now=now)
            return None
        if not evaluation.snapshots:
            member.delete()
            return None
        if member.roster_target_count is None:
            member.roster_target_count = _roster_target_for_member(
                demand,
                profile_id=int(member.profile_id),
                current_guest_count=_current_guest_count_for_profile(member.profile),
            )
        target_key = (
            int(demand.id),
            int(demand.version),
            int(member.roster_target_count),
        )
        growth_target = growth_targets.get(target_key)
        if growth_target is None:
            growth_target = _growth_target_for_demand(
                demand,
                roster_target_count=int(member.roster_target_count),
            )
            growth_targets[target_key] = growth_target
        try:
            active_budget_entries = prune_arena_growth_budget_entries(
                parse_arena_growth_budget_entries(
                    member.arena_growth_budget_entries,
                    now=now,
                ),
                now=now,
            )
            active_execution_attempt_count = actual_arena_growth_attempt_count(active_budget_entries)
        except InvalidArenaGrowthBudgetError:
            active_execution_attempt_count = int(member.growth_execution_attempt_count)
        # Let the durable attempt-budget reservation below own the retry
        # disposition once the rolling window is full.  The reachability
        # preflight remains authoritative for direct target/cap decisions, but
        # must not replace the existing budget-window retry timestamp/reason.
        reachability = None
        if active_execution_attempt_count < ARENA_GROWTH_BUDGET_MAX_ATTEMPTS:
            reachability = _arena_growth_reachability(
                demand=demand,
                profile=member.profile,
                selected_power=int(member.current_lineup_power),
                growth_target=growth_target,
                growth_execution_attempt_count=active_execution_attempt_count,
                growth_round_training_guest_ids=tuple(member.growth_round_training_guest_ids or ()),
            )
        if reachability is not None and not reachability.reachable:
            member.state = ArenaVirtualReserveMember.State.EXHAUSTED
            member.next_acceleration_at = None
            member.last_checked_at = now
            member.growth_retry_reason = reachability.reason
            member.lease_paused_at = None
            member.save(
                update_fields=[
                    "state",
                    "current_lineup_power",
                    "roster_target_count",
                    "next_acceleration_at",
                    "last_checked_at",
                    "growth_retry_reason",
                    "lease_paused_at",
                    "updated_at",
                ]
            )
            record_demand_failure_locked(
                demand,
                reason=reachability.reason,
                now=now,
            )
            log_demand_event(
                "arena_virtual_profile_exhausted",
                demand,
                message="arena virtual profile target is unreachable under roster or event constraints",
                level=logging.WARNING,
                failure_reason=reachability.reason,
                profile_id=int(member.profile_id),
                power_before=int(member.current_lineup_power),
                max_selected_power=reachability.max_selected_power,
                target_power_lower_bound=growth_target.selected_power_lower_bound,
                growth_rounds=int(member.growth_rounds_started),
                member_state=str(member.state),
            )
            return None
        # Allocate lifecycle identity and the next ordered slot only once for
        # a new business operation.  An expired claim retries the same slot
        # and therefore does not reset the round or slot ordinal.
        if not member.growth_round_id:
            member.growth_rounds_started = int(member.growth_rounds_started) + 1
            member.growth_round_id = f"arena-round-{uuid4().hex}"
            member.growth_action_ordinal_in_round = 0
            member.growth_slot_attempt_ordinal = 0
            member.growth_round_training_guest_ids = []
        # A retryable BUSY/NO_ACTION may consume the fifth slot attempt.  The
        # next claim closes that slot and allocates the next one; it must not
        # spin forever on the same slot or reset the lifecycle ledger.
        if int(member.growth_slot_attempt_ordinal) >= ARENA_GROWTH_MAX_SLOT_ATTEMPTS:
            member.growth_slot_attempt_ordinal = 0
        if member.growth_slot_attempt_ordinal == 0:
            if int(member.growth_action_ordinal_in_round) >= ARENA_SLOTS_PER_ROUND:
                member.growth_rounds_started = int(member.growth_rounds_started) + 1
                member.growth_round_id = f"arena-round-{uuid4().hex}"
                member.growth_action_ordinal_in_round = 0
                member.growth_round_training_guest_ids = []
            member.growth_action_ordinal_in_round = int(member.growth_action_ordinal_in_round) + 1
        member.growth_operation_id = f"arena-growth-{uuid4().hex}"
        member.growth_attempt_ordinal = 0
        member.growth_request_digest_schema = 3
        member.growth_policy_checksum = str(member.profile.policy_checksum or "").strip()
        member.growth_control_snapshot_digest = growth_control_digest_for_route(
            region=str(member.profile.manor.region),
            prestige_band=str(member.profile.current_prestige_band),
            now=now,
        )
        member.growth_requested_at = now
        member.growth_demand_version = int(demand.version)
        member.growth_member_version = int(member.evaluated_version)
        member.growth_power_before = int(member.current_lineup_power)
        member.growth_eligible_guest_count_before = _eligible_guest_count_for_profile(
            member.profile,
        )
        member.growth_minimum_guest_count = growth_target.minimum_guest_count
        member.growth_minimum_guest_level = growth_target.minimum_guest_level
        member.growth_guest_rarity_cap = growth_target.guest_rarity_cap or ""
        lineup_mode = "tournament" if demand.tournament_id is not None else "coop"
        lineup_event_id = int(demand.tournament_id or demand.coop_event_id or 0)
        member.growth_objective_payload = growth_target.objective(
            selected_power_before=int(member.growth_power_before),
            max_guest_level_step=ARENA_MAX_GUEST_LEVEL_STEP,
            target_team_power=int(demand.target_team_power),
            lineup_mode=lineup_mode,
            lineup_event_id=lineup_event_id,
            lineup_max_size=_max_lineup_size_for_demand(demand),
        ).to_payload()
    try:
        budget_entries = parse_arena_growth_budget_entries(
            member.arena_growth_budget_entries,
            now=now,
        )
    except InvalidArenaGrowthBudgetError:
        member.state = ArenaVirtualReserveMember.State.EXHAUSTED
        member.next_acceleration_at = None
        member.last_checked_at = now
        member.growth_retry_reason = "invalid_growth_budget"
        member.lease_paused_at = None
        _clear_growth_claim(member)
        member.save(
            update_fields=[
                "state",
                "next_acceleration_at",
                "last_checked_at",
                "growth_retry_reason",
                "lease_paused_at",
                *_GROWTH_CLAIM_FIELDS,
                "updated_at",
            ]
        )
        log_demand_event(
            "arena_virtual_profile_exhausted",
            demand,
            message="arena virtual profile growth budget was invalid",
            level=logging.ERROR,
            failure_reason="invalid_growth_budget",
            profile_id=int(member.profile_id),
            power_before=int(member.current_lineup_power),
            growth_rounds=int(member.growth_rounds_started),
            member_state=str(member.state),
        )
        return None

    if has_existing_claim:
        assert member.growth_claim_token is not None
        budget_entries = cancel_arena_growth_attempt(
            budget_entries,
            attempt_id=str(member.growth_claim_token),
        )

    claim_token = uuid4()
    try:
        budget_entries = reserve_arena_growth_attempt(
            budget_entries,
            now=now,
            attempt_id=str(claim_token),
        )
    except ArenaGrowthAttemptBudgetExceeded as exc:
        member.next_acceleration_at = exc.retry_at
        member.last_checked_at = now
        member.growth_retry_reason = "arena_attempt_budget_exhausted"
        member.arena_growth_budget_entries = serialize_arena_growth_budget_entries(budget_entries)
        _clear_growth_claim(member)
        member.save(
            update_fields=[
                "next_acceleration_at",
                "last_checked_at",
                "growth_retry_reason",
                "arena_growth_budget_entries",
                *_GROWTH_CLAIM_FIELDS,
                "updated_at",
            ]
        )
        log_demand_event(
            "arena_virtual_profile_growth_deferred",
            demand,
            message="arena virtual profile growth deferred by its 24-hour attempt budget",
            failure_reason="arena_attempt_budget_exhausted",
            profile_id=int(member.profile_id),
            power_before=int(member.current_lineup_power),
            growth_rounds=int(member.growth_rounds_started),
            member_state=str(member.state),
            attempt_count=len(budget_entries),
            retry_at=exc.retry_at.isoformat(),
        )
        return None

    member.growth_attempt_ordinal = int(member.growth_attempt_ordinal) + 1
    member.growth_claim_token = claim_token
    member.growth_claimed_at = now
    member.growth_claim_expires_at = now + GROWTH_CLAIM_LEASE
    member.arena_growth_budget_entries = serialize_arena_growth_budget_entries(budget_entries)
    member.save(
        update_fields=[
            "current_lineup_power",
            "last_checked_at",
            "roster_target_count",
            "arena_growth_budget_entries",
            *_GROWTH_CLAIM_FIELDS,
            "updated_at",
        ]
    )
    member.demand = demand
    return _growth_claim_from_member(member)


def _growth_claim_is_current(
    member: ArenaVirtualReserveMember,
    claim: ArenaVirtualGrowthClaim,
) -> bool:
    expected_objective_payload = claim.target.objective(
        selected_power_before=claim.power_before,
        max_guest_level_step=ARENA_MAX_GUEST_LEVEL_STEP,
        target_team_power=claim.target_team_power,
        lineup_mode=claim.lineup_mode,
        lineup_event_id=claim.lineup_event_id,
        lineup_max_size=claim.lineup_max_size,
    ).to_payload()
    objective_matches = member.growth_objective_payload == expected_objective_payload
    return bool(
        member.growth_claim_token == claim.claim_token
        and member.growth_operation_id == claim.operation_id
        and int(member.growth_attempt_ordinal) == claim.attempt_ordinal
        and member.growth_requested_at == claim.requested_at
        and int(member.growth_request_digest_schema) == int(claim.request_digest_schema)
        and str(member.growth_policy_checksum or "") == claim.policy_checksum
        and int(member.growth_demand_version or 0) == claim.demand_version
        and int(member.growth_member_version or 0) == claim.member_version
        and int(member.profile_id) == claim.profile_id
        and objective_matches
        and int(
            member.growth_eligible_guest_count_before if member.growth_eligible_guest_count_before is not None else -1
        )
        == claim.eligible_guest_count_before
        and member.state == ArenaVirtualReserveMember.State.TRAINING
    )


@transaction.atomic
def _finalize_virtual_reserve_growth(
    claim: ArenaVirtualGrowthClaim,
    *,
    growth_outcome: AcceleratedGrowthOutcome,
    retry_reason: str = "",
    now: datetime,
) -> bool:
    demand = (
        ArenaVirtualDemand.objects.select_for_update()
        .select_related("tournament", "coop_event")
        .filter(pk=claim.demand_id)
        .first()
    )
    if demand is None:
        return False
    member = (
        ArenaVirtualReserveMember.objects.select_for_update()
        .select_related("profile", "profile__manor")
        .filter(pk=claim.member_id, demand_id=demand.id)
        .first()
    )
    if member is None or member.growth_claim_token != claim.claim_token:
        return False
    member.demand = demand
    if not _growth_claim_is_current(member, claim):
        return False
    if demand.status != ArenaVirtualDemand.Status.ACTIVE:
        member.delete()
        return True

    handled_at = claim.claimed_at
    power_before = claim.power_before
    _record_arena_growth_attempt_audit(
        claim,
        growth_outcome=growth_outcome,
        reason=retry_reason,
    )
    if growth_outcome is AcceleratedGrowthOutcome.BUSY:
        deadline = _no_action_lease_deadline(member, now=max(handled_at, now))
        retry_at = _schedule_growth_retry(
            member,
            handled_at=handled_at,
            reason="profile_busy",
            initial_delay=BUSY_RETRY_INITIAL_DELAY,
        )
        member.last_checked_at = handled_at
        if max(handled_at, now) >= deadline:
            member.state = ArenaVirtualReserveMember.State.EXHAUSTED
            member.next_acceleration_at = None
            member.growth_retry_reason = "growth_busy_lease_deadline"
            member.lease_paused_at = None
            event = "arena_virtual_profile_exhausted"
            message = "arena virtual profile training exhausted"
            busy_event_fields: dict[str, Any] = {
                "failure_reason": "growth_busy_lease_deadline",
                "lease_deadline": deadline.isoformat(),
            }
        else:
            member.next_acceleration_at = min(deadline, retry_at)
            event = "arena_virtual_profile_growth_deferred"
            message = "arena virtual profile growth deferred"
            busy_event_fields = {"failure_reason": "profile_busy", "lease_deadline": deadline.isoformat()}
        _finalize_growth_budget_attempt(
            member,
            claim,
            outcome=ArenaGrowthAttemptOutcome.BUSY,
        )
        _record_growth_slot_result(member, outcome=ArenaGrowthAttemptOutcome.BUSY)
        _clear_growth_claim(member)
        member.save(
            update_fields=[
                "state",
                "next_acceleration_at",
                "last_checked_at",
                "growth_retry_streak",
                "growth_retry_reason",
                "arena_growth_budget_entries",
                "lease_paused_at",
                *_GROWTH_CLAIM_FIELDS,
                "updated_at",
            ]
        )
        log_demand_event(
            event,
            demand,
            message=message,
            profile_id=claim.profile_id,
            power_before=power_before,
            growth_rounds=int(member.growth_rounds_started),
            member_state=str(member.state),
            **busy_event_fields,
        )
        return True
    if growth_outcome is AcceleratedGrowthOutcome.PAUSED:
        budget_entries = parse_arena_growth_budget_entries(
            member.arena_growth_budget_entries,
            now=max(claim.claimed_at, now),
        )
        member.arena_growth_budget_entries = serialize_arena_growth_budget_entries(
            cancel_arena_growth_attempt(
                budget_entries,
                attempt_id=str(claim.claim_token),
            )
        )
        _record_growth_slot_result(member, outcome=ArenaGrowthAttemptOutcome.NO_ACTION, retry_reason="growth_paused")
        member.next_acceleration_at = max(claim.claimed_at, now)
        member.last_checked_at = claim.claimed_at
        # Keep the pause reason visible to the same-round scheduler.  The
        # budget reservation was refunded, but repeatedly invoking a paused
        # maintenance path across all eight slots would be a hot loop.
        _clear_growth_retry(member)
        member.growth_retry_reason = "growth_paused"
        _clear_growth_claim(member)
        member.save(
            update_fields=[
                "next_acceleration_at",
                "last_checked_at",
                "growth_retry_streak",
                "growth_retry_reason",
                "arena_growth_budget_entries",
                *_GROWTH_CLAIM_FIELDS,
                "updated_at",
            ]
        )
        log_demand_event(
            "arena_virtual_profile_growth_deferred",
            demand,
            message="arena virtual profile growth deferred while maintenance was paused",
            failure_reason="growth_paused",
            profile_id=claim.profile_id,
            power_before=power_before,
            power_after=power_before,
            growth_rounds=int(member.growth_rounds_started),
            member_state=str(member.state),
        )
        return True
    if growth_outcome is AcceleratedGrowthOutcome.INELIGIBLE:
        growth_rounds = int(member.growth_rounds_started)
        member.delete()
        log_demand_event(
            "arena_virtual_profile_released",
            demand,
            message="arena virtual profile released from training",
            failure_reason="growth_ineligible",
            profile_id=claim.profile_id,
            power_before=power_before,
            power_after=power_before,
            growth_rounds=growth_rounds,
            member_state="released",
        )
        return True
    if growth_outcome is AcceleratedGrowthOutcome.NO_ACTION:
        deadline = _no_action_lease_deadline(member, now=max(handled_at, now))
        member.last_checked_at = handled_at
        no_action_reason = str(retry_reason or "growth_no_action")[:64]
        retry_at = _schedule_growth_retry(
            member,
            handled_at=handled_at,
            reason=no_action_reason,
            initial_delay=_growth_interval_for_demand(demand, now=handled_at),
        )
        if max(handled_at, now) >= deadline:
            member.state = ArenaVirtualReserveMember.State.EXHAUSTED
            member.next_acceleration_at = None
            exhausted_reason = "no_action_lease_deadline"
            member.growth_retry_reason = exhausted_reason
            member.lease_paused_at = None
            event = "arena_virtual_profile_exhausted"
            message = "arena virtual profile training exhausted"
            event_fields: dict[str, Any] = {
                "failure_reason": exhausted_reason,
                "power_after": power_before,
                "retry_reason": no_action_reason,
            }
        else:
            member.next_acceleration_at = min(deadline, retry_at)
            event = "arena_virtual_profile_growth_deferred"
            message = "arena virtual profile growth deferred"
            event_fields = {
                "failure_reason": no_action_reason,
                "lease_deadline": deadline.isoformat(),
            }
        _finalize_growth_budget_attempt(
            member,
            claim,
            outcome=ArenaGrowthAttemptOutcome.NO_ACTION,
        )
        _record_growth_slot_result(
            member,
            outcome=ArenaGrowthAttemptOutcome.NO_ACTION,
            retry_reason=no_action_reason,
        )
        _record_training_assignment_from_receipt(
            member,
            claim,
            status=(
                ArenaReserveTrainingAssignment.Status.ASSIGNED
                if no_action_reason in {"profile_busy", "resource_snapshot_changed", "growth_business_error"}
                else ArenaReserveTrainingAssignment.Status.NO_ACTION
            ),
            reason=no_action_reason,
        )
        _clear_growth_claim(member)
        member.save(
            update_fields=[
                "state",
                "next_acceleration_at",
                "last_checked_at",
                "growth_retry_streak",
                "growth_retry_reason",
                "arena_growth_budget_entries",
                "lease_paused_at",
                *_GROWTH_CLAIM_FIELDS,
                "updated_at",
            ]
        )
        log_demand_event(
            event,
            demand,
            message=message,
            profile_id=claim.profile_id,
            power_before=power_before,
            growth_rounds=int(member.growth_rounds_started),
            member_state=str(member.state),
            **event_fields,
        )
        return True
    if growth_outcome is not AcceleratedGrowthOutcome.GROWN:
        raise ValueError(f"Unsupported accelerated growth outcome: {growth_outcome!r}")

    evaluation = _evaluate_member(member)
    if not evaluation.snapshots:
        growth_rounds = int(member.growth_rounds_started)
        member.delete()
        log_demand_event(
            "arena_virtual_profile_exhausted",
            demand,
            message="arena virtual profile removed from training",
            failure_reason="no_valid_lineup",
            profile_id=claim.profile_id,
            power_before=power_before,
            power_after=0,
            growth_rounds=growth_rounds,
            member_state="released",
        )
        return True

    eligible_guest_count_after = _eligible_guest_count_for_profile(member.profile)
    ready_lower_bound = (int(demand.target_team_power) * MIN_LINEUP_POWER_PERCENT + 99) // 100
    selected_lineup_gap_before = max(0, ready_lower_bound - power_before)
    selected_lineup_gap_after = max(
        0,
        ready_lower_bound - int(evaluation.selected_power),
    )
    readiness_progress = bool(
        evaluation.is_ready
        or eligible_guest_count_after > claim.eligible_guest_count_before
        or selected_lineup_gap_after < selected_lineup_gap_before
    )
    member.current_lineup_power = evaluation.selected_power
    member.growth_applied_action_count = int(member.growth_applied_action_count) + 1
    member.growth_execution_attempt_count = max(
        int(member.growth_execution_attempt_count),
        int(
            applied_arena_growth_attempt_count(
                parse_arena_growth_budget_entries(
                    member.arena_growth_budget_entries,
                    now=handled_at,
                )
            )
        ),
    )
    member.evaluated_version = demand.version
    _clear_growth_retry(member)
    if evaluation.is_ready:
        member.state = ArenaVirtualReserveMember.State.READY
        member.next_acceleration_at = None
        member.lease_paused_at = None
    else:
        member.next_acceleration_at = handled_at + _growth_interval_for_demand(
            demand,
            now=handled_at,
        )
    member.last_checked_at = handled_at
    _finalize_growth_budget_attempt(
        member,
        claim,
        outcome=ArenaGrowthAttemptOutcome.APPLIED,
        effective_progress=readiness_progress,
        selected_power_after=int(evaluation.selected_power),
    )
    _record_growth_slot_result(member, outcome=ArenaGrowthAttemptOutcome.APPLIED)
    _record_training_assignment_from_receipt(
        member,
        claim,
        status=ArenaReserveTrainingAssignment.Status.APPLIED,
    )
    if int(member.growth_action_ordinal_in_round) >= ARENA_SLOTS_PER_ROUND:
        completed_round = int(member.growth_rounds_started)
        completed_action = int(member.growth_action_ordinal_in_round)
        member.growth_round_id = ""
        member.growth_action_ordinal_in_round = 0
        member.growth_round_training_guest_ids = []
        if member.next_acceleration_at is not None:
            member.next_acceleration_at += _arena_rearm_jitter(
                member_id=int(member.id),
                round_ordinal=completed_round,
                action_ordinal=completed_action,
            )
    _clear_growth_claim(member)
    member.save(
        update_fields=[
            "state",
            "evaluated_version",
            "current_lineup_power",
            "next_acceleration_at",
            "last_checked_at",
            "growth_retry_streak",
            "growth_retry_reason",
            "arena_growth_budget_entries",
            "lease_paused_at",
            *_GROWTH_CLAIM_FIELDS,
            "updated_at",
        ]
    )
    log_demand_event(
        "arena_virtual_profile_grown",
        demand,
        message="arena virtual profile growth processed",
        profile_id=claim.profile_id,
        power_before=power_before,
        power_after=int(member.current_lineup_power),
        growth_rounds=int(member.growth_rounds_started),
        member_state=str(member.state),
        readiness_progress=readiness_progress,
        eligible_guest_count_before=claim.eligible_guest_count_before,
        eligible_guest_count_after=eligible_guest_count_after,
        selected_lineup_gap_before=selected_lineup_gap_before,
        selected_lineup_gap_after=selected_lineup_gap_after,
        selected_growth_bps=selected_growth_bps(
            selected_power_before=power_before,
            selected_power_after=int(member.current_lineup_power),
            ready_lower_bound=ready_lower_bound,
        ),
    )
    if readiness_progress:
        record_demand_progress_locked(demand, now=handled_at)
    return True


@transaction.atomic
def _defer_unclaimed_growth_business_error(
    *,
    member_id: int,
    demand_id: int,
    profile_id: int,
    operation_id: str,
    now: datetime,
) -> bool:
    """Back off one poison member when claiming failed before a lease existed."""

    demand = (
        ArenaVirtualDemand.objects.select_for_update()
        .select_related("tournament", "coop_event")
        .filter(pk=demand_id, status=ArenaVirtualDemand.Status.ACTIVE)
        .first()
    )
    if demand is None:
        return False
    member = (
        ArenaVirtualReserveMember.objects.select_for_update()
        .filter(
            pk=member_id,
            demand_id=demand.id,
            profile_id=profile_id,
            state=ArenaVirtualReserveMember.State.TRAINING,
            growth_claim_token__isnull=True,
        )
        .first()
    )
    if member is None:
        return False

    deadline = _no_action_lease_deadline(member, now=now)
    retry_at = _schedule_growth_retry(
        member,
        handled_at=now,
        reason=_GROWTH_BUSINESS_ERROR_REASON,
        initial_delay=BUSY_RETRY_INITIAL_DELAY,
    )
    member.last_checked_at = now
    if now >= deadline:
        member.state = ArenaVirtualReserveMember.State.EXHAUSTED
        member.next_acceleration_at = None
        member.growth_retry_reason = _GROWTH_BUSINESS_ERROR_LEASE_REASON
        member.lease_paused_at = None
        event = "arena_virtual_profile_exhausted"
        message = "arena virtual profile training exhausted after repeated business errors"
        event_fields: dict[str, Any] = {
            "failure_reason": _GROWTH_BUSINESS_ERROR_LEASE_REASON,
        }
    else:
        member.next_acceleration_at = min(deadline, retry_at)
        event = "arena_virtual_profile_growth_deferred"
        message = "arena virtual profile growth deferred after a member business error"
        event_fields = {
            "failure_reason": _GROWTH_BUSINESS_ERROR_REASON,
            "lease_deadline": deadline.isoformat(),
        }
    member.save(
        update_fields=[
            "state",
            "next_acceleration_at",
            "last_checked_at",
            "growth_retry_streak",
            "growth_retry_reason",
            "lease_paused_at",
            "updated_at",
        ]
    )
    log_demand_event(
        event,
        demand,
        message=message,
        level=logging.WARNING,
        profile_id=profile_id,
        member_id=member_id,
        operation_id=operation_id,
        growth_rounds=int(member.growth_rounds_started),
        member_state=str(member.state),
        **event_fields,
    )
    return True


def _log_growth_member_business_error(
    *,
    exc: Exception,
    member_id: int,
    demand_id: int,
    profile_id: int,
    operation_id: str,
    phase: str,
) -> None:
    logger.warning(
        "Arena virtual reserve growth member failed with a recoverable business error: "
        "demand_id=%s member_id=%s profile_id=%s operation_id=%s phase=%s",
        demand_id,
        member_id,
        profile_id,
        operation_id,
        phase,
        exc_info=True,
        extra={
            "event": "arena_virtual_growth_member_business_error",
            "demand_id": demand_id,
            "member_id": member_id,
            "profile_id": profile_id,
            "operation_id": operation_id,
            "phase": phase,
            "failure_reason": _GROWTH_BUSINESS_ERROR_REASON,
            "failure_code": type(exc).__name__,
        },
    )


def _grow_due_virtual_reserves_once(
    *,
    now=None,
    limit: int = 100,
    _member_id: int | None = None,
) -> int:
    """Finalize at most one business attempt per selected member.

    The public worker wrapper below uses this bounded primitive to keep the
    current arena growth can consume the remaining slots of one round in the
    same processing loop.
    """

    current_time = now or timezone.now()
    _release_expired_terminal_virtual_reserve_claims(now=current_time, limit=limit)
    release_expired_exhausted_virtual_reserve_members(now=current_time, limit=limit)
    growth_engine_version = _reserve_growth_engine_version(now=current_time)
    if growth_engine_version is None:
        return 0
    member_query = ArenaVirtualReserveMember.objects.filter(
        Q(
            state=ArenaVirtualReserveMember.State.TRAINING,
            next_acceleration_at__lte=current_time,
            demand__status=ArenaVirtualDemand.Status.ACTIVE,
            growth_claim_token__isnull=True,
        )
        | Q(
            growth_claim_token__isnull=False,
            growth_claim_expires_at__lte=current_time,
        )
    ).filter(
        profile__engine_version=int(growth_engine_version),
        profile__policy_version=2,
    )
    if _member_id is not None:
        member_query = member_query.filter(pk=int(_member_id))
    member_rows = list(
        member_query.order_by("next_acceleration_at", "id").values_list("id", "demand_id", "profile_id")[
            : max(0, int(limit))
        ]
    )
    processed = 0
    growth_targets: dict[tuple[int, int, int], ArenaVirtualGrowthTarget] = {}
    for member_id, demand_id, profile_id in member_rows:
        if recovery_is_blocked(
            scope="arena_member",
            entity_key=f"member:{int(member_id)}",
            now=current_time,
        ) and not _growth_member_operation_has_receipt(int(member_id)):
            continue
        try:
            claim = _claim_due_virtual_reserve_growth(
                member_id=int(member_id),
                demand_id=int(demand_id),
                now=current_time,
                growth_targets=growth_targets,
            )
        except _ARENA_GROWTH_MEMBER_BUSINESS_ERRORS as exc:
            diagnostic_operation_id = f"arena-growth-claim-{uuid4().hex}"
            _log_growth_member_business_error(
                exc=exc,
                member_id=int(member_id),
                demand_id=int(demand_id),
                profile_id=int(profile_id),
                operation_id=diagnostic_operation_id,
                phase="claim",
            )
            _defer_unclaimed_growth_business_error(
                member_id=int(member_id),
                demand_id=int(demand_id),
                profile_id=int(profile_id),
                operation_id=diagnostic_operation_id,
                now=current_time,
            )
            continue
        except (DatabaseError, SafetyProviderError) as exc:
            diagnostic_operation_id = f"arena-growth-claim-{uuid4().hex}"
            record_recovery_failure(
                scope="arena_member",
                entity_key=f"member:{int(member_id)}",
                failure_code=classify_failure(exc, commit_uncertain=True),
                error=exc,
                operation_id=diagnostic_operation_id,
                payload={"demand_id": int(demand_id), "profile_id": int(profile_id), "phase": "claim"},
            )
            _log_growth_member_business_error(
                exc=exc,
                member_id=int(member_id),
                demand_id=int(demand_id),
                profile_id=int(profile_id),
                operation_id=diagnostic_operation_id,
                phase="claim_database_failure",
            )
            raise
        except Exception as exc:
            diagnostic_operation_id = f"arena-growth-claim-{uuid4().hex}"
            record_recovery_failure(
                scope="arena_member",
                entity_key=f"member:{int(member_id)}",
                failure_code=classify_failure(exc),
                error=exc,
                operation_id=diagnostic_operation_id,
                payload={"demand_id": int(demand_id), "profile_id": int(profile_id), "phase": "claim"},
            )
            _log_growth_member_business_error(
                exc=exc,
                member_id=int(member_id),
                demand_id=int(demand_id),
                profile_id=int(profile_id),
                operation_id=diagnostic_operation_id,
                phase="claim_unexpected_failure",
            )
            continue
        if claim is None:
            continue
        try:
            growth_outcome = accelerate_virtual_player_growth(
                claim.profile_id,
                now=claim.requested_at,
                arena_growth_objective=claim.target.objective(
                    selected_power_before=claim.power_before,
                    max_guest_level_step=ARENA_MAX_GUEST_LEVEL_STEP,
                    target_team_power=claim.target_team_power,
                    lineup_mode=claim.lineup_mode,
                    lineup_event_id=claim.lineup_event_id,
                    lineup_max_size=claim.lineup_max_size,
                ),
                operation_id=claim.operation_id,
                attempt_ordinal=claim.attempt_ordinal,
                request_digest_schema=claim.request_digest_schema,
                _arena_member_id=claim.member_id,
                _arena_round_ordinal=claim.round_ordinal,
                _arena_action_ordinal=claim.action_ordinal_in_round,
                _arena_slot_attempt_ordinal=claim.slot_attempt_ordinal,
                _control_snapshot_digest=claim.control_snapshot_digest or None,
                _expected_policy_checksum=claim.policy_checksum or None,
            )
            retry_reason = _growth_retry_reason(
                operation_id=claim.operation_id,
                growth_outcome=growth_outcome,
            )
        except _ARENA_GROWTH_MEMBER_BUSINESS_ERRORS as exc:
            _log_growth_member_business_error(
                exc=exc,
                member_id=claim.member_id,
                demand_id=claim.demand_id,
                profile_id=claim.profile_id,
                operation_id=claim.operation_id,
                phase="execute",
            )
            growth_outcome = AcceleratedGrowthOutcome.NO_ACTION
            retry_reason = _GROWTH_BUSINESS_ERROR_REASON
        except (DatabaseError, SafetyProviderError) as exc:
            record_recovery_failure(
                scope="arena_member",
                entity_key=f"member:{int(claim.member_id)}",
                failure_code=classify_failure(exc, commit_uncertain=True),
                error=exc,
                operation_id=claim.operation_id,
                payload={"demand_id": int(claim.demand_id), "profile_id": int(claim.profile_id), "phase": "execute"},
            )
            _log_growth_member_business_error(
                exc=exc,
                member_id=claim.member_id,
                demand_id=claim.demand_id,
                profile_id=claim.profile_id,
                operation_id=claim.operation_id,
                phase="execute_database_failure",
            )
            raise
        except Exception as exc:
            record_recovery_failure(
                scope="arena_member",
                entity_key=f"member:{int(claim.member_id)}",
                failure_code=classify_failure(exc),
                error=exc,
                operation_id=claim.operation_id,
                payload={"demand_id": int(claim.demand_id), "profile_id": int(claim.profile_id), "phase": "execute"},
            )
            _log_growth_member_business_error(
                exc=exc,
                member_id=claim.member_id,
                demand_id=claim.demand_id,
                profile_id=claim.profile_id,
                operation_id=claim.operation_id,
                phase="execute_unexpected_failure",
            )
            continue
        lease_check_at = max(timezone.now(), current_time)
        try:
            finalized = _finalize_virtual_reserve_growth(
                claim,
                growth_outcome=growth_outcome,
                retry_reason=retry_reason,
                now=lease_check_at,
            )
        except (DatabaseError, SafetyProviderError) as exc:
            record_recovery_failure(
                scope="arena_member",
                entity_key=f"member:{int(claim.member_id)}",
                failure_code=classify_failure(exc, commit_uncertain=True),
                error=exc,
                operation_id=claim.operation_id,
                payload={
                    "demand_id": int(claim.demand_id),
                    "profile_id": int(claim.profile_id),
                    "phase": "finalize",
                },
                now=lease_check_at,
            )
            _log_growth_member_business_error(
                exc=exc,
                member_id=claim.member_id,
                demand_id=claim.demand_id,
                profile_id=claim.profile_id,
                operation_id=claim.operation_id,
                phase="finalize_database_failure",
            )
            raise
        except Exception as exc:
            record_recovery_failure(
                scope="arena_member",
                entity_key=f"member:{int(claim.member_id)}",
                failure_code=classify_failure(exc),
                error=exc,
                operation_id=claim.operation_id,
                payload={
                    "demand_id": int(claim.demand_id),
                    "profile_id": int(claim.profile_id),
                    "phase": "finalize",
                },
                now=lease_check_at,
            )
            _log_growth_member_business_error(
                exc=exc,
                member_id=claim.member_id,
                demand_id=claim.demand_id,
                profile_id=claim.profile_id,
                operation_id=claim.operation_id,
                phase="finalize_unexpected_failure",
            )
            continue
        if finalized:
            clear_recovery_failure(
                scope="arena_member",
                entity_key=f"member:{int(claim.member_id)}",
                now=lease_check_at,
            )
            processed += 1
    return processed


def _prepare_same_round_retry(*, member_id: int, now: datetime) -> bool:
    """Make the next arena slot immediately claimable when it is safe.

    Claim/finalize remains the sole owner of lifecycle and attempt counters.
    This adapter only removes the ordinary inter-cycle wall-clock delay while
    the current round still has ordered slots.  A missing candidate or a
    routing pause ends the current processing loop instead of hot-spinning.
    """

    member = (
        ArenaVirtualReserveMember.objects.filter(
            pk=int(member_id),
            state=ArenaVirtualReserveMember.State.TRAINING,
            growth_claim_token__isnull=True,
        )
        .values(
            "growth_round_id",
            "growth_action_ordinal_in_round",
            "arena_growth_budget_entries",
            "growth_retry_reason",
        )
        .first()
    )
    if member is None:
        return False
    round_id = str(member.get("growth_round_id") or "")
    if not round_id:
        return False
    if str(member.get("growth_retry_reason") or "") in {
        "arena_action_unavailable",
        "no_eligible_candidate",
        "growth_no_action",
        # A V2 maintenance/rule error is a bounded business failure.  Let
        # the member's durable retry/backoff handle a later scan; do not
        # spend every remaining slot of the current round on the same bad
        # operation.
        "growth_business_error",
        "growth_paused",
        "routing_unavailable",
    }:
        return False
    try:
        budget_entries = prune_arena_growth_budget_entries(
            parse_arena_growth_budget_entries(
                member.get("arena_growth_budget_entries"),
                now=now,
            ),
            now=now,
        )
    except InvalidArenaGrowthBudgetError:
        # The claim path owns invalid-budget recovery.  Do not overwrite its
        # durable error state from this scheduler adapter.
        return False
    if actual_arena_growth_attempt_count(budget_entries) >= ARENA_GROWTH_BUDGET_MAX_ATTEMPTS:
        if budget_entries:
            retry_at = budget_entries[0].attempted_at + ARENA_GROWTH_BUDGET_WINDOW
            ArenaVirtualReserveMember.objects.filter(
                pk=int(member_id),
                state=ArenaVirtualReserveMember.State.TRAINING,
                growth_claim_token__isnull=True,
            ).update(
                next_acceleration_at=retry_at,
                growth_retry_reason="arena_attempt_budget_exhausted",
            )
        return False
    # A BUSY retry may exhaust the current round without consuming a
    # successful action.  Let the next claim allocate a new round; the outer
    # worker loop is bounded by the 24-hour execution budget, so this cannot
    # turn into an unbounded hot loop.  APPLIED and terminal NO_ACTION paths
    # clear ``growth_round_id`` or return above before reaching this point.
    ArenaVirtualReserveMember.objects.filter(
        pk=int(member_id),
        state=ArenaVirtualReserveMember.State.TRAINING,
        growth_claim_token__isnull=True,
    ).update(next_acceleration_at=now)
    return True


def grow_due_virtual_reserves(*, now=None, limit: int = 100) -> int:
    """Process due reserve growth, filling arena round slots in order."""

    current_time = now or timezone.now()
    growth_engine_version = _reserve_growth_engine_version(now=current_time)
    if growth_engine_version is None:
        return 0
    requested_limit = max(0, int(limit))
    due_member_query = ArenaVirtualReserveMember.objects.filter(
        Q(
            state=ArenaVirtualReserveMember.State.TRAINING,
            next_acceleration_at__lte=current_time,
            demand__status=ArenaVirtualDemand.Status.ACTIVE,
            growth_claim_token__isnull=True,
        )
        | Q(
            growth_claim_token__isnull=False,
            growth_claim_expires_at__lte=current_time,
        )
    ).filter(
        profile__engine_version=int(growth_engine_version),
        profile__policy_version=2,
    )
    # Keep a bounded, independently fair queue per demand before applying the
    # worker limit.  A plain global ``LIMIT`` lets one hot demand occupy the
    # whole candidate window and starve every other due demand indefinitely.
    member_rows = list(
        due_member_query.annotate(
            demand_due_rank=Window(
                expression=RowNumber(),
                partition_by=[F("demand_id")],
                order_by=[F("next_acceleration_at").asc(), F("id").asc()],
            )
        )
        .filter(demand_due_rank__lte=ARENA_GROWTH_MAX_MEMBERS_PER_DEMAND)
        .order_by("next_acceleration_at", "id")
        .values_list("id", "demand_id", "next_acceleration_at")
    )
    if not member_rows:
        return _grow_due_virtual_reserves_once(now=current_time, limit=0)

    demand_buckets: dict[int, list[tuple[int, int]]] = {}
    demand_oldest_due: dict[int, datetime] = {}
    for member_id, demand_id, due_at in member_rows:
        normalized_demand_id = int(demand_id)
        demand_buckets.setdefault(normalized_demand_id, []).append((int(member_id), normalized_demand_id))
        if due_at is not None:
            demand_oldest_due.setdefault(normalized_demand_id, due_at)

    demand_order = sorted(
        demand_buckets,
        key=lambda demand_id: (
            demand_oldest_due.get(demand_id) or current_time,
            demand_id,
        ),
    )
    selected_member_rows: list[tuple[int, int]] = []
    demand_counts: Counter[int] = Counter()
    while len(selected_member_rows) < requested_limit:
        made_progress = False
        for demand_id in demand_order:
            bucket = demand_buckets[demand_id]
            if not bucket:
                continue
            selected_member_rows.append(bucket.pop(0))
            demand_counts[demand_id] += 1
            made_progress = True
            if len(selected_member_rows) >= requested_limit:
                break
        if not made_progress:
            break

    oldest_due_at = member_rows[0][2] if member_rows else None
    oldest_due_age_seconds = (
        max(0.0, (current_time - oldest_due_at).total_seconds()) if oldest_due_at is not None else 0.0
    )

    logger.info(
        "Arena virtual reserve growth selected a fair due batch",
        extra={
            "event": "arena_virtual_growth_batch_selected",
            "requested_limit": requested_limit,
            "candidate_count": len(member_rows),
            "selected_count": len(selected_member_rows),
            "demand_count": len(demand_counts),
            "candidate_limit_per_demand": ARENA_GROWTH_MAX_MEMBERS_PER_DEMAND,
            "oldest_due_member_id": member_rows[0][0] if member_rows else None,
            "oldest_due_at": oldest_due_at.isoformat() if oldest_due_at is not None else None,
            "oldest_due_age_seconds": oldest_due_age_seconds,
        },
    )

    processed = 0
    for member_id, _demand_id in selected_member_rows:
        # The rolling execution budget is the authoritative safety boundary.
        # Slot/round limits order retries but must not silently reduce the
        # configured 24-hour attempt budget when every result is BUSY.
        for _slot_attempt in range(ARENA_GROWTH_BUDGET_MAX_ATTEMPTS):
            attempt_result = _grow_due_virtual_reserves_once(
                now=current_time,
                limit=1,
                _member_id=int(member_id),
            )
            if attempt_result <= 0:
                break
            processed += attempt_result
            if not _prepare_same_round_retry(member_id=int(member_id), now=current_time):
                break
    return processed


__all__ = [
    "ArenaReserveCandidateAssessment",
    "ArenaReserveCandidateDisposition",
    "ArenaVirtualGrowthTarget",
    "EXHAUSTED_LEASE_GRACE",
    "MAX_RESERVE_MEMBER_LEASE_AGE",
    "ReserveReplenishmentResult",
    "assess_arena_reserve_candidate",
    "evaluate_bot_lineup",
    "grow_due_virtual_reserves",
    "reevaluate_existing_members",
    "release_virtual_reserve_member_for_manor",
    "release_virtual_reserve_members_for_demand",
    "replenish_virtual_reserve",
    "release_expired_exhausted_virtual_reserve_members",
]
