from __future__ import annotations

import random

import pytest
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from battle.combatants_pkg.core import Combatant
from battle.combatants_pkg.tech_effects import build_tech_effects
from battle.random_context import CURRENT_BATTLE_ENGINE_VERSION, PREVIOUS_BATTLE_ENGINE_VERSION
from battle.report_events import iter_damage_events
from battle.simulation.attack_execution import perform_attack
from battle.simulation.battle_flow import _resolve_standard_round, simulate_battle


class FixedRng(random.Random):
    def choice(self, sequence):
        return sequence[0]

    def shuffle(self, sequence):
        return None

    def uniform(self, _minimum, _maximum):
        return 1.0

    def random(self):
        return 0.99


def make_troop(count: int) -> Combatant:
    return Combatant(
        name="箭神",
        attack=count * 10,
        defense=count * 10,
        hp=count * 10,
        max_hp=count * 10,
        side="attacker",
        rarity="troop",
        luck=30,
        agility=100,
        priority=0,
        kind="troop",
        troop_strength=count,
        initial_troop_strength=count,
        initial_hp=count * 10,
        unit_attack=10,
        unit_defense=10,
        unit_hp=10,
        troop_class="gong",
    )


def make_target(*, hp: int = 1_000_000) -> Combatant:
    return Combatant(
        name="枪兵",
        attack=1,
        defense=0,
        hp=hp,
        max_hp=hp,
        side="defender",
        rarity="guest",
        luck=30,
        agility=1,
        priority=0,
        kind="guest",
        troop_strength=0,
        initial_hp=hp,
    )


@pytest.mark.parametrize(
    ("count", "expected_damage"),
    [
        (999, [9990]),
        (1000, [6000, 6000]),
        (1501, [9012, 9000]),
        (1999, [12000, 11988]),
        (2000, [8004, 8004, 7992]),
    ],
)
@pytest.mark.parametrize("engine_version", ["3", "4"])
def test_troop_attacks_use_current_strength_split_and_single_bonus(count, expected_damage, engine_version):
    actor = make_troop(count)
    target = make_target()

    event = perform_attack(actor, [actor], [target], FixedRng(), battle_engine_version=engine_version)

    assert event is not None
    attacks = [event, *event.get("additional_attacks", [])]
    assert [attack["damage"] for attack in attacks] == expected_damage
    assert sum(attack["applied_damage"] for attack in attacks) == sum(expected_damage)
    assert actor.troop_strength == count
    assert len(list(iter_damage_events([{"round": 1, "events": [event]}]))) == len(expected_damage)


def test_troop_stops_followup_attacks_when_target_is_defeated():
    actor = make_troop(1000)
    target = make_target(hp=5000)

    event = perform_attack(actor, [actor], [target], FixedRng())

    assert event is not None
    assert event["damage"] == 6000
    assert event["target_defeated"] is True
    assert event["additional_attacks"] == []


def test_troop_loses_extra_attack_and_bonus_after_dropping_below_threshold():
    actor = make_troop(2000)
    actor.troop_strength = 999
    actor.hp = 9990
    target = make_target()

    event = perform_attack(actor, [actor], [target], FixedRng())

    assert event is not None
    assert event["damage"] == 9990
    assert "additional_attacks" not in event


def test_troop_stops_followup_attacks_when_reflection_defeats_actor():
    actor = make_troop(1000)
    target = make_target()
    target.troop_class = "jian"
    target.tech_effects["damage_reflect"] = 2.0

    event = perform_attack(actor, [actor], [target], FixedRng())

    assert event is not None
    assert event["reflect_defeated"] is True
    assert event["additional_attacks"] == []
    assert actor.troop_strength == 0


def test_troop_rechecks_damage_bonus_after_reflection_reduces_strength():
    actor = make_troop(1000)
    target = make_target()
    target.troop_class = "jian"
    target.tech_effects["damage_reflect"] = 0.2

    event = perform_attack(actor, [actor], [target], FixedRng())

    assert event is not None
    assert event["reflect_damage"] == 1200
    assert [attack["damage"] for attack in [event, *event["additional_attacks"]]] == [6000, 5000]
    assert actor.troop_strength == 780


@pytest.mark.parametrize(
    ("engine_version", "expected_reflect"),
    [(PREVIOUS_BATTLE_ENGINE_VERSION, 1998), ("3", 0), (CURRENT_BATTLE_ENGINE_VERSION, 0)],
)
def test_previous_engine_preserves_ranged_sword_reflection(engine_version, expected_reflect):
    actor = make_troop(999)
    target = make_target()
    target.troop_class = "jian"
    target.tech_effects["damage_reflect"] = 0.2

    event = perform_attack(actor, [actor], [target], FixedRng(), -1, battle_engine_version=engine_version)

    assert event is not None
    assert event["attack_type"] == "ranged"
    assert event["reflect_damage"] == expected_reflect


@pytest.mark.parametrize(
    ("engine_version", "expected_counter"),
    [(PREVIOUS_BATTLE_ENGINE_VERSION, 300), ("3", 0), (CURRENT_BATTLE_ENGINE_VERSION, 0)],
)
def test_previous_engine_preserves_ranged_spear_counter(engine_version, expected_counter):
    actor = make_troop(999)
    target = make_target()
    target.attack = 300
    target.troop_class = "qiang"
    target.tech_effects.update({"counter_attack_chance": 1.0, "counter_attack_damage": 1.0})

    event = perform_attack(actor, [actor], [target], FixedRng(), -1, battle_engine_version=engine_version)

    assert event is not None
    assert event["counter_damage"] == expected_counter


@pytest.mark.parametrize(
    ("engine_version", "expected_damage"),
    [(PREVIOUS_BATTLE_ENGINE_VERSION, 9990), ("3", 6993), (CURRENT_BATTLE_ENGINE_VERSION, 6993)],
)
def test_previous_engine_ignores_new_ranged_damage_reduction(engine_version, expected_damage):
    actor = make_troop(999)
    target = make_target()
    target.troop_class = "quan"
    target.tech_effects = build_tech_effects("quan", {"quan_vs_ranged": 10})
    target.tech_effects.pop("ranged_defense")

    event = perform_attack(actor, [actor], [target], FixedRng(), -1, battle_engine_version=engine_version)

    assert event is not None
    assert event["damage"] == expected_damage


@pytest.mark.parametrize(("level", "expected_reduction"), [(0, 0.1), (4, 0.1), (5, 0.2), (9, 0.2), (10, 0.3)])
def test_quan_ranged_reduction_uses_technology_thresholds(level, expected_reduction):
    effects = build_tech_effects("quan", {"quan_vs_ranged": level})

    assert effects.get("ranged_damage_reduction") == expected_reduction


@pytest.mark.parametrize("tech_levels", [None, {}])
def test_quan_has_base_ranged_reduction_without_technology(monkeypatch, tech_levels):
    from battle.combatants_pkg import troop_builder
    from battle.troops import load_troop_templates_from_yaml

    monkeypatch.setattr(troop_builder, "load_troop_templates", load_troop_templates_from_yaml)
    units = troop_builder.build_troop_combatants({"quan_sheng": 1}, "attacker", tech_levels=tech_levels)

    assert len(units) == 1
    assert units[0].tech_effects["ranged_damage_reduction"] == 0.1


def test_battle_round_keeps_troop_attacks_in_one_action_event():
    actor = make_troop(1000)
    target = make_target()

    battle_round = _resolve_standard_round([actor], [target], FixedRng(), 1)

    actor_events = [event for event in battle_round["events"] if event.get("actor") == "箭神"]
    assert len(actor_events) == 1
    assert len(actor_events[0]["additional_attacks"]) == 1


def test_pending_battle_with_previous_engine_keeps_original_single_attack():
    actor = make_troop(1000)
    target = make_target()

    battle_round = _resolve_standard_round(
        [actor], [target], FixedRng(), 1, battle_engine_version=PREVIOUS_BATTLE_ENGINE_VERSION
    )

    actor_events = [event for event in battle_round["events"] if event.get("actor") == "箭神"]
    assert len(actor_events) == 1
    assert actor_events[0]["damage"] == 10000
    assert "additional_attacks" not in actor_events[0]


@pytest.mark.parametrize(
    ("engine_version", "expected_hits"),
    [(PREVIOUS_BATTLE_ENGINE_VERSION, 1), (CURRENT_BATTLE_ENGINE_VERSION, 2)],
)
def test_full_simulation_uses_persisted_engine_version(engine_version, expected_hits):
    actor = make_troop(1000)
    target = make_target()

    result = simulate_battle(
        [actor],
        [target],
        FixedRng(),
        seed=7,
        travel_seconds=0,
        config={"max_rounds": 1},
        battle_engine_version=engine_version,
    )

    actor_events = [event for event in result.rounds[0]["events"] if event.get("actor") == "箭神"]
    assert len(actor_events) == 1
    assert 1 + len(actor_events[0].get("additional_attacks", [])) == expected_hits


def test_report_shows_each_attack_under_one_troop_action():
    actor = make_troop(1000)
    target = make_target()
    event = perform_attack(actor, [actor], [target], FixedRng())

    assert event is not None
    event["order"] = 1
    html = render_to_string("battle/partials/round_event_list.html", {"events": [event]})
    text = "".join(strip_tags(html).split())

    assert html.count('class="event-unit-name">箭神</strong>') == 1
    assert text.count("普通攻击：对枪兵造成伤害6000") == 2
    assert "波及" not in text
    assert "分队" not in text
    assert "1.2" not in text
