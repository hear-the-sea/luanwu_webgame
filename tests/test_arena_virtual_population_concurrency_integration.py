from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from django.db import close_old_connections, connection
from django.utils import timezone

from gameplay.models import (
    ArenaTournament,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
    BotPopulationControl,
    BotProfile,
)
from gameplay.services.arena import virtual_reserve_pool
from gameplay.services.arena.virtual_reserve_pool import (
    evaluate_bot_lineup,
    grow_due_virtual_reserves,
    replenish_virtual_reserve,
)
from gameplay.services.virtual_player_core.contracts import AcceleratedGrowthOutcome
from gameplay.services.virtual_players import (
    BotProjectionConfig,
    PopulationMutationStatus,
    create_virtual_player_with_capacity,
    retire_virtual_player_if_unprotected,
)
from tests.arena_services.test_virtual_backfill import _create_bot_profile
from tests.test_virtual_player_backfill import _bootstrap_building_types

pytestmark = [pytest.mark.integration]


@pytest.mark.django_db(transaction=True)
def test_two_growth_workers_issue_one_claim_and_execute_without_arena_locks(
    monkeypatch,
) -> None:
    if connection.vendor != "mysql":
        pytest.skip("arena growth claim concurrency requires MySQL row locks")

    now = timezone.now()
    profile = _create_bot_profile("arena_growth_single_claim")
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=2,
    )
    demand = ArenaVirtualDemand.objects.create(
        tournament=tournament,
        status=ArenaVirtualDemand.Status.ACTIVE,
        target_guest_count=1,
        target_team_power=10**12,
        missing_entry_count=1,
        reserve_target_count=1,
        max_reserve_target_count=1,
        next_retry_at=now,
    )
    member = ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.TRAINING,
        evaluated_version=demand.version,
        current_lineup_power=1,
        next_acceleration_at=now - timedelta(seconds=1),
    )
    start = threading.Barrier(2)
    execution_started = threading.Event()
    no_work_finished = threading.Event()
    release_execution = threading.Event()
    calls: list[tuple[str, int, bool]] = []
    results: list[int] = []
    errors: list[BaseException] = []
    results_guard = threading.Lock()

    def blocked_maintenance(_profile_id: int, **kwargs):
        with results_guard:
            calls.append(
                (
                    str(kwargs["operation_id"]),
                    int(kwargs["attempt_ordinal"]),
                    connection.in_atomic_block,
                )
            )
        execution_started.set()
        if not release_execution.wait(timeout=10):
            raise TimeoutError("growth maintenance was not released")
        return AcceleratedGrowthOutcome.BUSY

    monkeypatch.setattr(
        virtual_reserve_pool,
        "accelerate_virtual_player_growth",
        blocked_maintenance,
    )

    def worker() -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            result = grow_due_virtual_reserves(now=now, limit=1)
            with results_guard:
                results.append(result)
            if result == 0:
                no_work_finished.set()
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=worker, daemon=True) for _index in range(2)]
    for thread in threads:
        thread.start()
    assert execution_started.wait(timeout=10)
    assert no_work_finished.wait(timeout=10)
    release_execution.set()
    for thread in threads:
        thread.join(timeout=30)

    member.refresh_from_db()
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert sorted(results) == [0, 1]
    assert len(calls) == 1
    assert calls[0][0].startswith("arena-growth-")
    assert calls[0][1:] == (1, False)
    assert member.growth_claim_token is None
    assert member.next_acceleration_at == now + timedelta(minutes=5)


@pytest.mark.django_db(transaction=True)
def test_concurrent_arena_demands_share_last_population_slot(settings):
    if connection.vendor != "mysql":
        pytest.skip("arena virtual population concurrency requires MySQL select_for_update semantics")

    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "region_floor": 0,
            "region_active_multiplier": 0,
            "global_floor": 1,
            "global_active_multiplier": 0,
        },
        "prestige_bands": {"newbie": [0, 500]},
    }
    now = timezone.now()
    BotPopulationControl.objects.get_or_create()
    profiles = [
        _create_bot_profile(
            f"arena_population_race_{index}",
            state=BotProfile.State.RETIRED,
        )
        for index in range(2)
    ]
    tournaments = [
        ArenaTournament.objects.create(
            status=ArenaTournament.Status.RECRUITING,
            player_limit=2,
        )
        for _index in range(2)
    ]
    probe = evaluate_bot_lineup(
        profiles[0],
        mode="tournament",
        event_id=tournaments[0].id,
        target_guest_count=1,
        target_team_power=10**12,
    )
    assert probe.snapshots
    demands = [
        ArenaVirtualDemand.objects.create(
            tournament=tournament,
            target_guest_count=1,
            target_team_power=probe.selected_power,
            missing_entry_count=1,
            reserve_target_count=1,
            max_reserve_target_count=1,
            next_retry_at=now,
        )
        for tournament in tournaments
    ]
    start = threading.Barrier(2)
    outcomes: list[int] = []
    errors: list[BaseException] = []
    results_guard = threading.Lock()

    def _worker(demand_id: int) -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            replenishment = replenish_virtual_reserve(demand_id, now=now)
            with results_guard:
                outcomes.append(replenishment.recovered_retired)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=_worker, args=(demand.id,), daemon=True) for demand in demands]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert sorted(outcomes) == [0, 1]
    assert (
        BotProfile.objects.exclude(
            state__in=[BotProfile.State.STALE, BotProfile.State.RETIRED],
        ).count()
        == 1
    )
    assert ArenaVirtualReserveMember.objects.count() == 1
    assert (
        BotProfile.objects.filter(pk__in=[profile.pk for profile in profiles], state=BotProfile.State.ACTIVE).count()
        == 1
    )
    assert (
        BotProfile.objects.filter(pk__in=[profile.pk for profile in profiles], state=BotProfile.State.RETIRED).count()
        == 1
    )


@pytest.mark.django_db(transaction=True)
def test_concurrent_capacity_owned_creation_rechecks_region_shortage(settings):
    if connection.vendor != "mysql":
        pytest.skip("arena virtual population concurrency requires MySQL select_for_update semantics")

    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "region_floor": 1,
            "region_active_multiplier": 0,
            "global_floor": 4,
            "global_active_multiplier": 0,
        },
        "prestige_bands": {"newbie": [0, 500]},
        "projection": {
            "guest_template_keys": [],
            "gear_template_keys": [],
            "troop_template_keys": [],
            "technology_keys": [],
        },
    }
    now = timezone.now()
    BotPopulationControl.objects.get_or_create()
    start = threading.Barrier(2)
    regions: list[str] = []
    errors: list[BaseException] = []
    results_guard = threading.Lock()

    def _worker(growth_seed: int) -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            mutation = create_virtual_player_with_capacity(
                region=None,
                prestige_band="newbie",
                growth_seed=growth_seed,
                now=now,
                projection=BotProjectionConfig(0, 1, 0, 1),
                start_from_zero=True,
            )
            assert mutation.status is PopulationMutationStatus.CREATED
            assert mutation.profile is not None
            with results_guard:
                regions.append(str(mutation.profile.manor.region))
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=_worker, args=(90_001 + index,), daemon=True) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(regions) == 2
    assert len(set(regions)) == 2


@pytest.mark.django_db(transaction=True)
def test_reserve_replenishment_racing_retirement_never_leases_retired_profile(settings):
    if connection.vendor != "mysql":
        pytest.skip("arena reserve retirement concurrency requires MySQL select_for_update semantics")

    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "region_floor": 0,
            "region_active_multiplier": 0,
            "global_floor": 1,
            "global_active_multiplier": 0,
        },
        "prestige_bands": {"newbie": [0, 500]},
    }
    now = timezone.now()
    BotPopulationControl.objects.get_or_create()
    profile = _create_bot_profile("arena_reserve_retirement_race")
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=2,
    )
    probe = evaluate_bot_lineup(
        profile,
        mode="tournament",
        event_id=tournament.id,
        target_guest_count=1,
        target_team_power=10**12,
    )
    assert probe.snapshots
    demand = ArenaVirtualDemand.objects.create(
        tournament=tournament,
        target_guest_count=1,
        target_team_power=probe.selected_power,
        missing_entry_count=1,
        reserve_target_count=1,
        max_reserve_target_count=1,
        next_retry_at=now,
    )

    start = threading.Barrier(2)
    replenishments = []
    retirement_results: list[bool] = []
    errors: list[BaseException] = []
    results_guard = threading.Lock()

    def _replenish_worker() -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            result = replenish_virtual_reserve(demand.id, now=now)
            with results_guard:
                replenishments.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    def _retire_worker() -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            result = retire_virtual_player_if_unprotected(profile.pk, now=now)
            with results_guard:
                retirement_results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    threads = [
        threading.Thread(target=_replenish_worker, daemon=True),
        threading.Thread(target=_retire_worker, daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    profile.refresh_from_db()
    member_exists = ArenaVirtualReserveMember.objects.filter(
        demand=demand,
        profile=profile,
    ).exists()
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(replenishments) == 1
    assert len(retirement_results) == 1
    assert not (member_exists and profile.state == BotProfile.State.RETIRED)
    if member_exists:
        assert profile.state == BotProfile.State.ACTIVE
