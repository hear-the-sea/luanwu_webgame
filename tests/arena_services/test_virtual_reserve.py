from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import IntegrityError
from django.utils import timezone

from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaEntryGuest,
    ArenaTournament,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
    BotProfile,
)
from gameplay.services.arena.virtual_backfill import BotLineupEvaluation
from gameplay.services.arena.virtual_reserve import (
    ReserveReplenishmentResult,
    create_due_virtual_reserve_profiles,
    fill_due_coop_reserve,
    fill_due_tournament_reserve,
    grow_due_virtual_reserves,
    reconcile_coop_demand,
    reconcile_tournament_demand,
    replenish_virtual_reserve,
    scan_virtual_reserve_demands,
)
from gameplay.services.virtual_players import AcceleratedGrowthOutcome
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
def test_reserve_priority_uses_active_then_abandoned_then_retired_then_training(
    bot_profiles_for_reserve,
    caplog,
):
    rows = bot_profiles_for_reserve
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve")

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
        "gameplay.services.arena.virtual_reserve.evaluate_bot_lineup",
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
        "gameplay.services.arena.virtual_reserve.evaluate_bot_lineup",
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
    from gameplay.services.arena.virtual_reserve import _reevaluate_existing_members

    profile = _create_bot_profile("reserve_previous_round_limit", guest_stats=[(150, 150, 25)])
    member = ArenaVirtualReserveMember.objects.create(
        demand=reserve_demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.EXHAUSTED,
        accelerated_growth_rounds=6,
    )
    now = timezone.now()

    _reevaluate_existing_members(reserve_demand, now=now)

    member.refresh_from_db()
    assert member.state == ArenaVirtualReserveMember.State.TRAINING
    assert member.next_acceleration_at == now


@pytest.mark.django_db
def test_overpopulation_retirement_skips_active_reserve_members(reserve_demand):
    from gameplay.services import virtual_players

    reserved = _create_bot_profile("reserve_retirement_protected")
    normal = _create_bot_profile("reserve_retirement_normal")
    ArenaVirtualReserveMember.objects.create(
        demand=reserve_demand,
        profile=reserved,
        state=ArenaVirtualReserveMember.State.READY,
    )

    assert virtual_players._retire_excess_virtual_players(target=0, now=timezone.now()) == 1

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

    from gameplay.services import virtual_players

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

    assert virtual_players._retire_excess_virtual_players(target=0, now=timezone.now()) == 0

    profile.refresh_from_db()
    assert injected is True
    assert profile.state == BotProfile.State.ACTIVE


@pytest.mark.django_db
def test_population_retargeting_rechecks_lease_before_band_update(
    monkeypatch,
    reserve_demand,
):
    from django.db.models.query import QuerySet

    from gameplay.services import virtual_players
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

    assert virtual_players.rebalance_virtual_player_target_bands(plan, limit=1) == 0

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
    from gameplay.services import virtual_players

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

    assert virtual_players._retire_excess_virtual_players(target=0, now=timezone.now()) == 1

    participating.refresh_from_db()
    normal.refresh_from_db()
    assert participating.state == BotProfile.State.ACTIVE
    assert normal.state == BotProfile.State.RETIRED


@pytest.mark.django_db
def test_member_reevaluation_releases_profile_that_is_no_longer_active(reserve_demand):
    from gameplay.services.arena.virtual_reserve import _reevaluate_existing_members

    profile = _create_bot_profile("reserve_reevaluation_retired")
    member = ArenaVirtualReserveMember.objects.create(
        demand=reserve_demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.READY,
    )
    BotProfile.objects.filter(pk=profile.pk).update(state=BotProfile.State.RETIRED)

    _reevaluate_existing_members(reserve_demand, now=timezone.now())

    assert not ArenaVirtualReserveMember.objects.filter(pk=member.pk).exists()


@pytest.mark.django_db
def test_overdue_member_reevaluation_pulls_distant_growth_schedule_forward(training_member):
    from gameplay.services.arena.virtual_reserve import _reevaluate_existing_members

    now = timezone.now()
    training_member.next_acceleration_at = now + timedelta(hours=1)
    training_member.save(update_fields=["next_acceleration_at"])

    _reevaluate_existing_members(training_member.demand, now=now)

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
    reference_guest.snapshot = {**reference_guest.snapshot, "level": 100, "rarity": "purple"}
    reference_guest.save(update_fields=["snapshot"])
    calls: list[tuple[int, dict]] = []
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve")
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve.accelerate_virtual_player_growth",
        lambda profile_id, **kwargs: calls.append((profile_id, kwargs)) or AcceleratedGrowthOutcome.GROWN,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve.evaluate_bot_lineup",
        lambda profile, **kwargs: BotLineupEvaluation(
            ({"attack": 200, "defense": 200, "max_hp": 2000},),
            600,
            True,
        ),
    )

    result = grow_due_virtual_reserves(now=now, limit=10)

    training_member.refresh_from_db()
    assert result == 1
    assert calls == [
        (
            training_member.profile_id,
            {
                "now": now,
                "minimum_guest_count": 1,
                "minimum_guest_level": 100,
                "guest_rarity_cap": "purple",
                "max_guest_level_step": 20,
            },
        )
    ]
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


@pytest.mark.django_db
def test_eighth_failed_growth_marks_member_exhausted(monkeypatch, training_member, caplog):
    training_member.accelerated_growth_rounds = 7
    training_member.save(update_fields=["accelerated_growth_rounds"])
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve")
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve.accelerate_virtual_player_growth",
        lambda profile_id, **kwargs: AcceleratedGrowthOutcome.GROWN,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve.evaluate_bot_lineup",
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
        "gameplay.services.arena.virtual_reserve.accelerate_virtual_player_growth",
        lambda profile_id, **kwargs: calls.append(profile_id) or AcceleratedGrowthOutcome.GROWN,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve.evaluate_bot_lineup",
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
        "gameplay.services.arena.virtual_reserve.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.GROWN,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve.evaluate_bot_lineup",
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
        "gameplay.services.arena.virtual_reserve.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.BUSY,
    )

    assert grow_due_virtual_reserves(now=now, limit=10) == 1

    training_member.refresh_from_db()
    assert training_member.state == ArenaVirtualReserveMember.State.TRAINING
    assert training_member.accelerated_growth_rounds == 0
    assert training_member.next_acceleration_at == now + timedelta(minutes=5)


@pytest.mark.django_db
def test_ineligible_growth_releases_training_member(monkeypatch, training_member):
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve.accelerate_virtual_player_growth",
        lambda *_args, **_kwargs: AcceleratedGrowthOutcome.INELIGIBLE,
    )

    assert grow_due_virtual_reserves(now=timezone.now(), limit=10) == 1

    assert not ArenaVirtualReserveMember.objects.filter(pk=training_member.pk).exists()


@pytest.mark.django_db
def test_unknown_growth_outcome_raises_without_consuming_round(monkeypatch, training_member):
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve.accelerate_virtual_player_growth",
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
        "projection": {"guest_template_keys": [], "gear_template_keys": [], "troop_template_keys": []},
    }
    reserve_demand.max_reserve_target_count = 6
    reserve_demand.created_profile_count = 0
    reserve_demand.save(update_fields=["max_reserve_target_count", "created_profile_count"])
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve")

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
    from gameplay.services.virtual_players import PopulationMutationResult, PopulationMutationStatus

    reserve_demand.max_reserve_target_count = 1
    reserve_demand.created_profile_count = 0
    reserve_demand.save(update_fields=["max_reserve_target_count", "created_profile_count"])
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve.replenish_virtual_reserve",
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
        "gameplay.services.arena.virtual_reserve.create_virtual_player_with_capacity",
        _create_profile,
    )

    assert create_due_virtual_reserve_profiles(now=timezone.now(), limit=1) == 1
    reserve_demand.refresh_from_db()
    assert observed_claims == [0]
    assert reserve_demand.created_profile_count == 1


@pytest.mark.django_db
def test_creation_delegates_region_selection_to_capacity_owner(monkeypatch, reserve_demand):
    from gameplay.services.virtual_players import PopulationMutationResult, PopulationMutationStatus

    reserve_demand.max_reserve_target_count = 1
    reserve_demand.created_profile_count = 0
    reserve_demand.save(update_fields=["max_reserve_target_count", "created_profile_count"])
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve.replenish_virtual_reserve",
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
        "gameplay.services.arena.virtual_reserve.create_virtual_player_with_capacity",
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
        "gameplay.services.arena.virtual_reserve.replenish_virtual_reserve",
        lambda demand_id, now: ReserveReplenishmentResult(0, 0, 0, 0, 1),
    )
    observed_claims: list[int] = []

    def _fail_projection(**_kwargs):
        reserve_demand.refresh_from_db()
        observed_claims.append(reserve_demand.created_profile_count)
        raise RuntimeError("projection failed")

    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve.create_virtual_player_with_capacity",
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
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve")

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
def test_due_fill_prefers_profiles_outside_shared_24_hour_cooldown(ready_reserve_demand):
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
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve")

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
    caplog.set_level(logging.INFO, logger="gameplay.services.arena.virtual_reserve")
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_backfill._select_bot_lineup",
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
