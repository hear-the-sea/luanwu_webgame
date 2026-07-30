from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.core.cache import cache
from django.db import close_old_connections, connection
from django.utils import timezone

from battle.models import BattleReport
from gameplay.models import ArenaTournament, ArenaVirtualDemand, ArenaVirtualReserveMember, BotProfile, RaidRun
from gameplay.services import virtual_players
from gameplay.services.arena.virtual_reserve_pool import evaluate_bot_lineup, replenish_virtual_reserve
from gameplay.services.raid.combat import battle as combat_battle
from gameplay.services.virtual_player_core import maintenance as virtual_player_maintenance
from gameplay.services.virtual_player_core import population_runtime as virtual_player_population
from guests.models import Guest, GuestStatus, GuestTemplate
from tests.raid_concurrency_integration.support import build_attacker_defender, create_marching_run

pytestmark = [pytest.mark.integration]


def _build_h01_raid_case(django_user_model, *, suffix: str):
    now = timezone.now()
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username=f"h01_{suffix}_attacker",
        defender_username=f"h01_{suffix}_defender",
    )
    type(defender).objects.filter(pk=defender.pk).update(
        region="north",
        prestige=900,
        newbie_protection_until=None,
        defeat_protection_until=None,
        peace_shield_until=None,
    )
    defender.refresh_from_db()
    profile = BotProfile.objects.create(
        manor=defender,
        archetype=BotProfile.Archetype.RICH,
        state=BotProfile.State.ACTIVE,
        prestige_band="junior",
        target_prestige_band="junior",
        current_prestige_band="junior",
        growth_seed=90_000 + defender.pk,
        growth_stage=3,
        next_growth_at=now - timedelta(minutes=1),
        abandon_at=now + timedelta(days=30),
        retire_at=now + timedelta(days=60),
        loot_budget_daily=1,
        maintenance_started_at=now - timedelta(days=1),
        last_planned_at=now,
    )
    template = GuestTemplate.objects.create(
        key=f"h01_{suffix}_defender_guest",
        name="H01 Defender Guest",
        archetype="military",
        rarity="green",
        base_attack=100,
        base_intellect=80,
        base_defense=90,
        base_agility=70,
        base_luck=50,
        base_hp=1200,
    )
    Guest.objects.create(
        manor=defender,
        template=template,
        status=GuestStatus.IDLE,
        level=30,
        force=120,
        intellect=100,
        defense_stat=110,
        agility=90,
        current_hp=template.base_hp,
    )
    run = create_marching_run(attacker, defender, battle_due=True)
    return run, profile, now


def _configure_h01_battle(monkeypatch) -> list[int]:
    executed_reports: list[int] = []

    def _execute(locked_run: RaidRun) -> BattleReport:
        report = BattleReport.objects.create(
            manor=locked_run.attacker,
            opponent_name=locked_run.defender.display_name,
            battle_type="raid",
            attacker_team=[],
            attacker_troops={},
            defender_team=[],
            defender_troops={},
            rounds=[],
            losses={},
            drops={},
            winner="attacker",
            starts_at=timezone.now(),
            completed_at=timezone.now(),
        )
        executed_reports.append(report.pk)
        return report

    monkeypatch.setattr(combat_battle, "_execute_raid_battle", _execute)
    monkeypatch.setattr(combat_battle, "audit_battle_replay_metadata", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "apply_defender_troop_losses", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_calculate_loot", lambda *_args, **_kwargs: ({"silver": 1}, {}))
    monkeypatch.setattr(combat_battle, "_apply_prestige_changes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_defeat_protection", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_capture_reward", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_apply_salvage_reward", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_get_defender_battle_block_reason", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_send_raid_battle_messages", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_dismiss_marching_raids_if_protected", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_battle, "_dispatch_complete_raid_task", lambda *_args, **_kwargs: None)
    return executed_reports


def _run_h01_cross_race(
    monkeypatch,
    *,
    run: RaidRun,
    profile: BotProfile,
    now,
    competitor: Callable[[], Any],
) -> tuple[list[Any], list[tuple[str, BaseException]], bool]:
    retirement_lock_held = threading.Event()
    release_retirement = threading.Event()
    results: list[Any] = []
    errors: list[tuple[str, BaseException]] = []
    result_guard = threading.Lock()
    original_protection_check = virtual_player_maintenance.is_virtual_profile_arena_protected

    def _hold_after_real_protection_check(*, profile_id: int, manor_id: int) -> bool:
        protected = original_protection_check(profile_id=profile_id, manor_id=manor_id)
        if profile_id == profile.pk:
            retirement_lock_held.set()
            if not release_retirement.wait(timeout=30):
                raise AssertionError("H-01 retirement row lock was not released by the test")
        return protected

    monkeypatch.setattr(
        virtual_player_maintenance,
        "is_virtual_profile_arena_protected",
        _hold_after_real_protection_check,
    )

    def _raid_worker() -> None:
        close_old_connections()
        try:
            local_run = RaidRun.objects.get(pk=run.pk)
            combat_battle.process_raid_battle(local_run, now=now)
        except BaseException as exc:  # pragma: no cover - asserted below
            with result_guard:
                errors.append(("raid", exc))
        finally:
            close_old_connections()

    def _competitor_worker() -> None:
        close_old_connections()
        try:
            outcome = competitor()
            with result_guard:
                results.append(outcome)
        except BaseException as exc:  # pragma: no cover - asserted below
            with result_guard:
                errors.append(("competitor", exc))
        finally:
            close_old_connections()

    raid_thread = threading.Thread(target=_raid_worker, daemon=True)
    competitor_thread = threading.Thread(target=_competitor_worker, daemon=True)
    competitor_finished_while_locked = False
    raid_thread.start()
    try:
        assert retirement_lock_held.wait(timeout=30)
        assert RaidRun.objects.get(pk=run.pk).status == RaidRun.Status.RETURNING
        competitor_thread.start()
        competitor_thread.join(timeout=30)
        competitor_finished_while_locked = not competitor_thread.is_alive()
    finally:
        release_retirement.set()
        if competitor_thread.ident is not None:
            competitor_thread.join(timeout=30)
        raid_thread.join(timeout=30)

    assert not raid_thread.is_alive()
    assert not competitor_thread.is_alive()
    return results, errors, competitor_finished_while_locked


def _assert_committed_raid_retired_profile(
    *,
    run: RaidRun,
    profile: BotProfile,
    executed_reports: list[int],
) -> None:
    run.refresh_from_db()
    profile.refresh_from_db()
    assert executed_reports == [run.battle_report_id]
    assert run.status == RaidRun.Status.RETURNING
    assert run.is_attacker_victory is True
    assert run.loot_resources == {"silver": 1}
    assert profile.state == BotProfile.State.RETIRED


@pytest.mark.django_db(transaction=True)
def test_h01_raid_callback_racing_maintenance_skips_locked_profile(monkeypatch, django_user_model, settings):
    if connection.vendor != "mysql":
        pytest.skip("H-01 cross-concurrency gate requires MySQL row-lock semantics")

    settings.VIRTUAL_PLAYER_CONFIG = {
        "enabled": True,
        "lifecycle": {
            "empty_hit_stale_threshold": 0,
            "stale_no_interaction_days": 0,
        },
        "prestige_bands": {"junior": [0, None]},
    }
    run, profile, now = _build_h01_raid_case(django_user_model, suffix="maintenance")
    executed_reports = _configure_h01_battle(monkeypatch)

    results, errors, competitor_finished = _run_h01_cross_race(
        monkeypatch,
        run=run,
        profile=profile,
        now=now,
        competitor=lambda: virtual_players.maintain_due_virtual_players(now=now, limit=1),
    )

    assert competitor_finished is True
    assert errors == []
    assert results == [0]
    _assert_committed_raid_retired_profile(run=run, profile=profile, executed_reports=executed_reports)


@pytest.mark.django_db(transaction=True)
def test_h01_raid_callback_racing_population_roll_skips_locked_profile(
    monkeypatch,
    django_user_model,
    settings,
):
    if connection.vendor != "mysql":
        pytest.skip("H-01 cross-concurrency gate requires MySQL and Redis semantics")

    settings.VIRTUAL_PLAYER_CONFIG = {
        "enabled": True,
        "population": {
            "cell_floor": 0,
            "cell_active_multiplier": 0,
            "exploration_supply": 0,
            "hard_cap": 0,
        },
        "prestige_bands": {"junior": [0, None]},
    }
    run, profile, now = _build_h01_raid_case(django_user_model, suffix="population")
    executed_reports = _configure_h01_battle(monkeypatch)
    lock_key = f"virtual_players:h01_roll_lock:{profile.pk}"
    monkeypatch.setattr(virtual_player_population, "ROLL_LOCK_KEY", lock_key)
    cache.delete(lock_key)
    try:
        results, errors, competitor_finished = _run_h01_cross_race(
            monkeypatch,
            run=run,
            profile=profile,
            now=now,
            competitor=lambda: virtual_player_population.roll_virtual_player_population(limit=0, now=now),
        )
    finally:
        cache.delete(lock_key)

    assert competitor_finished is True
    assert errors == []
    assert results == [0]
    _assert_committed_raid_retired_profile(run=run, profile=profile, executed_reports=executed_reports)


@pytest.mark.django_db(transaction=True)
def test_h01_raid_callback_racing_reserve_replenishment_never_leases_retired_profile(
    monkeypatch,
    django_user_model,
    settings,
):
    if connection.vendor != "mysql":
        pytest.skip("H-01 cross-concurrency gate requires MySQL row-lock semantics")

    settings.VIRTUAL_PLAYER_CONFIG = {
        "enabled": True,
        "population": {
            "region_floor": 0,
            "region_active_multiplier": 0,
            "global_floor": 1,
            "global_active_multiplier": 0,
        },
        "prestige_bands": {"junior": [0, None]},
    }
    run, profile, now = _build_h01_raid_case(django_user_model, suffix="reserve")
    executed_reports = _configure_h01_battle(monkeypatch)
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=2,
    )
    evaluation = evaluate_bot_lineup(
        profile,
        mode="tournament",
        event_id=tournament.pk,
        target_guest_count=1,
        target_team_power=10**12,
    )
    assert evaluation.snapshots
    demand = ArenaVirtualDemand.objects.create(
        tournament=tournament,
        target_guest_count=1,
        target_team_power=evaluation.selected_power,
        missing_entry_count=1,
        reserve_target_count=1,
        max_reserve_target_count=1,
        next_retry_at=now,
    )

    results, errors, competitor_finished = _run_h01_cross_race(
        monkeypatch,
        run=run,
        profile=profile,
        now=now,
        competitor=lambda: replenish_virtual_reserve(demand.pk, now=now),
    )

    assert competitor_finished is True
    assert errors == []
    assert len(results) == 1
    assert results[0].ready_count == 0
    assert results[0].training_count == 0
    assert not ArenaVirtualReserveMember.objects.filter(profile=profile).exists()
    _assert_committed_raid_retired_profile(run=run, profile=profile, executed_reports=executed_reports)
