import random
from pathlib import Path
from types import SimpleNamespace

import yaml

from battle.modifier_lifecycle import clear_modifier_scope
from battle.passives import run_passives_for_timing
from battle.simulation.target_selection import _weighted_choice
from tests.battle_passives.support import make_unit


def test_draw_enemy_blades_uses_tenfold_weight_without_stacking_between_rounds():
    skills = yaml.safe_load(Path("data/guest_skills.yaml").read_text())["skills"]
    skill = next(row for row in skills if row["key"] == "draw_enemy_blades")
    marked = make_unit(name="诱敌门客", skills=[skill])
    other = make_unit(name="其他门客")

    for round_no in (1, 2):
        clear_modifier_scope(marked, "round")
        run_passives_for_timing(
            "round_start",
            actor=marked,
            target=None,
            attacker_team=[marked, other],
            defender_team=[],
            round_no=round_no,
            event_sink=[],
            rng=random.Random(1),
        )
        assert marked.battle_modifiers["target_weight_multiplier"] == 10.0
        # 权重 10:1 的边界为 10/11；测试真实配置参与目标选择。
        assert _weighted_choice([marked, other], SimpleNamespace(random=lambda: 0.90)) is marked
        assert _weighted_choice([marked, other], SimpleNamespace(random=lambda: 0.92)) is other

    assert "10" not in skill["description"]
    assert "权重" not in skill["description"]
