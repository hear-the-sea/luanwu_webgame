"""小服实际活跃分组、固定NPC档位与旧在途快照兼容。"""

from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from core.exceptions import GameError
from gameplay.models import Manor, Season, SeasonAction, SeasonExpedition, SeasonMember, SeasonRally
from gameplay.services.arena.snapshots import build_entry_guest_snapshot
from gameplay.services.arena.virtual_lineups import snapshot_power
from gameplay.services.season.actions import perform_action
from gameplay.services.season.campaign_rules import load_campaign_rules
from gameplay.services.season.catalog import load_rules
from gameplay.services.season.combat import npc_opening_profile, pve_lineup
from gameplay.services.season.expeditions import process_expedition
from gameplay.services.season.playability_rules import DEFAULT_PLAYABILITY
from gameplay.services.season.registration import assign_factions_locked, register_locked
from guests.models import Guest, GuestStatus, GuestTemplate

pytestmark = pytest.mark.django_db


@pytest.fixture
def balance_world(user_factory, monkeypatch):
    clock = [timezone.now()]
    monkeypatch.setattr("gameplay.services.season.actions.timezone.now", lambda: clock[0])
    rules = load_rules()
    rules["campaign"] = load_campaign_rules()
    # 此夹具覆盖旧版开拓难度快照，新版敌军阵容由专项验收。
    rules["campaign"].pop("npc_encounters", None)
    rules["campaign"]["playability"] = deepcopy(DEFAULT_PLAYABILITY)
    for feature in ("population", "activities", "contribution", "simplification", "resource_economy"):
        rules["campaign"][feature]["enabled"] = False
    season = Season.objects.create(
        name="小服平衡", starts_at=clock[0] - timedelta(days=4), ends_at=clock[0] + timedelta(days=86), rules=rules
    )
    template = GuestTemplate.objects.create(
        key="base_black_military", name="乡勇", rarity="black", archetype="military"
    )

    def player(prestige=100, faction="", force=200, count=3, playtime="any"):
        manor, _ = Manor.objects.get_or_create(user=user_factory())
        manor.prestige = prestige
        manor.save(update_fields=["prestige"])
        member = SeasonMember.objects.create(
            season=season,
            manor=manor,
            faction=faction,
            prestige_snapshot=prestige,
            lineup={"registration": {"playtime": playtime}},
        )
        guests = [
            Guest.objects.create(
                manor=manor, template=template, force=force, defense_stat=force, intellect=force, agility=30
            )
            for _ in range(count)
        ]
        for guest in guests:
            guest.current_hp = guest.max_hp
            guest.save(update_fields=["current_hp"])
        return member, guests

    def activity(member, days=3, effective=True):
        for offset in range(days):
            at = clock[0] - timedelta(days=offset, seconds=1)
            action = SeasonAction.objects.create(
                member=member,
                token=uuid4(),
                request_hash="effective-test",
                kind="combat",
                date=timezone.localdate(at),
                week=1,
                result={"requested_merit": 20 if effective else 0},
            )
            SeasonAction.objects.filter(pk=action.pk).update(created_at=at)

    return season, player, activity, clock


def test_twenty_players_balance_active_members_timeslots_and_keep_top_two_apart(balance_world):
    season, player, activity, clock = balance_world
    members = []
    for index in range(20):
        member, _ = player(
            prestige=1000 - index * 20, force=300 if index < 6 else 100, playtime="evening" if index < 8 else "day"
        )
        if index < 8:
            activity(member)
        members.append(member)
    with transaction.atomic():
        assert assign_factions_locked(season, clock[0]) == 20
    for member in members:
        member.refresh_from_db()
    assert members[0].faction != members[1].faction
    counts = [sum(member.faction == faction for member in members) for faction in ("fiction", "history")]
    assert abs(counts[0] - counts[1]) <= 2
    active = [
        sum(member.faction == faction and member.lineup["registration"]["activity_days"] > 0 for member in members)
        for faction in ("fiction", "history")
    ]
    assert abs(active[0] - active[1]) <= 1
    frozen = list(season.members.values_list("pk", "faction"))
    with transaction.atomic():
        assert assign_factions_locked(season, clock[0]) == 0
    assert list(season.members.values_list("pk", "faction")) == frozen


def test_late_player_joins_weaker_active_side_without_moving_existing_members(balance_world, user_factory):
    season, player, activity, clock = balance_world
    active, _ = player(faction="fiction")
    inactive, _ = player(faction="history")
    activity(active, days=5)
    activity(inactive, days=5, effective=False)
    manor, _ = Manor.objects.get_or_create(user=user_factory())
    with transaction.atomic():
        late = register_locked(season, manor, "fiction", clock[0], registration_playtime="evening")
    assert late.faction == "history"
    assert late.lineup["registration"]["playtime"] == "evening"
    active.refresh_from_db()
    inactive.refresh_from_db()
    assert (active.faction, inactive.faction) == ("fiction", "history")


def test_balance_uses_three_best_noninjured_guests_and_counts_healthy_deployed_guests(balance_world):
    season, player, _, clock = balance_world
    member, guests = player(count=5)
    for index, guest in enumerate(guests):
        guest.force = guest.defense_stat = 100 + index * 100
        guest.status = GuestStatus.INJURED if index == 4 else GuestStatus.DEPLOYED if index == 3 else GuestStatus.IDLE
        guest.save(update_fields=["force", "defense_stat", "status"])
    expected = []
    for guest in guests[:4]:
        snapshot = build_entry_guest_snapshot(guest)
        snapshot["current_hp"] = snapshot["max_hp"]
        expected.append(snapshot_power(snapshot, formula_version=2))
    with transaction.atomic():
        assign_factions_locked(season, clock[0])
    member.refresh_from_db()
    assert member.lineup["registration"]["power"] == sum(sorted(expected, reverse=True)[:3])


def test_missing_playability_preserves_legacy_prestige_snake(balance_world):
    season, player, _, clock = balance_world
    season.rules["campaign"].pop("playability")
    season.save(update_fields=["rules"])
    members = [
        player(prestige=value, force=1000 if index % 2 else 10)[0]
        for index, value in enumerate([100, 90, 80, 70, 60, 50, 40, 30])
    ]
    with transaction.atomic():
        assign_factions_locked(season, clock[0])
    assert [season.members.get(pk=member.pk).faction for member in members] == [
        "fiction",
        "history",
        "history",
        "fiction",
    ] * 2


def test_registration_rejects_invalid_playtime_without_creating_member(balance_world, user_factory):
    season, _, _, clock = balance_world
    manor, _ = Manor.objects.get_or_create(user=user_factory())
    with pytest.raises(GameError, match="上线时段"), transaction.atomic():
        register_locked(season, manor, "random", clock[0], registration_playtime="all-night")
    assert not season.members.filter(manor=manor).exists()


def test_npc_fixed_tiers_never_chase_player_power_and_respect_actual_guests(balance_world):
    season, _, _, _ = balance_world
    weak = [{"current_hp": 10, "attack": 1}]
    strong = [{"current_hp": 100000, "attack": 1000000}]
    assert npc_opening_profile(season, weak, 1, 20) == npc_opening_profile(season, strong, 1, 20)
    single = npc_opening_profile(season, strong, 1, 20)
    assert (single["level"], single["count"]) == (14, 1)
    squad = npc_opening_profile(season, strong * 9, 3, 20)
    assert (squad["level"], squad["count"]) == (20, 3)
    guests = pve_lineup("forest:lodge", "固定守军", 0, 10, level_override=20, opening_profile=single)["guests"]
    assert len(guests) == 1 and guests[0]["level"] == 14


@pytest.mark.parametrize("old_snapshot", [False, True])
def test_dispatched_npc_difficulty_is_frozen_and_old_runs_keep_original_enemy_count(balance_world, old_snapshot):
    season, player, _, clock = balance_world
    member, guests = player(faction="fiction", force=600)
    if old_snapshot:
        season.rules["campaign"]["playability"]["enabled"] = False
        season.save(update_fields=["rules"])
    result = perform_action(
        member.manor,
        season.pk,
        "dispatch",
        str(uuid4()),
        {"node_key": "mountain:ruins", "mode": "pve", "guest_ids": [guest.pk for guest in guests], "troops": {}},
    )
    run = SeasonExpedition.objects.get(pk=result["run_id"])
    if old_snapshot:
        assert "npc_opening" not in run.result
    else:
        assert run.result["npc_opening"]["count"] == 1
    season.rules["campaign"]["playability"]["enabled"] = True
    season.rules["campaign"]["playability"]["npc"]["max_guests_by_players"] = dict.fromkeys(
        ["1", "2", "3", "4", "5"], 3
    )
    season.rules["campaign"]["playability"]["npc"]["level_percent_by_players"] = dict.fromkeys(
        ["1", "2", "3", "4", "5"], 100
    )
    season.save(update_fields=["rules"])
    clock[0] = run.due_at
    assert process_expedition(run.pk, clock[0])
    run.refresh_from_db()
    assert len(run.report.defender_team) == (2 if old_snapshot else 1)
    assert member.actions.get(kind="logistics").result["occurred_at"] == clock[0].isoformat()


def test_rally_npc_difficulty_freezes_actual_launch_members(balance_world):
    season, player, _, clock = balance_world
    leader, guests = player(faction="fiction", force=600)
    teammate, others = player(faction="fiction", force=600)
    result = perform_action(
        leader.manor,
        season.pk,
        "rally_create",
        str(uuid4()),
        {
            "node_key": "mountain:gate",
            "mode": "attack",
            "guest_ids": [guest.pk for guest in guests],
            "troops": {},
            "rally_target_members": 2,
        },
    )
    rally = SeasonRally.objects.get(pk=result["rally_id"])
    perform_action(
        teammate.manor,
        season.pk,
        "rally_join",
        str(uuid4()),
        {"rally_id": rally.pk, "guest_ids": [guest.pk for guest in others], "troops": {}},
    )
    rally.refresh_from_db()
    profile = rally.result["npc_opening"]
    assert profile["players"] == profile["count"] == 2
    assert profile["level"] == profile["base_level"] * 85 // 100
    season.rules["campaign"]["playability"]["npc"]["max_guests_by_players"] = dict.fromkeys(
        ["1", "2", "3", "4", "5"], 3
    )
    season.save(update_fields=["rules"])
    clock[0] = rally.due_at
    assert process_expedition(result["run_id"], clock[0])
    run = SeasonExpedition.objects.get(pk=result["run_id"])
    assert len(run.report.defender_team) == 2
    rally.refresh_from_db()
    assert rally.result["npc_opening"] == profile


def test_twenty_registered_players_two_attackers_can_relay_against_one_real_defender(balance_world):
    from gameplay.services.season.warfare import ensure_sites_locked

    season, player, _, clock = balance_world
    roster = [
        player(faction="fiction" if index < 10 else "history", force=8000 if index < 10 else 20, count=1)
        for index in range(20)
    ]
    defender, defending_guests = roster[10]
    with transaction.atomic():
        ensure_sites_locked(season, clock[0])
    site = season.sites.get(key="mountain:gate")
    site.owner, site.guards = "history", 1
    site.save(update_fields=["owner", "guards"])
    response = perform_action(
        defender.manor,
        season.pk,
        "dispatch",
        str(uuid4()),
        {
            "node_key": site.key,
            "mode": "garrison",
            "station_hours": 4,
            "guest_ids": [defending_guests[0].pk],
            "troops": {},
        },
    )
    defense = SeasonExpedition.objects.get(pk=response["run_id"])
    clock[0] = defense.due_at
    assert process_expedition(defense.pk, clock[0])
    defense.refresh_from_db()
    assert defense.status == "stationed"
    leader, guests = roster[0]
    teammate, others = roster[1]
    response = perform_action(
        leader.manor,
        season.pk,
        "rally_create",
        str(uuid4()),
        {
            "node_key": site.key,
            "mode": "attack",
            "rally_target_members": 2,
            "guest_ids": [guests[0].pk],
            "troops": {},
        },
    )
    perform_action(
        teammate.manor,
        season.pk,
        "rally_join",
        str(uuid4()),
        {
            "rally_id": response["rally_id"],
            "guest_ids": [others[0].pk],
            "troops": {},
        },
    )
    rally = SeasonRally.objects.get(pk=response["rally_id"])
    clock[0] = rally.due_at
    assert process_expedition(response["run_id"], clock[0])
    attack = SeasonExpedition.objects.get(pk=response["run_id"])
    assert attack.report is not None
    assert len(attack.report.attacker_team) == 2
    assert len(attack.report.defender_team) == 1
    assert season.members.count() == 20
    assert season.members.filter(expeditions__status__in=["outbound", "stationed", "returning"]).distinct().count() == 3
    site.refresh_from_db()
    assert site.guards == 1  # 真人守军波次不会顺便消灭NPC守军。
    rally.refresh_from_db()
    clock[0] = rally.due_at
    assert process_expedition(attack.pk, clock[0])
    attack.refresh_from_db()
    assert len(attack.report.defender_team) <= 2
    assert season.members.filter(faction="fiction").count() == 10
