from __future__ import annotations

import pytest

from battle.simulation.attack_execution import perform_attack
from gameplay.services.arena.coop_damage import aggregate_event_damage
from tests.test_guest_burst_attack import DirectedRng, burst_skill, unit
from tests.test_qiang_wang_counter import reactions, spear


@pytest.mark.parametrize("owner_entry_id", [17, None])
@pytest.mark.parametrize("is_boss", [True, False])
def test_deferred_counter_contribution_uses_its_source_and_actual_target(owner_entry_id, is_boss):
    actor = unit(
        "首领" if is_boss else "随从",
        "attacker",
        template_key="boss" if is_boss else "guard",
        is_boss=is_boss,
        hp=100,
        skills=[burst_skill()],
    )
    counter_source = spear(owner_entry_id=owner_entry_id)
    last_target = unit("末次普攻目标", owner_entry_id=99)

    event = perform_attack(
        actor,
        [actor],
        [counter_source, last_target],
        DirectedRng(targets=[counter_source.name, last_target.name]),
        battle_engine_version="7",
    )

    (counter,) = reactions(event)
    assert event["additional_attacks"][0]["target"] == last_target.name
    assert counter["actor_owner_entry_id"] == owner_entry_id
    assert counter["target_template_key"] == actor.template_key
    assert counter["raw_damage"] > counter["applied_damage"] == 100
    assert counter["overkill_damage"] == counter["raw_damage"] - 100
    rows = aggregate_event_damage([{"events": [event]}], boss_template_key="boss")
    if owner_entry_id is None:
        # 敌方无参赛归属，不能借最后受击者的归属计入玩家贡献。
        assert dict(rows) == {}
    else:
        assert dict(rows) == {
            owner_entry_id: {
                "total_damage": 100,
                "boss_damage": 100 if is_boss else 0,
                "guard_damage": 0 if is_boss else 100,
            }
        }


def test_same_round_repeated_attack_counts_only_one_deferred_counter_contribution():
    actor = unit(
        "首领", "attacker", template_key="boss", is_boss=True, hp=100000, max_hp=100000, skills=[burst_skill()]
    )
    counter_source = spear(owner_entry_id=17)
    actor.current_round = counter_source.current_round = 1

    events = [
        perform_attack(actor, [actor], [counter_source], DirectedRng(), battle_engine_version="7") for _ in range(2)
    ]

    (counter,) = reactions(events[0])
    assert not reactions(events[1])
    assert dict(aggregate_event_damage([{"events": events}], boss_template_key="boss")) == {
        17: {"total_damage": counter["applied_damage"], "boss_damage": counter["applied_damage"], "guard_damage": 0}
    }
