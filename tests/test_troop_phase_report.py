from __future__ import annotations

import random

import pytest
from bs4 import BeautifulSoup
from django.template.loader import render_to_string

from battle.combatants_pkg.troop_builder import build_troop_combatants
from battle.report_events import troop_phase_skill
from battle.simulation.battle_flow import resolve_priority_phases
from battle.simulation.constants import TROOP_EXTRA_ATTACK_INTERVAL
from battle.troops import load_troop_templates_from_yaml


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("nested", [None, "additional_attacks", "additional_targets"])
@pytest.mark.parametrize("dodge", [False, True])
@pytest.mark.parametrize(
    "key,name,priority,phase,expected",
    [
        ("archer", "弓箭手", -1, -1, "凤舞九天"),
        ("fast_archer", "快箭手", -1, -1, "凤舞九天"),
        ("divine_archer", "神箭手", -1, -1, "凤舞九天"),
        ("arrow_god", "箭神", -1, -1, "凤舞九天"),
        ("arrow_god", "箭神", -1, 0, ""),
        ("arrow_god", "箭神", -2, -2, "凤舞九天"),
        ("arrow_god", "箭神", -2, -1, "凤舞九天"),
        ("arrow_god", "箭神", -2, 0, ""),
        ("jian_sheng", "剑圣", -1, -1, "驭剑之术"),
        ("jian_sheng", "剑圣", -1, 0, ""),
        ("bloodthirsty_cavalry", "嗜血铁骑", -1, -1, ""),
        ("dao_sheng", "刀圣", -1, -1, ""),
        ("jian_sheng", "剑圣", -2, -2, ""),
    ],
)
def test_troop_phase_labels_match_class_and_preemptive_phase(
    legacy, nested, dodge, key, name, priority, phase, expected
):
    action = {
        "actor": name,
        "target": "枪王",
        "kind": "troop",
        "side": "attacker",
        "priority": priority,
        "damage": 123,
        "skills": [],
        "is_dodge": dodge,
    }
    if not legacy:
        action["actor_template_key"] = key
        action["priority_phase"] = phase
    hit = action
    if nested:
        hit = {"actor": name, "target": "枪王", "damage": 123, "skills": [], "is_dodge": dodge}
        action[nested] = [hit]

    assert troop_phase_skill(hit, action, phase) == expected
    document = BeautifulSoup(
        render_to_string("battle/partials/round_event_list.html", {"events": [action], "round": {"priority": phase}}),
        "html.parser",
    )
    text = "".join(document.get_text().split())
    for skill in ("凤舞九天", "驭剑之术"):
        # 波及目标沿用主攻击技能，仅独立追加攻击重复显示技能名称。
        assert text.count(f"{name}爆发{skill}") == (
            (2 if nested == "additional_attacks" else 1) if skill == expected else 0
        )
    if nested == "additional_targets" and expected:
        assert "波及枪王" in text
        assert len(document.select(".event-skill-wave")) == 1
    if not expected:
        assert "普通攻击" in text


@pytest.mark.parametrize("level", [1, 5])
def test_arrow_god_real_preemptive_actions_use_only_bow_technology(monkeypatch, level):
    templates = load_troop_templates_from_yaml()
    monkeypatch.setattr("battle.combatants_pkg.troop_builder.load_troop_templates", lambda: templates)

    def fight(sword_level):
        attackers = build_troop_combatants(
            {"arrow_god": TROOP_EXTRA_ATTACK_INTERVAL + 1},
            "attacker",
            tech_levels={"gong_range": level, "jian_preempt": sword_level},
        )
        defenders = build_troop_combatants({"jian_sheng": 5000}, "defender", tech_levels={})
        assert attackers[0].tech_effects["extra_range_damage"] == pytest.approx(0.35 + 0.1 * level)
        assert "preemptive_damage" not in attackers[0].tech_effects
        rounds, _ = resolve_priority_phases(attackers, defenders, random.Random(42))
        return rounds

    rounds = fight(5)
    assert rounds == fight(0)
    assert [row["priority"] for row in rounds] == [-2, -1]
    for row in rounds:
        attacks = [event for event in row["events"] if event.get("actor_template_key") == "arrow_god"]
        assert attacks
        for action in attacks:
            assert action["additional_attacks"]
            for hit in [action, *action["additional_attacks"]]:
                assert troop_phase_skill(hit, action, row["priority"]) == "凤舞九天"
