from copy import deepcopy

import pytest
from bs4 import BeautifulSoup
from django.template.loader import render_to_string

from battle.report_events import group_action_recovery_events
from battle.view_helpers import build_rounds_display


@pytest.mark.parametrize("viewer", ["attacker", "defender", "spectator"])
@pytest.mark.parametrize("effect", ["绝处逢生", "五气朝元"])
def test_recovery_and_attack_share_one_action_for_all_viewers(viewer, effect):
    recovery = {
        "type": "passive" if effect == "绝处逢生" else "heal",
        "effect": effect,
        "unit": "拳圣",
        "side": "attacker",
        "healed": 120,
        "order": 2,
        "unit_state": {"kind": "troop", "side": "attacker", "percent": 80},
    }
    if effect == "五气朝元":
        recovery["heal_strength"] = 12
    attack = {
        "actor": "拳圣",
        "side": "attacker",
        "kind": "troop",
        "target": "刀圣",
        "damage": 100,
        "order": 3,
        "additional_attacks": [{"actor": "拳圣", "target": "刀圣", "damage": 50}],
    }
    events = [recovery, attack]
    if effect == "绝处逢生":
        events.insert(
            0,
            {
                "type": "passive",
                "effect": effect,
                "unit": "拳圣",
                "side": "attacker",
                "message": "血量回复",
                "order": 1,
            },
        )
    original = deepcopy(events)
    display = build_rounds_display([{"events": events}], {})[0]
    document = BeautifulSoup(
        render_to_string(
            "battle/partials/round_event_list.html",
            {
                "events": display["attacker_events"],
                "my_side": viewer,
                "is_spectator": viewer == "spectator",
            },
        ),
        "html.parser",
    )

    assert len(document.select(".round-action-list > li")) == 1
    assert len(document.select(".event-action-recovery")) == 1
    assert document.get_text().count(effect) == 1
    assert "120" in document.select_one(".event-action-recovery").get_text()
    assert len(document.select(".event-damage-summary")) == 2
    assert document.select_one(".event-order").get_text() == "1"
    if effect == "五气朝元":
        assert "恢复人数12" in document.get_text()
    assert events == original


def test_recovery_keeps_its_position_and_does_not_merge_another_units_action():
    heal = {"type": "heal", "effect": "五气朝元", "unit": "拳圣", "side": "attacker", "healed": 10}
    other = {"actor": "刀圣", "side": "attacker", "damage": 20}
    same_name_enemy = {"actor": "拳圣", "side": "defender", "damage": 20}
    assert group_action_recovery_events([heal, other]) == [heal, other]
    assert group_action_recovery_events([heal, same_name_enemy]) == [heal, same_name_enemy]
    assert group_action_recovery_events([heal]) == [heal]


def test_other_passives_and_healing_are_preserved_between_recovery_and_attack():
    recovery = {"type": "passive", "effect": "绝处逢生", "unit": "吕布", "side": "attacker", "healed": 30}
    other = {"type": "passive", "effect": "困兽犹斗", "unit": "吕布", "side": "attacker"}
    attack = {"actor": "吕布", "side": "attacker", "damage": 100}
    grouped = group_action_recovery_events([recovery, other, attack])
    assert len(grouped) == 1
    assert grouped[0]["action_recovery_events_before"] == [recovery, other]
    assert group_action_recovery_events(grouped) == grouped


@pytest.mark.parametrize("viewer", ["attacker", "defender", "spectator"])
@pytest.mark.parametrize("followup", [False, True])
def test_bloodthirsty_loss_and_extra_target_share_the_attack_action(viewer, followup):
    passive = {"type": "passive", "effect": "嗜血狂怒", "unit": "吕布", "side": "attacker", "lost": 90, "order": 1}
    attack = {
        "actor": "吕布",
        "side": "attacker",
        "kind": "guest",
        "target": "赵云",
        "damage": 100,
        "order": 2,
        "additional_targets": [
            {
                "actor": "吕布",
                "side": "attacker",
                "kind": "guest",
                "target": "关羽",
                "damage": 200,
                "passive_events_before": [
                    {"type": "passive", "effect": "嗜血狂怒", "unit": "吕布", "message": "随机追加目标"}
                ],
            }
        ],
    }
    if followup:
        attack["additional_attacks"] = [{"actor": "吕布", "side": "attacker", "target": "张飞", "damage": 50}]
    events = [passive, attack]
    original = deepcopy(events)
    rows = build_rounds_display([{"events": events}], {})[0]
    document = BeautifulSoup(
        render_to_string(
            "battle/partials/round_event_list.html",
            {"events": rows["attacker_events"], "my_side": viewer, "is_spectator": viewer == "spectator"},
        ),
        "html.parser",
    )
    assert len(document.select(".round-action-list > li")) == 1
    assert len(document.select(".event-order")) == 1
    assert "损失 90 生命" in document.select_one(".event-action-recovery").get_text()
    assert "关羽" in document.get_text() and "200" in document.get_text()
    assert "随机追加目标" in document.get_text()
    assert len(document.select(".event-damage-summary")) == (3 if followup else 2)
    assert events == original
