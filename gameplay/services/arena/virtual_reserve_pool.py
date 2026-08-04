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
    BotProfile,
)
from gameplay.services.arena.snapshots import build_entry_guest_snapshot
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

from .virtual_lineups import BotLineupEvaluation, LineupSelectionContext, evaluate_lineup_snapshots
from .virtual_protection import is_virtual_profile_arena_match_eligible, with_arena_reconciliation_state
from .virtual_reserve_observability import log_demand_event
from .virtual_reserve_references import median_entry, reference_snapshots_for_demand

MAX_ACCELERATED_GROWTH_ROUNDS = 8
MAX_NO_ACTION_LEASE_AGE = timedelta(hours=12)
ARENA_MAX_GUEST_LEVEL_STEP = 20
PRE_FILL_GROWTH_INTERVAL = timedelta(hours=1)
POST_FILL_GROWTH_INTERVAL = timedelta(minutes=15)
GROWTH_CLAIM_LEASE = timedelta(minutes=5)
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
) -> BotLineupEvaluation:
    return evaluate_lineup_snapshots(
        _profile_lineup_snapshots(profile),
        context=LineupSelectionContext(
            mode=str(mode),
            event_id=int(event_id),
            profile_id=int(profile.id),
        ),
        target_guest_count=int(target_guest_count),
        target_team_power=int(target_team_power),
    )


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


def _growth_target_for_demand(demand: ArenaVirtualDemand) -> ArenaVirtualGrowthTarget:
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
        minimum_guest_count=max(int(demand.target_guest_count), len(snapshots)),
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


def _no_action_lease_deadline(member: ArenaVirtualReserveMember):
    return member.created_at + MAX_NO_ACTION_LEASE_AGE


def _no_action_lease_expired(member: ArenaVirtualReserveMember, *, now) -> bool:
    return now >= _no_action_lease_deadline(member)


def reevaluate_existing_members(demand: ArenaVirtualDemand, *, now) -> None:
    mode = "tournament" if demand.tournament_id is not None else "coop"
    event_id = demand.tournament_id or demand.coop_event_id
    if event_id is None:
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
        member.evaluated_version = demand.version
        member.last_checked_at = now
        if member.state == ArenaVirtualReserveMember.State.EXHAUSTED and (
            member.accelerated_growth_rounds >= MAX_ACCELERATED_GROWTH_ROUNDS
            or _no_action_lease_expired(member, now=now)
        ):
            member.save(update_fields=["evaluated_version", "last_checked_at", "updated_at"])
            continue

        evaluation = evaluate_bot_lineup(
            member.profile,
            mode=mode,
            event_id=int(event_id),
            target_guest_count=demand.target_guest_count,
            target_team_power=demand.target_team_power,
        )
        member.current_lineup_power = evaluation.selected_power
        if evaluation.is_ready:
            member.state = ArenaVirtualReserveMember.State.READY
            member.next_acceleration_at = None
        elif evaluation.snapshots and member.accelerated_growth_rounds < MAX_ACCELERATED_GROWTH_ROUNDS:
            member.state = ArenaVirtualReserveMember.State.TRAINING
            member.next_acceleration_at = member.next_acceleration_at or now
            if _demand_fill_is_due(demand, now=now) and member.next_acceleration_at > now + POST_FILL_GROWTH_INTERVAL:
                member.next_acceleration_at = now
        elif evaluation.snapshots:
            member.state = ArenaVirtualReserveMember.State.EXHAUSTED
            member.next_acceleration_at = None
        else:
            member.delete()
            continue
        member.save(
            update_fields=[
                "state",
                "evaluated_version",
                "current_lineup_power",
                "next_acceleration_at",
                "last_checked_at",
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


def _evaluate_profile_for_demand(demand: ArenaVirtualDemand, profile: BotProfile):
    mode = "tournament" if demand.tournament_id is not None else "coop"
    event_id = demand.tournament_id or demand.coop_event_id
    return evaluate_bot_lineup(
        profile,
        mode=mode,
        event_id=int(event_id or 0),
        target_guest_count=demand.target_guest_count,
        target_team_power=demand.target_team_power,
    )


def _trim_surplus_members(demand: ArenaVirtualDemand) -> None:
    members = list(demand.reserve_members.exclude(state=ArenaVirtualReserveMember.State.EXHAUSTED).order_by("id"))
    surplus = len(members) - max(0, int(demand.reserve_target_count))
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
    if demand.next_retry_at is not None and demand.next_retry_at > current_time + timedelta(seconds=1):
        return ReserveReplenishmentResult(0, 0, 0, 0, 0)

    reevaluate_existing_members(demand, now=current_time)
    _trim_surplus_members(demand)
    active_member_count = demand.reserve_members.exclude(state=ArenaVirtualReserveMember.State.EXHAUSTED).count()
    slots_needed = max(0, int(demand.reserve_target_count) - active_member_count)
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
    creation_budget = max(0, int(demand.max_reserve_target_count) - int(demand.created_profile_count))
    creation_needed = min(max(0, slots_needed), creation_budget)
    result = ReserveReplenishmentResult(
        ready_count=ready_count,
        training_count=training_count,
        recovered_abandoned=recovered_abandoned,
        recovered_retired=recovered_retired,
        creation_needed=creation_needed,
    )
    log_demand_event(
        "arena_virtual_reserve_replenished",
        demand,
        message="arena virtual reserve replenished",
        recovered_abandoned=int(recovered_abandoned),
        recovered_retired=int(recovered_retired),
        creation_needed=int(creation_needed),
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
    growth_targets: dict[tuple[int, int], ArenaVirtualGrowthTarget],
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
        target_key = (int(demand.id), int(demand.version))
        growth_target = growth_targets.get(target_key)
        if growth_target is None:
            growth_target = _growth_target_for_demand(demand)
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
    member.save(update_fields=[*_GROWTH_CLAIM_FIELDS, "updated_at"])
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
        member.next_acceleration_at = handled_at + timedelta(minutes=5)
        member.last_checked_at = handled_at
        _clear_growth_claim(member)
        member.save(
            update_fields=[
                "next_acceleration_at",
                "last_checked_at",
                *_GROWTH_CLAIM_FIELDS,
                "updated_at",
            ]
        )
        log_demand_event(
            "arena_virtual_profile_growth_deferred",
            demand,
            message="arena virtual profile growth deferred",
            failure_reason="profile_busy",
            profile_id=claim.profile_id,
            power_before=power_before,
            growth_rounds=int(member.accelerated_growth_rounds),
            member_state=str(member.state),
        )
        return True
    if growth_outcome is AcceleratedGrowthOutcome.PAUSED:
        member.next_acceleration_at = handled_at + timedelta(minutes=5)
        member.last_checked_at = handled_at
        _clear_growth_claim(member)
        member.save(
            update_fields=[
                "next_acceleration_at",
                "last_checked_at",
                *_GROWTH_CLAIM_FIELDS,
                "updated_at",
            ]
        )
        log_demand_event(
            "arena_virtual_profile_growth_deferred",
            demand,
            message="arena virtual profile growth deferred",
            failure_reason="growth_paused",
            profile_id=claim.profile_id,
            power_before=power_before,
            growth_rounds=int(member.accelerated_growth_rounds),
            member_state=str(member.state),
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
        if handled_at >= deadline:
            member.state = ArenaVirtualReserveMember.State.EXHAUSTED
            member.next_acceleration_at = None
            event = "arena_virtual_profile_exhausted"
            message = "arena virtual profile training exhausted"
            event_fields: dict[str, Any] = {
                "failure_reason": "no_action_lease_deadline",
                "power_after": power_before,
            }
        else:
            member.next_acceleration_at = min(
                deadline,
                handled_at + _growth_interval_for_demand(demand, now=handled_at),
            )
            event = "arena_virtual_profile_growth_deferred"
            message = "arena virtual profile growth deferred"
            event_fields = {
                "failure_reason": "growth_no_action",
                "lease_deadline": deadline.isoformat(),
            }
        _clear_growth_claim(member)
        member.save(
            update_fields=[
                "state",
                "next_acceleration_at",
                "last_checked_at",
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
    exhausted_reason = ""
    if evaluation.is_ready:
        member.state = ArenaVirtualReserveMember.State.READY
        member.next_acceleration_at = None
    elif member.accelerated_growth_rounds >= MAX_ACCELERATED_GROWTH_ROUNDS:
        member.state = ArenaVirtualReserveMember.State.EXHAUSTED
        member.next_acceleration_at = None
        exhausted_reason = "growth_round_limit"
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
    growth_targets: dict[tuple[int, int], ArenaVirtualGrowthTarget] = {}
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
        lease_check_at = max(timezone.now(), current_time)
        if _finalize_virtual_reserve_growth(
            claim,
            growth_outcome=growth_outcome,
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
        needed = min(replenishment.creation_needed, remaining_limit - created)
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

                active_slots = claimed_demand.reserve_members.exclude(
                    state=ArenaVirtualReserveMember.State.EXHAUSTED
                ).count()
                if (
                    claimed_demand.status != ArenaVirtualDemand.Status.ACTIVE
                    or active_slots >= claimed_demand.reserve_target_count
                ):
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
    "MAX_NO_ACTION_LEASE_AGE",
    "ReserveReplenishmentResult",
    "create_due_virtual_reserve_profiles",
    "evaluate_bot_lineup",
    "grow_due_virtual_reserves",
    "reevaluate_existing_members",
    "release_virtual_reserve_member_for_manor",
    "release_virtual_reserve_members_for_demand",
    "replenish_virtual_reserve",
]
