from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEntryGuest,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaEntryGuest,
    ArenaRuntimeBudget,
    ArenaTournament,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
)
from gameplay.services.arena import low_population_budget as budget
from gameplay.services.arena import low_population_policy as policy
from gameplay.services.arena import virtual_reserve_pool as pool
from tests.arena_services.test_virtual_reserve import _create_bot_profile, _create_tournament_demand

pytestmark = pytest.mark.django_db


@pytest.fixture
def low_population(settings):
    settings.ARENA_LOW_POPULATION_MODE = True
    for key in ("materialization", "training", "growth"):
        ArenaRuntimeBudget.objects.update_or_create(key=key, defaults={"entries": []})


def test_opt_in_waits_and_frozen_existing_deadlines(settings):
    settings.ARENA_LOW_POPULATION_MODE = False
    assert policy.new_event_wait_seconds("tournament", 7200) == 7200
    assert policy.new_event_wait_seconds("coop", 7200) == 7200
    demand = _create_tournament_demand()
    fill_at = demand.tournament.virtual_fill_at
    settings.ARENA_LOW_POPULATION_MODE = True
    assert policy.new_event_wait_seconds("tournament", 7200) == 900
    assert policy.new_event_wait_seconds("coop", 7200) == 1800
    demand.tournament.refresh_from_db()
    assert demand.tournament.virtual_fill_at == fill_at


def test_growth_cannot_skip_preheat_boundary_or_accelerate_ready(low_population):
    now = timezone.now()
    normal = timedelta(hours=1)
    assert policy.growth_interval(fill_at=now + timedelta(minutes=20), now=now, normal=normal) == timedelta(minutes=5)
    assert policy.growth_interval(fill_at=now + timedelta(minutes=15), now=now, normal=normal) == timedelta(minutes=5)
    assert policy.growth_interval(fill_at=now - timedelta(minutes=30), now=now, normal=normal) == timedelta(minutes=5)
    assert policy.growth_batch_limits(100, 8) == (8, 2)


def test_shared_creation_budget_rolls_and_refunds_only_unattempted(low_population):
    now = timezone.now()
    with transaction.atomic():
        first = budget.reserve_materialization_locked(demand_id=1, now=now)
        assert first
        assert budget.reserve_materialization_locked(demand_id=1, now=now)
        assert budget.reserve_materialization_locked(demand_id=1, now=now) is None
        assert budget.reserve_materialization_locked(demand_id=2, now=now)
        assert budget.reserve_materialization_locked(demand_id=2, now=now)
        assert budget.reserve_materialization_locked(demand_id=3, now=now) is None
        budget.cancel_materialization_locked(first)
        assert budget.reserve_materialization_locked(demand_id=3, now=now)
    with transaction.atomic():
        assert budget.reserve_materialization_locked(demand_id=1, now=now + timedelta(minutes=5))
    assert len(ArenaRuntimeBudget.objects.get(pk="materialization").entries) == 1


def test_rolled_back_materialization_does_not_spend_and_missing_ledger_fails_closed(low_population):
    with pytest.raises(RuntimeError), transaction.atomic():
        assert budget.reserve_materialization_locked(demand_id=1, now=timezone.now())
        raise RuntimeError("materialization rollback")
    assert ArenaRuntimeBudget.objects.get(pk="materialization").entries == []
    ArenaRuntimeBudget.objects.filter(pk="materialization").delete()
    with transaction.atomic():
        assert budget.reserve_materialization_locked(demand_id=1, now=timezone.now()) is None


def test_dispatch_budget_is_global_not_per_task(low_population):
    now = timezone.now()
    members = [(index, (index - 1) // 4 + 1) for index in range(1, 25)]
    first = budget.reserve_growth_batch(members=members, now=now)
    assert len(first) == 8
    assert all(sum(d == demand for _, d in first) <= 2 for _, demand in first)
    assert budget.reserve_growth_batch(members=members, now=now) == []
    assert len(budget.reserve_growth_batch(members=members, now=now + timedelta(minutes=1))) == 8


def test_arena_population_expansion_and_reactivation_share_budget(low_population):
    from gameplay.services.virtual_player_core import population_runtime

    demand = _create_tournament_demand()
    target = pool._target_population_cell_for_demand(demand)
    assert target is not None
    now = timezone.now()
    with transaction.atomic():
        assert budget.reserve_materialization_locked(demand_id=demand.pk, now=now)
        assert budget.reserve_population_cell_materialization_locked(
            region=target[0],
            prestige_band=target[1],
            config=population_runtime._v2_population_runtime_config(),
            now=now,
        )
        assert (
            budget.reserve_population_cell_materialization_locked(
                region=target[0],
                prestige_band=target[1],
                config=population_runtime._v2_population_runtime_config(),
                now=now,
            )
            is None
        )


@pytest.mark.parametrize("mode", ["tournament", "coop"])
@pytest.mark.parametrize("demand_count", [2, 20])
@pytest.mark.parametrize("global_full", [False, True])
def test_cell_budget_queries_stay_bounded_when_candidates_have_spent_quota(
    low_population, django_assert_max_num_queries, mode, demand_count, global_full
):
    from gameplay.services.virtual_player_core import population_runtime

    reference_demand = _create_tournament_demand()
    target = pool._target_population_cell_for_demand(reference_demand)
    assert target is not None
    reference_entry = reference_demand.tournament.entries.get(source="player")
    snapshot = reference_entry.entry_guests.first().snapshot
    reference_demand.status = ArenaVirtualDemand.Status.CLOSED
    reference_demand.save(update_fields=["status"])
    demands = []
    # Active demand rows awaiting reconciliation may outlive their activities.
    # They must not turn a full ledger into hundreds of lock-held queries.
    for _ in range(demand_count):
        if mode == "tournament":
            event = ArenaTournament.objects.create(status=ArenaTournament.Status.COMPLETED)
            entry = ArenaEntry.objects.create(tournament=event, manor=reference_entry.manor)
            ArenaEntryGuest.objects.create(entry=entry, snapshot=snapshot)
            relation = {"tournament_id": event.pk}
        else:
            event = ArenaCoopEvent.objects.create(status=ArenaCoopEvent.Status.COMPLETED)
            entry = ArenaCoopEntry.objects.create(event=event, manor=reference_entry.manor)
            ArenaCoopEntryGuest.objects.create(entry=entry, snapshot=snapshot)
            relation = {"coop_event_id": event.pk}
        demands.append(
            ArenaVirtualDemand.objects.create(
                **relation,
                warm_target_count=2,
                reserve_target_count=2,
                max_reserve_target_count=2,
                arena_training_policy_version=reference_demand.arena_training_policy_version,
                arena_training_policy_checksum=reference_demand.arena_training_policy_checksum,
                arena_strength_segment=reference_demand.arena_strength_segment,
                arena_strength_envelope_digest=reference_demand.arena_strength_envelope_digest,
                arena_supply_prestige_band=reference_demand.arena_supply_prestige_band,
                arena_supply_prestige_band_priority=reference_demand.arena_supply_prestige_band_priority,
                arena_supply_prestige=reference_demand.arena_supply_prestige,
            )
        )
    now = timezone.now()
    ledger_entries = [
        {"token": f"spent-{index}", "demand_id": demands[0].pk, "at": now.timestamp(), "probe_ordinal": None}
        for index in range(2)
    ]
    if global_full:
        ledger_entries.extend(
            {"token": f"global-{index}", "demand_id": reference_demand.pk, "at": now.timestamp()} for index in range(2)
        )
    ArenaRuntimeBudget.objects.filter(pk="materialization").update(entries=ledger_entries)
    config = population_runtime._v2_population_runtime_config()
    with transaction.atomic(), django_assert_max_num_queries(6):
        token = budget.reserve_population_cell_materialization_locked(
            region=target[0], prestige_band=target[1], config=config, now=now
        )
    ledger = ArenaRuntimeBudget.objects.get(pk="materialization")
    if global_full:
        assert token is None
        assert ledger.entries == ledger_entries
    else:
        assert token
        assert ledger.entries[-1]["demand_id"] == demands[1].pk
        assert len(ledger.entries) == 3


@pytest.mark.parametrize("entries", [{}, [{"at": float("nan"), "demand_id": 1}], [{"at": 1, "demand_id": False}]])
def test_corrupt_budget_evidence_is_not_refunded(low_population, entries):
    ledger = ArenaRuntimeBudget(key="materialization", entries=entries)
    with pytest.raises(ValueError):
        budget._recent_entries(ledger, now=timezone.now(), seconds=300)


@pytest.mark.parametrize(
    "key, entries",
    [
        ("materialization", [{"demand_id": 1, "at": 1}]),
        ("growth", [{"demand_id": 1, "at": 1}]),
        ("training", [{"demand_id": 1, "at": 1}]),
    ],
)
def test_budget_ledger_requires_purpose_specific_identity(key, entries):
    with pytest.raises(ValueError):
        budget.validate_arena_runtime_budget_entries(key, entries)


def test_probe_materialization_is_once_per_cooldown_even_before_handoff(low_population):
    demand = _create_tournament_demand()
    now = timezone.now()
    demand.admission_pause_reason = "no_effective_progress"
    demand.admission_paused_at = now
    demand.admission_probe_target_ordinal = demand.admission_attempt_high_water + 1
    demand.save()
    with transaction.atomic():
        assert budget.reserve_materialization_locked(demand_id=demand.pk, now=now)
        assert budget.reserve_materialization_locked(demand_id=demand.pk, now=now) is None
        assert budget.reserve_materialization_locked(demand_id=demand.pk, now=now + timedelta(minutes=5)) is None
        assert budget.reserve_materialization_locked(demand_id=demand.pk, now=now + timedelta(minutes=30))


def test_immediate_reconcile_deduplicates_per_event_and_retries_dispatch_failure(low_population, monkeypatch):
    from unittest.mock import Mock

    from django.core.cache.backends.locmem import LocMemCache

    from common.utils import celery as celery_utils
    from gameplay.services.arena import virtual_reserve_demand

    monkeypatch.setattr(celery_utils, "cache", LocMemCache("arena-low-population-test", {}))
    dispatch = Mock(side_effect=[False, True, True])
    monkeypatch.setattr(celery_utils, "safe_apply_async", dispatch)
    assert not virtual_reserve_demand.queue_virtual_reserve_reconcile("coop", 8001)
    assert virtual_reserve_demand.queue_virtual_reserve_reconcile("coop", 8001)
    assert virtual_reserve_demand.queue_virtual_reserve_reconcile("coop", 8001)
    assert dispatch.call_count == 2
    assert virtual_reserve_demand.queue_virtual_reserve_reconcile("tournament", 8001)
    assert dispatch.call_count == 3
    assert dispatch.call_args.kwargs["args"] == ["tournament", 8001]


def test_normal_mode_keeps_existing_reconcile_dispatch(settings, monkeypatch):
    from unittest.mock import Mock

    from gameplay.services.arena import virtual_reserve_demand

    settings.ARENA_LOW_POPULATION_MODE = False
    dispatch = Mock(return_value=True)
    monkeypatch.setattr(virtual_reserve_demand, "safe_apply_async", dispatch)
    assert virtual_reserve_demand.queue_virtual_reserve_reconcile("coop", 8002)
    assert virtual_reserve_demand.queue_virtual_reserve_reconcile("coop", 8002)
    assert dispatch.call_count == 2


def test_training_capacity_and_scheduled_wait_not_stall(low_population):
    demand = _create_tournament_demand()
    now = timezone.now()
    for index in range(16):
        profile = _create_bot_profile(f"low_population_training_{index}")
        ArenaVirtualReserveMember.objects.create(
            demand=demand,
            profile=profile,
            state="training",
            evaluated_version=demand.version,
            next_acceleration_at=now + timedelta(minutes=5),
        )
    with transaction.atomic():
        assert not budget.training_admission_available_locked()
    demand.consecutive_failure_count = 2
    demand.last_progress_at = now - timedelta(hours=1)
    demand.last_input_change_at = now - timedelta(hours=1)
    demand.created_at = now - timedelta(hours=1)
    assert not pool._demand_stalled_without_explained_constraint(
        demand,
        member_retry_reasons={},
        attempt_high_water=16,
        now=now,
    )
