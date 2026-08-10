"""Arena-facing population demand and reserve-supply integration."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from celery import current_app
from django.db.models import Prefetch
from django.utils import timezone

from common.utils.celery import safe_apply_async_with_dedup
from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEntryGuest,
    ArenaEntry,
    ArenaEntryGuest,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
)
from gameplay.services.arena.rules import load_arena_rules
from gameplay.services.arena.virtual_protection import (
    arena_protected_bot_manor_ids,
    is_virtual_profile_arena_match_eligible,
    with_arena_reconciliation_state,
)
from gameplay.services.arena.virtual_reserve_policy import assess_reserve_admission
from gameplay.services.arena.virtual_reserve_references import (
    active_arena_population_activations,
    active_arena_population_funnel_snapshots,
    median_entry,
)
from gameplay.services.arena.virtual_reserve_training_policy import (
    demand_supply_prestige_band,
    demand_supply_prestige_band_priority,
    demand_uses_arena_training_policy,
)
from gameplay.services.virtual_player_state_policy import VIRTUAL_PROFILE_ARENA_ELIGIBLE_STATES
from guests.models import Guest, GuestStatus

from .population import ArenaHandoffSupply
from .runtime_assessment import assess_virtual_player_runtime
from .selectors import prestige_band_for_value as _prestige_band_for_value
from .selectors import prestige_bands as _prestige_bands
from .selectors import profile_target_prestige_band as _profile_target_prestige_band
from .selectors import regions as _regions

logger = logging.getLogger(__name__)

ARENA_POPULATION_HANDOFF_DEDUP_SECONDS = 60


@dataclass(frozen=True, slots=True)
class ArenaPopulationFunnelObservation:
    region: str
    prestige_band: str
    demand_count: int
    materialization_need: int
    raw_materialization_need: int
    suppressed_materialization_need: int
    handoff_available: int
    population_materialization_additional: int
    warm_target_count: int
    replacement_target_count: int
    admission_attempt_high_water: int
    admission_high_water_lag_count: int
    ready_count: int
    training_count: int
    exhausted_count: int
    growth_attempt_count: int
    growth_applied_count: int
    effective_progress_count: int
    selected_growth_bps_total: int
    selected_growth_bps_max: int
    invalid_growth_budget_count: int
    oldest_ready_member_age_seconds: int
    oldest_training_member_age_seconds: int
    oldest_exhausted_member_age_seconds: int
    guard_reason_counts: tuple[tuple[str, int], ...]
    retry_reason_counts: tuple[tuple[str, int], ...]


@dataclass(slots=True)
class _ArenaPopulationFunnelCell:
    demand_count: int = 0
    materialization_need: int = 0
    raw_materialization_need: int = 0
    suppressed_materialization_need: int = 0
    warm_target_count: int = 0
    replacement_target_count: int = 0
    admission_attempt_high_water: int = 0
    admission_high_water_lag_count: int = 0
    ready_count: int = 0
    training_count: int = 0
    exhausted_count: int = 0
    growth_attempt_count: int = 0
    growth_applied_count: int = 0
    effective_progress_count: int = 0
    selected_growth_bps_total: int = 0
    selected_growth_bps_max: int = 0
    invalid_growth_budget_count: int = 0
    oldest_ready_member_age_seconds: int = 0
    oldest_training_member_age_seconds: int = 0
    oldest_exhausted_member_age_seconds: int = 0
    guard_reason_counts: Counter[str] = field(default_factory=Counter)
    retry_reason_counts: Counter[str] = field(default_factory=Counter)


@dataclass(slots=True)
class _ArenaHandoffDemandContext:
    demand: ArenaVirtualDemand
    cell: tuple[str, str]
    supply_prestige_band_priority: tuple[str, ...]
    mode: str
    event_id: int
    target_guest_count: int
    target_team_power: int
    max_lineup_size: int
    remaining: int


def active_arena_population_demand_by_cell(
    config: dict[str, Any],
) -> dict[tuple[str, str], int]:
    """Translate bounded Arena handoff requests into V2 population cells."""

    valid_regions = frozenset(_regions())
    arena_demands: dict[tuple[str, str], int] = {}
    for activation in active_arena_population_activations():
        region = str(activation.region)
        band_name = _prestige_band_for_value(activation.prestige, config)
        if region not in valid_regions or band_name is None:
            continue
        key = (region, band_name)
        arena_demands[key] = arena_demands.get(key, 0) + max(0, int(activation.needed or 0))
    return arena_demands


def _active_arena_population_funnel_by_cell(
    config: dict[str, Any],
    *,
    now: datetime,
) -> dict[tuple[str, str], _ArenaPopulationFunnelCell]:
    valid_regions = frozenset(_regions())
    cells: dict[tuple[str, str], _ArenaPopulationFunnelCell] = {}
    for snapshot in active_arena_population_funnel_snapshots(now=now):
        region = str(snapshot.region)
        prestige_band = _prestige_band_for_value(snapshot.prestige, config)
        if region not in valid_regions or prestige_band is None:
            continue
        key = (region, prestige_band)
        cell = cells.setdefault(key, _ArenaPopulationFunnelCell())
        for field_name in (
            "demand_count",
            "materialization_need",
            "raw_materialization_need",
            "suppressed_materialization_need",
            "warm_target_count",
            "replacement_target_count",
            "admission_attempt_high_water",
            "admission_high_water_lag_count",
            "ready_count",
            "training_count",
            "exhausted_count",
            "growth_attempt_count",
            "growth_applied_count",
            "effective_progress_count",
            "selected_growth_bps_total",
            "invalid_growth_budget_count",
        ):
            setattr(cell, field_name, getattr(cell, field_name) + int(getattr(snapshot, field_name)))
        for field_name in (
            "selected_growth_bps_max",
            "oldest_ready_member_age_seconds",
            "oldest_training_member_age_seconds",
            "oldest_exhausted_member_age_seconds",
        ):
            setattr(cell, field_name, max(getattr(cell, field_name), int(getattr(snapshot, field_name))))
        cell.guard_reason_counts.update(dict(snapshot.guard_reason_counts))
        cell.retry_reason_counts.update(dict(snapshot.retry_reason_counts))
    return cells


def observe_arena_population_funnel(
    config: dict[str, Any],
    *,
    maintained,
    target_based: bool,
    now: datetime,
) -> tuple[ArenaPopulationFunnelObservation, ...]:
    """Emit one low-cardinality shadow observation per active Arena cell."""

    cells = _active_arena_population_funnel_by_cell(config, now=now)
    blocked_lease_count = ArenaVirtualReserveMember.objects.filter(
        demand__status=ArenaVirtualDemand.Status.BLOCKED
    ).count()
    if blocked_lease_count:
        logger.error(
            "blocked arena virtual demands still hold reserve leases",
            extra={
                "event": "arena_virtual_blocked_lease_guard",
                "blocked_lease_count": int(blocked_lease_count),
            },
        )
    if not cells:
        return ()
    runtime_assessment = assess_virtual_player_runtime()
    handoff_supply = arena_handoff_supply_by_cell(
        maintained,
        arena_demands={key: cell.materialization_need for key, cell in cells.items()},
        config=config,
        target_based=target_based,
        candidate_engine_version=runtime_assessment.reserve_engine_version,
        ready_handoff_allowed=runtime_assessment.ready_handoff_allowed,
        training_admission_allowed=runtime_assessment.training_admission_allowed,
    )
    observations: list[ArenaPopulationFunnelObservation] = []
    for (region, prestige_band), cell in sorted(cells.items()):
        handoff_available = handoff_supply.get((region, prestige_band), ArenaHandoffSupply()).available
        observation = ArenaPopulationFunnelObservation(
            region=region,
            prestige_band=prestige_band,
            demand_count=cell.demand_count,
            materialization_need=cell.materialization_need,
            raw_materialization_need=cell.raw_materialization_need,
            suppressed_materialization_need=cell.suppressed_materialization_need,
            handoff_available=handoff_available,
            population_materialization_additional=max(0, cell.materialization_need - handoff_available),
            warm_target_count=cell.warm_target_count,
            replacement_target_count=cell.replacement_target_count,
            admission_attempt_high_water=cell.admission_attempt_high_water,
            admission_high_water_lag_count=cell.admission_high_water_lag_count,
            ready_count=cell.ready_count,
            training_count=cell.training_count,
            exhausted_count=cell.exhausted_count,
            growth_attempt_count=cell.growth_attempt_count,
            growth_applied_count=cell.growth_applied_count,
            effective_progress_count=cell.effective_progress_count,
            selected_growth_bps_total=cell.selected_growth_bps_total,
            selected_growth_bps_max=cell.selected_growth_bps_max,
            invalid_growth_budget_count=cell.invalid_growth_budget_count,
            oldest_ready_member_age_seconds=cell.oldest_ready_member_age_seconds,
            oldest_training_member_age_seconds=cell.oldest_training_member_age_seconds,
            oldest_exhausted_member_age_seconds=cell.oldest_exhausted_member_age_seconds,
            guard_reason_counts=tuple(sorted(cell.guard_reason_counts.items())),
            retry_reason_counts=tuple(sorted(cell.retry_reason_counts.items())),
        )
        observations.append(observation)
        logger.log(
            logging.WARNING if observation.guard_reason_counts else logging.INFO,
            "arena virtual admission funnel observed",
            extra={
                "event": "arena_virtual_admission_funnel",
                "region": observation.region,
                "prestige_band": observation.prestige_band,
                "demand_count": observation.demand_count,
                "materialization_need": observation.materialization_need,
                "raw_materialization_need": observation.raw_materialization_need,
                "suppressed_materialization_need": observation.suppressed_materialization_need,
                "handoff_available": observation.handoff_available,
                "population_materialization_additional": observation.population_materialization_additional,
                "warm_target_count": observation.warm_target_count,
                "replacement_target_count": observation.replacement_target_count,
                "admission_attempt_high_water": observation.admission_attempt_high_water,
                "admission_high_water_lag_count": observation.admission_high_water_lag_count,
                "ready_count": observation.ready_count,
                "training_count": observation.training_count,
                "exhausted_count": observation.exhausted_count,
                "growth_attempt_count": observation.growth_attempt_count,
                "growth_applied_count": observation.growth_applied_count,
                "effective_progress_count": observation.effective_progress_count,
                "effective_progress_ratio": (
                    None
                    if observation.growth_applied_count == 0
                    else observation.effective_progress_count / observation.growth_applied_count
                ),
                "selected_growth_bps_total": observation.selected_growth_bps_total,
                "selected_growth_bps_max": observation.selected_growth_bps_max,
                "invalid_growth_budget_count": observation.invalid_growth_budget_count,
                "oldest_ready_member_age_seconds": observation.oldest_ready_member_age_seconds,
                "oldest_training_member_age_seconds": observation.oldest_training_member_age_seconds,
                "oldest_exhausted_member_age_seconds": observation.oldest_exhausted_member_age_seconds,
                "guard_reason_distribution": dict(observation.guard_reason_counts),
                "retry_reason_distribution": dict(observation.retry_reason_counts),
            },
        )
    return tuple(observations)


def arena_handoff_supply_by_cell(
    maintained,
    *,
    arena_demands: dict[tuple[str, str], int],
    config: dict[str, Any],
    target_based: bool,
    candidate_engine_version: int | None = None,
    ready_handoff_allowed: bool = True,
    training_admission_allowed: bool = True,
) -> dict[tuple[str, str], ArenaHandoffSupply]:
    """Count profiles that reserve reconciliation can claim immediately.

    READY and reachable TRAINING candidates use the same assessment as the
    reserve writer. Matching is a bounded bipartite allocation so a broad
    candidate cannot consume a slot needed by a demand with narrower rules.
    """

    handoff_supply = {key: ArenaHandoffSupply() for key in arena_demands}
    if not handoff_supply or not ready_handoff_allowed:
        return handoff_supply

    requested_bands = frozenset(band for _region, band in arena_demands)
    if not requested_bands.intersection(_prestige_bands(config)):
        return handoff_supply

    eligible_maintained = with_arena_reconciliation_state(
        maintained.filter(
            state__in=VIRTUAL_PROFILE_ARENA_ELIGIBLE_STATES,
            arena_virtual_reserve__isnull=True,
            manor__guests__status=GuestStatus.IDLE,
        )
        .select_related("manor")
        .distinct()
        .prefetch_related(
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
    )
    if candidate_engine_version is not None:
        eligible_maintained = eligible_maintained.filter(engine_version=int(candidate_engine_version))
        if int(candidate_engine_version) == 2:
            eligible_maintained = eligible_maintained.filter(policy_version=2)
    occupied_manor_ids = arena_protected_bot_manor_ids()
    if occupied_manor_ids:
        eligible_maintained = eligible_maintained.exclude(manor_id__in=occupied_manor_ids)
    # Build bounded, demand-specific slots first. A profile is counted only
    # after it passes the live cap and a deterministic lineup evaluation for
    # one concrete demand; this prevents one profile from satisfying several
    # aggregated cell requests.
    demand_contexts: dict[tuple[str, str], list[_ArenaHandoffDemandContext]] = {key: [] for key in arena_demands}
    active_demands = list(
        ArenaVirtualDemand.objects.filter(status=ArenaVirtualDemand.Status.ACTIVE)
        .select_related("tournament", "coop_event")
        .prefetch_related(
            Prefetch(
                "reserve_members",
                queryset=ArenaVirtualReserveMember.objects.only("id", "demand_id", "state").order_by("id"),
                to_attr="arena_handoff_members",
            )
        )
        .order_by("id")
    )
    tournament_ids = {int(demand.tournament_id) for demand in active_demands if demand.tournament_id is not None}
    coop_event_ids = {int(demand.coop_event_id) for demand in active_demands if demand.coop_event_id is not None}
    tournament_entries: dict[int, list[ArenaEntry]] = defaultdict(list)
    if tournament_ids:
        for tournament_entry in (
            ArenaEntry.objects.filter(
                tournament_id__in=sorted(tournament_ids),
                status=ArenaEntry.Status.REGISTERED,
                source=ArenaEntry.Source.PLAYER,
            )
            .select_related("manor")
            .prefetch_related(
                Prefetch(
                    "entry_guests",
                    queryset=ArenaEntryGuest.objects.select_related("guest").order_by("id"),
                )
            )
            .order_by("tournament_id", "id")
        ):
            tournament_entries[int(tournament_entry.tournament_id)].append(tournament_entry)
    coop_entries: dict[int, list[ArenaCoopEntry]] = defaultdict(list)
    if coop_event_ids:
        for coop_entry in (
            ArenaCoopEntry.objects.filter(
                event_id__in=sorted(coop_event_ids),
                status=ArenaCoopEntry.Status.REGISTERED,
                source=ArenaCoopEntry.Source.PLAYER,
            )
            .select_related("manor")
            .prefetch_related(
                Prefetch(
                    "entry_guests",
                    queryset=ArenaCoopEntryGuest.objects.select_related("guest").order_by("id"),
                )
            )
            .order_by("event_id", "id")
        ):
            coop_entries[int(coop_entry.event_id)].append(coop_entry)
    tournament_max_lineup_size = (
        int(load_arena_rules()["registration"]["max_guests_per_entry"]) if tournament_ids else 0
    )
    for demand in active_demands:
        real_entries = (
            tournament_entries.get(int(demand.tournament_id), [])
            if demand.tournament_id is not None
            else coop_entries.get(int(demand.coop_event_id or 0), [])
        )
        if not real_entries or int(demand.target_guest_count or 0) <= 0 or int(demand.target_team_power or 0) <= 0:
            continue
        reference_entry = median_entry(real_entries)
        supply_band_priority = demand_supply_prestige_band_priority(demand)
        if demand_uses_arena_training_policy(demand) and supply_band_priority is None:
            continue
        supply_band = demand_supply_prestige_band(demand)
        band = supply_band or _prestige_band_for_value(int(reference_entry.manor.prestige or 0), config)
        cell = (str(reference_entry.manor.region), str(band or ""))
        if cell not in demand_contexts:
            continue
        allowed_bands = supply_band_priority or ((str(band),) if band else ())
        if not allowed_bands or any(allowed_band not in _prestige_bands(config) for allowed_band in allowed_bands):
            continue
        members = list(demand.arena_handoff_members)
        active_member_count = sum(
            member.state
            in {
                ArenaVirtualReserveMember.State.READY,
                ArenaVirtualReserveMember.State.TRAINING,
            }
            for member in members
        )
        ready_count = sum(member.state == ArenaVirtualReserveMember.State.READY for member in members)
        training_count = active_member_count - ready_count
        admission = assess_reserve_admission(
            warm_target=int(demand.warm_target_count),
            ready_count=ready_count,
            training_count=training_count,
            leased_attempts=len(members),
            admission_attempt_high_water=int(demand.admission_attempt_high_water),
            replacement_target=int(demand.max_reserve_target_count),
            active_pause_reason=str(demand.admission_pause_reason or ""),
            admission_probe_target_ordinal=demand.admission_probe_target_ordinal,
        )
        remaining = min(
            admission.admitted_materialization_needed,
            max(0, int(arena_demands[cell])),
        )
        if remaining <= 0:
            continue
        mode = "tournament" if demand.tournament_id is not None else "coop"
        if mode == "tournament":
            max_lineup_size = tournament_max_lineup_size
        else:
            coop_event = demand.coop_event
            if coop_event is None:
                continue
            max_lineup_size = max(1, int(coop_event.guest_limit_per_entry))
        demand_contexts[cell].append(
            _ArenaHandoffDemandContext(
                demand=demand,
                cell=cell,
                supply_prestige_band_priority=allowed_bands,
                mode=mode,
                event_id=int(demand.tournament_id or demand.coop_event_id or 0),
                target_guest_count=int(demand.target_guest_count),
                target_team_power=int(demand.target_team_power),
                max_lineup_size=max_lineup_size,
                remaining=remaining,
            )
        )

    # Import lazily to avoid the population_runtime -> arena_population import
    # cycle during Django app initialization.
    from gameplay.services.arena.virtual_reserve_pool import (
        ArenaReserveCandidateDisposition,
        assess_arena_reserve_candidate,
    )

    # Expand each demand's bounded capacity into deterministic slots. Profiles
    # are then matched to slots with augmenting paths, which handles a generic
    # candidate followed by a demand-specific candidate without undercounting.
    slot_records: list[tuple[tuple[str, str], int]] = []
    slots_by_context: dict[tuple[tuple[str, str], int], tuple[int, ...]] = {}
    for cell, contexts in sorted(demand_contexts.items()):
        for context_index, context in enumerate(contexts):
            slot_ids: list[int] = []
            for _ in range(max(0, int(context.remaining))):
                slot_ids.append(len(slot_records))
                slot_records.append((cell, context_index))
            slots_by_context[(cell, context_index)] = tuple(slot_ids)

    matched_slot_profiles: dict[int, int] = {}
    profile_edges: dict[int, tuple[int, ...]] = {}

    def _augment(profile_id: int, seen_slots: set[int], seen_profiles: set[int]) -> bool:
        if profile_id in seen_profiles:
            return False
        seen_profiles.add(profile_id)
        for slot_id in profile_edges.get(profile_id, ()):
            if slot_id in seen_slots:
                continue
            seen_slots.add(slot_id)
            incumbent = matched_slot_profiles.get(slot_id)
            if incumbent is None or _augment(incumbent, seen_slots, seen_profiles):
                matched_slot_profiles[slot_id] = profile_id
                return True
        return False

    fallback_supply: Counter[tuple[str, str]] = Counter()
    remaining_by_cell = {key: max(0, int(requested)) for key, requested in arena_demands.items()}
    current_time = timezone.now()
    for profile in (
        eligible_maintained.filter(
            manor__region__in={region for region, _band in arena_demands},
        )
        .order_by("id")
        .iterator(chunk_size=100)
    ):
        if not is_virtual_profile_arena_match_eligible(profile, now=current_time):
            continue
        profile_band = (
            _prestige_band_for_value(int(profile.manor.prestige or 0), config)
            if not target_based
            else _profile_target_prestige_band(profile)
        )
        if profile_band is None:
            continue
        profile_cell = (str(profile.manor.region), str(profile_band))
        matching_contexts: list[tuple[int, tuple[str, str], int, _ArenaHandoffDemandContext]] = []
        for cell, contexts in demand_contexts.items():
            if cell[0] != profile_cell[0]:
                continue
            for context_index, context in enumerate(contexts):
                try:
                    priority_index = context.supply_prestige_band_priority.index(profile_cell[1])
                except ValueError:
                    continue
                matching_contexts.append((priority_index, cell, context_index, context))
        if matching_contexts:
            edges: list[int] = []
            for _priority_index, cell, context_index, context in sorted(
                matching_contexts,
                key=lambda row: (row[0], row[1], row[2]),
            ):
                assessment = assess_arena_reserve_candidate(
                    context.demand,
                    profile,
                )
                if assessment.disposition is ArenaReserveCandidateDisposition.READY or (
                    training_admission_allowed and assessment.disposition is ArenaReserveCandidateDisposition.TRAINING
                ):
                    edges.extend(slots_by_context.get((cell, context_index), ()))
            if edges:
                profile_edges[int(profile.id)] = tuple(edges)
                _augment(int(profile.id), set(), set())
            continue
        # If a caller supplied an external cell quota but there is no persisted
        # demand row yet, keep the old conservative handoff behavior: count only
        # live-cap eligible profiles, never profiles whose cap is stale.
        if profile_cell in handoff_supply and remaining_by_cell[profile_cell] > 0:
            remaining_by_cell[profile_cell] -= 1
            fallback_supply[profile_cell] += 1

    for key in handoff_supply:
        matched = sum(
            1
            for slot_id, (cell, _context_index) in enumerate(slot_records)
            if cell == key and slot_id in matched_slot_profiles
        )
        handoff_supply[key] = ArenaHandoffSupply(
            available=min(
                max(0, int(arena_demands[key])),
                max(0, int(matched)) + int(fallback_supply[key]),
            )
        )
    return handoff_supply


def queue_arena_population_handoff(*, region: str) -> bool:
    """Dispatch one idempotent Arena wakeup for a region after population changes."""
    task = current_app.signature("gameplay.wake_active_arena_demands_for_population_region")
    normalized_region = str(region)
    return safe_apply_async_with_dedup(
        task,
        dedup_key=f"virtual-player-arena-population-handoff:{normalized_region}",
        dedup_timeout=ARENA_POPULATION_HANDOFF_DEDUP_SECONDS,
        args=[normalized_region],
        logger=logger,
        log_message=(
            f"arena population handoff dispatch failed for region={normalized_region}; relying on periodic scan"
        ),
    )


__all__ = [
    "ARENA_POPULATION_HANDOFF_DEDUP_SECONDS",
    "ArenaPopulationFunnelObservation",
    "active_arena_population_demand_by_cell",
    "arena_handoff_supply_by_cell",
    "observe_arena_population_funnel",
    "queue_arena_population_handoff",
]
