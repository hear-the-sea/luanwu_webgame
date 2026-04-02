from __future__ import annotations

import random

from battle.combatants_pkg.core import Combatant
from battle.simulation.attack_execution import perform_attack


def test_perform_attack_includes_coop_owner_metadata_in_event_logs():
    actor = Combatant(
        name="甲",
        attack=300,
        defense=120,
        hp=1200,
        max_hp=1200,
        side="attacker",
        rarity="green",
        luck=50,
        agility=120,
        priority=0,
        kind="guest",
        troop_strength=0,
        initial_hp=1200,
        template_key="ally_tpl",
        guest_id=101,
        owner_entry_id=11,
        combatant_slot=1,
    )
    target = Combatant(
        name="张无忌",
        attack=220,
        defense=90,
        hp=1000,
        max_hp=1000,
        side="defender",
        rarity="purple",
        luck=50,
        agility=80,
        priority=0,
        kind="guest",
        troop_strength=0,
        initial_hp=1000,
        template_key="arena_gl_top_zhang_wuji_boss",
        guest_id=202,
        combatant_slot=0,
        is_boss=True,
    )

    event = perform_attack(actor, [actor], [target], random.Random(1))

    assert event is not None
    assert event["actor_owner_entry_id"] == 11
    assert event["actor_combatant_slot"] == 1
    assert event["target_template_key"] == "arena_gl_top_zhang_wuji_boss"
    assert event["target_is_boss"] is True
