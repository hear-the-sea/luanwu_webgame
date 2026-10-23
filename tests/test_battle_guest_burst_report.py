from copy import deepcopy

import pytest
from bs4 import BeautifulSoup
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from battle.view_helpers import build_rounds_display


@pytest.mark.parametrize("viewer", ["attacker", "defender", "spectator"])
def test_skill_critical_and_dodged_secondary_targets_stay_before_normal_attack(viewer):
    event = {
        "actor": "石破天",
        "side": "attacker",
        "kind": "guest",
        "attack_phase": "burst",
        "skills": ["龙吟破阵"],
        "target": "主目标",
        "damage": 100,
        "additional_targets": [
            {"actor": "石破天", "target": "次目标甲", "damage": 200, "is_crit": True},
            {"actor": "石破天", "target": "次目标乙", "is_dodge": True},
        ],
        "additional_attacks": [{"actor": "石破天", "target": "普攻目标", "damage": 300, "attack_phase": "normal"}],
    }
    original = deepcopy(event)
    html = render_to_string(
        "battle/partials/round_event_list.html",
        {
            "events": [event],
            "my_side": viewer,
            "is_spectator": viewer == "spectator",
        },
    )
    text = "".join(strip_tags(html).split())
    assert "波及次目标甲，造成暴击，伤害200" in text
    assert "波及次目标乙闪避了攻击" in text
    assert text.count("普通攻击") == 1
    assert (
        text.index("爆发技能：龙吟破阵")
        < text.index("波及次目标甲")
        < text.index("波及次目标乙")
        < text.index("普通攻击")
    )
    assert len(BeautifulSoup(html, "html.parser").select(".event-skill-wave")) == 2
    assert event == original


@pytest.mark.parametrize("bloodthirsty", [False, True])
def test_only_bloodthirsty_extra_target_is_described_as_wave_after_normal_attack(bloodthirsty):
    secondary = {"actor": "石破天", "side": "attacker", "target": "追加目标", "damage": 200}
    events = []
    if bloodthirsty:
        events.append({"type": "passive", "unit": "石破天", "side": "attacker", "effect": "嗜血狂怒", "lost": 90})
        secondary["passive_events_before"] = [
            {
                "type": "passive",
                "unit": "石破天",
                "effect": "嗜血狂怒",
                "message": "随机追加目标",
            }
        ]
    events.append(
        {
            "actor": "石破天",
            "side": "attacker",
            "target": "普攻目标",
            "attack_phase": "normal",
            "damage": 100,
            "additional_targets": [secondary],
        }
    )
    original = deepcopy(events)
    display = build_rounds_display([{"events": events}], {})[0]
    html = render_to_string(
        "battle/partials/round_event_list.html",
        {
            "events": display["attacker_events"],
            "my_side": "attacker",
        },
    )
    text = "".join(strip_tags(html).split())
    assert ("波及追加目标" in text) == bloodthirsty
    if bloodthirsty:
        assert text.index("普通攻击：对普攻目标") < text.index("波及追加目标")
        assert "损失90生命" in text and text.count("嗜血狂怒") == 1
    else:
        assert "普通攻击：对追加目标造成伤害200" in text
    assert not BeautifulSoup(html, "html.parser").select(".event-skill-wave")
    assert events == original


@pytest.mark.parametrize("viewer", ["attacker", "defender", "spectator"])
@pytest.mark.parametrize("normal_crit", [False, True])
def test_guest_burst_and_reselected_normal_target_share_one_action(viewer, normal_crit):
    event = {
        "order": 1,
        "actor": "李利贞",
        "kind": "guest",
        "side": "attacker",
        "attack_phase": "burst",
        "target": "恶来",
        "damage": 600,
        "kills": 0,
        "skills": ["五雷轰顶"],
        "status_inflicted": ["眩晕"],
        "additional_targets": [{"actor": "李利贞", "target": "剑圣", "damage": 200, "kills": 2}],
        "additional_attacks": [
            {
                "actor": "李利贞",
                "kind": "guest",
                "side": "attacker",
                "attack_phase": "normal",
                "target": "枪王",
                "damage": 2000,
                "kills": 20,
                "skills": [],
                "is_crit": normal_crit,
            }
        ],
    }

    html = render_to_string(
        "battle/partials/round_event_list.html",
        {"events": [event], "my_side": viewer, "is_spectator": viewer == "spectator"},
    )
    text = "".join(strip_tags(html).split())

    assert html.count('class="event-unit-name">李利贞</strong>') == 1
    assert text.count("爆发技能：五雷轰顶") == 1
    assert text.count("对恶来造成伤害600，伤害人数0") == 1
    assert text.count("对恶来造成状态：眩晕") == 1
    assert text.count("波及剑圣，造成伤害200，伤害人数2") == 1
    normal_description = "造成暴击，伤害" if normal_crit else "造成伤害"
    assert text.count(f"普通攻击：对枪王{normal_description}2000，伤害人数20") == 1
    assert text.index("爆发技能") < text.index("波及剑圣") < text.index("普通攻击")
    assert "分队" not in text


def test_burst_dodge_does_not_hide_followup_normal_damage():
    event = {
        "actor": "李利贞",
        "attack_phase": "burst",
        "skills": ["五雷轰顶"],
        "target": "恶来",
        "is_dodge": True,
        "additional_attacks": [
            {
                "actor": "李利贞",
                "attack_phase": "normal",
                "target": "枪王",
                "damage": 2000,
                "skills": [],
            }
        ],
    }
    html = render_to_string("battle/partials/round_event_list.html", {"events": [event]})
    text = "".join(strip_tags(html).split())
    assert "爆发技能：五雷轰顶，恶来闪避了攻击" in text
    assert "普通攻击：对枪王造成伤害2000" in text


def test_status_only_burst_shows_effect_without_zero_damage_count():
    event = {
        "actor": "李利贞",
        "attack_phase": "burst",
        "skills": ["冰魄咒"],
        "target": "恶来",
        "damage": 0,
        "status_inflicted": ["冰封"],
    }
    html = render_to_string("battle/partials/round_event_list.html", {"events": [event]})
    text = "".join(strip_tags(html).split())
    assert "爆发技能：冰魄咒，对恶来未造成直接伤害" in text
    assert text.count("对恶来造成状态：冰封") == 1
    assert "伤害人数" not in text
