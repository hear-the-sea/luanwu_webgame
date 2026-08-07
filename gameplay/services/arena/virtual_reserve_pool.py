from __future__ import annotations

import logging
import random
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from django.db import transaction
from django.db.models import F, Prefetch, Q
from django.utils import timezone

from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaTournament,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
    BotMaintenanceExecution,
    BotProfile,
)
from gameplay.services.arena.snapshots import build_entry_guest_snapshot
from gameplay.services.runtime_configs import RuntimeRoutingError, read_virtual_player_routing
from gameplay.services.virtual_player_core.config import BootstrapMode, MaintenanceMode
from gameplay.services.virtual_player_core.contracts import (
    AcceleratedGrowthOutcome,
    BotProjectionConfig,
    PopulationMutationStatus,
)
from gameplay.services.virtual_player_core.maintenance import accelerate_virtual_player_growth
from gameplay.services.virtual_player_core.population_runtime import (
    create_virtual_player_with_capacity,
    get_virtual_player_capacity,
    reactivate_retired_virtual_player_with_capacity,
    reactivate_virtual_player_profile,
    virtual_player_prestige_bands,
)
from gameplay.services.virtual_player_state_policy import (
    VIRTUAL_PROFILE_ARENA_ELIGIBLE_STATES,
    is_virtual_profile_arena_eligible,
)
from guests.models import Guest, GuestRarity, GuestStatus

from .coop_rules import load_arena_coop_rules
from .rules import load_arena_rules
from .virtual_lineups import BotLineupEvaluation, LineupSelectionContext, evaluate_lineup_snapshots
from .virtual_protection import is_virtual_profile_arena_match_eligible, with_arena_reconciliation_state
from .virtual_reserve_observability import log_demand_event
from .virtual_reserve_policy import reserve_warm_target, virtual_roster_target_count
from .virtual_reserve_references import median_entry, reference_snapshots_for_demand

logger = logging.getLogger(__name__)

MAX_ACCELERATED_GROWTH_ROUNDS = 8
MAX_RESERVE_MEMBER_LEASE_AGE = timedelta(hours=12)
# Kept as a compatibility alias for the Gate A contract and existing callers.
MAX_NO_ACTION_LEASE_AGE = MAX_RESERVE_MEMBER_LEASE_AGE
EXHAUSTED_LEASE_GRACE = timedelta(minutes=30)
# Arena reserve growth may catch up faster than ordinary V2 maintenance, but it
# remains bounded so a reserve member cannot jump into the target band at once.
ARENA_MAX_GUEST_LEVEL_STEP = 6
PRE_FILL_GROWTH_INTERVAL = timedelta(hours=1)
POST_FILL_GROWTH_INTERVAL = timedelta(minutes=15)
GROWTH_CLAIM_LEASE = timedelta(minutes=5)
GROWTH_RETRY_MAX_DELAY = timedelta(hours=1)
BUSY_RETRY_INITIAL_DELAY = timedelta(minutes=5)
MAX_STRENGTH_CAP_RETRIES = 3
MAX_NEW_PROFILES_PER_DEMAND_PER_RUN = 2
DEMAND_RETRY_INITIAL = timedelta(minutes=5)
DEMAND_RETRY_MAX = timedelta(hours=1)
_GUEST_RARITY_RANK = {rarity.value: index for index, rarity in enumerate(GuestRarity)}


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
    """Persist bounded backoff without allowing reconciliation to erase it."""
    failure_count = min(255, int(demand.consecutive_failure_count) + 1)
    demand.consecutive_failure_count = failure_count
    demand.last_failure_reason = str(reason)[:64]
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
    demand.save(
        update_fields=[
            "consecutive_failure_count",
            "last_failure_reason",
            "last_checked_at",
            "last_progress_at",
            "next_retry_at",
            "updated_at",
        ]
    )


@dataclass(frozen=True)
class ArenaVirtualGrowthTarget:
    minimum_guest_count: int
    minimum_guest_level: int
    guest_rarity_cap: str | None


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
    requested_at: datetime
    demand_version: int
    member_version: int
    power_before: int
    target: ArenaVirtualGrowthTarget


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
        .select_related("profile", "profile__manor")
        .order_by("id")
    )
    for member in members:
        member.roster_target_count = _roster_target_for_member(
            demand,
            profile_id=int(member.profile_id),
            current_guest_count=member.profile.manor.guests.count(),
        )
        member.save(update_fields=["roster_target_count", "updated_at"])
    return len(members)


def _growth_target_for_demand(
    demand: ArenaVirtualDemand,
    *,
    roster_target_count: int | None = None,
) -> ArenaVirtualGrowthTarget:
    snapshots = reference_snapshots_for_demand(demand)
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
    return ArenaVirtualGrowthTarget(
        minimum_guest_count=max(
            int(demand.target_guest_count),
            len(snapshots),
            int(roster_target_count or 0),
        ),
        minimum_guest_level=max(guest_levels, default=1),
        guest_rarity_cap=rarity_cap,
    )


def _demand_fill_at(demand: ArenaVirtualDemand):
    event = demand.tournament if demand.tournament_id is not None else demand.coop_event
    return event.virtual_fill_at if event is not None else None


def _demand_fill_is_due(demand: ArenaVirtualDemand, *, now) -> bool:
    fill_at = _demand_fill_at(demand)
    return fill_at is not None and fill_at <= now


def _growth_interval_for_demand(demand: ArenaVirtualDemand, *, now) -> timedelta:
    return POST_FILL_GROWTH_INTERVAL if _demand_fill_is_due(demand, now=now) else PRE_FILL_GROWTH_INTERVAL


def _reserve_growth_runtime_available() -> bool:
    try:
        routing = read_virtual_player_routing()
    except RuntimeRoutingError as exc:
        logger.warning(
            "arena virtual reserve growth paused because runtime routing is unavailable",
            extra={"event": "arena_virtual_growth_routing_unavailable", "failure_reason": str(exc)[:64]},
        )
        return False
    maintenance_mode = MaintenanceMode(routing.maintenance_mode)
    if maintenance_mode in {MaintenanceMode.V2_CUTOVER, MaintenanceMode.V2_PAUSED}:
        logger.info(
            "arena virtual reserve growth deferred while maintenance routing is paused",
            extra={
                "event": "arena_virtual_growth_routing_paused",
                "maintenance_mode": maintenance_mode.value,
                "pause_reason": routing.pause_reason,
            },
        )
        return False
    return True


def _reserve_legacy_creation_available() -> bool:
    try:
        routing = read_virtual_player_routing()
    except RuntimeRoutingError as exc:
        logger.warning(
            "arena virtual reserve creation paused because runtime routing is unavailable",
            extra={"event": "arena_virtual_creation_routing_unavailable", "failure_reason": str(exc)[:64]},
        )
        return False
    return (
        BootstrapMode(routing.bootstrap_mode) is BootstrapMode.LEGACY_BEFORE_GATE
        and MaintenanceMode(routing.maintenance_mode) is MaintenanceMode.LEGACY_BEFORE_GATE
    )


def _reserve_creation_target(demand: ArenaVirtualDemand) -> int:
    """Return the persisted active target, with a safe fallback for old rows."""
    persisted_target = int(demand.warm_target_count or 0)
    if persisted_target > 0 or int(demand.missing_entry_count or 0) == 0:
        return persisted_target
    return reserve_warm_target(
        missing=demand.missing_entry_count,
        reserve_target=demand.reserve_target_count,
    )


def _no_action_lease_deadline(member: ArenaVirtualReserveMember):
    return member.created_at + MAX_RESERVE_MEMBER_LEASE_AGE


def _no_action_lease_expired(member: ArenaVirtualReserveMember, *, now) -> bool:
    return now >= _no_action_lease_deadline(member)


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


def _release_training_members_for_runtime_pause(*, now: datetime, limit: int) -> int:
    """Release safe training leases so a paused runtime cannot pin reserve capacity."""
    candidate_rows = list(
        ArenaVirtualReserveMember.objects.filter(
            state=ArenaVirtualReserveMember.State.TRAINING,
            demand__status=ArenaVirtualDemand.Status.ACTIVE,
        )
        .filter(Q(growth_claim_token__isnull=True) | Q(growth_claim_expires_at__lte=now))
        .order_by("id")
        .values_list("id", "demand_id")[: max(0, int(limit))]
    )
    released = 0
    for member_id, demand_id in candidate_rows:
        with transaction.atomic():
            demand = (
                ArenaVirtualDemand.objects.select_for_update()
                .filter(pk=demand_id, status=ArenaVirtualDemand.Status.ACTIVE)
                .first()
            )
            if demand is None:
                continue
            member = (
                ArenaVirtualReserveMember.objects.select_for_update(skip_locked=True)
                .filter(
                    pk=member_id,
                    demand=demand,
                    state=ArenaVirtualReserveMember.State.TRAINING,
                )
                .filter(Q(growth_claim_token__isnull=True) | Q(growth_claim_expires_at__lte=now))
                .first()
            )
            if member is None:
                continue
            profile_id = int(member.profile_id)
            growth_rounds = int(member.accelerated_growth_rounds)
            member.delete()
            released += 1
            log_demand_event(
                "arena_virtual_profile_released",
                demand,
                message="arena virtual profile released while growth runtime was paused",
                failure_reason="growth_runtime_paused",
                profile_id=profile_id,
                power_after=int(member.current_lineup_power),
                growth_rounds=growth_rounds,
                member_state="released",
            )
    if released:
        logger.info(
            "arena virtual training leases released while growth runtime was paused",
            extra={"event": "arena_virtual_paused_training_released", "released_count": released},
        )
    return released


def reevaluate_existing_members(demand: ArenaVirtualDemand, *, now) -> None:
    if demand.tournament_id is None and demand.coop_event_id is None:
        return

    members = list(
        demand.reserve_members.select_for_update().select_related("profile", "profile__manor").order_by("id")
    )
    for member in members:
        if member.growth_claim_token is not None:
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
        if member.state == ArenaVirtualReserveMember.State.EXHAUSTED and (
            member.accelerated_growth_rounds >= MAX_ACCELERATED_GROWTH_ROUNDS
            or _no_action_lease_expired(member, now=now)
            or (
                member.growth_retry_reason in {"strength_cap_retry_limit", "growth_busy_lease_deadline"}
                and not demand_version_changed
            )
        ):
            if not member.growth_retry_reason:
                member.growth_retry_reason = (
                    "growth_round_limit"
                    if member.accelerated_growth_rounds >= MAX_ACCELERATED_GROWTH_ROUNDS
                    else "no_action_lease_deadline"
                )
            member.save(
                update_fields=[
                    "evaluated_version",
                    "roster_target_count",
                    "last_checked_at",
                    "growth_retry_reason",
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
        elif evaluation.snapshots and member.accelerated_growth_rounds < MAX_ACCELERATED_GROWTH_ROUNDS:
            member.state = ArenaVirtualReserveMember.State.TRAINING
            member.next_acceleration_at = now if demand_version_changed else (member.next_acceleration_at or now)
            if (
                _demand_fill_is_due(demand, now=now)
                and not member.growth_retry_reason
                and member.next_acceleration_at > now + POST_FILL_GROWTH_INTERVAL
            ):
                member.next_acceleration_at = now
        elif evaluation.snapshots:
            member.state = ArenaVirtualReserveMember.State.EXHAUSTED
            member.next_acceleration_at = None
            member.growth_retry_reason = "growth_round_limit"
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
                "updated_at",
            ]
        )


def _occupied_arena_manor_ids() -> set[int]:
    occupied = set(
        ArenaEntry.objects.filter(
            status=ArenaEntry.Status.REGISTERED,
            tournament__status__in=[
                ArenaTournament.Status.RECRUITING,
                ArenaTournament.Status.RUNNING,
            ],
        ).values_list("manor_id", flat=True)
    )
    occupied.update(
        ArenaCoopEntry.objects.filter(
            status=ArenaCoopEntry.Status.REGISTERED,
            event__status__in=[
                ArenaCoopEvent.Status.RECRUITING,
                ArenaCoopEvent.Status.PREPARING,
                ArenaCoopEvent.Status.RUNNING,
            ],
        ).values_list("manor_id", flat=True)
    )
    return occupied


def _candidate_queryset(states: Collection[str]):
    queryset = (
        BotProfile.objects.filter(
            state__in=list(states),
            arena_virtual_reserve__isnull=True,
        )
        .exclude(manor_id__in=_occupied_arena_manor_ids())
        .select_related("manor")
        .prefetch_related(
            Prefetch(
                "manor__guests",
                queryset=Guest.objects.filter(status=GuestStatus.IDLE).select_related("template").order_by("id"),
                to_attr="arena_idle_guests",
            )
        )
        .order_by("id")
    )
    return with_arena_reconciliation_state(queryset)


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
            current_guest_count=profile.manor.guests.count(),
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


def _trim_surplus_members(demand: ArenaVirtualDemand) -> None:
    members = list(demand.reserve_members.exclude(state=ArenaVirtualReserveMember.State.EXHAUSTED).order_by("id"))
    surplus = len(members) - _reserve_creation_target(demand)
    if surplus <= 0:
        return
    removable = sorted(
        [member for member in members if member.growth_claim_token is None],
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
    now,
    recover: bool = False,
) -> ArenaVirtualReserveMember | None:
    try:
        with transaction.atomic():
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
                        arena_virtual_reserve__isnull=True,
                    )
                    .exclude(manor_id__in=_occupied_arena_manor_ids())
                    .select_related("manor")
                )
                profile = with_arena_reconciliation_state(profile_queryset).first()
            if profile is None:
                return None
            if not is_virtual_profile_arena_match_eligible(profile, now=now):
                raise _CandidateLeaseRejected
            if (
                ArenaVirtualReserveMember.objects.filter(profile_id=profile.id).exists()
                or profile.manor_id in _occupied_arena_manor_ids()
            ):
                raise _CandidateLeaseRejected
            evaluation = _evaluate_profile_for_demand(demand, profile)
            if member_state == ArenaVirtualReserveMember.State.READY and not evaluation.is_ready:
                raise _CandidateLeaseRejected
            if member_state == ArenaVirtualReserveMember.State.TRAINING and (
                evaluation.is_ready or not evaluation.snapshots
            ):
                raise _CandidateLeaseRejected
            member = ArenaVirtualReserveMember.objects.create(
                demand=demand,
                profile=profile,
                state=member_state,
                evaluated_version=demand.version,
                current_lineup_power=evaluation.selected_power,
                roster_target_count=_roster_target_for_member(
                    demand,
                    profile_id=int(profile.id),
                    current_guest_count=profile.manor.guests.count(),
                ),
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
    demand = (
        ArenaVirtualDemand.objects.select_for_update()
        .filter(pk=demand_id, status=ArenaVirtualDemand.Status.ACTIVE)
        .first()
    )
    if demand is None:
        return ReserveReplenishmentResult(0, 0, 0, 0, 0)
    _ensure_roster_targets_for_demand(demand)
    if demand.next_retry_at is not None and demand.next_retry_at > current_time + timedelta(seconds=1):
        return ReserveReplenishmentResult(0, 0, 0, 0, 0)

    reevaluate_existing_members(demand, now=current_time)
    _trim_surplus_members(demand)
    active_member_count = demand.reserve_members.exclude(state=ArenaVirtualReserveMember.State.EXHAUSTED).count()
    creation_target = _reserve_creation_target(demand)
    slots_needed = max(0, creation_target - active_member_count)
    recovered_abandoned = 0
    recovered_retired = 0
    made_progress = False
    training_candidates: list[tuple[int, int]] = []

    for profile in _candidate_queryset(VIRTUAL_PROFILE_ARENA_ELIGIBLE_STATES).iterator(chunk_size=100):
        if slots_needed <= 0:
            break
        if not is_virtual_profile_arena_match_eligible(profile, now=current_time):
            continue
        evaluation = _evaluate_profile_for_demand(demand, profile)
        if evaluation.is_ready:
            member = _lease_candidate(
                demand=demand,
                profile_id=profile.id,
                allowed_states=VIRTUAL_PROFILE_ARENA_ELIGIBLE_STATES,
                member_state=ArenaVirtualReserveMember.State.READY,
                now=current_time,
            )
            if member is not None:
                made_progress = True
                slots_needed -= 1
        elif evaluation.snapshots:
            training_candidates.append((evaluation.selected_power, profile.id))

    if slots_needed > 0:
        for profile in _candidate_queryset([BotProfile.State.ABANDONED]).iterator(chunk_size=100):
            if slots_needed <= 0:
                break
            if not is_virtual_profile_arena_match_eligible(profile, now=current_time):
                continue
            if not _evaluate_profile_for_demand(demand, profile).is_ready:
                continue
            member = _lease_candidate(
                demand=demand,
                profile_id=profile.id,
                allowed_states=[BotProfile.State.ABANDONED],
                member_state=ArenaVirtualReserveMember.State.READY,
                now=current_time,
                recover=True,
            )
            if member is not None:
                made_progress = True
                recovered_abandoned += 1
                slots_needed -= 1

    if slots_needed > 0:
        hard_cap, maintained_count = get_virtual_player_capacity(now=current_time)
        for profile in _candidate_queryset([BotProfile.State.RETIRED]).iterator(chunk_size=100):
            if slots_needed <= 0 or (hard_cap > 0 and maintained_count >= hard_cap):
                break
            if not is_virtual_profile_arena_match_eligible(profile, now=current_time):
                continue
            if not _evaluate_profile_for_demand(demand, profile).is_ready:
                continue
            member = _lease_candidate(
                demand=demand,
                profile_id=profile.id,
                allowed_states=[BotProfile.State.RETIRED],
                member_state=ArenaVirtualReserveMember.State.READY,
                now=current_time,
                recover=True,
            )
            if member is not None:
                made_progress = True
                recovered_retired += 1
                maintained_count += 1
                slots_needed -= 1

    for _power, profile_id in sorted(training_candidates, key=lambda row: (-row[0], row[1])):
        if slots_needed <= 0:
            break
        member = _lease_candidate(
            demand=demand,
            profile_id=profile_id,
            allowed_states=VIRTUAL_PROFILE_ARENA_ELIGIBLE_STATES,
            member_state=ArenaVirtualReserveMember.State.TRAINING,
            now=current_time,
        )
        if member is not None:
            made_progress = True
            slots_needed -= 1

    if made_progress:
        record_demand_progress_locked(demand, now=current_time)

    ready_count = demand.reserve_members.filter(state=ArenaVirtualReserveMember.State.READY).count()
    training_count = demand.reserve_members.filter(state=ArenaVirtualReserveMember.State.TRAINING).count()
    active_member_count = ready_count + training_count
    creation_budget = max(0, int(demand.max_reserve_target_count) - int(demand.created_profile_count))
    creation_needed = min(
        max(0, creation_target - active_member_count),
        creation_budget,
    )
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
        warm_target_count=int(creation_target),
    )
    return result


def _evaluate_member(member: ArenaVirtualReserveMember):
    return _evaluate_profile_for_demand(member.demand, member.profile)


_GROWTH_CLAIM_FIELDS = (
    "growth_operation_id",
    "growth_attempt_ordinal",
    "growth_claim_token",
    "growth_claimed_at",
    "growth_claim_expires_at",
    "growth_requested_at",
    "growth_demand_version",
    "growth_member_version",
    "growth_power_before",
    "growth_minimum_guest_count",
    "growth_minimum_guest_level",
    "growth_guest_rarity_cap",
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
                member.growth_minimum_guest_count,
                member.growth_minimum_guest_level,
            )
        )
        or not member.growth_operation_id
    ):
        raise RuntimeError("arena virtual growth claim is incomplete")
    assert member.growth_claim_token is not None
    assert member.growth_claimed_at is not None
    assert member.growth_claim_expires_at is not None
    assert member.growth_requested_at is not None
    assert member.growth_demand_version is not None
    assert member.growth_member_version is not None
    assert member.growth_power_before is not None
    assert member.growth_minimum_guest_count is not None
    assert member.growth_minimum_guest_level is not None
    return ArenaVirtualGrowthClaim(
        member_id=int(member.id),
        demand_id=int(member.demand_id),
        profile_id=int(member.profile_id),
        claim_token=member.growth_claim_token,
        claimed_at=member.growth_claimed_at,
        claim_expires_at=member.growth_claim_expires_at,
        operation_id=str(member.growth_operation_id),
        attempt_ordinal=int(member.growth_attempt_ordinal),
        requested_at=member.growth_requested_at,
        demand_version=int(member.growth_demand_version),
        member_version=int(member.growth_member_version),
        power_before=int(member.growth_power_before),
        target=ArenaVirtualGrowthTarget(
            minimum_guest_count=int(member.growth_minimum_guest_count),
            minimum_guest_level=int(member.growth_minimum_guest_level),
            guest_rarity_cap=(member.growth_guest_rarity_cap or None),
        ),
    )


def _clear_growth_claim(member: ArenaVirtualReserveMember) -> None:
    member.growth_operation_id = ""
    member.growth_attempt_ordinal = 0
    member.growth_claim_token = None
    member.growth_claimed_at = None
    member.growth_claim_expires_at = None
    member.growth_requested_at = None
    member.growth_demand_version = None
    member.growth_member_version = None
    member.growth_power_before = None
    member.growth_minimum_guest_count = None
    member.growth_minimum_guest_level = None
    member.growth_guest_rarity_cap = ""


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
    if demand.status != ArenaVirtualDemand.Status.ACTIVE:
        if has_existing_claim:
            member.delete()
        return None
    if has_existing_claim:
        if member.growth_claim_expires_at is not None and member.growth_claim_expires_at > now:
            return None
    else:
        if member.next_acceleration_at is None or member.next_acceleration_at > now:
            return None
        if int(member.evaluated_version) != int(demand.version):
            return None

    if not has_existing_claim:
        if member.roster_target_count is None:
            member.roster_target_count = _roster_target_for_member(
                demand,
                profile_id=int(member.profile_id),
                current_guest_count=member.profile.manor.guests.count(),
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
        member.growth_operation_id = f"arena-growth-{uuid4().hex}"
        member.growth_attempt_ordinal = 0
        member.growth_requested_at = now
        member.growth_demand_version = int(demand.version)
        member.growth_member_version = int(member.evaluated_version)
        member.growth_power_before = int(member.current_lineup_power)
        member.growth_minimum_guest_count = growth_target.minimum_guest_count
        member.growth_minimum_guest_level = growth_target.minimum_guest_level
        member.growth_guest_rarity_cap = growth_target.guest_rarity_cap or ""

    member.growth_attempt_ordinal = int(member.growth_attempt_ordinal) + 1
    member.growth_claim_token = uuid4()
    member.growth_claimed_at = now
    member.growth_claim_expires_at = now + GROWTH_CLAIM_LEASE
    member.save(update_fields=["roster_target_count", *_GROWTH_CLAIM_FIELDS, "updated_at"])
    return _growth_claim_from_member(member)


def _growth_claim_is_current(
    member: ArenaVirtualReserveMember,
    claim: ArenaVirtualGrowthClaim,
) -> bool:
    return bool(
        member.growth_claim_token == claim.claim_token
        and member.growth_operation_id == claim.operation_id
        and int(member.growth_attempt_ordinal) == claim.attempt_ordinal
        and member.growth_requested_at == claim.requested_at
        and int(member.growth_demand_version or 0) == claim.demand_version
        and int(member.growth_member_version or 0) == claim.member_version
        and int(member.profile_id) == claim.profile_id
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
    if growth_outcome is AcceleratedGrowthOutcome.BUSY:
        deadline = _no_action_lease_deadline(member)
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
        _clear_growth_claim(member)
        member.save(
            update_fields=[
                "state",
                "next_acceleration_at",
                "last_checked_at",
                "growth_retry_streak",
                "growth_retry_reason",
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
            growth_rounds=int(member.accelerated_growth_rounds),
            member_state=str(member.state),
            **busy_event_fields,
        )
        return True
    if growth_outcome is AcceleratedGrowthOutcome.PAUSED:
        growth_rounds = int(member.accelerated_growth_rounds)
        member.delete()
        log_demand_event(
            "arena_virtual_profile_released",
            demand,
            message="arena virtual profile released while growth was paused",
            failure_reason="growth_paused",
            profile_id=claim.profile_id,
            power_before=power_before,
            power_after=power_before,
            growth_rounds=growth_rounds,
            member_state="released",
        )
        return True
    if growth_outcome is AcceleratedGrowthOutcome.INELIGIBLE:
        growth_rounds = int(member.accelerated_growth_rounds)
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
        deadline = _no_action_lease_deadline(member)
        member.last_checked_at = handled_at
        no_action_reason = str(retry_reason or "growth_no_action")[:64]
        retry_at = _schedule_growth_retry(
            member,
            handled_at=handled_at,
            reason=no_action_reason,
            initial_delay=_growth_interval_for_demand(demand, now=handled_at),
        )
        strength_cap_exhausted = (
            no_action_reason == "strength_cap" and int(member.growth_retry_streak) >= MAX_STRENGTH_CAP_RETRIES
        )
        if max(handled_at, now) >= deadline or strength_cap_exhausted:
            member.state = ArenaVirtualReserveMember.State.EXHAUSTED
            member.next_acceleration_at = None
            exhausted_reason = "strength_cap_retry_limit" if strength_cap_exhausted else "no_action_lease_deadline"
            member.growth_retry_reason = exhausted_reason
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
        _clear_growth_claim(member)
        member.save(
            update_fields=[
                "state",
                "next_acceleration_at",
                "last_checked_at",
                "growth_retry_streak",
                "growth_retry_reason",
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
            growth_rounds=int(member.accelerated_growth_rounds),
            member_state=str(member.state),
            **event_fields,
        )
        return True
    if growth_outcome is not AcceleratedGrowthOutcome.GROWN:
        raise ValueError(f"Unsupported accelerated growth outcome: {growth_outcome!r}")

    evaluation = _evaluate_member(member)
    if not evaluation.snapshots:
        growth_rounds = int(member.accelerated_growth_rounds)
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

    member.current_lineup_power = evaluation.selected_power
    member.accelerated_growth_rounds += 1
    member.evaluated_version = demand.version
    _clear_growth_retry(member)
    exhausted_reason = ""
    if evaluation.is_ready:
        member.state = ArenaVirtualReserveMember.State.READY
        member.next_acceleration_at = None
    elif member.accelerated_growth_rounds >= MAX_ACCELERATED_GROWTH_ROUNDS:
        member.state = ArenaVirtualReserveMember.State.EXHAUSTED
        member.next_acceleration_at = None
        exhausted_reason = "growth_round_limit"
        member.growth_retry_reason = exhausted_reason
    else:
        member.next_acceleration_at = handled_at + _growth_interval_for_demand(
            demand,
            now=handled_at,
        )
    member.last_checked_at = handled_at
    _clear_growth_claim(member)
    member.save(
        update_fields=[
            "state",
            "evaluated_version",
            "current_lineup_power",
            "accelerated_growth_rounds",
            "next_acceleration_at",
            "last_checked_at",
            "growth_retry_streak",
            "growth_retry_reason",
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
        growth_rounds=int(member.accelerated_growth_rounds),
        member_state=str(member.state),
    )
    if exhausted_reason:
        log_demand_event(
            "arena_virtual_profile_exhausted",
            demand,
            message="arena virtual profile training exhausted",
            failure_reason=exhausted_reason,
            profile_id=claim.profile_id,
            power_before=power_before,
            power_after=int(member.current_lineup_power),
            growth_rounds=int(member.accelerated_growth_rounds),
            member_state=str(member.state),
        )
    record_demand_progress_locked(demand, now=handled_at)
    return True


def grow_due_virtual_reserves(*, now=None, limit: int = 100) -> int:
    current_time = now or timezone.now()
    _release_expired_terminal_virtual_reserve_claims(now=current_time, limit=limit)
    release_expired_exhausted_virtual_reserve_members(now=current_time, limit=limit)
    if not _reserve_growth_runtime_available():
        _release_training_members_for_runtime_pause(now=current_time, limit=limit)
        return 0
    member_rows = list(
        ArenaVirtualReserveMember.objects.filter(
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
        )
        .order_by("next_acceleration_at", "id")
        .values_list("id", "demand_id")[: max(0, int(limit))]
    )
    processed = 0
    growth_targets: dict[tuple[int, int, int], ArenaVirtualGrowthTarget] = {}
    for member_id, demand_id in member_rows:
        claim = _claim_due_virtual_reserve_growth(
            member_id=int(member_id),
            demand_id=int(demand_id),
            now=current_time,
            growth_targets=growth_targets,
        )
        if claim is None:
            continue
        growth_outcome = accelerate_virtual_player_growth(
            claim.profile_id,
            now=claim.requested_at,
            minimum_guest_count=claim.target.minimum_guest_count,
            minimum_guest_level=claim.target.minimum_guest_level,
            guest_rarity_cap=claim.target.guest_rarity_cap,
            max_guest_level_step=ARENA_MAX_GUEST_LEVEL_STEP,
            operation_id=claim.operation_id,
            attempt_ordinal=claim.attempt_ordinal,
        )
        retry_reason = _growth_retry_reason(
            operation_id=claim.operation_id,
            growth_outcome=growth_outcome,
        )
        lease_check_at = max(timezone.now(), current_time)
        if _finalize_virtual_reserve_growth(
            claim,
            growth_outcome=growth_outcome,
            retry_reason=retry_reason,
            now=lease_check_at,
        ):
            processed += 1
    return processed


def _target_prestige_band_for_demand(demand: ArenaVirtualDemand) -> str:
    real_entries: Sequence[ArenaEntry | ArenaCoopEntry]
    if demand.tournament_id is not None:
        real_entries = list(
            ArenaEntry.objects.filter(
                tournament_id=demand.tournament_id,
                status=ArenaEntry.Status.REGISTERED,
                source=ArenaEntry.Source.PLAYER,
            )
            .select_related("manor")
            .prefetch_related("entry_guests")
        )
    else:
        real_entries = list(
            ArenaCoopEntry.objects.filter(
                event_id=demand.coop_event_id,
                status=ArenaCoopEntry.Status.REGISTERED,
                source=ArenaCoopEntry.Source.PLAYER,
            )
            .select_related("manor")
            .prefetch_related("entry_guests")
        )
    bands = virtual_player_prestige_bands()
    if real_entries:
        prestige = int(median_entry(real_entries).manor.prestige or 0)
        for band_name, (low, high) in bands.items():
            if prestige >= low and (high is None or prestige < high):
                return band_name
    for band_name, (low, high) in bands.items():
        if 0 >= low and (high is None or 0 < high):
            return band_name
    return next(iter(bands), "newbie")


def create_due_virtual_reserve_profiles(*, now=None, limit: int = 100) -> int:
    current_time = now or timezone.now()
    if not _reserve_legacy_creation_available():
        return 0
    due_cutoff = current_time + timedelta(seconds=1)
    remaining_limit = max(0, int(limit))
    if remaining_limit <= 0:
        return 0
    demand_ids = (
        ArenaVirtualDemand.objects.filter(status=ArenaVirtualDemand.Status.ACTIVE)
        .filter(created_profile_count__lt=F("max_reserve_target_count"))
        .filter(Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=due_cutoff))
        .order_by("next_retry_at", "id")
        .values_list("id", flat=True)
        .iterator(chunk_size=100)
    )
    created = 0
    for demand_id in demand_ids:
        if created >= remaining_limit:
            break
        replenishment = replenish_virtual_reserve(demand_id, now=current_time)
        needed = min(
            replenishment.creation_needed,
            MAX_NEW_PROFILES_PER_DEMAND_PER_RUN,
            remaining_limit - created,
        )
        if needed <= 0:
            continue
        for _ in range(needed):
            with transaction.atomic():
                claimed_demand = (
                    ArenaVirtualDemand.objects.select_for_update()
                    .filter(
                        pk=demand_id,
                        status=ArenaVirtualDemand.Status.ACTIVE,
                    )
                    .first()
                )
                if (
                    claimed_demand is None
                    or claimed_demand.created_profile_count >= claimed_demand.max_reserve_target_count
                ):
                    break
                creation_target = _reserve_creation_target(claimed_demand)
                active_slots = claimed_demand.reserve_members.exclude(
                    state=ArenaVirtualReserveMember.State.EXHAUSTED
                ).count()
                if active_slots >= creation_target:
                    break
                target_band = _target_prestige_band_for_demand(claimed_demand)
                bands = virtual_player_prestige_bands()
                target_low, _target_high = bands.get(target_band, (0, None))
                claim_ordinal = int(claimed_demand.created_profile_count) + 1
                seed_rng = random.Random(f"arena-reserve:{demand_id}:{claim_ordinal}:{current_time.isoformat()}")
                growth_seed = seed_rng.randint(1, 2_147_483_647)
                mutation = create_virtual_player_with_capacity(
                    region=None,
                    prestige_band=target_band,
                    growth_seed=growth_seed,
                    now=current_time,
                    projection=BotProjectionConfig(
                        prestige=max(0, int(target_low)),
                        building_level=1,
                        guest_count=1,
                        guest_level=1,
                        troop_count=50,
                    ),
                    start_from_zero=True,
                )
                if mutation.status is PopulationMutationStatus.CAP_REACHED:
                    record_demand_failure_locked(
                        claimed_demand,
                        reason="dynamic_population_cap_reached",
                        now=current_time,
                    )
                    log_demand_event(
                        "arena_virtual_fill_deferred",
                        claimed_demand,
                        message="arena virtual profile creation deferred by population cap",
                        level=logging.WARNING,
                        failure_reason="dynamic_population_cap_reached",
                        hard_cap=int(mutation.hard_cap),
                        maintained_count=int(mutation.maintained_count),
                    )
                    return created
                if mutation.status is PopulationMutationStatus.UNAVAILABLE:
                    record_demand_failure_locked(
                        claimed_demand,
                        reason="population_region_unavailable",
                        now=current_time,
                    )
                    log_demand_event(
                        "arena_virtual_fill_deferred",
                        claimed_demand,
                        message="arena virtual profile creation deferred without target region",
                        level=logging.WARNING,
                        failure_reason="population_region_unavailable",
                    )
                    return created
                profile = mutation.profile
                if profile is None:
                    continue
                claimed_demand.created_profile_count = claim_ordinal
                claimed_demand.save(update_fields=["created_profile_count", "updated_at"])
                record_demand_progress_locked(claimed_demand, now=current_time)

                if claimed_demand.status != ArenaVirtualDemand.Status.ACTIVE or active_slots >= creation_target:
                    evaluation = None
                else:
                    evaluation = _evaluate_profile_for_demand(claimed_demand, profile)
                if evaluation is not None and evaluation.snapshots:
                    state = (
                        ArenaVirtualReserveMember.State.READY
                        if evaluation.is_ready
                        else ArenaVirtualReserveMember.State.TRAINING
                    )
                    ArenaVirtualReserveMember.objects.create(
                        demand=claimed_demand,
                        profile=profile,
                        state=state,
                        evaluated_version=claimed_demand.version,
                        current_lineup_power=evaluation.selected_power,
                        roster_target_count=_roster_target_for_member(
                            claimed_demand,
                            profile_id=int(profile.id),
                            current_guest_count=profile.manor.guests.count(),
                        ),
                        next_acceleration_at=(
                            current_time if state == ArenaVirtualReserveMember.State.TRAINING else None
                        ),
                        last_checked_at=current_time,
                    )
            created += 1
            log_demand_event(
                "arena_virtual_profile_created",
                claimed_demand,
                message="arena virtual profile created",
                profile_id=int(profile.id),
                region=str(profile.manor.region),
                target_prestige_band=str(target_band),
                actual_prestige=int(profile.manor.prestige),
            )
    return created


__all__ = [
    "ArenaVirtualGrowthTarget",
    "EXHAUSTED_LEASE_GRACE",
    "MAX_RESERVE_MEMBER_LEASE_AGE",
    "MAX_NO_ACTION_LEASE_AGE",
    "MAX_STRENGTH_CAP_RETRIES",
    "ReserveReplenishmentResult",
    "create_due_virtual_reserve_profiles",
    "evaluate_bot_lineup",
    "grow_due_virtual_reserves",
    "reevaluate_existing_members",
    "release_virtual_reserve_member_for_manor",
    "release_virtual_reserve_members_for_demand",
    "replenish_virtual_reserve",
    "release_expired_exhausted_virtual_reserve_members",
]
