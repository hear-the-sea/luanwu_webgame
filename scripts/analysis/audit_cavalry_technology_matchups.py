#!/usr/bin/env python3
"""只读YAML，使用正式引擎审计嗜血铁骑在不同科技、兵种下的胜率。"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import time
from functools import lru_cache
from pathlib import Path
from statistics import mean
from types import SimpleNamespace
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.analysis.compare_troop_first_turn_order import setup_offline_django  # noqa: E402

setup_offline_django()

from battle.combatants_pkg import (  # noqa: E402
    assign_agility_based_priorities,
    build_guest_combatants,
    build_troop_combatants,
)
from battle.random_context import CURRENT_BATTLE_ENGINE_VERSION, CURRENT_RNG_VERSION  # noqa: E402
from battle.simulation import build_rng, simulate_battle  # noqa: E402
from battle.troops import load_troop_templates_from_yaml  # noqa: E402
from core.game_data.technology import build_uniform_tech_levels  # noqa: E402

CAVALRY = "bloodthirsty_cavalry"
TOP = ("dao_sheng", "qiang_wang", "jian_sheng", "quan_sheng", "arrow_god")
LEVELS = (0, 1, 3, 5, 7, 10)
SEED_START = 100000


def read_yaml(relative):
    return yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))


def cavalry_levels(level):
    return {
        **{key: level for key in ("wolf_force", "wolf_defense", "wolf_hp", "wolf_agility")},
        "wolf_berserk": min(level, 5),
    }


@lru_cache(maxsize=1)
def guest_roster():
    """与此前报告相同：100级橙色门客成长均值，无装备和技能。"""
    from guests.guest_rules import build_guest_stat_block

    profiles = read_yaml("data/guest_templates.yaml")["attribute_profiles"]["orange"]
    hp = read_yaml("data/guest_growth_rules.yaml")["rarity_hp_profiles"]["orange"]["base"]
    guests = []
    seen = set()
    for path in sorted((ROOT / "data/guests").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for entry in data.get("heroes", {}).get("orange", []):
            if entry.get("recruitable", True) is False or "custom_stats" in entry or entry["key"] in seen:
                continue
            seen.add(entry["key"])
            archetype = entry.get("archetype", "military")
            base = profiles[archetype]
            growth = mean(entry.get("growth_range", [6, 14])) * 99
            attrs = {key: base[key] + int(growth * weight / 100) for key, weight in entry["attribute_weights"].items()}
            guest = SimpleNamespace(
                name=entry["name"],
                display_name=entry["name"],
                rarity="orange",
                archetype=archetype,
                force=attrs["force"],
                intellect=attrs["intellect"],
                defense_stat=attrs["defense"],
                agility=attrs["agility"],
                luck=base["luck"],
                level=100,
                template=SimpleNamespace(key=entry["key"], base_hp=hp),
            )
            guest.stat_block = lambda guest=guest: build_guest_stat_block(guest)
            guests.append(guest)
    return sorted(guests, key=lambda guest: (-guest.stat_block()["attack"], guest.template.key))[:5]


def team(key, side, levels, count, mixed):
    units = build_troop_combatants({key: count}, side, tech_levels=levels)
    if mixed:
        units += build_guest_combatants(guest_roster(), side, limit=5)
    return units


def matchup(cavalry_level, ordinary_level, opponent, mixed, count, seeds):
    row = dict(
        cavalry_level=cavalry_level,
        cavalry_charge_level=min(cavalry_level, 5),
        ordinary_level=ordinary_level,
        opponent=opponent,
        mixed=mixed,
        count=count,
        seeds_per_orientation=seeds,
        n=2 * seeds,
        wins=0,
        attack_wins=0,
        defend_wins=0,
        draws=0,
        timeouts=0,
        cavalry_survivors=0,
        opponent_survivors=0,
    )
    for swap in (False, True):
        cavalry_side, other_side = ("defender", "attacker") if swap else ("attacker", "defender")
        cavalry = team(CAVALRY, cavalry_side, cavalry_levels(cavalry_level), count, mixed)
        other = team(opponent, other_side, build_uniform_tech_levels(ordinary_level), count, mixed)
        assign_agility_based_priorities(cavalry, other)
        template = (other, cavalry) if swap else (cavalry, other)
        for seed in range(SEED_START, SEED_START + seeds):
            attacker, defender = copy.deepcopy(template)
            _, rng = build_rng(seed)
            result = simulate_battle(
                attacker,
                defender,
                rng,
                seed,
                0,
                {"max_rounds": 100},
                battle_engine_version=CURRENT_BATTLE_ENGINE_VERSION,
            )
            won = result.winner == cavalry_side
            row["wins"] += won
            row["defend_wins" if swap else "attack_wins"] += won
            row["draws"] += result.winner not in ("attacker", "defender")
            row["timeouts"] += bool(any(unit.hp > 0 for unit in attacker) and any(unit.hp > 0 for unit in defender))
            cavalry_team, other_team = (defender, attacker) if swap else (attacker, defender)
            row["cavalry_survivors"] += next(
                unit.troop_strength for unit in cavalry_team if unit.template_key == CAVALRY
            )
            row["opponent_survivors"] += next(
                unit.troop_strength for unit in other_team if unit.template_key == opponent
            )
    row["win_pct"] = round(100 * row["wins"] / row["n"], 2)
    for field in ("cavalry_survivors", "opponent_survivors"):
        row[field + "_mean"] = round(row.pop(field) / row["n"], 3)
    return row


def main():
    start = time.monotonic()
    templates = load_troop_templates_from_yaml()
    # 与正常数据库导入一致，恢复YAML回退被截断的负优先级。
    for raw in read_yaml("data/troop_templates.yaml")["troops"]:
        templates[raw["key"]]["priority"] = raw["priority"]
    ordinary = [key for key in templates if key not in (CAVALRY, "scout")]
    jobs = []
    seen = set()

    def add(cavalry_level, ordinary_level, opponents, mixed, count):
        for opponent in opponents:
            key = (cavalry_level, ordinary_level, opponent, mixed, count)
            if key not in seen:
                seen.add(key)
                jobs.append(key)

    # 先输出同科技阶段对顶兵结果，然后补全跨科技与各阶级兵种。
    for level in LEVELS:
        for mixed, count in ((False, 4000), (True, 1000)):
            add(level, level, TOP, mixed, count)
    for cavalry_level in (0, 5, 10):
        for ordinary_level in LEVELS:
            for mixed, count in ((False, 4000), (True, 1000)):
                add(cavalry_level, ordinary_level, TOP, mixed, count)
    for level in (0, 5, 10):
        for mixed, count in ((False, 4000), (True, 1000)):
            add(level, level, ordinary, mixed, count)
    add(10, 10, TOP, False, 1000)

    stats = {}
    rows = []
    with patch("battle.combatants_pkg.troop_builder.load_troop_templates", return_value=templates):
        for level in LEVELS:
            units = team(CAVALRY, "attacker", cavalry_levels(level), 4000, False)
            units += build_troop_combatants(
                {key: 4000 for key in ordinary}, "defender", tech_levels=build_uniform_tech_levels(level)
            )
            stats[str(level)] = {
                unit.template_key: dict(
                    name=unit.name,
                    attack=unit.unit_attack,
                    defense=unit.unit_defense,
                    hp=unit.unit_hp,
                    agility=unit.agility,
                    priority=unit.priority,
                    skills=unit.skills,
                    tech_effects=unit.tech_effects,
                )
                for unit in units
            }
        assert stats["0"][CAVALRY]["attack"] == 17
        assert stats["10"][CAVALRY]["attack"] == 34
        for index, (cavalry_level, ordinary_level, opponent, mixed, count) in enumerate(jobs):
            seeds = 300 if cavalry_level == ordinary_level == 10 and opponent in TOP else 150
            row = matchup(cavalry_level, ordinary_level, opponent, mixed, count, seeds)
            rows.append(row)
            if (index + 1) % 10 == 0 or index + 1 == len(jobs):
                print(
                    json.dumps(
                        dict(
                            completed=index + 1,
                            total=len(jobs),
                            seconds=round(time.monotonic() - start, 1),
                            latest=rows[-5:],
                        ),
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    sources = [
        "data/troop_templates.yaml",
        "data/technology_templates.yaml",
        "data/guest_templates.yaml",
        "data/guest_growth_rules.yaml",
        "core/game_data/cavalry.py",
        "core/game_data/technology.py",
        "battle/combatants_pkg/troop_builder.py",
        "battle/combatants_pkg/tech_effects.py",
        "battle/combat_math.py",
        "battle/skills.py",
        "battle/simulation/target_selection.py",
        "battle/simulation/damage_calculation.py",
        "battle/simulation/damage_application.py",
        "battle/simulation/attack_execution.py",
        "battle/simulation/battle_flow.py",
        "battle/simulation/turn_order.py",
        "battle/simulation/constants.py",
    ]
    sources += [str(path.relative_to(ROOT)) for path in sorted((ROOT / "data/guests").glob("*.yaml"))]
    report = dict(
        engine=CURRENT_BATTLE_ENGINE_VERSION,
        rng_version=CURRENT_RNG_VERSION,
        rng_stream="combat",
        seed_start=SEED_START,
        total_battles=sum(row["n"] for row in rows),
        cells=len(rows),
        seconds=round(time.monotonic() - start, 2),
        draws=sum(row["draws"] for row in rows),
        timeouts=sum(row["timeouts"] for row in rows),
        stats=stats,
        rows=rows,
        guests=[dict(name=guest.name, key=guest.template.key, stats=guest.stat_block()) for guest in guest_roster()],
        source_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources},
    )
    output = ROOT / "reports/bloodthirsty_cavalry_attack34_technology_20261018.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("total_battles", "cells", "seconds", "draws", "timeouts")}))
    print(output)


if __name__ == "__main__":
    main()
