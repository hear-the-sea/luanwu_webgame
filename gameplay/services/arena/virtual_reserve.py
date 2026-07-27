from __future__ import annotations

import logging
import random
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import timedelta
from hashlib import blake2b

from django.db import transaction
from django.db.models import F, Prefetch
from django.utils import timezone

from common.utils.celery import safe_apply_async
from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaTournament,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
    BotProfile,
)
from gameplay.services.virtual_player_state_policy import (
    VIRTUAL_PROFILE_ARENA_ELIGIBLE_STATES,
    is_virtual_profile_arena_eligible,
)
from gameplay.services.virtual_players import (
    AcceleratedGrowthOutcome,
    BotProjectionConfig,
    PopulationMutationStatus,
    accelerate_virtual_player_growth,
    create_virtual_player_with_capacity,
    get_virtual_player_capacity,
    reactivate_retired_virtual_player_with_capacity,
    reactivate_virtual_player_profile,
    virtual_player_prestige_bands,
)
from guests.models import Guest, GuestRarity, GuestStatus

from .virtual_backfill import (
    _lineup_power,
    _median_entry,
    _reference_snapshots,
    backfill_coop_event_locked,
    backfill_tournament_locked,
    evaluate_bot_lineup,
)

logger = logging.getLogger(__name__)

RESERVE_MULTIPLIER = 3
RESERVE_MINIMUM = 6
PARTICIPATION_COOLDOWN = timedelta(hours=24)
MAX_ACCELERATED_GROWTH_ROUNDS = 8
ARENA_MAX_GUEST_LEVEL_STEP = 20
PRE_FILL_GROWTH_INTERVAL = timedelta(hours=1)
POST_FILL_GROWTH_INTERVAL = timedelta(minutes=15)
_GUEST_RARITY_RANK = {rarity.value: index for index, rarity in enumerate(GuestRarity)}


@dataclass(frozen=True)
class ReserveReplenishmentResult:
    ready_count: int
    training_count: int
    recovered_abandoned: int
    recovered_retired: int
    creation_needed: int


@dataclass(frozen=True)
class ArenaVirtualGrowthTarget:
    minimum_guest_count: int
    minimum_guest_level: int
    guest_rarity_cap: str | None


class _AtomicFillAborted(RuntimeError):
    def __init__(self, demand_id: int, reason: str):
        super().__init__(reason)
        self.demand_id = int(demand_id)
        self.reason = str(reason)


class _CandidateLeaseRejected(RuntimeError):
    pass


def _demand_mode_and_event_id(demand: ArenaVirtualDemand) -> tuple[str, int]:
    if demand.tournament_id is not None:
        return "tournament", int(demand.tournament_id)
    return "coop", int(demand.coop_event_id or 0)


def _log_demand_event(
    event_name: str,
    demand: ArenaVirtualDemand,
    *,
    message: str,
    level: int = logging.INFO,
    failure_reason: str | None = None,
    **details,
) -> None:
    mode, event_id = _demand_mode_and_event_id(demand)
    ready_count = demand.reserve_members.filter(
        state=ArenaVirtualReserveMember.State.READY,
    ).count()
    training_count = demand.reserve_members.filter(
        state=ArenaVirtualReserveMember.State.TRAINING,
    ).count()
    extra = {
        "event": event_name,
        "mode": mode,
        "event_id": event_id,
        "demand_id": int(demand.id),
        "demand_version": int(demand.version),
        "missing_entry_count": int(demand.missing_entry_count),
        "reserve_target_count": int(demand.reserve_target_count),
        "ready_count": int(ready_count),
        "training_count": int(training_count),
        "failure_reason": str(demand.last_failure_reason if failure_reason is None else failure_reason),
    }
    extra.update(details)
    logger.log(level, message, extra=extra)


def queue_virtual_reserve_reconcile(mode: str, event_id: int) -> bool:
    from gameplay.tasks.arena import reconcile_arena_virtual_reserve

    return safe_apply_async(
        reconcile_arena_virtual_reserve,
        args=[str(mode), int(event_id)],
        logger=logger,
        log_message="arena virtual reserve reconcile dispatch failed; relying on periodic scan",
        log_extra={
            "event": "arena_virtual_reconcile_dispatch_deferred",
            "mode": str(mode),
            "event_id": int(event_id),
        },
    )


def _reserve_target(missing: int) -> int:
    normalized = max(0, int(missing))
    return 0 if normalized == 0 else max(normalized * RESERVE_MULTIPLIER, RESERVE_MINIMUM)


def _reference_snapshots_for_demand(demand: ArenaVirtualDemand) -> list[dict]:
    real_entries: Sequence[ArenaEntry | ArenaCoopEntry]
    if demand.tournament_id is not None:
        real_entries = list(
            ArenaEntry.objects.filter(
                tournament_id=demand.tournament_id,
                status=ArenaEntry.Status.REGISTERED,
                source=ArenaEntry.Source.PLAYER,
            ).prefetch_related("entry_guests")
        )
    else:
        real_entries = list(
            ArenaCoopEntry.objects.filter(
                event_id=demand.coop_event_id,
                status=ArenaCoopEntry.Status.REGISTERED,
                source=ArenaCoopEntry.Source.PLAYER,
            ).prefetch_related("entry_guests")
        )
    if not real_entries:
        return []
    snapshots = _reference_snapshots(_median_entry(real_entries))
    return snapshots[: max(0, int(demand.target_guest_count))]


def _growth_target_for_demand(demand: ArenaVirtualDemand) -> ArenaVirtualGrowthTarget:
    snapshots = _reference_snapshots_for_demand(demand)
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


def close_virtual_demand_locked(demand: ArenaVirtualDemand, *, status: str) -> None:
    demand.reserve_members.all().delete()
    demand.status = status
    demand.missing_entry_count = 0
    demand.reserve_target_count = 0
    demand.next_retry_at = None
    demand.save(
        update_fields=[
            "status",
            "missing_entry_count",
            "reserve_target_count",
            "next_retry_at",
            "updated_at",
        ]
    )


def _reevaluate_existing_members(demand: ArenaVirtualDemand, *, now) -> None:
    mode = "tournament" if demand.tournament_id is not None else "coop"
    event_id = demand.tournament_id or demand.coop_event_id
    if event_id is None:
        return

    members = list(
        demand.reserve_members.select_for_update().select_related("profile", "profile__manor").order_by("id")
    )
    for member in members:
        if not is_virtual_profile_arena_eligible(member.profile):
            member.delete()
            continue
        member.evaluated_version = demand.version
        member.last_checked_at = now
        if (
            member.state == ArenaVirtualReserveMember.State.EXHAUSTED
            and member.accelerated_growth_rounds >= MAX_ACCELERATED_GROWTH_ROUNDS
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
    return (
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
        members,
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
                profile = (
                    BotProfile.objects.select_for_update(skip_locked=True)
                    .filter(
                        pk=profile_id,
                        state__in=list(allowed_states),
                        arena_virtual_reserve__isnull=True,
                    )
                    .exclude(manor_id__in=_occupied_arena_manor_ids())
                    .select_related("manor")
                    .first()
                )
            if profile is None:
                return None
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
                _log_demand_event(
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

    _reevaluate_existing_members(demand, now=current_time)
    _trim_surplus_members(demand)
    active_member_count = demand.reserve_members.exclude(state=ArenaVirtualReserveMember.State.EXHAUSTED).count()
    slots_needed = max(0, int(demand.reserve_target_count) - active_member_count)
    recovered_abandoned = 0
    recovered_retired = 0
    training_candidates: list[tuple[int, int]] = []

    for profile in _candidate_queryset(VIRTUAL_PROFILE_ARENA_ELIGIBLE_STATES).iterator(chunk_size=100):
        if slots_needed <= 0:
            break
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
                slots_needed -= 1
        elif evaluation.snapshots:
            training_candidates.append((evaluation.selected_power, profile.id))

    if slots_needed > 0:
        for profile in _candidate_queryset([BotProfile.State.ABANDONED]).iterator(chunk_size=100):
            if slots_needed <= 0:
                break
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
                recovered_abandoned += 1
                slots_needed -= 1

    if slots_needed > 0:
        hard_cap, maintained_count = get_virtual_player_capacity(now=current_time)
        for profile in _candidate_queryset([BotProfile.State.RETIRED]).iterator(chunk_size=100):
            if slots_needed <= 0 or (hard_cap > 0 and maintained_count >= hard_cap):
                break
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
            slots_needed -= 1

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
    _log_demand_event(
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


def grow_due_virtual_reserves(*, now=None, limit: int = 100) -> int:
    current_time = now or timezone.now()
    member_ids = list(
        ArenaVirtualReserveMember.objects.filter(
            state=ArenaVirtualReserveMember.State.TRAINING,
            next_acceleration_at__lte=current_time,
            demand__status=ArenaVirtualDemand.Status.ACTIVE,
        )
        .order_by("next_acceleration_at", "id")
        .values_list("id", flat=True)[: max(0, int(limit))]
    )
    processed = 0
    growth_targets: dict[int, ArenaVirtualGrowthTarget] = {}
    for member_id in member_ids:
        with transaction.atomic():
            member = (
                ArenaVirtualReserveMember.objects.select_for_update(skip_locked=True)
                .select_related(
                    "demand",
                    "demand__tournament",
                    "demand__coop_event",
                    "profile",
                    "profile__manor",
                )
                .filter(
                    pk=member_id,
                    state=ArenaVirtualReserveMember.State.TRAINING,
                    next_acceleration_at__lte=current_time,
                    demand__status=ArenaVirtualDemand.Status.ACTIVE,
                )
                .first()
            )
            if member is None:
                continue

            power_before = int(member.current_lineup_power)
            exhausted_reason = ""
            growth_target = growth_targets.get(member.demand_id)
            if growth_target is None:
                growth_target = _growth_target_for_demand(member.demand)
                growth_targets[member.demand_id] = growth_target
            growth_outcome = accelerate_virtual_player_growth(
                member.profile_id,
                now=current_time,
                minimum_guest_count=growth_target.minimum_guest_count,
                minimum_guest_level=growth_target.minimum_guest_level,
                guest_rarity_cap=growth_target.guest_rarity_cap,
                max_guest_level_step=ARENA_MAX_GUEST_LEVEL_STEP,
            )
            if growth_outcome is AcceleratedGrowthOutcome.BUSY:
                member.next_acceleration_at = current_time + timedelta(minutes=5)
                member.last_checked_at = current_time
                member.save(
                    update_fields=[
                        "next_acceleration_at",
                        "last_checked_at",
                        "updated_at",
                    ]
                )
                _log_demand_event(
                    "arena_virtual_profile_growth_deferred",
                    member.demand,
                    message="arena virtual profile growth deferred",
                    failure_reason="profile_busy",
                    profile_id=int(member.profile_id),
                    power_before=power_before,
                    growth_rounds=int(member.accelerated_growth_rounds),
                    member_state=str(member.state),
                )
                processed += 1
                continue
            if growth_outcome is AcceleratedGrowthOutcome.INELIGIBLE:
                demand = member.demand
                profile_id = member.profile_id
                growth_rounds = int(member.accelerated_growth_rounds)
                member.delete()
                _log_demand_event(
                    "arena_virtual_profile_released",
                    demand,
                    message="arena virtual profile released from training",
                    failure_reason="growth_ineligible",
                    profile_id=int(profile_id),
                    power_before=power_before,
                    power_after=power_before,
                    growth_rounds=growth_rounds,
                    member_state="released",
                )
                processed += 1
                continue
            if growth_outcome is not AcceleratedGrowthOutcome.GROWN:
                raise ValueError(f"Unsupported accelerated growth outcome: {growth_outcome!r}")

            evaluation = _evaluate_member(member)
            if not evaluation.snapshots:
                demand = member.demand
                profile_id = member.profile_id
                growth_rounds = int(member.accelerated_growth_rounds)
                member.delete()
                _log_demand_event(
                    "arena_virtual_profile_exhausted",
                    demand,
                    message="arena virtual profile removed from training",
                    failure_reason="no_valid_lineup",
                    profile_id=int(profile_id),
                    power_before=power_before,
                    power_after=0,
                    growth_rounds=growth_rounds,
                    member_state="released",
                )
                processed += 1
                continue
            member.current_lineup_power = evaluation.selected_power
            member.accelerated_growth_rounds += 1
            member.evaluated_version = member.demand.version
            if evaluation.is_ready:
                member.state = ArenaVirtualReserveMember.State.READY
                member.next_acceleration_at = None
            elif member.accelerated_growth_rounds >= MAX_ACCELERATED_GROWTH_ROUNDS:
                member.state = ArenaVirtualReserveMember.State.EXHAUSTED
                member.next_acceleration_at = None
                exhausted_reason = "growth_round_limit"
            else:
                member.next_acceleration_at = current_time + _growth_interval_for_demand(
                    member.demand,
                    now=current_time,
                )
            member.last_checked_at = current_time
            member.save(
                update_fields=[
                    "state",
                    "evaluated_version",
                    "current_lineup_power",
                    "accelerated_growth_rounds",
                    "next_acceleration_at",
                    "last_checked_at",
                    "updated_at",
                ]
            )
            _log_demand_event(
                "arena_virtual_profile_grown",
                member.demand,
                message="arena virtual profile growth processed",
                profile_id=int(member.profile_id),
                power_before=power_before,
                power_after=int(member.current_lineup_power),
                growth_rounds=int(member.accelerated_growth_rounds),
                member_state=str(member.state),
            )
            if exhausted_reason:
                _log_demand_event(
                    "arena_virtual_profile_exhausted",
                    member.demand,
                    message="arena virtual profile training exhausted",
                    failure_reason=exhausted_reason,
                    profile_id=int(member.profile_id),
                    power_before=power_before,
                    power_after=int(member.current_lineup_power),
                    growth_rounds=int(member.accelerated_growth_rounds),
                    member_state=str(member.state),
                )
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
        prestige = int(_median_entry(real_entries).manor.prestige or 0)
        for band_name, (low, high) in bands.items():
            if prestige >= low and (high is None or prestige < high):
                return band_name
    for band_name, (low, high) in bands.items():
        if 0 >= low and (high is None or 0 < high):
            return band_name
    return next(iter(bands), "newbie")


def create_due_virtual_reserve_profiles(*, now=None, limit: int = 100) -> int:
    current_time = now or timezone.now()
    remaining_limit = max(0, int(limit))
    if remaining_limit <= 0:
        return 0
    demand_ids = list(
        ArenaVirtualDemand.objects.filter(status=ArenaVirtualDemand.Status.ACTIVE)
        .filter(created_profile_count__lt=F("max_reserve_target_count"))
        .order_by("next_retry_at", "id")
        .values_list("id", flat=True)
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
                    _log_demand_event(
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
                    _log_demand_event(
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
            _log_demand_event(
                "arena_virtual_profile_created",
                claimed_demand,
                message="arena virtual profile created",
                profile_id=int(profile.id),
                region=str(profile.manor.region),
                target_prestige_band=str(target_band),
                actual_prestige=int(profile.manor.prestige),
            )
    return created


def _stable_member_rank(*, mode: str, event_id: int, version: int, profile_id: int) -> bytes:
    payload = f"{mode}:{event_id}:{version}:{profile_id}".encode("utf-8")
    return blake2b(payload, digest_size=16).digest()


def _ordered_ready_members(
    demand: ArenaVirtualDemand,
    *,
    now,
) -> list[ArenaVirtualReserveMember]:
    members = list(
        demand.reserve_members.filter(state=ArenaVirtualReserveMember.State.READY).select_related(
            "profile", "profile__manor"
        )
    )
    mode = "tournament" if demand.tournament_id is not None else "coop"
    event_id = int(demand.tournament_id or demand.coop_event_id or 0)
    cutoff = now - PARTICIPATION_COOLDOWN
    fresh = [
        member
        for member in members
        if member.profile.last_arena_participated_at is None or member.profile.last_arena_participated_at < cutoff
    ]
    recent = [
        member
        for member in members
        if member.profile.last_arena_participated_at is not None and member.profile.last_arena_participated_at >= cutoff
    ]
    fresh.sort(
        key=lambda member: _stable_member_rank(
            mode=mode,
            event_id=event_id,
            version=demand.version,
            profile_id=member.profile_id,
        )
    )
    recent.sort(
        key=lambda member: (
            member.profile.last_arena_participated_at,
            _stable_member_rank(
                mode=mode,
                event_id=event_id,
                version=demand.version,
                profile_id=member.profile_id,
            ),
        )
    )
    return fresh + recent


def _record_fill_deferred(*, demand_id: int, reason: str, now) -> None:
    with transaction.atomic():
        demand = (
            ArenaVirtualDemand.objects.select_for_update()
            .filter(pk=demand_id, status=ArenaVirtualDemand.Status.ACTIVE)
            .first()
        )
        if demand is None:
            return
        demand.last_failure_reason = reason[:64]
        demand.last_checked_at = now
        demand.next_retry_at = now + timedelta(minutes=5)
        demand.save(
            update_fields=[
                "last_failure_reason",
                "last_checked_at",
                "next_retry_at",
                "updated_at",
            ]
        )
        _log_demand_event(
            "arena_virtual_fill_deferred",
            demand,
            message="arena virtual fill deferred",
            level=logging.WARNING,
            failure_reason=reason,
        )


def _lock_selected_ready_profiles(
    *,
    demand: ArenaVirtualDemand,
    profile_ids: Sequence[int],
) -> bool:
    locked_ids = set(
        BotProfile.objects.select_for_update(skip_locked=True)
        .filter(
            id__in=list(profile_ids),
            state__in=VIRTUAL_PROFILE_ARENA_ELIGIBLE_STATES,
            arena_virtual_reserve__demand=demand,
            arena_virtual_reserve__state=ArenaVirtualReserveMember.State.READY,
        )
        .values_list("id", flat=True)
    )
    return locked_ids == set(profile_ids)


def _complete_demand_fill(
    *,
    demand: ArenaVirtualDemand,
    profile_ids: Sequence[int],
    now,
    used_cooldown: bool = False,
) -> None:
    wait_seconds = max(0.0, (now - demand.created_at).total_seconds())
    BotProfile.objects.filter(id__in=list(profile_ids)).update(
        last_arena_participated_at=now,
        arena_participation_count=F("arena_participation_count") + 1,
    )
    demand.reserve_members.all().delete()
    demand.status = ArenaVirtualDemand.Status.SATISFIED
    demand.missing_entry_count = 0
    demand.reserve_target_count = 0
    demand.next_retry_at = None
    demand.last_checked_at = now
    demand.last_failure_reason = ""
    demand.save(
        update_fields=[
            "status",
            "missing_entry_count",
            "reserve_target_count",
            "next_retry_at",
            "last_checked_at",
            "last_failure_reason",
            "updated_at",
        ]
    )
    _log_demand_event(
        "arena_virtual_fill_completed",
        demand,
        message="arena virtual fill completed",
        selected_profile_ids=[int(profile_id) for profile_id in profile_ids],
        used_cooldown=bool(used_cooldown),
        wait_seconds=wait_seconds,
    )


def fill_due_tournament_reserve(tournament_id: int, *, now=None) -> int:
    current_time = now or timezone.now()
    try:
        with transaction.atomic():
            tournament = ArenaTournament.objects.select_for_update().filter(pk=tournament_id).first()
            if (
                tournament is None
                or tournament.status != ArenaTournament.Status.RECRUITING
                or tournament.virtual_fill_completed
                or tournament.virtual_fill_at is None
                or tournament.virtual_fill_at > current_time
            ):
                return 0
            demand = reconcile_tournament_demand_locked(tournament, now=current_time)
            if demand is None:
                return 0
            demand = ArenaVirtualDemand.objects.select_for_update().get(pk=demand.pk)
            gap = max(0, int(demand.missing_entry_count))
            if gap <= 0:
                return 0
            ordered_members = _ordered_ready_members(demand, now=current_time)
            if len(ordered_members) < gap:
                demand.last_failure_reason = "insufficient_ready_members"
                demand.last_checked_at = current_time
                demand.next_retry_at = current_time + timedelta(minutes=5)
                demand.save(
                    update_fields=[
                        "last_failure_reason",
                        "last_checked_at",
                        "next_retry_at",
                        "updated_at",
                    ]
                )
                _log_demand_event(
                    "arena_virtual_fill_deferred",
                    demand,
                    message="arena virtual fill deferred",
                    level=logging.WARNING,
                    failure_reason="insufficient_ready_members",
                )
                return 0
            selected_members = ordered_members[:gap]
            profile_ids = [member.profile_id for member in selected_members]
            cooldown_cutoff = current_time - PARTICIPATION_COOLDOWN
            used_cooldown = any(
                member.profile.last_arena_participated_at is not None
                and member.profile.last_arena_participated_at >= cooldown_cutoff
                for member in selected_members
            )
            if not _lock_selected_ready_profiles(demand=demand, profile_ids=profile_ids):
                raise _AtomicFillAborted(demand.id, "ready_member_lock_failed")
            filled = backfill_tournament_locked(
                tournament,
                candidate_profile_ids=profile_ids,
            )
            if filled != gap:
                raise _AtomicFillAborted(demand.id, "ready_member_revalidation_failed")
            from .core import _start_tournament_locked

            if not _start_tournament_locked(tournament, now=current_time):
                raise _AtomicFillAborted(demand.id, "tournament_start_failed")
            _complete_demand_fill(
                demand=demand,
                profile_ids=profile_ids,
                now=current_time,
                used_cooldown=used_cooldown,
            )
            return filled
    except _AtomicFillAborted as exc:
        _record_fill_deferred(demand_id=exc.demand_id, reason=exc.reason, now=current_time)
        return 0


def fill_due_coop_reserve(event_id: int, *, now=None) -> int:
    current_time = now or timezone.now()
    try:
        with transaction.atomic():
            event = ArenaCoopEvent.objects.select_for_update().filter(pk=event_id).first()
            if (
                event is None
                or event.status != ArenaCoopEvent.Status.RECRUITING
                or event.virtual_fill_completed
                or event.virtual_fill_at is None
                or event.virtual_fill_at > current_time
            ):
                return 0
            demand = reconcile_coop_demand_locked(event, now=current_time)
            if demand is None:
                return 0
            demand = ArenaVirtualDemand.objects.select_for_update().get(pk=demand.pk)
            gap = max(0, int(demand.missing_entry_count))
            if gap <= 0:
                return 0
            ordered_members = _ordered_ready_members(demand, now=current_time)
            if len(ordered_members) < gap:
                demand.last_failure_reason = "insufficient_ready_members"
                demand.last_checked_at = current_time
                demand.next_retry_at = current_time + timedelta(minutes=5)
                demand.save(
                    update_fields=[
                        "last_failure_reason",
                        "last_checked_at",
                        "next_retry_at",
                        "updated_at",
                    ]
                )
                _log_demand_event(
                    "arena_virtual_fill_deferred",
                    demand,
                    message="arena virtual fill deferred",
                    level=logging.WARNING,
                    failure_reason="insufficient_ready_members",
                )
                return 0
            selected_members = ordered_members[:gap]
            profile_ids = [member.profile_id for member in selected_members]
            cooldown_cutoff = current_time - PARTICIPATION_COOLDOWN
            used_cooldown = any(
                member.profile.last_arena_participated_at is not None
                and member.profile.last_arena_participated_at >= cooldown_cutoff
                for member in selected_members
            )
            if not _lock_selected_ready_profiles(demand=demand, profile_ids=profile_ids):
                raise _AtomicFillAborted(demand.id, "ready_member_lock_failed")
            filled = backfill_coop_event_locked(
                event,
                candidate_profile_ids=profile_ids,
            )
            if filled != gap:
                raise _AtomicFillAborted(demand.id, "ready_member_revalidation_failed")
            from .coop_lifecycle import move_event_to_preparing_locked

            if not move_event_to_preparing_locked(event, now=current_time):
                raise _AtomicFillAborted(demand.id, "coop_prepare_failed")
            _complete_demand_fill(
                demand=demand,
                profile_ids=profile_ids,
                now=current_time,
                used_cooldown=used_cooldown,
            )
            return filled
    except _AtomicFillAborted as exc:
        _record_fill_deferred(demand_id=exc.demand_id, reason=exc.reason, now=current_time)
        return 0


def scan_virtual_reserve_demands(*, now=None, limit: int = 20) -> dict[str, int]:
    current_time = now or timezone.now()
    normalized_limit = max(0, int(limit))
    result = {
        "scanned": 0,
        "reconciled": 0,
        "ready": 0,
        "training": 0,
        "filled_entries": 0,
    }
    if normalized_limit <= 0:
        return result

    candidates: dict[tuple[str, int], float] = {}

    def _add_candidate(mode: str, event_id: int, due_at, created_at) -> None:
        priority_at = due_at or created_at
        candidates[(mode, int(event_id))] = float("inf") if priority_at is None else float(priority_at.timestamp())

    active_demands = list(
        ArenaVirtualDemand.objects.filter(status=ArenaVirtualDemand.Status.ACTIVE)
        .select_related("tournament", "coop_event")
        .order_by("next_retry_at", "id")[:normalized_limit]
    )
    for demand in active_demands:
        if demand.tournament_id is not None and demand.tournament is not None:
            _add_candidate(
                "tournament",
                demand.tournament_id,
                demand.tournament.virtual_fill_at,
                demand.tournament.created_at,
            )
        elif demand.coop_event_id is not None and demand.coop_event is not None:
            _add_candidate(
                "coop",
                demand.coop_event_id,
                demand.coop_event.virtual_fill_at,
                demand.coop_event.created_at,
            )

    tournament_rows = list(
        ArenaTournament.objects.filter(
            status=ArenaTournament.Status.RECRUITING,
            entries__status=ArenaEntry.Status.REGISTERED,
            entries__source=ArenaEntry.Source.PLAYER,
        )
        .distinct()
        .order_by(F("virtual_fill_at").asc(nulls_last=True), "created_at", "id")
        .values_list("id", "virtual_fill_at", "created_at")[:normalized_limit]
    )
    for event_id, due_at, created_at in tournament_rows:
        _add_candidate("tournament", event_id, due_at, created_at)

    coop_rows = list(
        ArenaCoopEvent.objects.filter(
            status=ArenaCoopEvent.Status.RECRUITING,
            entries__status=ArenaCoopEntry.Status.REGISTERED,
            entries__source=ArenaCoopEntry.Source.PLAYER,
        )
        .distinct()
        .order_by(F("virtual_fill_at").asc(nulls_last=True), "created_at", "id")
        .values_list("id", "virtual_fill_at", "created_at")[:normalized_limit]
    )
    for event_id, due_at, created_at in coop_rows:
        _add_candidate("coop", event_id, due_at, created_at)

    ordered_candidates = sorted(
        candidates,
        key=lambda item: (candidates[item], item[0], item[1]),
    )[:normalized_limit]
    result["scanned"] = len(ordered_candidates)
    for mode, event_id in ordered_candidates:
        if mode == "tournament":
            reconciled_demand = reconcile_tournament_demand(event_id, now=current_time)
        elif mode == "coop":
            reconciled_demand = reconcile_coop_demand(event_id, now=current_time)
        else:
            continue
        if reconciled_demand is None:
            continue

        result["reconciled"] += 1
        replenished = replenish_virtual_reserve(reconciled_demand.id, now=current_time)
        result["ready"] += int(replenished.ready_count)
        result["training"] += int(replenished.training_count)
        if mode == "tournament":
            result["filled_entries"] += fill_due_tournament_reserve(
                event_id,
                now=current_time,
            )
        else:
            result["filled_entries"] += fill_due_coop_reserve(
                event_id,
                now=current_time,
            )
    return result


def _upsert_demand_locked(
    *,
    tournament: ArenaTournament | None = None,
    coop_event: ArenaCoopEvent | None = None,
    target_guest_count: int,
    target_team_power: int,
    missing_entry_count: int,
    now,
) -> ArenaVirtualDemand:
    lookup: dict[str, ArenaTournament | ArenaCoopEvent | None] = (
        {"tournament": tournament} if tournament is not None else {"coop_event": coop_event}
    )
    demand = ArenaVirtualDemand.objects.select_for_update().filter(**lookup).first()
    reserve_target_count = _reserve_target(missing_entry_count)
    if demand is None:
        demand = ArenaVirtualDemand.objects.create(
            **lookup,
            status=ArenaVirtualDemand.Status.ACTIVE,
            target_guest_count=target_guest_count,
            target_team_power=target_team_power,
            missing_entry_count=missing_entry_count,
            reserve_target_count=reserve_target_count,
            max_reserve_target_count=reserve_target_count,
            next_retry_at=now,
            last_checked_at=now,
        )
        _log_demand_event(
            "arena_virtual_demand_reconciled",
            demand,
            message="arena virtual demand reconciled",
            demand_created=True,
        )
        return demand

    changed = (
        demand.target_guest_count != target_guest_count
        or demand.target_team_power != target_team_power
        or demand.missing_entry_count != missing_entry_count
        or demand.reserve_target_count != reserve_target_count
    )
    if changed:
        demand.version += 1
    demand.status = ArenaVirtualDemand.Status.ACTIVE
    demand.target_guest_count = target_guest_count
    demand.target_team_power = target_team_power
    demand.missing_entry_count = missing_entry_count
    demand.reserve_target_count = reserve_target_count
    demand.max_reserve_target_count = max(demand.max_reserve_target_count, reserve_target_count)
    demand.next_retry_at = now
    demand.last_checked_at = now
    demand.last_failure_reason = ""
    demand.save(
        update_fields=[
            "status",
            "version",
            "target_guest_count",
            "target_team_power",
            "missing_entry_count",
            "reserve_target_count",
            "max_reserve_target_count",
            "next_retry_at",
            "last_checked_at",
            "last_failure_reason",
            "updated_at",
        ]
    )
    if changed:
        _reevaluate_existing_members(demand, now=now)
    _log_demand_event(
        "arena_virtual_demand_reconciled",
        demand,
        message="arena virtual demand reconciled",
        demand_created=False,
    )
    return demand


def _close_existing_tournament_demand(tournament: ArenaTournament) -> None:
    demand = ArenaVirtualDemand.objects.select_for_update().filter(tournament=tournament).first()
    if demand is not None:
        close_virtual_demand_locked(demand, status=ArenaVirtualDemand.Status.CLOSED)


def _close_existing_coop_demand(event: ArenaCoopEvent) -> None:
    demand = ArenaVirtualDemand.objects.select_for_update().filter(coop_event=event).first()
    if demand is not None:
        close_virtual_demand_locked(demand, status=ArenaVirtualDemand.Status.CLOSED)


def reconcile_tournament_demand_locked(
    tournament: ArenaTournament,
    *,
    now=None,
) -> ArenaVirtualDemand | None:
    current_time = now or timezone.now()
    if tournament.status != ArenaTournament.Status.RECRUITING:
        _close_existing_tournament_demand(tournament)
        return None

    registered_entries = tournament.entries.filter(status=ArenaEntry.Status.REGISTERED)
    real_entries = list(registered_entries.filter(source=ArenaEntry.Source.PLAYER).prefetch_related("entry_guests"))
    missing = max(0, int(tournament.player_limit) - registered_entries.count())
    if not real_entries or missing <= 0:
        _close_existing_tournament_demand(tournament)
        return None

    reference_entry = _median_entry(real_entries)
    snapshots = _reference_snapshots(reference_entry)
    return _upsert_demand_locked(
        tournament=tournament,
        target_guest_count=len(snapshots),
        target_team_power=_lineup_power(snapshots),
        missing_entry_count=missing,
        now=current_time,
    )


def reconcile_coop_demand_locked(
    event: ArenaCoopEvent,
    *,
    now=None,
) -> ArenaVirtualDemand | None:
    current_time = now or timezone.now()
    if event.status != ArenaCoopEvent.Status.RECRUITING:
        _close_existing_coop_demand(event)
        return None

    registered_entries = event.entries.filter(status=ArenaCoopEntry.Status.REGISTERED)
    real_entries = list(registered_entries.filter(source=ArenaCoopEntry.Source.PLAYER).prefetch_related("entry_guests"))
    missing = max(0, int(event.player_limit) - registered_entries.count())
    if not real_entries or missing <= 0:
        _close_existing_coop_demand(event)
        return None

    reference_entry = _median_entry(real_entries)
    snapshots = _reference_snapshots(reference_entry)[: int(event.guest_limit_per_entry)]
    return _upsert_demand_locked(
        coop_event=event,
        target_guest_count=len(snapshots),
        target_team_power=_lineup_power(snapshots),
        missing_entry_count=missing,
        now=current_time,
    )


@transaction.atomic
def reconcile_tournament_demand(tournament_id: int, *, now=None) -> ArenaVirtualDemand | None:
    tournament = ArenaTournament.objects.select_for_update().filter(pk=tournament_id).first()
    if tournament is None:
        return None
    return reconcile_tournament_demand_locked(tournament, now=now or timezone.now())


@transaction.atomic
def reconcile_coop_demand(event_id: int, *, now=None) -> ArenaVirtualDemand | None:
    event = ArenaCoopEvent.objects.select_for_update().filter(pk=event_id).first()
    if event is None:
        return None
    return reconcile_coop_demand_locked(event, now=now or timezone.now())
