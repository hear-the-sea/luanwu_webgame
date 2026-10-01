from __future__ import annotations

import random
from pathlib import Path

import pytest
import yaml
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from battle.combatants_pkg.core import Combatant
from battle.random_context import CURRENT_BATTLE_ENGINE_VERSION, PREVIOUS_BATTLE_ENGINE_VERSION
from battle.simulation.attack_execution import perform_attack
from battle.simulation.battle_flow import _append_attack_event


class FixedRng(random.Random):
    def choice(self, sequence):
        return sequence[0]

    def shuffle(self, sequence):
        return None

    def uniform(self, _minimum, _maximum):
        return 1.0

    def random(self):
        return 0.99


def make_attack_event(engine_version: str) -> dict:
    actor = Combatant(
        name="拳圣",
        attack=10000,
        defense=10000,
        hp=10000,
        max_hp=10000,
        side="attacker",
        rarity="troop",
        luck=30,
        agility=100,
        priority=0,
        kind="troop",
        troop_strength=1000,
        initial_troop_strength=1000,
        unit_attack=10,
        unit_defense=10,
        unit_hp=10,
        troop_class="quan",
        current_round=1,
    )
    skill_path = Path(__file__).resolve().parents[1] / "data" / "arena_coop_special_skills.yaml"
    skills = yaml.safe_load(skill_path.read_text(encoding="utf-8"))["skills"]
    target = Combatant(
        name="张无忌",
        attack=1,
        defense=0,
        hp=40000,
        max_hp=100000,
        side="defender",
        rarity="purple",
        luck=30,
        agility=1,
        priority=0,
        kind="guest",
        troop_strength=0,
        skills=[next(skill for skill in skills if skill["key"] == "gl_top_qiankun_shift")],
        current_round=1,
    )
    attack = perform_attack(actor, [actor], [target], FixedRng(), battle_engine_version=engine_version)
    assert attack is not None
    return attack


@pytest.mark.parametrize("viewer", ["attacker", "defender", "spectator"])
def test_grouped_troop_attacks_keep_real_hit_taken_passive_between_hits(viewer):
    attack = make_attack_event(CURRENT_BATTLE_ENGINE_VERSION)
    assert attack["additional_attacks"][0]["reflect_damage"] > 0
    events = []
    _append_attack_event(events, attack)

    html = render_to_string(
        "battle/partials/round_event_list.html",
        {"events": events, "my_side": viewer, "is_spectator": viewer == "spectator"},
    )
    text = "".join(strip_tags(html).split())
    first_hit = text.index("普通攻击：对张无忌")
    first_passive = text.index("被动技能：乾坤大挪移")
    second_hit = text.index("普通攻击：对张无忌", first_hit + 1)
    second_passive = text.index("被动技能：乾坤大挪移", first_passive + 1)

    assert first_hit < first_passive < second_hit < second_passive
    assert text.count("被动技能：乾坤大挪移") == 2
    assert len(events) == 1


def test_previous_engine_keeps_single_attack_passive_as_ordered_top_level_event():
    attack = make_attack_event(PREVIOUS_BATTLE_ENGINE_VERSION)
    events = []
    _append_attack_event(events, attack)

    assert "additional_attacks" not in attack
    assert "passive_events_after" not in attack
    assert [event.get("type", "attack") for event in events] == ["attack", "passive"]
    assert [event["order"] for event in events] == [1, 2]
    assert events[1]["effect"] == "乾坤大挪移"
