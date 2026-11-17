"""采集延迟入账与异常队伍恢复。"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils.dateparse import parse_datetime

from gameplay.services.season import expeditions, merit
from guests.models import GuestStatus
from tests.test_season_collection_waves import arrival
from tests.test_season_collection_waves import world as _world  # noqa: F401
from tests.test_season_resource_integration import advance_resource_run, issue
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(request):
    return request.getfixturevalue("_world")


def test_delayed_collection_credits_planned_last_wave_not_scanner_time(world):
    season, player, clock = world
    member, guest = player()
    run = arrival(member, guest, clock)
    last_wave = run.due_at + timedelta(minutes=20)
    late = last_wave + timedelta(days=8)
    state = season.faction_states.get(faction=member.faction)
    state.produced_at = late
    state.save(update_fields=["produced_at"])
    assert expeditions.process_expedition(run.pk, late)
    run.refresh_from_db()
    credit = member.actions.get(kind="merit_credit", result__reason="讨伐胜利")
    assert run.result["collection_waves"]["wins"] == 5
    assert parse_datetime(credit.result["occurred_at"]) == last_wave
    assert credit.week == (last_wave - season.starts_at).days // 7 + 1
    assert credit.result["merit"] > 0
    assert not expeditions.process_expedition(run.pk, late)


def test_failed_collection_recovers_prior_wins_once(world):
    _, player, clock = world
    member, guest = player()
    run = arrival(member, guest, clock)
    advance_resource_run(run, clock)
    assert run.result["collection_waves"]["wins"] == 1
    run.status = "failed"
    run.save(update_fields=["status"])
    late = run.due_at + timedelta(minutes=15)
    assert expeditions.advance_due_expeditions(late)["processed"] >= 1
    run.refresh_from_db()
    assert run.status == "returning" and run.result["collection_waves"]["wins"] == 5
    assert member.actions.filter(kind="merit_credit", result__reason="讨伐胜利").count() == 1
    assert not expeditions.process_expedition(run.pk, late)


def test_delayed_collection_stops_at_action_cutoff_and_credits_last_valid_wave(world):
    season, player, clock = world
    member, guest = player()
    run = issue(member, guest, clock)
    interval = run.result["collection_waves"]["interval_seconds"]
    arrival_at = run.launched_at + timedelta(seconds=run.travel_seconds)
    season.starts_at = arrival_at + timedelta(seconds=interval * 2 + interval // 2) - timedelta(days=83)
    season.save(update_fields=["starts_at"])
    assert expeditions.process_expedition(run.pk, arrival_at + timedelta(seconds=interval * 4))
    run.refresh_from_db()
    credit = member.actions.get(kind="merit_credit", result__reason="讨伐胜利")
    assert run.result["collection_waves"]["wins"] == 2
    assert parse_datetime(credit.result["occurred_at"]) == arrival_at + timedelta(seconds=interval * 2)


def test_failed_collection_without_phase_snapshot_returns_once(world):
    _, player, clock = world
    member, guest = player()
    run = arrival(member, guest, clock)
    advance_resource_run(run, clock)
    run.status = "failed"
    run.result["collection_waves"].pop("next_wave_at")
    run.save(update_fields=["status", "result"])
    due = run.due_at
    assert expeditions.process_expedition(run.pk, due)
    run.refresh_from_db()
    guest.refresh_from_db()
    assert run.status == "completed" and run.battles == 1
    assert guest.status != GuestStatus.DEPLOYED
    assert "缺少波次时间" in run.result["collection_recovery"]["reason"]
    assert not member.actions.filter(kind="merit_credit", result__reason="讨伐胜利").exists()
    assert not expeditions.process_expedition(run.pk, due)


def test_scanner_returns_failed_collection_when_snapshot_invalid(world):
    _, player, clock = world
    member, guest = player()
    run = arrival(member, guest, clock)
    run.status = "failed"
    run.result["collection_waves"] = {}
    run.save(update_fields=["status", "result"])
    result = expeditions.advance_due_expeditions(run.due_at)
    run.refresh_from_db()
    guest.refresh_from_db()
    assert result["errors"] == 0 and result["processed"] >= 1
    assert run.status == "completed" and run.error
    assert guest.status != GuestStatus.DEPLOYED


@pytest.mark.parametrize("phase", ["outbound", "stationed"])
def test_corrupt_active_collection_returns_and_does_not_block_settlement(world, phase):
    from gameplay.services.season.settlement_pending import has_pending_contributions_locked

    season, player, clock = world
    member, guest = player()
    run = issue(member, guest, clock) if phase == "outbound" else arrival(member, guest, clock)
    original_snapshot = {**run.result["collection_waves"], "interval_seconds": 0}
    run.result = {**run.result, "collection_waves": original_snapshot}
    run.save(update_fields=["result"])
    assert expeditions.process_expedition(run.pk, run.due_at)
    run.refresh_from_db()
    guest.refresh_from_db()
    assert run.status == "completed" and guest.status != GuestStatus.DEPLOYED
    assert run.result["collection_waves"] == original_snapshot
    assert not has_pending_contributions_locked(season, season.ends_at)


def test_player_recall_returns_corrupt_collection_immediately(world):
    from django.db import transaction

    _, player, clock = world
    member, guest = player()
    run = arrival(member, guest, clock)
    run.status = "failed"
    run.result["collection_waves"] = {}
    run.save(update_fields=["status", "result"])
    with transaction.atomic():
        result = expeditions.recall_locked(member, run.pk, clock[0])
    run.refresh_from_db()
    guest.refresh_from_db()
    assert run.status == "completed" and guest.status != GuestStatus.DEPLOYED
    assert "已归队" in result["message"]


def test_stationed_legacy_collection_uses_due_at_when_wave_time_missing(world):
    _, player, clock = world
    member, guest = player()
    run = arrival(member, guest, clock)
    run.result["collection_waves"].pop("next_wave_at")
    run.save(update_fields=["result"])
    assert expeditions.process_expedition(run.pk, run.due_at)
    run.refresh_from_db()
    assert run.status == "stationed" and run.result["collection_waves"]["wins"] == 1
    assert "collection_recovery" not in run.result


def test_corrupt_collection_returns_surviving_troops_once_and_preserves_claims(world):
    season, player, clock = world
    member, guest = player()
    state = season.faction_states.get(faction=member.faction)
    state.troops = {**state.troops, "dao_sheng": 100}
    state.save(update_fields=["troops"])
    run = issue(member, guest, clock, troops={"dao_sheng": 10})
    advance_resource_run(run, clock)
    state.refresh_from_db()
    stock_before = state.troops["dao_sheng"]
    member.claims.create(key=f"merit:existing:{run.pk}")
    run.status = "failed"
    run.result["collection_waves"] = {}
    run.save(update_fields=["status", "result"])
    assert expeditions.process_expedition(run.pk, run.due_at)
    run.refresh_from_db()
    state.refresh_from_db()
    guest.refresh_from_db()
    assert state.troops["dao_sheng"] == stock_before + 10
    assert run.result["returned_troops"]["dao_sheng"] == 10 and not run.troops
    assert guest.status != GuestStatus.DEPLOYED
    assert member.claims.filter(key=f"merit:existing:{run.pk}").exists()
    assert not expeditions.process_expedition(run.pk, run.due_at)
    state.refresh_from_db()
    assert state.troops["dao_sheng"] == stock_before + 10


@pytest.mark.parametrize("invalid", ["2026-01-01T00:00:00", "not-a-date", "2026-19-99"])
def test_failed_collection_returns_on_invalid_or_naive_wave_time(world, invalid):
    _, player, clock = world
    member, guest = player()
    run = arrival(member, guest, clock)
    run.status = "failed"
    run.result["collection_waves"]["next_wave_at"] = invalid
    run.save(update_fields=["status", "result"])
    assert expeditions.process_expedition(run.pk, run.due_at)
    run.refresh_from_db()
    assert run.status == "completed" and run.battles == 0
    assert "有效的波次时间" in run.result["collection_recovery"]["reason"]


def test_finalized_failed_collection_returns_without_regrant(world):
    _, player, clock = world
    member, guest = player()
    run = arrival(member, guest, clock)
    waves = run.result["collection_waves"]
    waves.update(finalized=True, reward={"merit": 0, "resources": {}})
    waves.pop("next_wave_at")
    run.status = "failed"
    run.save(update_fields=["status", "result"])
    assert expeditions.process_expedition(run.pk, run.due_at)
    run.refresh_from_db()
    assert run.status == "returning" and run.battles == 0
    assert not member.actions.filter(kind="merit_credit", result__reason="讨伐胜利").exists()


def test_single_army_merit_uses_reported_initial_troops_after_total_loss(monkeypatch):
    monkeypatch.setattr(
        merit,
        "feature_rules",
        lambda *_: {
            "minimum_battle_value": 100,
            "battle_pool_per_player": 60,
            "npc_pool_per_player": 25,
            "loss_percent": 35,
            "participation_percent": 30,
            "damage_weight": 50,
            "tank_weight": 30,
            "support_weight": 20,
        },
    )
    run = SimpleNamespace(pk=1, member=SimpleNamespace(season=None), result={}, guest_ids=[7], troops={"dao_ke": 0})
    report = SimpleNamespace(
        attacker_troops={"dao_ke": 20},
        attacker_team=[],
        defender_team=[],
        rounds=[{"events": [{"side": "attacker", "kind": "troop", "applied_damage": 300}]}],
    )
    row = merit.battle_merit_shares([run], report, "attacker", True)[run.pk]
    assert row["metrics"]["damage"] == 300
    assert row["effective"] and row["merit"] == 60
