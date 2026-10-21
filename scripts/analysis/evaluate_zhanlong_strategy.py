#!/usr/bin/env python3
"""同档阵容与固定种子比较战龙前三关的配兵、控制策略，仅使用内存数据库。"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.analysis import rate_mission_difficulty as rating  # noqa: E402

CASES = (("zhanlong_yanshan_shizi", 8), ("zhanlong_changban_tuwei", 9), ("zhanlong_changban_duanqiao", 9))
VARIANTS = ("balanced", "quan", "jian", "qiang", "balanced_control", "jian_control")


def main():
    missions = {mission["key"]: mission for mission in rating.bootstrap()}
    from battle.combatants_pkg import build_troop_combatants
    from battle.combatants_pkg.guest_builder import _serialize_skill
    from core.game_data.technology import build_uniform_tech_levels
    from guests.models import Skill
    from guests.services.skills import collect_unmet_skill_requirements

    original = rating.build_player
    control = Skill.objects.get(key="stratagem_burst")
    control_payload = _serialize_skill(control)

    def player_variant(variant, index, side, guest_only, seed):
        units = copy.deepcopy(original(index, side, guest_only, seed))
        guests = [unit for unit in units if unit.kind == "guest"]
        total = sum(unit.initial_troop_strength for unit in units)
        equipment = rating.PROFILES[index][3]
        if "control" in variant:
            for unit in guests:
                if not unit.template_key.endswith("_civil"):
                    continue
                stats = dict(
                    force=unit.force_attr,
                    intellect=unit.intellect_attr,
                    defense=unit.defense_attr,
                    agility=unit.agility,
                )
                proxy = SimpleNamespace(
                    level=unit.level,
                    force=stats["force"],
                    intellect=stats["intellect"],
                    defense_stat=stats["defense"],
                    agility=stats["agility"],
                )
                naked = {key: value - equipment for key, value in stats.items()}
                assert not collect_unmet_skill_requirements(proxy, control, non_equipment_attributes=naked)
                unit.skills = [skill for skill in unit.skills if skill["kind"] != "active"] + [
                    copy.deepcopy(control_payload)
                ]
        if variant.startswith("quan"):
            loadout = {"quan_sheng": total * 4 // 5, "qiang_wang": total - total * 4 // 5}
        elif variant.startswith("jian"):
            loadout = {"jian_sheng": total * 2 // 3, "qiang_wang": total - total * 2 // 3}
        elif variant.startswith("qiang"):
            loadout = {"qiang_wang": total}
        else:
            return units
        troops = build_troop_combatants(loadout, side, tech_levels=build_uniform_tech_levels(rating.PROFILES[index][4]))
        assert sum(unit.initial_troop_strength for unit in troops) == total
        return guests + troops

    rows = []
    for key, index in CASES:
        for variant in VARIANTS:
            with patch.object(
                rating, "build_player", side_effect=lambda i, s, g, r, v=variant: player_variant(v, i, s, g, r)
            ):
                trials = [rating.evaluate(missions[key], index, 460000 + offset) for offset in range(100)]
            row = dict(
                mission=key,
                profile=index + 1,
                variant=variant,
                trials=100,
                wins=sum(trial["win"] for trial in trials),
                mean_survivors=sum(trial["survivors"] for trial in trials) / 100,
            )
            rows.append(row)
            print(json.dumps(row), flush=True)
    sources = ("data/mission_templates.yaml", "data/guests/zhanlong.yaml", "data/guest_skills.yaml")
    report = dict(
        seed_start=460000,
        trials_per_case=100,
        total_battles=len(rows) * 100,
        rows=rows,
        mission_inputs={key: missions[key] for key, _ in CASES},
        profiles={str(index + 1): rating.PROFILES[index] for _, index in CASES},
        methodology={
            "constants": "同档人数、等级、品质、装备补正、科技和总兵量一致，仅改变兵种比例或文系主动技能",
            "variants": "balanced=五兵种均分；quan=80%拳圣20%枪王；jian=2/3剑圣1/3枪王；qiang=全枪王",
            "control": "通过正式学习条件与非装备属性检查后，文系主动改为奇策突袭，武系主动及所有被动保持",
            "survivors": "仅统计存活门客；护院存活也能获胜，高胜率不等于低门客损耗",
            "limits": "固定基准的相对参考；无器械、帮会科技或城防加成",
        },
        source_sha256={source: hashlib.sha256((ROOT / source).read_bytes()).hexdigest() for source in sources},
    )
    output = ROOT / "reports/zhanlong_progression_strategy_simulation_20261021.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
