"""资源收集多场真实战斗与奖励结算。"""

from copy import deepcopy
from datetime import timedelta

import pytest
from django.db import transaction
from django.test import override_settings

from gameplay.models import Message
from gameplay.services.season import expeditions
from gameplay.services.season.expeditions import process_expedition, recall_locked
from gameplay.services.season.material_rewards import material_budget_summary
from tests.test_season_resource_integration import advance_resource_run, issue
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(request):
    season, player, clock = request.getfixturevalue("_resource_campaign")
    season.rules["campaign"]["resource_economy"]["pve"].update(
        action_merit=30,
        effective_material_merit=True,
        collection_waves={"enabled": True, "max_waves": 5, "interval_seconds": 300, "level_step": 2, "win_percent": 25},
    )
    season.save(update_fields=["rules"])
    return season, player, clock


def arrival(member, guest, clock):
    run = issue(member, guest, clock)
    advance_resource_run(run, clock)
    assert run.status == "stationed" and run.battles == 0
    assert run.due_at == clock[0] + timedelta(minutes=5)
    return run


def test_five_real_fights_raise_level_preserve_health_and_pay_once(world, monkeypatch):
    season, player, clock = world
    member, guest = player()
    levels = []
    original = expeditions.pve_lineup

    def capture(*args, **kwargs):
        levels.append(kwargs["opening_profile"]["level"])
        return original(*args, **kwargs)

    monkeypatch.setattr(expeditions, "pve_lineup", capture)
    run = arrival(member, guest, clock)
    starting_hp = run.lineup["guests"][0]["current_hp"]
    health = []
    for _ in range(5):
        advance_resource_run(run, clock)
        health.append(run.lineup["guests"][0]["current_hp"])
    assert run.status == "returning" and run.battles == 5
    assert run.result["collection_waves"]["wins"] == 5
    assert levels == [levels[0] + 2 * index for index in range(5)]
    assert health == sorted(health, reverse=True) and health[0] <= starting_hp
    assert not run.result.get("fatigue", 0)
    reward = run.result["collection_waves"]["reward"]
    assert reward["resources"] == {"grain": 750, "wood": 75, "iron": 37}
    assert reward["merit"] == 37 and reward["construction_points"] == 25
    assert Message.objects.filter(manor=member.manor, kind="battle").count() == 5
    assert material_budget_summary(member, clock[0])["used"] == 1
    before = member.actions.filter(kind="merit_credit").count()
    assert not process_expedition(run.pk, clock[0])
    assert member.actions.filter(kind="merit_credit").count() == before


def test_failure_after_two_wins_awards_two_only_and_stops(world, monkeypatch):
    _, player, clock = world
    member, guest = player()
    original = expeditions.simulate_combat
    calls = []

    def battle(*args, **kwargs):
        report = original(*args, **kwargs)
        calls.append(1)
        if len(calls) == 3:
            report.winner = "defender"
        return report

    monkeypatch.setattr(expeditions, "simulate_combat", battle)
    run = arrival(member, guest, clock)
    for _ in range(3):
        advance_resource_run(run, clock)
    assert run.status == "returning"
    assert run.result["collection_waves"]["wins"] == 2
    assert run.result["collection_waves"]["reward"]["resources"] == {"grain": 300, "wood": 30, "iron": 15}
    assert run.result["collection_waves"]["reward"]["merit"] == 15
    assert Message.objects.filter(manor=member.manor, kind="battle").count() == 3


def test_recall_settles_wins_once_and_releases_untouched_material_slot(world):
    _, player, clock = world
    member, guest = player()
    run = arrival(member, guest, clock)
    advance_resource_run(run, clock)
    advance_resource_run(run, clock)
    with transaction.atomic():
        recall_locked(member, run.pk, clock[0] + timedelta(seconds=1))
    run.refresh_from_db()
    assert run.status == "returning"
    assert run.result["collection_waves"]["reward"]["resources"]["grain"] == 300
    assert Message.objects.filter(manor=member.manor, title="阵营任务-资源收集结果").count() == 1
    advance_resource_run(run, clock)
    assert material_budget_summary(member, clock[0])["used"] == 1
    run = arrival(member, guest, clock)
    with transaction.atomic():
        recall_locked(member, run.pk, clock[0])
    advance_resource_run(run, clock)
    assert material_budget_summary(member, clock[0])["used"] == 1


def test_existing_outbound_run_without_snapshot_keeps_one_fight(world):
    _, player, clock = world
    member, guest = player()
    run = issue(member, guest, clock)
    result = deepcopy(run.result)
    result.pop("collection_waves")
    run.result = result
    run.save(update_fields=["result"])
    advance_resource_run(run, clock)
    assert run.status == "returning" and run.battles == 1


def test_first_battle_defeat_has_no_collection_reward(world, monkeypatch):
    _, player, clock = world
    member, guest = player()
    original = expeditions.simulate_combat

    def battle(*args, **kwargs):
        report = original(*args, **kwargs)
        report.winner = "defender"
        return report

    monkeypatch.setattr(expeditions, "simulate_combat", battle)
    run = arrival(member, guest, clock)
    advance_resource_run(run, clock)
    assert run.status == "returning" and run.battles == 1
    assert run.result["collection_waves"]["wins"] == 0
    assert run.result["collection_waves"]["reward"] == {"merit": 0, "construction_points": 0, "resources": {}}
    assert material_budget_summary(member, clock[0])["used"] == 1


def test_delayed_recall_processes_arrival_and_due_fights_before_return(world):
    _, player, clock = world
    member, guest = player()
    run = issue(member, guest, clock)
    arrival_at = run.due_at
    recall_at = arrival_at + timedelta(minutes=12)
    with transaction.atomic():
        recall_locked(member, run.pk, recall_at)
    run.refresh_from_db()
    assert run.status == "returning" and run.battles == 2
    assert run.result["collection_waves"]["wins"] == 2
    assert run.result["collection_waves"]["reward"]["resources"]["grain"] == 300
    assert run.due_at == recall_at + timedelta(seconds=run.travel_seconds)


def test_delayed_scan_catches_up_before_season_cutoff(world):
    season, player, clock = world
    member, guest = player()
    season.starts_at = clock[0] - timedelta(days=83) + timedelta(minutes=45)
    season.save(update_fields=["starts_at"])
    run = issue(member, guest, clock)
    late = clock[0] + timedelta(minutes=50)
    assert process_expedition(run.pk, late)
    run.refresh_from_db()
    assert run.status == "returning" and run.battles == 5
    assert run.result["collection_waves"]["wins"] == 5
    assert run.result["collection_waves"]["reward"]["resources"]["grain"] == 750


@override_settings(GAME_TIME_MULTIPLIER=10)
def test_wave_interval_obeys_global_time_scale(world):
    _, player, clock = world
    member, guest = player()
    run = issue(member, guest, clock)
    assert run.result["collection_waves"]["interval_seconds"] == 30
    advance_resource_run(run, clock)
    assert run.due_at == clock[0] + timedelta(seconds=30)


def test_capture_does_not_recall_enemy_resource_collection(world, monkeypatch):
    from types import SimpleNamespace

    from gameplay.services.season import hitpoint_waves, rallies

    season, player, clock = world
    collector, guest = player("history")
    attacker, _ = player("fiction")
    run = issue(collector, guest, clock, node="river:docks")
    advance_resource_run(run, clock)
    site = season.sites.get(key="river:docks")
    site.owner = "fiction"
    site.save(update_fields=["owner"])
    rally = SimpleNamespace(season=season, leader=attacker)
    monkeypatch.setattr(hitpoint_waves, "capture_locked", lambda *_: True)
    monkeypatch.setattr(hitpoint_waves, "award_capture_locked", lambda *_: None)
    monkeypatch.setattr(rallies, "_resolve_locked", lambda *_: None)
    with transaction.atomic():
        assert hitpoint_waves.capture_hp_site_locked(rally, site, [], clock[0])
    run.refresh_from_db()
    assert run.status == "stationed" and run.battles == 0
    advance_resource_run(run, clock)
    assert run.status == "stationed" and run.result["collection_waves"]["wins"] == 1


def test_surviving_guest_only_commits_available_capacity_and_retains_reserves(world, monkeypatch):
    from guests.models import Guest, GuestStatus

    _, player, clock = world
    member, guest = player()
    run = arrival(member, guest, clock)
    fallen = Guest.objects.create(
        manor=member.manor,
        template=guest.template,
        status=GuestStatus.DEPLOYED,
        force=guest.force,
        intellect=guest.intellect,
        defense_stat=guest.defense_stat,
        agility=guest.agility,
        current_hp=1,
    )
    first = run.lineup["guests"][0]
    first["troop_capacity"] = 10
    second = {**deepcopy(first), "guest_id": fallen.pk}
    run.lineup["guests"].append(second)
    run.guest_ids.append(fallen.pk)
    run.result["defeated_ids"] = [fallen.pk]
    run.troops = {"dao_sheng": 20}
    run.save(update_fields=["lineup", "guest_ids", "result", "troops"])
    allocations = []
    original = expeditions.charge_attack_battle_locked

    def observe(runs, deployed, now):
        allocations.append(deepcopy(deployed))
        return original(runs, deployed, now)

    monkeypatch.setattr(expeditions, "charge_attack_battle_locked", observe)
    advance_resource_run(run, clock)
    assert allocations == [{run.pk: {"dao_sheng": 10}}]
    assert run.troops["dao_sheng"] >= 10
    assert fallen.pk in run.result["defeated_ids"]
    assert run.battles == 1


def test_season_archive_waits_for_batched_or_failed_collections(world):
    from gameplay.models import Season, SeasonExpedition
    from gameplay.services.season.lifecycle import advance_seasons

    season, player, clock = world
    member, guest = player()
    run = arrival(member, guest, clock)
    # 模拟超过单次扫描上限的积压；错误队伍也要允许重试或由玩家召回。
    SeasonExpedition.objects.bulk_create(
        [
            SeasonExpedition(
                member=member,
                node_key=run.node_key,
                kind="pve",
                status="failed" if index == 100 else "stationed",
                result=deepcopy(run.result),
                launched_at=run.launched_at,
                due_at=run.due_at,
            )
            for index in range(101)
        ]
    )
    end = season.ends_at + timedelta(seconds=1)
    result = advance_seasons(end)
    season.refresh_from_db()
    assert not season.closed and not result["started"]
    assert Season.objects.count() == 1
    SeasonExpedition.objects.filter(member=member, kind="pve").update(status="returning")
    result = advance_seasons(end)
    season.refresh_from_db()
    assert season.closed and result["started"] == 1
