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


@pytest.mark.parametrize("message", ["随机追加目标", "当前目标追加伤害", "本次攻击追加真实伤害"])
@pytest.mark.parametrize("nested_key", ["additional_targets", "additional_attacks"])
@pytest.mark.parametrize("include_nested_side", [False, True])
def test_bloodthirsty_merges_only_the_matching_hint_without_changing_logs(message, nested_key, include_nested_side):
    cost = {"type": "passive", "unit": "石破天", "side": "attacker", "effect": "嗜血狂怒", "lost": 2931}
    hint = {"type": "passive", "unit": "石破天", "effect": "嗜血狂怒", "message": message}
    enemy_hint = {**hint, "side": "defender"}
    other = {**hint, "unit": "其他门客"}
    action = {
        "actor": "石破天",
        "side": "attacker",
        nested_key: [
            {"actor": "石破天", "side": "attacker", "passive_events_before": [enemy_hint, other, hint]},
        ],
    }
    if not include_nested_side:
        action[nested_key][0].pop("side")
    events = [cost, action]
    original = deepcopy(events)
    grouped = group_action_recovery_events(events)
    combined = grouped[0]["action_recovery_events_before"][0]
    assert combined["lost"] == 2931
    assert combined["message"] == message
    assert grouped[0][nested_key][0]["passive_events_before"] == [enemy_hint, other]
    assert group_action_recovery_events(grouped) == grouped
    assert events == original


def test_bloodthirsty_current_target_cost_and_hint_share_one_box():
    events = [
        {"type": "passive", "unit": "黄蓉", "side": "attacker", "effect": "嗜血狂怒", "lost": 843},
        {
            "type": "passive",
            "unit": "黄蓉",
            "side": "attacker",
            "effect": "嗜血狂怒",
            "message": "本次攻击追加真实伤害",
        },
        {"actor": "黄蓉", "side": "attacker", "target": "对手", "damage": 17242},
    ]
    original = deepcopy(events)
    rows = build_rounds_display([{"events": events}], {})[0]
    document = BeautifulSoup(
        render_to_string(
            "battle/partials/round_event_list.html",
            {
                "events": rows["attacker_events"],
                "my_side": "attacker",
            },
        ),
        "html.parser",
    )
    boxes = document.select(".event-action-recovery")
    assert len(boxes) == 1
    assert "损失 843 生命" in boxes[0].get_text()
    assert "本次攻击追加真实伤害" in boxes[0].get_text()
    assert document.get_text().count("嗜血狂怒") == 1
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
    combined = document.select_one(".event-action-recovery")
    assert combined.select_one(".event-unit-name").get_text() == "吕布"
    assert "随机追加目标" in combined.get_text()
    assert document.get_text().count("嗜血狂怒") == 1
    assert document.get_text().count("随机追加目标") == 1
    assert len(document.select(".event-passive-entry")) == 1
    assert "关羽" in document.get_text() and "200" in document.get_text()
    assert "随机追加目标" in document.get_text()
    assert len(document.select(".event-damage-summary")) == (3 if followup else 2)
    assert events == original
