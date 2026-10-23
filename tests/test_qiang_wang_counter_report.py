import pytest
from django.template.loader import render_to_string
from django.utils.html import strip_tags


@pytest.mark.parametrize(
    ("viewer", "counter_side_label", "defeated_side_label"),
    [
        ("attacker", "敌方", "我方"),
        ("defender", "我方", "敌方"),
        ("spectator", "防守方", "进攻方"),
    ],
)
def test_counter_after_reselected_normal_shows_actual_source_and_sides(viewer, counter_side_label, defeated_side_label):
    event = {
        "order": 1,
        "actor": "李利贞",
        "kind": "guest",
        "side": "attacker",
        "attack_phase": "burst",
        "target": "枪王",
        "damage": 600,
        "skills": ["五雷轰顶"],
        "additional_attacks": [
            {
                "actor": "李利贞",
                "kind": "guest",
                "side": "attacker",
                "attack_phase": "normal",
                "target": "恶来",
                "damage": 2000,
                "skills": [],
            },
            {
                "actor": "枪王",
                "kind": "troop",
                "side": "defender",
                "reaction_type": "counter",
                "target": "李利贞",
                "target_state": {"kind": "guest", "side": "attacker"},
                "target_defeated": True,
                "damage": 500,
                "applied_damage": 500,
                "skills": [],
            },
        ],
    }
    html = render_to_string(
        "battle/partials/round_event_list.html",
        {"events": [event], "my_side": viewer, "is_spectator": viewer == "spectator"},
    )
    text = "".join(strip_tags(html).split())

    assert text.count(f"{counter_side_label}枪王爆发反戈一击") == 1
    assert "对李利贞造成伤害500，伤害人数0" in text
    assert f"{defeated_side_label}李利贞被击败" in text
    assert text.index("普通攻击：对恶来") < text.index("枪王爆发反戈一击")
    assert text.count("普通攻击") == 1
    assert "恶来爆发反戈一击" not in text
    assert "分队" not in text


@pytest.mark.parametrize("priority", [-2, -1])
def test_counter_does_not_inherit_attacker_technology_phase(priority):
    event = {
        "actor": "箭神" if priority == -2 else "剑圣",
        "kind": "troop",
        "side": "attacker",
        "priority": priority,
        "priority_phase": priority,
        "target": "枪王",
        "damage": 1000,
        "additional_attacks": [
            {
                "actor": "枪王",
                "kind": "troop",
                "side": "defender",
                "reaction_type": "counter",
                "target": "箭神" if priority == -2 else "剑圣",
                "target_state": {"kind": "troop", "side": "attacker"},
                "target_defeated": True,
                "damage": 400,
                "kills": 4,
                "skills": [],
            }
        ],
    }
    html = render_to_string("battle/partials/round_event_list.html", {"events": [event], "my_side": "attacker"})
    text = "".join(strip_tags(html).split())

    assert "枪王爆发反戈一击，对" in text
    assert "造成伤害400，伤害人数4" in text
    assert f"我方{event['actor']}全军覆没" in text
    assert "枪王爆发凤舞九天" not in text
    assert "枪王爆发驭剑之术" not in text
    assert "普通攻击" not in text


def test_actual_counter_destroying_arrow_tower_has_no_person_casualty_count():
    from bs4 import BeautifulSoup

    from tests.test_guest_burst_attack import unit
    from tests.test_qiang_wang_counter import fire, spear

    tower = unit("箭塔", "attacker", kind="city_defense", hp=100, max_hp=100, attack=100)
    event = fire(tower, [spear()])
    html = render_to_string("battle/partials/round_event_list.html", {"events": [event], "my_side": "attacker"})
    counter = BeautifulSoup(html, "html.parser").select(".event-damage-summary")[-1]
    text = "".join(counter.get_text().split())

    assert "敌方枪王爆发反戈一击，对箭塔造成伤害" in text
    assert "箭塔被摧毁" in text
    assert "伤害人数" not in text
    assert "全军覆没" not in text


def test_debugger_counter_line_does_not_inherit_original_actor_prefix():
    from io import StringIO

    from battle_debugger.management.commands.battle_debug import Command
    from tests.test_guest_burst_attack import unit
    from tests.test_qiang_wang_counter import fire, spear

    actor = unit("李利贞", "attacker")
    event = fire(actor, [spear()])
    output = StringIO()
    command = Command(stdout=output)
    command.print_combat_log([{"round": 1, "events": [event]}])
    line = next(line.strip() for line in output.getvalue().splitlines() if "反戈一击" in line)

    assert line.startswith("→防守方枪王爆发反戈一击")
    assert "对李利贞造成伤害" in line
