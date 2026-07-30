from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import IntegrityError, connection
from django.utils import timezone

from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaEntryGuest,
    ArenaTournament,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
    BotExternalStrengthReconciliation,
    BotMaintenanceExecution,
    BotPopulationRecomputeDemand,
    BotProfile,
    BotRuntimeRoutingState,
    BotSafetyMetricEvent,
)
from gameplay.services.arena import virtual_reserve_demand as reserve_demand_service
from gameplay.services.arena import virtual_reserve_pool
from gameplay.services.arena.virtual_lineups import BotLineupEvaluation
from gameplay.services.arena.virtual_reserve_fill import fill_due_coop_reserve, fill_due_tournament_reserve
from gameplay.services.arena.virtual_reserve_pool import (
    MAX_NO_ACTION_LEASE_AGE,
    ReserveReplenishmentResult,
    create_due_virtual_reserve_profiles,
    grow_due_virtual_reserves,
    reevaluate_existing_members,
    replenish_virtual_reserve,
)
from gameplay.services.arena.virtual_reserve_reconcile import reconcile_coop_demand, reconcile_tournament_demand
from gameplay.services.arena.virtual_reserve_scan import scan_virtual_reserve_demands
from gameplay.services.virtual_player_core import maintenance, population_runtime
from gameplay.services.virtual_player_core.config import MaintenanceMode
from gameplay.services.virtual_player_core.contracts import AcceleratedGrowthOutcome, PopulationMutationStatus
from gameplay.services.virtual_player_core.population_runtime import PopulationMutationResult
from gameplay.services.virtual_player_core.safety_metrics import HARD_CONSTRAINT_METRIC
from gameplay.services.virtual_player_core.safety_provider import record_safety_metric_event
from tests.arena_services.test_virtual_backfill import _add_real_arena_entry, _add_real_coop_entry, _create_bot_profile
from tests.test_virtual_player_backfill import _bootstrap_building_types


def _create_tournament_demand(*, player_limit: int = 2) -> ArenaVirtualDemand:
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=player_limit,
        virtual_fill_at=timezone.now() - timedelta(minutes=1),
    )
    _add_real_arena_entry(
        tournament,
        f"reserve_reference_{tournament.id}",
        attack=200,
        defense=200,
        max_hp=2000,
    )
    demand = reconcile_tournament_demand(tournament.id)
    assert demand is not None
    return demand


@pytest.fixture
def reserve_demand() -> ArenaVirtualDemand:
    demand = _create_tournament_demand(player_limit=2)
    demand.reserve_target_count = 1
    demand.max_reserve_target_count = 1
    demand.save(update_fields=["reserve_target_count", "max_reserve_target_count"])
    return demand


@pytest.fixture
def training_member(reserve_demand: ArenaVirtualDemand) -> ArenaVirtualReserveMember:
    profile = _create_bot_profile(
        "reserve_training_member",
        guest_stats=[(150, 150, 25)],
    )
    return ArenaVirtualReserveMember.objects.create(
        demand=reserve_demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.TRAINING,
        current_lineup_power=450,
        next_acceleration_at=timezone.now() - timedelta(minutes=1),
    )


@dataclass(frozen=True)
class ReservePriorityRows:
    demand: ArenaVirtualDemand
    active: BotProfile
    abandoned: BotProfile
    retired: BotProfile
    weak: BotProfile


@pytest.fixture
def bot_profiles_for_reserve() -> ReservePriorityRows:
    demand = _create_tournament_demand(player_limit=3)
    demand.reserve_target_count = 4
    demand.max_reserve_target_count = 4
    demand.save(update_fields=["reserve_target_count", "max_reserve_target_count"])
    return ReservePriorityRows(
        demand=demand,
        active=_create_bot_profile("reserve_priority_active", state=BotProfile.State.ACTIVE),
        abandoned=_create_bot_profile("reserve_priority_abandoned", state=BotProfile.State.ABANDONED),
        retired=_create_bot_profile("reserve_priority_retired", state=BotProfile.State.RETIRED),
        weak=_create_bot_profile(
            "reserve_priority_weak",
            state=BotProfile.State.ACTIVE,
            guest_stats=[(150, 150, 25)],
        ),
    )


@pytest.fixture
def shared_ready_profile() -> SimpleNamespace:
    tournament_demand = _create_tournament_demand(player_limit=2)
    event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.RECRUITING,
        player_limit=2,
        guest_limit_per_entry=1,
    )
    _add_real_coop_entry(event, "reserve_shared_coop_reference")
    coop_demand = reconcile_coop_demand(event.id)
    assert coop_demand is not None
    return SimpleNamespace(
        demands=(tournament_demand, coop_demand),
        profile=_create_bot_profile("reserve_shared_ready"),
    )


@pytest.fixture
def ready_reserve_demand() -> SimpleNamespace:
    demand = _create_tournament_demand(player_limit=2)
    profiles = [
        _create_bot_profile("reserve_ready_one"),
        _create_bot_profile("reserve_ready_two"),
    ]
    members = [
        ArenaVirtualReserveMember.objects.create(
            demand=demand,
            profile=profile,
            state=ArenaVirtualReserveMember.State.READY,
            current_lineup_power=600,
        )
        for profile in profiles
    ]
    return SimpleNamespace(demand=demand, members=members)


@pytest.mark.django_db
def test_tournament_reconcile_persists_gap_target_and_reference():
    tournament = ArenaTournament.objects.create(player_limit=4)
    _add_real_arena_entry(
        tournament,
        "demand_reference",
        attack=200,
        defense=200,
        max_hp=2000,
    )

    demand = reconcile_tournament_demand(tournament.id)

    assert demand is not None
    assert demand.missing_entry_count == 3
    assert demand.reserve_target_count == 9
    assert demand.max_reserve_target_count == 9
    assert demand.target_guest_count == 1
    assert demand.target_team_power == 600


@pytest.mark.django_db
def test_reconcile_increments_version_only_when_inputs_change():
    tournament = ArenaTournament.objects.create(player_limit=3)
    _add_real_arena_entry(
        tournament,
        "version_reference",
        attack=200,
        defense=200,
        max_hp=2000,
    )
    first = reconcile_tournament_demand(tournament.id)
    assert first is not None

    repeated = reconcile_tournament_demand(tournament.id)
    assert repeated is not None
    assert repeated.version == first.version

    _add_real_arena_entry(
        tournament,
        "version_second",
        attack=200,
        defense=200,
        max_hp=2000,
    )
    changed = reconcile_tournament_demand(tournament.id)

    assert changed is not None
    assert changed.version == first.version + 1
    assert changed.missing_entry_count == 1
    assert changed.reserve_target_count == 6


@pytest.mark.django_db
def test_coop_reconcile_uses_registered_real_entries_only():
    event = ArenaCoopEvent.objects.create(player_limit=3, guest_limit_per_entry=1)
    _add_real_coop_entry(event, "coop_registered")
    _add_real_coop_entry(
        event,
        "coop_cancelled",
        status=ArenaCoopEntry.Status.CANCELLED,
    )

    demand = reconcile_coop_demand(event.id)

    assert demand is not None
    assert demand.missing_entry_count == 2
    assert demand.reserve_target_count == 6


@pytest.mark.django_db
def test_reconcile_closes_inactive_event_and_releases_members():
    demand = _create_tournament_demand(player_limit=2)
    profile = _create_bot_profile("reserve_close_member")
    ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.READY,
    )
    ArenaTournament.objects.filter(pk=demand.tournament_id).update(
        status=ArenaTournament.Status.RUNNING,
    )

    assert reconcile_tournament_demand(demand.tournament_id) is None

    demand.refresh_from_db()
    assert demand.status == ArenaVirtualDemand.Status.CLOSED
    assert demand.missing_entry_count == 0
    assert demand.reserve_target_count == 0
    assert demand.reserve_members.count() == 0


@pytest.mark.django_db
def test_changed_reference_reevaluates_member_without_resetting_growth_rounds():
    demand = _create_tournament_demand(player_limit=2)
    profile = _create_bot_profile(
        "reserve_recheck_member",
        guest_stats=[(150, 150, 25)],
    )
    member = ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.TRAINING,
        evaluated_version=demand.version,
        current_lineup_power=450,
        accelerated_growth_rounds=4,
    )
    real_link = ArenaEntryGuest.objects.get(
        entry__tournament_id=demand.tournament_id,
        entry__source="player",
    )
    real_link.snapshot = {
        "display_name": "调整后真人门客",
        "attack": 150,
        "defense": 150,
        "max_hp": 1500,
        "current_hp": 1500,
    }
    real_link.save(update_fields=["snapshot"])

    changed = reconcile_tournament_demand(demand.tournament_id)

    assert changed is not None
    member.refresh_from_db()
    assert changed.version == demand.version + 1
    assert member.evaluated_version == changed.version
    assert member.state == ArenaVirtualReserveMember.State.READY
    assert member.current_lineup_power == 450
    assert member.accelerated_growth_rounds == 4


@pytest.mark.django_db
def test_active_arena_demand_merges_its_v2_population_cell_once_per_change(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    current_time = timezone.now()
    BotRuntimeRoutingState.objects.create(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE,
        calibration_routes=[],
    )
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=2,
        virtual_fill_at=current_time - timedelta(minutes=1),
    )
    entry = _add_real_arena_entry(
        tournament,
        "arena_population_reference",
        attack=200,
        defense=200,
        max_hp=2_000,
    )
    entry.manor.region = "north"
    entry.manor.prestige = 130_000
    entry.manor.last_active_at = current_time - timedelta(days=31)
    entry.manor.save(update_fields=["region", "prestige", "last_active_at"])
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        reserve_demand_service,
        "_queue_virtual_player_population_reconcile",
        lambda *, region, prestige_band: queued.append((region, prestige_band)) or True,
    )

    with django_capture_on_commit_callbacks(execute=True):
        demand = reconcile_tournament_demand(tournament.id)

    assert demand is not None
    population_demand = BotPopulationRecomputeDemand.objects.get(
        region="north",
        prestige_band="legend",
    )
    assert population_demand.requested_revision == 1
    assert queued == [("north", "legend")]
    population_plan = population_runtime._build_population_plan(
        population_runtime._v2_population_runtime_config(),
        now=current_time,
        target_based_membership=True,
        required_engine_version=2,
    )
    legend_cell = population_plan.by_key[("north", "legend")]
    assert legend_cell.active_real == 0
    assert legend_cell.search_demand == 1
    assert legend_cell.target >= 1

    with django_capture_on_commit_callbacks(execute=True):
        reconcile_tournament_demand(tournament.id)
    population_demand.refresh_from_db()
    assert population_demand.requested_revision == 1
    assert queued == [("north", "legend")]

    link = ArenaEntryGuest.objects.get(entry=entry)
    link.snapshot = {
        "display_name": "arena_population_reference_changed",
        "attack": 300,
        "defense": 300,
        "max_hp": 3_000,
        "current_hp": 3_000,
    }
    link.save(update_fields=["snapshot"])
    with django_capture_on_commit_callbacks(execute=True):
        reconcile_tournament_demand(tournament.id)

    population_demand.refresh_from_db()
    assert population_demand.requested_revision == 2
    assert queued == [("north", "legend"), ("north", "legend")]


@pytest.mark.django_db
def test_reserve_priority_uses_active_then_abandoned_then_retired_then_training(
    bot_profiles_for_reserve,
    caplog,
):
    rows = bot_profiles_for_reserve
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve_demand")

    reconcile_tournament_demand(rows.demand.tournament_id)
    rows.demand.refresh_from_db()

    result = replenish_virtual_reserve(rows.demand.id)

    members = list(rows.demand.reserve_members.select_related("profile").order_by("id"))
    assert result.ready_count == 3
    assert result.training_count == 1
    assert [member.profile_id for member in members[:3]] == [
        rows.active.id,
        rows.abandoned.id,
        rows.retired.id,
    ]
    assert members[-1].profile_id == rows.weak.id
    assert members[-1].state == ArenaVirtualReserveMember.State.TRAINING
    rows.abandoned.refresh_from_db()
    rows.retired.refresh_from_db()
    assert rows.abandoned.state == BotProfile.State.ACTIVE
    assert rows.retired.state == BotProfile.State.ACTIVE
    reconciled_record = next(
        record for record in caplog.records if getattr(record, "event", None) == "arena_virtual_demand_reconciled"
    )
    replenished_record = next(
        record for record in caplog.records if getattr(record, "event", None) == "arena_virtual_reserve_replenished"
    )
    recovered_records = [
        record for record in caplog.records if getattr(record, "event", None) == "arena_virtual_profile_recovered"
    ]
    for record in [reconciled_record, replenished_record, *recovered_records]:
        assert record.mode == "tournament"
        assert record.event_id == rows.demand.tournament_id
        assert record.demand_id == rows.demand.id
        assert record.demand_version == rows.demand.version
        assert record.missing_entry_count == rows.demand.missing_entry_count
        assert record.reserve_target_count == rows.demand.reserve_target_count
        assert isinstance(record.ready_count, int)
        assert isinstance(record.training_count, int)
    assert {record.previous_state for record in recovered_records} == {
        BotProfile.State.ABANDONED,
        BotProfile.State.RETIRED,
    }
    assert replenished_record.recovered_abandoned == 1
    assert replenished_record.recovered_retired == 1
    assert replenished_record.creation_needed == result.creation_needed


@pytest.mark.django_db
def test_zero_hard_cap_allows_retired_reserve_reactivation(settings, reserve_demand):
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "cell_floor": 0,
            "cell_active_multiplier": 0,
            "hard_cap": 0,
        },
    }
    retired = _create_bot_profile(
        "reserve_unlimited_retired",
        state=BotProfile.State.RETIRED,
    )

    result = replenish_virtual_reserve(reserve_demand.id, now=timezone.now())

    retired.refresh_from_db()
    assert result.recovered_retired == 1
    assert retired.state == BotProfile.State.ACTIVE


@pytest.mark.django_db
def test_profile_cannot_be_leased_by_tournament_and_coop_at_once(shared_ready_profile):
    tournament_demand, coop_demand = shared_ready_profile.demands

    replenish_virtual_reserve(tournament_demand.id)
    replenish_virtual_reserve(coop_demand.id)

    assert ArenaVirtualReserveMember.objects.filter(profile=shared_ready_profile.profile).count() == 1


@pytest.mark.django_db
def test_failed_final_evaluation_rolls_back_retired_reactivation(
    monkeypatch,
    reserve_demand,
):
    retired = _create_bot_profile(
        "reserve_reactivation_rollback",
        state=BotProfile.State.RETIRED,
    )
    evaluations = iter(
        [
            BotLineupEvaluation(({"attack": 200, "defense": 200, "max_hp": 2000},), 600, True),
            BotLineupEvaluation((), 0, False),
        ]
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.evaluate_bot_lineup",
        lambda *_args, **_kwargs: next(evaluations),
    )

    replenish_virtual_reserve(reserve_demand.id)

    retired.refresh_from_db()
    assert retired.state == BotProfile.State.RETIRED
    assert not ArenaVirtualReserveMember.objects.filter(profile=retired).exists()


@pytest.mark.django_db
def test_unrelated_integrity_error_from_member_create_is_not_swallowed(
    monkeypatch,
    reserve_demand,
):
    _create_bot_profile("reserve_unrelated_integrity")
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.evaluate_bot_lineup",
        lambda *_args, **_kwargs: BotLineupEvaluation(
            ({"attack": 200, "defense": 200, "max_hp": 2000},),
            600,
            True,
        ),
    )

    def _raise_integrity_error(**_kwargs):
        raise IntegrityError("unrelated member constraint")

    monkeypatch.setattr(ArenaVirtualReserveMember.objects, "create", _raise_integrity_error)

    with pytest.raises(IntegrityError, match="unrelated member constraint"):
        replenish_virtual_reserve(reserve_demand.id)


@pytest.mark.django_db
def test_reserve_slot_count_is_ready_plus_training_not_exhausted(reserve_demand):
    exhausted_profile = _create_bot_profile("reserve_exhausted")
    ArenaVirtualReserveMember.objects.create(
        demand=reserve_demand,
        profile=exhausted_profile,
        state=ArenaVirtualReserveMember.State.EXHAUSTED,
        accelerated_growth_rounds=8,
    )
    available = _create_bot_profile("reserve_after_exhausted")

    result = replenish_virtual_reserve(reserve_demand.id)

    assert result.ready_count + result.training_count == reserve_demand.reserve_target_count
    assert reserve_demand.reserve_members.filter(profile=available).exists()


@pytest.mark.django_db
def test_reevaluation_resumes_member_exhausted_by_previous_round_limit(reserve_demand):
    profile = _create_bot_profile("reserve_previous_round_limit", guest_stats=[(150, 150, 25)])
    member = ArenaVirtualReserveMember.objects.create(
        demand=reserve_demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.EXHAUSTED,
        accelerated_growth_rounds=6,
    )
    now = timezone.now()

    reevaluate_existing_members(reserve_demand, now=now)

    member.refresh_from_db()
    assert member.state == ArenaVirtualReserveMember.State.TRAINING
    assert member.next_acceleration_at == now


@pytest.mark.django_db
def test_overpopulation_retirement_skips_active_reserve_members(reserve_demand):
    from gameplay.services.virtual_player_core import population_runtime

    reserved = _create_bot_profile("reserve_retirement_protected")
    normal = _create_bot_profile("reserve_retirement_normal")
    ArenaVirtualReserveMember.objects.create(
        demand=reserve_demand,
        profile=reserved,
        state=ArenaVirtualReserveMember.State.READY,
    )

    assert population_runtime._retire_excess_virtual_players(target=0, now=timezone.now()) == 1

    reserved.refresh_from_db()
    normal.refresh_from_db()
    assert reserved.state == BotProfile.State.ACTIVE
    assert normal.state == BotProfile.State.RETIRED


@pytest.mark.django_db
def test_overpopulation_retirement_rechecks_lease_before_state_update(
    monkeypatch,
    reserve_demand,
):
    from django.db.models.query import QuerySet

    from gameplay.services.virtual_player_core import population_runtime

    profile = _create_bot_profile("reserve_retirement_race")
    original_update = QuerySet.update
    injected = False

    def _inject_lease_before_retirement(queryset, **kwargs):
        nonlocal injected
        if not injected and queryset.model is BotProfile and kwargs.get("state") == BotProfile.State.RETIRED:
            injected = True
            ArenaVirtualReserveMember.objects.create(
                demand=reserve_demand,
                profile=profile,
                state=ArenaVirtualReserveMember.State.READY,
            )
        return original_update(queryset, **kwargs)

    monkeypatch.setattr(QuerySet, "update", _inject_lease_before_retirement)

    assert population_runtime._retire_excess_virtual_players(target=0, now=timezone.now()) == 0

    profile.refresh_from_db()
    assert injected is True
    assert profile.state == BotProfile.State.ACTIVE


@pytest.mark.django_db
def test_population_retargeting_rechecks_lease_before_band_update(
    monkeypatch,
    reserve_demand,
):
    from django.db.models.query import QuerySet

    from gameplay.services.virtual_player_core import population_runtime
    from gameplay.services.virtual_player_population import PlannedPopulationCell, PopulationPlan

    profile = _create_bot_profile("reserve_retarget_race")
    region = profile.manor.region
    plan = PopulationPlan(
        cells=(
            PlannedPopulationCell(region, "newbie", 0, 1, 1, 0, 0),
            PlannedPopulationCell(region, "junior", 1, 0, 0, 0, 1),
        ),
        hard_cap=1,
        region_target_rows=((region, 1),),
    )
    original_update = QuerySet.update
    injected = False

    def _inject_lease_before_retarget(queryset, **kwargs):
        nonlocal injected
        if not injected and queryset.model is BotProfile and kwargs.get("target_prestige_band") == "junior":
            injected = True
            ArenaVirtualReserveMember.objects.create(
                demand=reserve_demand,
                profile=profile,
                state=ArenaVirtualReserveMember.State.READY,
            )
        return original_update(queryset, **kwargs)

    monkeypatch.setattr(QuerySet, "update", _inject_lease_before_retarget)

    assert population_runtime.rebalance_virtual_player_target_bands(plan, limit=1) == 0

    profile.refresh_from_db()
    assert injected is True
    assert profile.target_prestige_band == "newbie"


@pytest.mark.django_db
def test_lifecycle_retirement_defers_profile_with_active_reserve_lease(reserve_demand):
    from gameplay.services.virtual_players import maintain_due_virtual_players

    now = timezone.now()
    profile = _create_bot_profile("reserve_lifecycle_retirement_protected")
    member = ArenaVirtualReserveMember.objects.create(
        demand=reserve_demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.READY,
    )
    BotProfile.objects.filter(pk=profile.pk).update(
        next_growth_at=now - timedelta(minutes=1),
        retire_at=now - timedelta(minutes=1),
    )

    assert maintain_due_virtual_players(now=now, limit=10) == 1

    profile.refresh_from_db()
    assert profile.state == BotProfile.State.ACTIVE
    assert profile.next_growth_at > now
    assert ArenaVirtualReserveMember.objects.filter(pk=member.pk).exists()


@pytest.mark.django_db
def test_overpopulation_retirement_skips_bot_in_live_arena_entry():
    from gameplay.services.virtual_player_core import population_runtime

    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=2,
    )
    participating = _create_bot_profile("reserve_live_entry_protected")
    normal = _create_bot_profile("reserve_live_entry_normal")
    ArenaEntry.objects.create(
        tournament=tournament,
        manor=participating.manor,
        source=ArenaEntry.Source.VIRTUAL,
    )

    assert population_runtime._retire_excess_virtual_players(target=0, now=timezone.now()) == 1

    participating.refresh_from_db()
    normal.refresh_from_db()
    assert participating.state == BotProfile.State.ACTIVE
    assert normal.state == BotProfile.State.RETIRED


@pytest.mark.django_db
@pytest.mark.parametrize(
    "state",
    [BotProfile.State.ABANDONED, BotProfile.State.RETIRED, BotProfile.State.STALE],
)
def test_member_reevaluation_releases_profile_that_is_not_arena_eligible(reserve_demand, state):
    profile = _create_bot_profile("reserve_reevaluation_ineligible")
    member = ArenaVirtualReserveMember.objects.create(
        demand=reserve_demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.READY,
    )
    BotProfile.objects.filter(pk=profile.pk).update(state=state)

    reevaluate_existing_members(reserve_demand, now=timezone.now())

    assert not ArenaVirtualReserveMember.objects.filter(pk=member.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [
        BotExternalStrengthReconciliation.Status.PENDING_PROFILE,
        BotExternalStrengthReconciliation.Status.QUARANTINED,
    ],
)
def test_replenish_releases_existing_member_with_unresolved_reconciliation(
    reserve_demand,
    status,
):
    profile = _create_bot_profile(f"reserve_reconciliation_{status}")
    member = ArenaVirtualReserveMember.objects.create(
        demand=reserve_demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.READY,
        current_lineup_power=600,
    )
    now = timezone.now()
    quarantine_fields = (
        {
            "quarantined_at": now,
            "quarantined_phase": BotExternalStrengthReconciliation.Phase.PROFILE,
            "failure_code": "manual_review_required",
        }
        if status == BotExternalStrengthReconciliation.Status.QUARANTINED
        else {}
    )
    BotExternalStrengthReconciliation.objects.create(
        profile_id=profile.id,
        domain_event_kind="arena_member_test",
        domain_event_id=f"member:{member.id}",
        origin_committed_at=now,
        pre_strength_summary={},
        pre_prestige_band="newbie",
        status=status,
        available_at=now,
        **quarantine_fields,
    )

    result = replenish_virtual_reserve(reserve_demand.id, now=now)

    assert not ArenaVirtualReserveMember.objects.filter(pk=member.pk).exists()
    assert result.ready_count == 0
    assert result.training_count == 0
    assert result.creation_needed == 1


@pytest.mark.django_db
def test_overdue_member_reevaluation_pulls_distant_growth_schedule_forward(
    training_member,
):
    now = timezone.now()
    training_member.next_acceleration_at = now + timedelta(hours=1)
    training_member.save(update_fields=["next_acceleration_at"])

    reevaluate_existing_members(training_member.demand, now=now)

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.TRAINING
    assert training_member.next_acceleration_at == now


@pytest.mark.django_db
def test_growth_uses_reference_targets_and_marks_member_ready(monkeypatch, training_member, caplog):
    now = timezone.now()
    reference_guest = ArenaEntryGuest.objects.get(
        entry__tournament_id=training_member.demand.tournament_id,
        entry__source=ArenaEntry.Source.PLAYER,
    )
    reference_guest.snapshot = {
        **reference_guest.snapshot,
        "level": 100,
        "rarity": "purple",
    }
    reference_guest.save(update_fields=["snapshot"])
    calls: list[tuple[int, dict]] = []
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve_demand")
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda profile_id, **kwargs: calls.append((profile_id, kwargs)) or AcceleratedGrowthOutcome.GROWN,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.evaluate_bot_lineup",
        lambda profile, **kwargs: BotLineupEvaluation(
            ({"attack": 200, "defense": 200, "max_hp": 2000},),
            600,
            True,
        ),
    )

    result = grow_due_virtual_reserves(now=now, limit=10)

    training_member.refresh_from_db()
    assert result == 1
    assert len(calls) == 1
    called_profile_id, called_kwargs = calls[0]
    assert called_profile_id == training_member.profile_id
    assert called_kwargs.pop("operation_id").startswith("arena-growth-")
    assert called_kwargs.pop("attempt_ordinal") == 1
    assert called_kwargs == {
        "now": now,
        "minimum_guest_count": 1,
        "minimum_guest_level": 100,
        "guest_rarity_cap": "purple",
        "max_guest_level_step": 20,
    }
    assert training_member.accelerated_growth_rounds == 1
    assert training_member.state == ArenaVirtualReserveMember.State.READY
    assert training_member.next_acceleration_at is None
    record = next(
        record for record in caplog.records if getattr(record, "event", None) == "arena_virtual_profile_grown"
    )
    assert record.profile_id == training_member.profile_id
    assert record.power_before == 450
    assert record.power_after == 600
    assert record.growth_rounds == 1
    assert record.member_state == ArenaVirtualReserveMember.State.READY


@pytest.mark.django_db(transaction=True)
def test_growth_executes_maintenance_without_an_arena_transaction(
    monkeypatch,
    training_member,
):
    atomic_states: list[bool] = []

    def observe_transaction_state(*_args, **_kwargs):
        atomic_states.append(connection.in_atomic_block)
        return AcceleratedGrowthOutcome.BUSY

    monkeypatch.setattr(
        virtual_reserve_pool,
        "accelerate_virtual_player_growth",
        observe_transaction_state,
    )

    assert grow_due_virtual_reserves(now=timezone.now(), limit=1) == 1
    assert atomic_states == [False]


@pytest.mark.django_db(transaction=True)
def test_growth_finalize_failure_cannot_rollback_maintenance_or_safety_event(
    monkeypatch,
    training_member,
):
    now = timezone.now()

    def commit_external_growth(profile_id, **_kwargs):
        BotProfile.objects.filter(pk=profile_id).update(maintenance_sequence=1)
        record_safety_metric_event(
            event_id="arena-growth-finalize-failure",
            metric_name=HARD_CONSTRAINT_METRIC,
            occurred_at=now,
            dimensions={},
            value=1,
        )
        return AcceleratedGrowthOutcome.GROWN

    monkeypatch.setattr(
        virtual_reserve_pool,
        "accelerate_virtual_player_growth",
        commit_external_growth,
    )
    monkeypatch.setattr(
        virtual_reserve_pool,
        "_finalize_virtual_reserve_growth",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("arena finalize failed")),
    )

    with pytest.raises(RuntimeError, match="arena finalize failed"):
        grow_due_virtual_reserves(now=now, limit=1)

    training_member.profile.refresh_from_db()
    training_member.refresh_from_db()
    assert training_member.profile.maintenance_sequence == 1
    assert training_member.growth_claim_token is not None
    assert BotSafetyMetricEvent.objects.filter(event_id="arena-growth-finalize-failure").exists()


@pytest.mark.django_db(transaction=True)
def test_committed_growth_receipt_recovers_finalize_without_repeating_execution(
    monkeypatch,
    training_member,
):
    now = timezone.now()
    execution_count = 0

    monkeypatch.setattr(
        maintenance,
        "read_virtual_player_routing",
        lambda: SimpleNamespace(maintenance_mode=MaintenanceMode.LEGACY_BEFORE_GATE),
    )

    def commit_growth(profile, **_kwargs):
        nonlocal execution_count
        execution_count += 1
        profile.growth_stage += 1
        profile.save(update_fields=["growth_stage", "updated_at"])

    monkeypatch.setattr(maintenance, "_maintain_active_profile", commit_growth)
    monkeypatch.setattr(
        virtual_reserve_pool,
        "_evaluate_member",
        lambda _member: BotLineupEvaluation(
            ({"attack": 200, "defense": 200, "max_hp": 2000},),
            600,
            True,
        ),
    )
    original_finalize = virtual_reserve_pool._finalize_virtual_reserve_growth
    monkeypatch.setattr(
        virtual_reserve_pool,
        "_finalize_virtual_reserve_growth",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("simulated crash after maintenance commit")),
    )

    with pytest.raises(RuntimeError, match="simulated crash"):
        grow_due_virtual_reserves(now=now, limit=1)

    training_member.refresh_from_db()
    operation_id = training_member.growth_operation_id
    assert execution_count == 1
    assert operation_id
    assert BotMaintenanceExecution.objects.filter(operation_id=operation_id).exists()

    monkeypatch.setattr(
        virtual_reserve_pool,
        "_finalize_virtual_reserve_growth",
        original_finalize,
    )
    retry_at = now + virtual_reserve_pool.GROWTH_CLAIM_LEASE + timedelta(seconds=1)
    assert grow_due_virtual_reserves(now=retry_at, limit=1) == 1

    training_member.refresh_from_db()
    assert execution_count == 1
    assert training_member.growth_claim_token is None
    assert training_member.accelerated_growth_rounds == 1
    assert training_member.state == ArenaVirtualReserveMember.State.READY


@pytest.mark.django_db
def test_expired_growth_claim_reuses_operation_and_fences_stale_finalize(
    training_member,
):
    now = timezone.now()
    growth_targets: dict[tuple[int, int], object] = {}
    first = virtual_reserve_pool._claim_due_virtual_reserve_growth(
        member_id=training_member.id,
        demand_id=training_member.demand_id,
        now=now,
        growth_targets=growth_targets,
    )
    assert first is not None
    reclaimed_at = now + virtual_reserve_pool.GROWTH_CLAIM_LEASE + timedelta(seconds=1)
    second = virtual_reserve_pool._claim_due_virtual_reserve_growth(
        member_id=training_member.id,
        demand_id=training_member.demand_id,
        now=reclaimed_at,
        growth_targets=growth_targets,
    )
    assert second is not None
    assert second.claim_token != first.claim_token
    assert second.operation_id == first.operation_id
    assert second.attempt_ordinal == 2

    assert not virtual_reserve_pool._finalize_virtual_reserve_growth(
        first,
        growth_outcome=AcceleratedGrowthOutcome.BUSY,
        now=reclaimed_at,
    )
    training_member.refresh_from_db()
    assert training_member.growth_claim_token == second.claim_token


@pytest.mark.django_db
def test_growth_claim_can_finalize_after_lease_when_it_was_not_reclaimed(
    training_member,
):
    now = timezone.now()
    claim = virtual_reserve_pool._claim_due_virtual_reserve_growth(
        member_id=training_member.id,
        demand_id=training_member.demand_id,
        now=now,
        growth_targets={},
    )
    assert claim is not None

    assert virtual_reserve_pool._finalize_virtual_reserve_growth(
        claim,
        growth_outcome=AcceleratedGrowthOutcome.BUSY,
        now=claim.claim_expires_at + timedelta(seconds=1),
    )

    training_member.refresh_from_db()
    assert training_member.growth_claim_token is None
    assert training_member.next_acceleration_at == now + timedelta(minutes=5)


@pytest.mark.django_db
def test_growth_finalize_revalidates_demand_version(
    monkeypatch,
    training_member,
):
    now = timezone.now()

    def change_demand_version(*_args, **_kwargs):
        ArenaVirtualDemand.objects.filter(pk=training_member.demand_id).update(version=2)
        return AcceleratedGrowthOutcome.GROWN

    monkeypatch.setattr(
        virtual_reserve_pool,
        "accelerate_virtual_player_growth",
        change_demand_version,
    )
    monkeypatch.setattr(
        virtual_reserve_pool,
        "_evaluate_member",
        lambda *_args, **_kwargs: BotLineupEvaluation(
            ({"attack": 200, "defense": 200, "max_hp": 2000},),
            600,
            True,
        ),
    )

    assert grow_due_virtual_reserves(now=now, limit=1) == 1

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.READY
    assert training_member.evaluated_version == 2
    assert training_member.accelerated_growth_rounds == 1
    assert training_member.growth_claim_token is None


@pytest.mark.django_db
def test_reevaluation_and_trim_preserve_member_with_growth_claim(
    monkeypatch,
    training_member,
):
    now = timezone.now()
    claim = virtual_reserve_pool._claim_due_virtual_reserve_growth(
        member_id=training_member.id,
        demand_id=training_member.demand_id,
        now=now,
        growth_targets={},
    )
    assert claim is not None
    demand = training_member.demand
    demand.version += 1
    demand.reserve_target_count = 1
    demand.save(update_fields=["version", "reserve_target_count"])
    monkeypatch.setattr(
        virtual_reserve_pool,
        "evaluate_bot_lineup",
        lambda *_args, **_kwargs: pytest.fail("claimed member must not be reevaluated"),
    )

    reevaluate_existing_members(demand, now=now)
    removable = ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=_create_bot_profile(
            "reserve_growth_claim_removable",
            guest_stats=[(150, 150, 25)],
        ),
        state=ArenaVirtualReserveMember.State.TRAINING,
        current_lineup_power=450,
        next_acceleration_at=now,
    )
    virtual_reserve_pool._trim_surplus_members(demand)
    assert virtual_reserve_pool.release_virtual_reserve_members_for_demand(demand) == 0

    training_member.refresh_from_db()
    assert training_member.growth_claim_token == claim.claim_token
    assert training_member.evaluated_version == claim.member_version
    assert not ArenaVirtualReserveMember.objects.filter(pk=removable.pk).exists()


@pytest.mark.django_db
def test_successful_fill_preserves_in_flight_growth_claim(ready_reserve_demand):
    now = timezone.now()
    claimed_member = ArenaVirtualReserveMember.objects.create(
        demand=ready_reserve_demand.demand,
        profile=_create_bot_profile(
            "reserve_fill_growth_claim",
            guest_stats=[(150, 150, 25)],
        ),
        state=ArenaVirtualReserveMember.State.TRAINING,
        current_lineup_power=450,
        next_acceleration_at=now,
    )
    claim = virtual_reserve_pool._claim_due_virtual_reserve_growth(
        member_id=claimed_member.id,
        demand_id=claimed_member.demand_id,
        now=now,
        growth_targets={},
    )
    assert claim is not None

    assert (
        fill_due_tournament_reserve(
            ready_reserve_demand.demand.tournament_id,
            now=now,
        )
        == 1
    )

    claimed_member.refresh_from_db()
    claimed_member.demand.refresh_from_db()
    assert claimed_member.growth_claim_token == claim.claim_token
    assert claimed_member.demand.status == ArenaVirtualDemand.Status.SATISFIED

    assert (
        grow_due_virtual_reserves(
            now=claim.claim_expires_at + timedelta(seconds=1),
            limit=1,
        )
        == 0
    )
    assert not ArenaVirtualReserveMember.objects.filter(pk=claimed_member.pk).exists()


@pytest.mark.django_db
def test_eighth_failed_growth_marks_member_exhausted(monkeypatch, training_member, caplog):
    training_member.accelerated_growth_rounds = 7
    training_member.save(update_fields=["accelerated_growth_rounds"])
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve_demand")
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda profile_id, **kwargs: AcceleratedGrowthOutcome.GROWN,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.evaluate_bot_lineup",
        lambda profile, **kwargs: BotLineupEvaluation(
            ({"attack": 10, "defense": 10, "max_hp": 100},),
            30,
            False,
        ),
    )

    assert grow_due_virtual_reserves(now=timezone.now(), limit=10) == 1

    training_member.refresh_from_db()
    assert training_member.accelerated_growth_rounds == 8
    assert training_member.state == ArenaVirtualReserveMember.State.EXHAUSTED
    assert training_member.next_acceleration_at is None
    exhausted_record = next(
        record for record in caplog.records if getattr(record, "event", None) == "arena_virtual_profile_exhausted"
    )
    assert exhausted_record.profile_id == training_member.profile_id
    assert exhausted_record.failure_reason == "growth_round_limit"
    assert exhausted_record.growth_rounds == 8


@pytest.mark.django_db
def test_post_fill_growth_waits_fifteen_minutes_before_repeating(monkeypatch, training_member):
    now = timezone.now()
    calls: list[int] = []
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda profile_id, **kwargs: calls.append(profile_id) or AcceleratedGrowthOutcome.GROWN,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.evaluate_bot_lineup",
        lambda profile, **kwargs: BotLineupEvaluation(
            ({"attack": 10, "defense": 10, "max_hp": 100},),
            30,
            False,
        ),
    )

    assert grow_due_virtual_reserves(now=now, limit=10) == 1
    assert grow_due_virtual_reserves(now=now, limit=10) == 0
    assert calls == [training_member.profile_id]
    training_member.refresh_from_db()
    assert training_member.next_acceleration_at == now + timedelta(minutes=15)


@pytest.mark.django_db
def test_pre_fill_growth_waits_one_hour_before_repeating(monkeypatch, training_member):
    now = timezone.now()
    tournament = training_member.demand.tournament
    tournament.virtual_fill_at = now + timedelta(hours=2)
    tournament.save(update_fields=["virtual_fill_at"])
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.GROWN,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.evaluate_bot_lineup",
        lambda profile, **kwargs: BotLineupEvaluation(
            ({"attack": 10, "defense": 10, "max_hp": 100},),
            30,
            False,
        ),
    )

    assert grow_due_virtual_reserves(now=now, limit=10) == 1

    training_member.refresh_from_db()
    assert training_member.next_acceleration_at == now + timedelta(hours=1)


@pytest.mark.django_db
def test_busy_growth_keeps_training_member(monkeypatch, training_member):
    now = timezone.now()
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.BUSY,
    )

    assert grow_due_virtual_reserves(now=now, limit=10) == 1

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.TRAINING
    assert training_member.accelerated_growth_rounds == 0
    assert training_member.next_acceleration_at == now + timedelta(minutes=5)


@pytest.mark.django_db
def test_no_action_growth_retries_without_consuming_a_growth_round(monkeypatch, training_member, caplog):
    now = timezone.now()
    created_at = now - timedelta(hours=11)
    ArenaVirtualReserveMember.objects.filter(pk=training_member.pk).update(created_at=created_at)
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve_demand")
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.NO_ACTION,
    )

    assert grow_due_virtual_reserves(now=now, limit=10) == 1

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.TRAINING
    assert training_member.accelerated_growth_rounds == 0
    assert training_member.next_acceleration_at == now + timedelta(minutes=15)
    record = next(record for record in caplog.records if getattr(record, "failure_reason", None) == "growth_no_action")
    assert record.lease_deadline == (created_at + MAX_NO_ACTION_LEASE_AGE).isoformat()


@pytest.mark.django_db
def test_no_action_retry_stops_at_the_absolute_lease_deadline(monkeypatch, training_member):
    deadline = timezone.now() + timedelta(minutes=10)
    created_at = deadline - MAX_NO_ACTION_LEASE_AGE
    ArenaVirtualReserveMember.objects.filter(pk=training_member.pk).update(created_at=created_at)
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.NO_ACTION,
    )

    assert grow_due_virtual_reserves(now=deadline - timedelta(minutes=10), limit=10) == 1

    training_member.refresh_from_db()
    assert training_member.next_acceleration_at == deadline
    assert training_member.accelerated_growth_rounds == 0

    assert grow_due_virtual_reserves(now=deadline, limit=10) == 1

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.EXHAUSTED
    assert training_member.next_acceleration_at is None
    assert training_member.accelerated_growth_rounds == 0
    assert training_member.created_at == created_at


@pytest.mark.django_db
def test_no_action_at_deadline_frees_active_capacity(monkeypatch, training_member, caplog):
    now = timezone.now()
    ArenaVirtualReserveMember.objects.filter(pk=training_member.pk).update(created_at=now - MAX_NO_ACTION_LEASE_AGE)
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve_demand")
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.NO_ACTION,
    )

    assert grow_due_virtual_reserves(now=now, limit=10) == 1

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.EXHAUSTED
    assert training_member.accelerated_growth_rounds == 0
    assert training_member.next_acceleration_at is None
    assert training_member.demand.reserve_members.exclude(state=ArenaVirtualReserveMember.State.EXHAUSTED).count() == 0
    record = next(
        record for record in caplog.records if getattr(record, "failure_reason", None) == "no_action_lease_deadline"
    )
    assert record.growth_rounds == 0


@pytest.mark.django_db
def test_reevaluation_does_not_reactivate_a_no_action_expired_member(monkeypatch, training_member):
    now = timezone.now()
    ArenaVirtualReserveMember.objects.filter(pk=training_member.pk).update(
        state=ArenaVirtualReserveMember.State.EXHAUSTED,
        created_at=now - MAX_NO_ACTION_LEASE_AGE,
        next_acceleration_at=None,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.evaluate_bot_lineup",
        lambda *_args, **_kwargs: pytest.fail("expired member must not be reevaluated"),
    )

    reevaluate_existing_members(training_member.demand, now=now)

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.EXHAUSTED
    assert training_member.accelerated_growth_rounds == 0
    assert training_member.next_acceleration_at is None


@pytest.mark.django_db
def test_paused_growth_releases_training_member(monkeypatch, training_member, caplog):
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve_demand")
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.PAUSED,
    )

    assert grow_due_virtual_reserves(now=timezone.now(), limit=10) == 1

    assert not ArenaVirtualReserveMember.objects.filter(pk=training_member.pk).exists()
    record = next(record for record in caplog.records if getattr(record, "failure_reason", None) == "growth_paused")
    assert record.growth_rounds == 0


@pytest.mark.django_db
def test_ineligible_growth_releases_training_member(monkeypatch, training_member):
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.INELIGIBLE,
    )

    assert grow_due_virtual_reserves(now=timezone.now(), limit=10) == 1

    assert not ArenaVirtualReserveMember.objects.filter(pk=training_member.pk).exists()


@pytest.mark.django_db
def test_unknown_growth_outcome_raises_without_consuming_round(monkeypatch, training_member):
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: "unexpected",
    )

    with pytest.raises(ValueError, match="Unsupported accelerated growth outcome"):
        grow_due_virtual_reserves(now=timezone.now(), limit=10)

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.TRAINING
    assert training_member.accelerated_growth_rounds == 0


@pytest.mark.django_db
def test_creation_budget_creates_zero_prestige_long_term_reserve(settings, reserve_demand, caplog):
    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "region_floor": 8,
            "region_active_multiplier": 8,
            "global_floor": 32,
            "global_active_multiplier": 20,
        },
        "prestige_bands": {"newbie": [0, 500]},
        "projection": {
            "guest_template_keys": [],
            "gear_template_keys": [],
            "troop_template_keys": [],
        },
    }
    reserve_demand.max_reserve_target_count = 6
    reserve_demand.created_profile_count = 0
    reserve_demand.save(update_fields=["max_reserve_target_count", "created_profile_count"])
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve_demand")

    created = create_due_virtual_reserve_profiles(now=timezone.now(), limit=1)

    reserve_demand.refresh_from_db()
    profile = BotProfile.objects.latest("id")
    assert created == 1
    assert profile.manor.prestige == 0
    assert profile.target_prestige_band == "newbie"
    assert reserve_demand.created_profile_count == 1
    record = next(
        record for record in caplog.records if getattr(record, "event", None) == "arena_virtual_profile_created"
    )
    assert record.profile_id == profile.id
    assert record.demand_id == reserve_demand.id
    assert record.target_prestige_band == "newbie"
    assert record.actual_prestige == 0


@pytest.mark.django_db
def test_creation_records_budget_only_after_profile_is_created(monkeypatch, reserve_demand):
    reserve_demand.max_reserve_target_count = 1
    reserve_demand.created_profile_count = 0
    reserve_demand.save(update_fields=["max_reserve_target_count", "created_profile_count"])
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.replenish_virtual_reserve",
        lambda demand_id, now: ReserveReplenishmentResult(0, 0, 0, 0, 1),
    )
    observed_claims: list[int] = []

    def _create_profile(**_kwargs):
        reserve_demand.refresh_from_db()
        observed_claims.append(reserve_demand.created_profile_count)
        return PopulationMutationResult(
            status=PopulationMutationStatus.CREATED,
            profile=_create_bot_profile("reserve_creation_claimed_profile"),
            hard_cap=10,
            maintained_count=0,
        )

    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.create_virtual_player_with_capacity",
        _create_profile,
    )

    assert create_due_virtual_reserve_profiles(now=timezone.now(), limit=1) == 1
    reserve_demand.refresh_from_db()
    assert observed_claims == [0]
    assert reserve_demand.created_profile_count == 1


@pytest.mark.django_db
def test_creation_delegates_region_selection_to_capacity_owner(monkeypatch, reserve_demand):
    reserve_demand.max_reserve_target_count = 1
    reserve_demand.created_profile_count = 0
    reserve_demand.save(update_fields=["max_reserve_target_count", "created_profile_count"])
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.replenish_virtual_reserve",
        lambda demand_id, now: ReserveReplenishmentResult(0, 0, 0, 0, 1),
    )
    selected_regions: list[str | None] = []

    def _capacity_owned_create(**kwargs):
        selected_regions.append(kwargs["region"])
        return PopulationMutationResult(
            status=PopulationMutationStatus.CAP_REACHED,
            profile=None,
            hard_cap=1,
            maintained_count=1,
        )

    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.create_virtual_player_with_capacity",
        _capacity_owned_create,
    )

    assert create_due_virtual_reserve_profiles(now=timezone.now(), limit=1) == 0
    assert selected_regions == [None]


@pytest.mark.django_db
def test_creation_releases_claim_when_profile_projection_fails(monkeypatch, reserve_demand):
    reserve_demand.max_reserve_target_count = 1
    reserve_demand.created_profile_count = 0
    reserve_demand.save(update_fields=["max_reserve_target_count", "created_profile_count"])
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.replenish_virtual_reserve",
        lambda demand_id, now: ReserveReplenishmentResult(0, 0, 0, 0, 1),
    )
    observed_claims: list[int] = []

    def _fail_projection(**_kwargs):
        reserve_demand.refresh_from_db()
        observed_claims.append(reserve_demand.created_profile_count)
        raise RuntimeError("projection failed")

    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_pool.create_virtual_player_with_capacity",
        _fail_projection,
    )

    with pytest.raises(RuntimeError, match="projection failed"):
        create_due_virtual_reserve_profiles(now=timezone.now(), limit=1)

    reserve_demand.refresh_from_db()
    assert observed_claims == [0]
    assert reserve_demand.created_profile_count == 0


@pytest.mark.django_db
def test_creation_stops_at_dynamic_population_cap(settings, reserve_demand, caplog):
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "region_floor": 0,
            "region_active_multiplier": 0,
            "global_floor": 1,
            "global_active_multiplier": 0,
        },
        "prestige_bands": {"newbie": [0, 500]},
    }
    existing = _create_bot_profile("reserve_creation_cap_existing")
    occupied_tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=2,
    )
    ArenaEntry.objects.create(
        tournament=occupied_tournament,
        manor=existing.manor,
        source=ArenaEntry.Source.VIRTUAL,
    )
    reserve_demand.max_reserve_target_count = 6
    reserve_demand.created_profile_count = 0
    reserve_demand.save(update_fields=["max_reserve_target_count", "created_profile_count"])
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve_demand")

    assert create_due_virtual_reserve_profiles(now=timezone.now(), limit=5) == 0
    assert BotProfile.objects.count() == 1
    assert BotProfile.objects.get() == existing
    record = next(
        record for record in caplog.records if getattr(record, "event", None) == "arena_virtual_fill_deferred"
    )
    assert record.failure_reason == "dynamic_population_cap_reached"
    assert record.hard_cap == 1
    assert record.maintained_count == 1


@pytest.mark.django_db
def test_due_fill_prefers_profiles_outside_shared_24_hour_cooldown(
    ready_reserve_demand,
):
    now = timezone.now()
    recent = ready_reserve_demand.members[0].profile
    old = ready_reserve_demand.members[1].profile
    BotProfile.objects.filter(pk=recent.pk).update(
        last_arena_participated_at=now - timedelta(hours=1),
    )
    BotProfile.objects.filter(pk=old.pk).update(
        last_arena_participated_at=now - timedelta(days=2),
    )

    filled = fill_due_tournament_reserve(
        ready_reserve_demand.demand.tournament_id,
        now=now,
    )

    assert filled == 1
    virtual_entry = ready_reserve_demand.demand.tournament.entries.get(
        source=ArenaEntry.Source.VIRTUAL,
    )
    assert virtual_entry.manor_id == old.manor_id


@pytest.mark.django_db
def test_due_fill_falls_back_to_oldest_recent_profile(ready_reserve_demand):
    now = timezone.now()
    older_recent = ready_reserve_demand.members[0].profile
    newer_recent = ready_reserve_demand.members[1].profile
    BotProfile.objects.filter(pk=older_recent.pk).update(
        last_arena_participated_at=now - timedelta(hours=3),
    )
    BotProfile.objects.filter(pk=newer_recent.pk).update(
        last_arena_participated_at=now - timedelta(hours=1),
    )

    assert (
        fill_due_tournament_reserve(
            ready_reserve_demand.demand.tournament_id,
            now=now,
        )
        == 1
    )

    virtual_entry = ready_reserve_demand.demand.tournament.entries.get(
        source=ArenaEntry.Source.VIRTUAL,
    )
    assert virtual_entry.manor_id == older_recent.manor_id


@pytest.mark.django_db
def test_successful_fill_updates_shared_participation_history(ready_reserve_demand, caplog):
    now = timezone.now()
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve_demand")

    assert (
        fill_due_tournament_reserve(
            ready_reserve_demand.demand.tournament_id,
            now=now,
        )
        == 1
    )

    virtual_entry = ready_reserve_demand.demand.tournament.entries.get(
        source=ArenaEntry.Source.VIRTUAL,
    )
    selected_profile = BotProfile.objects.get(manor_id=virtual_entry.manor_id)
    selected_profile.refresh_from_db()
    assert selected_profile.last_arena_participated_at == now
    assert selected_profile.arena_participation_count == 1
    record = next(
        record for record in caplog.records if getattr(record, "event", None) == "arena_virtual_fill_completed"
    )
    assert record.mode == "tournament"
    assert record.event_id == ready_reserve_demand.demand.tournament_id
    assert record.selected_profile_ids == [selected_profile.id]
    assert record.used_cooldown is False
    assert record.failure_reason == ""


@pytest.mark.django_db
def test_fill_rolls_back_all_virtual_entries_when_ready_member_becomes_invalid(
    monkeypatch,
    ready_reserve_demand,
    caplog,
):
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve_demand")
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_fill._select_bot_lineup",
        lambda *args, **kwargs: [],
    )

    assert fill_due_tournament_reserve(ready_reserve_demand.demand.tournament_id) == 0
    assert (
        ready_reserve_demand.demand.tournament.entries.filter(
            source=ArenaEntry.Source.VIRTUAL,
        ).count()
        == 0
    )
    record = next(
        record for record in caplog.records if getattr(record, "event", None) == "arena_virtual_fill_deferred"
    )
    assert record.failure_reason == "ready_member_revalidation_failed"
    assert record.demand_id == ready_reserve_demand.demand.id


@pytest.mark.django_db
def test_due_coop_fill_uses_shared_reserve_and_moves_to_preparing():
    now = timezone.now()
    event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.RECRUITING,
        player_limit=2,
        guest_limit_per_entry=1,
        prepare_duration_seconds=120,
        virtual_fill_at=now - timedelta(minutes=1),
    )
    _add_real_coop_entry(event, "reserve_due_coop_reference")
    demand = reconcile_coop_demand(event.id, now=now)
    assert demand is not None
    profile = _create_bot_profile("reserve_due_coop_ready")
    ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.READY,
        current_lineup_power=600,
    )

    assert fill_due_coop_reserve(event.id, now=now) == 1

    event.refresh_from_db()
    demand.refresh_from_db()
    assert event.status == ArenaCoopEvent.Status.PREPARING
    assert event.virtual_fill_completed is True
    assert event.prepare_ends_at == now + timedelta(seconds=120)
    assert event.entries.filter(source=ArenaCoopEntry.Source.VIRTUAL).count() == 1
    assert demand.status == ArenaVirtualDemand.Status.SATISFIED


@pytest.mark.django_db
def test_periodic_scan_replenishes_and_fills_due_reserve():
    now = timezone.now()
    demand = _create_tournament_demand(player_limit=2)
    _create_bot_profile("reserve_periodic_scan_ready")

    result = scan_virtual_reserve_demands(now=now, limit=20)

    demand.tournament.refresh_from_db()
    assert result == {
        "scanned": 1,
        "reconciled": 1,
        "ready": 1,
        "training": 0,
        "filled_entries": 1,
    }
    assert demand.tournament.status == ArenaTournament.Status.RUNNING
    assert demand.tournament.entries.filter(source=ArenaEntry.Source.VIRTUAL).count() == 1


@pytest.mark.django_db
def test_periodic_scan_discovers_recruiting_event_without_persisted_demand():
    now = timezone.now()
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=2,
        virtual_fill_at=now - timedelta(minutes=1),
    )
    _add_real_arena_entry(
        tournament,
        "reserve_periodic_discovery_reference",
        attack=200,
        defense=200,
        max_hp=2000,
    )
    _create_bot_profile("reserve_periodic_discovery_ready")
    assert not ArenaVirtualDemand.objects.filter(tournament=tournament).exists()

    result = scan_virtual_reserve_demands(now=now, limit=20)

    tournament.refresh_from_db()
    demand = ArenaVirtualDemand.objects.get(tournament=tournament)
    assert result["scanned"] == 1
    assert result["reconciled"] == 1
    assert result["filled_entries"] == 1
    assert tournament.status == ArenaTournament.Status.RUNNING
    assert demand.status == ArenaVirtualDemand.Status.SATISFIED
