from __future__ import annotations

import random
from types import SimpleNamespace

from battle.combatants_pkg.core import Combatant
from battle.report_events import iter_damage_events
from battle.simulation.attack_execution import perform_attack
from gameplay.services.arena.coop_damage import aggregate_event_damage


def _make_guest(
    *,
    name: str,
    side: str,
    hp: int,
    owner_entry_id: int | None = None,
    template_key: str | None = None,
    is_boss: bool = False,
) -> Combatant:
    return Combatant(
        name=name,
        attack=300,
        defense=100,
        hp=hp,
        max_hp=hp,
        side=side,
        rarity="purple",
        luck=50,
        agility=100,
        priority=0,
        kind="guest",
        troop_strength=0,
        initial_hp=hp,
        owner_entry_id=owner_entry_id,
        template_key=template_key,
        is_boss=is_boss,
    )


def test_iter_damage_events_expands_secondary_targets_and_uses_applied_damage():
    rounds = [
        {
            "round": 1,
            "events": [
                {
                    "actor_owner_entry_id": 7,
                    "target_template_key": "boss",
                    "target_is_boss": True,
                    "damage": 100,
                    "applied_damage": 5,
                    "additional_targets": [
                        {
                            "actor_owner_entry_id": 7,
                            "target_template_key": "guard",
                            "target_is_boss": False,
                            "damage": 50,
                            "applied_damage": 7,
                        }
                    ],
                }
            ],
        }
    ]

    events = list(iter_damage_events(rounds))
    damage = aggregate_event_damage(rounds, boss_template_key="boss")

    assert len(events) == 2
    assert damage[7] == {"total_damage": 12, "boss_damage": 5, "guard_damage": 7}


def test_aggregate_event_damage_keeps_legacy_damage_fallback():
    rounds = [
        {
            "events": [
                {
                    "actor_owner_entry_id": 3,
                    "target_template_key": "boss",
                    "damage": 9,
                }
            ]
        }
    ]

    assert aggregate_event_damage(rounds, boss_template_key="boss")[3]["total_damage"] == 9


def test_real_multi_target_attack_clamps_each_target_and_classifies_guard_before_boss(monkeypatch):
    actor = _make_guest(name="参战门客", side="attacker", hp=1000, owner_entry_id=11)
    guard = _make_guest(name="前排守卫", side="defender", hp=2, template_key="guard_front")
    boss = _make_guest(name="首领", side="defender", hp=1, template_key="boss", is_boss=True)
    rear_guard = _make_guest(name="后排守卫", side="defender", hp=3, template_key="guard_rear")
    targets = [guard, boss, rear_guard]

    monkeypatch.setattr(
        "battle.simulation.attack_execution.select_attack_targets",
        lambda *_args, **_kwargs: SimpleNamespace(engaged_targets=targets, skills=[]),
    )
    monkeypatch.setattr(
        "battle.simulation.attack_execution.calculate_attack_damage",
        lambda *_args, **_kwargs: SimpleNamespace(damage=10_000, is_crit=False, is_double_strike=False),
    )

    event = perform_attack(actor, [actor], targets, random.Random(7))

    assert event is not None
    rounds = [{"round": 1, "events": [event]}]
    expanded = list(iter_damage_events(rounds))
    damage = aggregate_event_damage(rounds, boss_template_key="boss")

    assert [item["target_template_key"] for item in expanded] == ["guard_front", "boss", "guard_rear"]
    assert [item["applied_damage"] for item in expanded] == [2, 1, 3]
    assert all(item["raw_damage"] == 10_000 for item in expanded)
    assert damage[11] == {"total_damage": 6, "boss_damage": 1, "guard_damage": 5}
    assert damage[11]["total_damage"] <= sum(target.max_hp for target in targets)
