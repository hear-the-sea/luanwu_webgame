from __future__ import annotations

import random
from types import SimpleNamespace

import pytest
from django.utils import timezone

from battle.city_defense import (
    ARROW_TOWER_MAX_ATTACK,
    ARROW_TOWER_MAX_DEFENSE,
    ARROW_TOWER_MAX_HP,
    WALL_INTERCEPT_CHANCE,
    WALL_MAX_DEFENSE,
    WALL_MAX_HP,
    build_city_defense_combatants,
    serialize_city_defenses_for_report,
)
from battle.combatants_pkg.core import Combatant
from battle.execution import BattleOptions, _finalize_battle_results
from battle.simulation.attack_execution import perform_attack
from battle.simulation.battle_flow import simulate_battle
from battle.simulation.damage_calculation import calculate_attack_damage
from battle.simulation.target_selection import select_target_with_priority
from battle.simulation.turn_order import determine_turn_order
from battle.status_manager import prepare_combatants_for_round
from core.config import BUILDING_KEYS
from gameplay.models import Building, BuildingType
from gameplay.services.manor.core import ensure_manor


class _FixedRandom(random.Random):
    def __init__(self, rolls: list[float]):
        super().__init__(1)
        self._rolls = list(rolls)

    def random(self) -> float:
        if self._rolls:
            return self._rolls.pop(0)
        return 0.99

    def choice(self, seq):
        return seq[0]

    def shuffle(self, x):
        return None

    def uniform(self, _a, _b):
        return 1.0


def _unit(name: str, *, side: str, kind: str = "guest", attack: int = 100, hp: int = 1000, priority: int = 0):
    return Combatant(
        name=name,
        attack=attack,
        defense=10,
        hp=hp,
        max_hp=hp,
        side=side,
        rarity="test",
        luck=30,
        agility=20,
        priority=priority,
        kind=kind,
        troop_strength=1,
        initial_troop_strength=1,
        initial_hp=hp,
        unit_attack=attack,
        unit_defense=10,
        unit_hp=hp,
        template_key=name.lower(),
    )


def _create_city_defense_building(manor, key: str, name: str, level: int) -> None:
    building_type, _ = BuildingType.objects.update_or_create(
        key=key,
        defaults={
            "name": name,
            "description": name,
            "category": "city_defense",
            "resource_type": "silver",
            "base_rate_per_hour": 0,
            "rate_growth": 0.0,
            "base_upgrade_time": 900,
            "time_growth": 1.85,
            "base_cost": {"silver": 18000},
            "cost_growth": 1.85,
        },
    )
    Building.objects.update_or_create(manor=manor, building_type=building_type, defaults={"level": level})


@pytest.mark.django_db
def test_build_city_defense_combatants_scale_to_max_stats(django_user_model):
    manor = ensure_manor(django_user_model.objects.create_user(username="city_defense_stats", password="pass123"))
    _create_city_defense_building(manor, BUILDING_KEYS.WALL, "城墙", 10)
    _create_city_defense_building(manor, BUILDING_KEYS.ARROW_TOWER, "箭塔", 10)

    units = build_city_defense_combatants(manor, side="defender")
    by_key = {unit.template_key: unit for unit in units}

    assert WALL_MAX_DEFENSE == 300
    assert ARROW_TOWER_MAX_DEFENSE == 150
    assert by_key[BUILDING_KEYS.WALL].hp == WALL_MAX_HP
    assert by_key[BUILDING_KEYS.WALL].defense == WALL_MAX_DEFENSE
    assert by_key[BUILDING_KEYS.WALL].attack == 0
    assert by_key[BUILDING_KEYS.ARROW_TOWER].hp == ARROW_TOWER_MAX_HP
    assert by_key[BUILDING_KEYS.ARROW_TOWER].defense == ARROW_TOWER_MAX_DEFENSE
    assert by_key[BUILDING_KEYS.ARROW_TOWER].attack == ARROW_TOWER_MAX_ATTACK
    assert by_key[BUILDING_KEYS.ARROW_TOWER].battle_modifiers["city_defense_attack_targets"] == 3


@pytest.mark.django_db
def test_build_city_defense_combatants_use_persisted_current_hp(django_user_model):
    manor = ensure_manor(django_user_model.objects.create_user(username="city_defense_current_hp", password="pass123"))
    _create_city_defense_building(manor, BUILDING_KEYS.WALL, "城墙", 10)
    wall = manor.buildings.select_related("building_type").get(building_type__key=BUILDING_KEYS.WALL)
    wall.current_hp = 12345
    wall.hp_updated_at = timezone.now()
    wall.save(update_fields=["current_hp", "hp_updated_at"])

    units = build_city_defense_combatants(manor, side="defender")
    wall_unit = next(unit for unit in units if unit.template_key == BUILDING_KEYS.WALL)

    assert wall_unit.hp == 12345
    assert wall_unit.max_hp == WALL_MAX_HP


@pytest.mark.django_db
def test_finalize_battle_results_persists_defender_city_defense_hp(django_user_model):
    attacker = ensure_manor(django_user_model.objects.create_user(username="city_defense_attacker", password="pass123"))
    defender = ensure_manor(django_user_model.objects.create_user(username="city_defense_defender", password="pass123"))
    _create_city_defense_building(defender, BUILDING_KEYS.WALL, "城墙", 10)
    wall = defender.buildings.select_related("building_type").get(building_type__key=BUILDING_KEYS.WALL)
    wall.current_hp = WALL_MAX_HP
    wall.hp_updated_at = timezone.now()
    wall.save(update_fields=["current_hp", "hp_updated_at"])
    wall_unit = _unit("城墙", side="defender", kind="city_defense", hp=0)
    wall_unit.template_key = BUILDING_KEYS.WALL
    wall_unit.level = 10
    wall_unit.max_hp = WALL_MAX_HP

    simulation = SimpleNamespace(
        drops={},
        winner="attacker",
        losses={"attacker": {}, "defender": {}},
        rounds=[],
        starts_at=timezone.now(),
        completed_at=timezone.now(),
        seed=123,
    )

    _finalize_battle_results(
        attacker,
        simulation,
        [],
        [],
        [],
        [wall_unit],
        {},
        {},
        BattleOptions(auto_reward=False, send_message=False, defender_manor=defender),
        "测试敌人",
    )

    wall.refresh_from_db()
    assert wall.current_hp == 1


def test_city_defense_damage_multipliers_apply_to_buildings_and_arrow_tower():
    rng = _FixedRandom([0.99])
    guest = _unit("门客", side="attacker", kind="guest", attack=100)
    wall = _unit("城墙", side="defender", kind="city_defense", hp=30000)
    wall.template_key = BUILDING_KEYS.WALL
    wall.defense = 0

    assert calculate_attack_damage(guest, wall, [], rng, round_priority=0).damage == 300
    rng = _FixedRandom([0.99])
    siege_skill = {"name": "攻城技", "damage_formula": {"base": 50}}

    assert calculate_attack_damage(guest, wall, [siege_skill], rng, round_priority=0).damage == 350

    rng = _FixedRandom([0.99])
    troop = _unit("护院", side="attacker", kind="troop", attack=300, hp=1000)
    troop.troop_strength = 10
    troop.initial_troop_strength = 10
    troop.unit_attack = 30

    assert calculate_attack_damage(troop, wall, [], rng, round_priority=0).damage == 100

    rng = _FixedRandom([0.99])
    tower = _unit("箭塔", side="defender", kind="city_defense", attack=100, hp=15000)
    tower.template_key = BUILDING_KEYS.ARROW_TOWER
    target_guest = _unit("进攻门客", side="attacker", kind="guest", hp=1000)
    target_guest.defense = 0

    assert calculate_attack_damage(tower, target_guest, [], rng, round_priority=0).damage == 300

    rng = _FixedRandom([0.99])
    target_troop = _unit("进攻护院", side="attacker", kind="troop", hp=1000)
    target_troop.troop_strength = 1
    target_troop.initial_troop_strength = 1
    target_troop.unit_defense = 1

    assert calculate_attack_damage(tower, target_troop, [], rng, round_priority=0).damage == 198


def test_attacker_targets_wall_when_intercept_roll_succeeds():
    actor = _unit("进攻者", side="attacker")
    wall = _unit("城墙", side="defender", kind="city_defense", hp=30000)
    wall.template_key = BUILDING_KEYS.WALL
    target = _unit("守方门客", side="defender")

    selected = select_target_with_priority(actor, [target, wall], _FixedRandom([WALL_INTERCEPT_CHANCE - 0.01]))

    assert selected is wall


def test_wall_does_not_act_and_arrow_tower_acts_before_priority_zero_units():
    wall = _unit("城墙", side="defender", kind="city_defense", hp=30000)
    wall.template_key = BUILDING_KEYS.WALL
    wall.battle_modifiers["skip_turn"] = True
    tower = _unit("箭塔", side="defender", kind="city_defense", attack=1500, hp=15000)
    tower.template_key = BUILDING_KEYS.ARROW_TOWER
    tower.battle_modifiers["fixed_first"] = True
    guard = _unit("守方门客", side="defender")
    attacker = _unit("进攻者", side="attacker")

    ordered = determine_turn_order([attacker], [guard, wall, tower], random.Random(1))

    assert wall not in ordered
    assert ordered[0] is tower


def test_arrow_tower_acts_first_in_standard_round_after_negative_priority_phase():
    tower = _unit("箭塔", side="defender", kind="city_defense", attack=1500, hp=15000)
    tower.template_key = BUILDING_KEYS.ARROW_TOWER
    tower.battle_modifiers["fixed_first"] = True
    attacker = _unit("急先锋", side="attacker", hp=5000, priority=-1)

    result = simulate_battle(
        [attacker],
        [tower],
        random.Random(1),
        seed=1,
        travel_seconds=0,
        config={"max_rounds": 3},
    )

    attack_events = [
        event
        for round_data in result.rounds
        for event in round_data.get("events", [])
        if "actor" in event and "target" in event and "damage" in event
    ]
    assert attack_events[0]["actor"] == "急先锋"
    assert attack_events[0]["priority"] == -1
    assert attack_events[1]["actor"] == "箭塔"
    assert attack_events[1]["priority"] == 0


def test_wall_does_not_attack_in_full_battle():
    wall = _unit("城墙", side="defender", kind="city_defense", hp=30000)
    wall.template_key = BUILDING_KEYS.WALL
    wall.battle_modifiers["skip_turn"] = True
    attacker = _unit("进攻者", side="attacker", hp=5000)

    result = simulate_battle(
        [attacker],
        [wall],
        random.Random(1),
        seed=1,
        travel_seconds=0,
        config={"max_rounds": 3},
    )

    attack_events = [
        event
        for round_data in result.rounds
        for event in round_data.get("events", [])
        if "actor" in event and "target" in event and "damage" in event
    ]
    assert attack_events
    assert all(event["actor"] != "城墙" for event in attack_events)


def test_city_defense_modifiers_survive_round_preparation():
    wall = _unit("城墙", side="defender", kind="city_defense", hp=30000)
    wall.battle_modifiers["skip_turn"] = True
    wall.battle_modifiers["wall_intercept_chance"] = WALL_INTERCEPT_CHANCE
    tower = _unit("箭塔", side="defender", kind="city_defense", attack=1500, hp=15000)
    tower.battle_modifiers["fixed_first"] = True
    tower.battle_modifiers["city_defense_attack_targets"] = 3

    prepare_combatants_for_round([], [wall, tower], 1)

    assert wall.battle_modifiers["skip_turn"] is True
    assert wall.battle_modifiers["wall_intercept_chance"] == WALL_INTERCEPT_CHANCE
    assert tower.battle_modifiers["fixed_first"] is True
    assert tower.battle_modifiers["city_defense_attack_targets"] == 3


def test_max_level_arrow_tower_hits_three_targets_without_skill():
    tower = _unit("箭塔", side="defender", kind="city_defense", attack=1500, hp=15000)
    tower.template_key = BUILDING_KEYS.ARROW_TOWER
    tower.battle_modifiers["city_defense_attack_targets"] = 3
    attackers = [_unit(f"进攻者{i}", side="attacker", hp=1000) for i in range(3)]

    event = perform_attack(tower, attackers, [tower], _FixedRandom([0.99, 0.99, 0.99, 0.99]), round_priority=0)

    assert event is not None
    assert len(event["additional_targets"]) == 2


def test_serialize_city_defenses_for_report_keeps_level_and_stats():
    wall = _unit("城墙", side="defender", kind="city_defense", hp=30000, attack=0)
    wall.template_key = BUILDING_KEYS.WALL
    wall.level = 10
    wall.defense = 300

    assert serialize_city_defenses_for_report([wall]) == [
        {
            "key": BUILDING_KEYS.WALL,
            "name": "城墙",
            "level": 10,
            "hp": 30000,
            "max_hp": 30000,
            "attack": 0,
            "defense": 300,
        }
    ]
