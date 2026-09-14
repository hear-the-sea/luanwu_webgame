"""Database-backed, cross-worker admission and scheduling budgets.

Materializers reserve immediately before creation/reactivation, inside the same
transaction. Rollback refunds uncommitted attempts; committed attempts, including
unusable created candidates, remain charged for five minutes. Never charge merely
for queueing population work or leasing an already ACTIVE profile.

Routing must be acquired before population control, demand and profile locks.
These leaf budget locks never acquire routing/population/demand/profile locks.
SKIP LOCKED defers contending callers, avoiding new reverse lock dependencies.
The deployed Django MySQL connection uses READ COMMITTED for occupancy reads.
"""

from datetime import datetime, timedelta
from math import isfinite
from uuid import uuid4

from django.db import transaction
from django.db.models import Count, Prefetch, Q

from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEntryGuest,
    ArenaEntry,
    ArenaEntryGuest,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
)
from gameplay.models.arena_runtime_budget import ArenaRuntimeBudget

from .low_population_policy import low_population_enabled

_RUNTIME_BUDGET_KEYS = frozenset(("materialization", "training", "growth"))


def validate_arena_runtime_budget_entries(key: str, entries: object, *, now: datetime | None = None) -> list[dict]:
    """Validate a budget ledger before reading, refunding or recovering it.

    The JSON field is shared state, so a syntactically valid list is not enough:
    each writer relies on a purpose-specific identity field. Recovery uses the
    same validator as admission to avoid clearing a pause over unusable ledger
    evidence. ``probe_ordinal`` remains optional for older materialization rows.
    """
    if key not in _RUNTIME_BUDGET_KEYS or not isinstance(entries, list):
        raise ValueError("invalid arena runtime budget ledger")
    now_timestamp = now.timestamp() if now is not None else None
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("invalid arena runtime budget entry")
        at = entry.get("at")
        demand_id = entry.get("demand_id")
        if (
            isinstance(at, bool)
            or not isinstance(at, (int, float))
            or not isfinite(at)
            or (now_timestamp is not None and at > now_timestamp + 60)
            or type(demand_id) is not int
            or demand_id < 1
        ):
            raise ValueError("invalid arena runtime budget entry")
        if key == "materialization":
            token = entry.get("token")
            probe_ordinal = entry.get("probe_ordinal")
            if not isinstance(token, str) or not token:
                raise ValueError("invalid arena runtime budget entry")
            if probe_ordinal is not None and (type(probe_ordinal) is not int or probe_ordinal < 1):
                raise ValueError("invalid arena runtime budget entry")
        elif key == "growth":
            member_id = entry.get("member_id")
            if type(member_id) is not int or member_id < 1:
                raise ValueError("invalid arena runtime budget entry")
        else:
            # The training row is a mutex, not a reservation ledger. Any entry
            # would be unowned evidence that must block recovery.
            raise ValueError("invalid arena runtime budget entry")
    return entries


def _locked_budget(key: str) -> ArenaRuntimeBudget | None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Arena budget reservations require transaction.atomic()")
    # Rows are seeded by migration. Missing rows fail closed, not get_or_create:
    # an insert race must not wait while the caller owns a demand/member lock.
    return ArenaRuntimeBudget.objects.select_for_update(skip_locked=True).filter(pk=key).first()


def _recent_entries(budget: ArenaRuntimeBudget, *, now: datetime, seconds: int) -> list[dict]:
    cutoff = (now - timedelta(seconds=seconds)).timestamp()
    entries = validate_arena_runtime_budget_entries(budget.key, budget.entries, now=now)
    return [entry for entry in entries if entry["at"] > cutoff]


def reserve_materialization_locked(*, demand_id: int, now: datetime) -> str | None:
    """Return a reservation token, or None when admission must wait.

    The population owner must attribute arena-caused expansion to a concrete
    demand and call this at the actual mutation boundary. Structural population
    deficits are not arena materialization and do not consume this budget.
    """
    if not low_population_enabled():
        return "normal"
    demand = (
        ArenaVirtualDemand.objects.filter(pk=demand_id)
        .only("admission_pause_reason", "admission_probe_target_ordinal")
        .first()
    )
    probe_ordinal = (
        demand.admission_probe_target_ordinal
        if demand is not None and demand.admission_pause_reason == "no_effective_progress"
        else None
    )
    budget = _locked_budget("materialization")
    if budget is None:
        return None
    return _reserve_materialization_from_budget(
        budget=budget, demand_id=demand_id, probe_ordinal=probe_ordinal, now=now
    )


def _reserve_materialization_from_budget(
    *, budget: ArenaRuntimeBudget, demand_id: int, probe_ordinal: int | None, now: datetime
) -> str | None:
    """Charge the already locked ledger using the caller's demand snapshot."""
    cutoff = (now - timedelta(minutes=5)).timestamp()
    entries = [
        e
        for e in _recent_entries(budget, now=now, seconds=1800)
        if e["at"] > cutoff or e.get("probe_ordinal") is not None
    ]
    recent = [e for e in entries if e["at"] > cutoff]
    if len(recent) >= 4 or sum(int(e["demand_id"]) == int(demand_id) for e in recent) >= 2:
        return None
    if probe_ordinal is not None and any(
        e["demand_id"] == demand_id and e.get("probe_ordinal") == probe_ordinal for e in entries
    ):
        return None
    token = uuid4().hex
    entries.append({"token": token, "demand_id": int(demand_id), "at": now.timestamp(), "probe_ordinal": probe_ordinal})
    budget.entries = entries
    budget.save(update_fields=["entries", "updated_at"])
    return token


def cancel_materialization_locked(token: str) -> None:
    """Refund a reservation only when no creation/reactivation was attempted."""
    if token == "normal":
        return
    budget = _locked_budget("materialization")
    if budget is None:
        raise RuntimeError("Materialization reservation must be finalized in its owning transaction")
    entries = validate_arena_runtime_budget_entries("materialization", budget.entries)
    budget.entries = [entry for entry in entries if entry["token"] != token]
    budget.save(update_fields=["entries", "updated_at"])


def reserve_population_cell_materialization_locked(
    *, region: str, prestige_band: str, config: dict, now: datetime
) -> str | None:
    """Attribute an arena-only cell expansion to an active, guarded demand.

    The population owner already holds routing + population control. Demand
    reads here never lock in reverse order. Each actual expansion spends one
    global and one demand token even if handoff later rejects the candidate.
    """
    if not low_population_enabled():
        return "normal"
    from gameplay.services.virtual_player_core.selectors import prestige_band_for_value

    from .virtual_reserve_policy import assess_reserve_admission
    from .virtual_reserve_references import median_entry
    from .virtual_reserve_training_policy import demand_supply_prestige

    # Batch the same registered human references used by reserve admission.
    # Keep entry ordering intact: median_entry uses stable sorting for ties.
    # Counts include exhausted members because they still spend attempt budget.
    demands = (
        ArenaVirtualDemand.objects.filter(status=ArenaVirtualDemand.Status.ACTIVE)
        .select_related("tournament", "coop_event")
        .annotate(
            budget_ready_count=Count("reserve_members", filter=Q(reserve_members__state="ready")),
            budget_training_count=Count("reserve_members", filter=Q(reserve_members__state="training")),
            budget_attempt_count=Count("reserve_members"),
        )
        .prefetch_related(
            Prefetch(
                "tournament__entries",
                queryset=ArenaEntry.objects.filter(status=ArenaEntry.Status.REGISTERED, source=ArenaEntry.Source.PLAYER)
                .select_related("manor")
                .prefetch_related(Prefetch("entry_guests", queryset=ArenaEntryGuest.objects.select_related("guest"))),
                to_attr="budget_tournament_entries",
            ),
            Prefetch(
                "coop_event__entries",
                queryset=ArenaCoopEntry.objects.filter(
                    status=ArenaCoopEntry.Status.REGISTERED, source=ArenaCoopEntry.Source.PLAYER
                )
                .select_related("manor")
                .prefetch_related(
                    Prefetch("entry_guests", queryset=ArenaCoopEntryGuest.objects.select_related("guest"))
                ),
                to_attr="budget_coop_entries",
            ),
        )
        .order_by("last_checked_at", "pk")[:200]
    )
    candidates = []
    for demand in demands:
        entries = (
            getattr(demand.tournament, "budget_tournament_entries", ())
            if demand.tournament_id is not None
            else getattr(demand.coop_event, "budget_coop_entries", ())
        )
        if not entries:
            continue
        reference = median_entry(entries)
        prestige = demand_supply_prestige(demand)
        if (
            str(reference.manor.region) != region
            or prestige_band_for_value(reference.manor.prestige if prestige is None else prestige, config)
            != prestige_band
        ):
            continue
        assessment = assess_reserve_admission(
            warm_target=demand.warm_target_count,
            ready_count=demand.budget_ready_count,
            training_count=demand.budget_training_count,
            leased_attempts=demand.budget_attempt_count,
            admission_attempt_high_water=demand.admission_attempt_high_water,
            replacement_target=demand.max_reserve_target_count,
            active_pause_reason=demand.admission_pause_reason,
            admission_probe_target_ordinal=demand.admission_probe_target_ordinal,
        )
        if assessment.admitted_materialization_needed:
            candidates.append(demand)
    if not candidates:
        return None
    # Acquire the leaf ledger once, after loading references. A full ledger or
    # spent per-demand quota must not reintroduce a query for every candidate.
    budget = _locked_budget("materialization")
    if budget is None:
        return None
    for demand in candidates:
        probe_ordinal = (
            demand.admission_probe_target_ordinal if demand.admission_pause_reason == "no_effective_progress" else None
        )
        token = _reserve_materialization_from_budget(
            budget=budget, demand_id=demand.pk, probe_ordinal=probe_ordinal, now=now
        )
        if token is not None:
            return token
    return None


def training_admission_available_locked() -> bool | None:
    """Hold the training mutex through the member's insert/state transition.

    Call after member/profile locks; never acquire more member/profile locks
    while waiting for this mutex. Existing TRAINING and READY handoff need no
    admission. Every transition into TRAINING must use this check. ``None``
    means that the mutex row was unavailable through ``SKIP LOCKED`` and the
    caller should defer without turning a transient contention into a terminal
    capacity decision.
    """
    if not low_population_enabled():
        return True
    budget = _locked_budget("training")
    if budget is None:
        return None
    try:
        validate_arena_runtime_budget_entries("training", budget.entries)
    except (TypeError, ValueError):
        return False
    return ArenaVirtualReserveMember.objects.filter(state=ArenaVirtualReserveMember.State.TRAINING).count() < 16


@transaction.atomic
def reserve_growth_batch(*, members: list[tuple[int, int]], now: datetime) -> list[tuple[int, int]]:
    """Reserve at most eight distinct members/two per demand per rolling minute.

    Commit before acquiring growth claims. A crashed dispatcher costs at most
    one minute; the existing persistent claim and retry deadlines remain final.
    The eight-slot round is independent of the per-task work bound; recent
    attempt history is retained for observation, not used to throttle growth.
    """
    if not low_population_enabled():
        return members
    budget = _locked_budget("growth")
    if budget is None:
        return []
    entries = _recent_entries(budget, now=now, seconds=60)
    selected = []
    for member_id, demand_id in members:
        if len(entries) >= 8:
            break
        if any(int(e["member_id"]) == member_id for e in entries):
            continue
        if sum(int(e["demand_id"]) == demand_id for e in entries) >= 2:
            continue
        entries.append({"member_id": member_id, "demand_id": demand_id, "at": now.timestamp()})
        selected.append((member_id, demand_id))
    budget.entries = entries
    budget.save(update_fields=["entries", "updated_at"])
    return selected
