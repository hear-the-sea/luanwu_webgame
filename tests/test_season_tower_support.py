"""真实战斗引擎验收箭塔：有界齐射、实体承伤与持久化磨损。"""

from copy import deepcopy

import pytest
from django.db import transaction

from gameplay.services.battle_snapshots import build_guest_battle_snapshots
from gameplay.services.season.combat import prepare_combat, simulate_combat
from gameplay.services.season.tower_support import prepare_tower_support, settle_tower_support_locked
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def tower_world(_resource_campaign):  # noqa: F811
    season, player, _ = _resource_campaign
    from gameplay.services.season.fortifications import DEFAULT_FORTIFICATIONS

    season.rules["campaign"]["fortifications"] = {
        **deepcopy(DEFAULT_FORTIFICATIONS),
        "enabled": True,
        "sites": {"mountain:gate": {"wall_multiplier": 2, "tower_hp": 300}},
        "tower": {
            **DEFAULT_FORTIFICATIONS["tower"],
            "damage_percent": 3,
            "max_damage_percent": 6,
            "wear_per_battle": 25,
        },
        "fatigue": {"per_stack_percent": 5, "max_stacks": 2},
    }
    season.save(update_fields=["rules"])
    site = season.sites.get(key="mountain:gate")
    site.owner = "history"
    site.defenses = {"arrow_tower": {"hp": 300, "max_hp": 300}}
    site.save(update_fields=["owner", "defenses"])
    member, guest = player()
    defender, defending_guest = player("history")
    attacker = {"guests": build_guest_battle_snapshots([guest])}
    defending = {"guests": build_guest_battle_snapshots([defending_guest])}
    return season, site, member, guest, attacker, defending


def prepare(world, *, defenders=1, attacker=None):
    _, _, member, _, attacking, defending = world
    defending = deepcopy(defending)
    defending["guests"] *= max(1, defenders)
    prepared = prepare_combat(member.manor, attacker or attacking, defending, "守城军", 451, preserve_state=True)
    if defenders == 0:
        prepared["prepared"].defender_guests.clear()
        prepared["defend"].clear()
    return prepared


def volleys(report):
    return [
        event
        for round_data in report.rounds
        for event in round_data.get("events", [])
        if event.get("effect") == "守城箭塔齐射"
    ]


def test_real_engine_records_once_only_volley_and_damageable_tower(tower_world):
    _, site, member, guest, _, _ = tower_world
    prepared = prepare(tower_world)
    before = prepared["prepared"].attacker_guests[0].hp
    snapshot = prepare_tower_support(prepared, site, "history")
    guest.refresh_from_db()
    assert guest.current_hp == before
    report = simulate_combat(member.manor, prepared, 451)
    arrows = volleys(report)
    assert len(arrows) == 1
    assert 0 < arrows[0]["lost"] <= before * 0.03
    assert len(report.attacker_team) == 1
    assert len(report.defender_team) == 1
    assert len(report.defender_city_defenses) == 1
    row = report.defender_city_defenses[0]
    assert row["initial_hp"] == 300
    assert row["hp"] < row["initial_hp"]
    report.battle_type = "season"
    report.save(force_insert=True)
    with transaction.atomic():
        result = settle_tower_support_locked(site, report, snapshot)
        repeated = settle_tower_support_locked(site, report, snapshot)
    assert repeated == result
    site.refresh_from_db()
    assert site.defenses["arrow_tower"]["hp"] == max(0, row["hp"] - 25)
    guest.refresh_from_db()
    assert guest.current_hp == before  # 外层统一按战报结算门客，这里不场外扣真实生命。


def test_tower_only_defense_is_destroyed_without_ghost_guest(tower_world):
    _, site, member, _, _, _ = tower_world
    prepared = prepare(tower_world, defenders=0)
    prepare_tower_support(prepared, site, "history")
    report = simulate_combat(member.manor, prepared, 451)
    assert report.winner == "attacker"
    assert report.defender_team == []
    assert report.defender_city_defenses[0]["destroyed"]
    assert report.defender_city_defenses[0]["hp"] == 0
    assert len(volleys(report)) == 1


def test_support_scales_with_tower_hp_and_not_defending_guest_count(tower_world):
    _, site, member, _, _, _ = tower_world
    full = prepare(tower_world, defenders=1)
    prepare_tower_support(full, site, "history")
    full_report = simulate_combat(member.manor, full, 451)
    crowded = prepare(tower_world, defenders=5)
    prepare_tower_support(crowded, site, "history")
    crowded_report = simulate_combat(member.manor, crowded, 451)
    assert sum(e["lost"] for e in volleys(crowded_report)) == sum(e["lost"] for e in volleys(full_report))
    site.defenses["arrow_tower"]["hp"] = 150
    damaged = prepare(tower_world)
    snapshot = prepare_tower_support(damaged, site, "history")
    damaged_report = simulate_combat(member.manor, damaged, 451)
    assert snapshot["damage_ratio"] == 0.015
    assert sum(e["lost"] for e in volleys(damaged_report)) <= sum(e["lost"] for e in volleys(full_report)) // 2


def test_no_support_from_destroyed_wrong_owner_or_disabled_tower(tower_world):
    season, site, _, _, _, _ = tower_world
    assert prepare_tower_support(prepare(tower_world), site, "fiction") == {}
    site.defenses["arrow_tower"]["hp"] = 0
    assert prepare_tower_support(prepare(tower_world), site, "history") == {}
    site.defenses["arrow_tower"]["hp"] = 300
    season.rules["campaign"]["fortifications"]["enabled"] = False
    assert prepare_tower_support(prepare(tower_world), site, "history") == {}


def test_npc_tower_and_damage_cap_use_real_report(tower_world):
    season, site, member, _, _, _ = tower_world
    site.owner = ""
    season.rules["campaign"]["fortifications"]["tower"]["damage_percent"] = 50
    prepared = prepare(tower_world, defenders=0)
    initial = sum(unit.hp for unit in prepared["prepared"].attacker_guests)
    snapshot = prepare_tower_support(prepared, site, "")
    assert snapshot["damage_ratio"] == 0.06
    report = simulate_combat(member.manor, prepared, 451)
    assert 0 < sum(e["lost"] for e in volleys(report)) <= initial * 0.06


def test_empty_defense_requires_actual_tower_at_execution(tower_world):
    from core.exceptions import GameError

    _, _, member, _, attacking, _ = tower_world
    with pytest.raises(GameError, match="阵容为空"):
        prepare_combat(member.manor, attacking, {"guests": []}, "空城", 99, preserve_state=True)
    prepared = prepare_combat(
        member.manor,
        attacking,
        {"guests": []},
        "空城",
        99,
        preserve_state=True,
        allow_empty_defenders=True,
    )
    with pytest.raises(ValueError):
        simulate_combat(member.manor, prepared, 99)
