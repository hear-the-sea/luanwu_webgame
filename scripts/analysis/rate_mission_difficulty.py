#!/usr/bin/env python3
"""在隔离内存数据库中用正式引擎评估个人任务，生成可复核的难度评级。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
import sys
import time
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from statistics import mean
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

ICON_VALUES = (1, 2, 3, 4, 8, 12, 16, 32, 48, 64, 128)
METHODOLOGY = {
    "data_scope": "按当前工作区YAML中的任务、敌将、技能、兵种与科技配置评估；不读取运行中游戏库的旧参数",
    "threshold": 0.7,
    "rating": "在首次达到70%胜率的相邻基准梯度间插值并向上取整为1至128分；最高梯度仍未达标时记128分",
    "icons": "4星=1月亮，4月亮=1太阳，4太阳=1皇冠；允许不同图标混合显示，128分=双皇冠",
    "players": "标准品质成长，2武1文循环，合法技能，装备按四维等额属性补正建模",
    "troops": "遵守各梯度带兵容量；仅门客任务禁用护院；己方科技独立于敌方科技",
    "defense": "保留攻守侧翻转，使用同档固定守军，不计城墙与箭塔；正式防守可使用全体闲置门客和库存护院",
    "player_bonuses": "玩家采用基准科技，不计器械及帮会科技加成；装备按四维等额属性补正建模",
    "limitations": "相对难度参考；不承诺特定实际门客、装备或技能搭配必定通关",
}


def describe_difficulty(value):
    if value == 1:
        return "一星"
    if value == 128:
        return "双皇冠"
    parts = []
    for divisor, label in ((64, "个皇冠"), (16, "个太阳"), (4, "个月亮"), (1, "颗星")):
        count, value = divmod(value, divisor)
        if count:
            parts.append(f"{count}{label}")
    return "＋".join(parts)


def calculate_difficulty_score(measurements):
    """以原模拟梯度为锚点插值胜率门槛，保留四进制图标的混合位。"""
    target = Fraction(7, 10)
    for index, cell in enumerate(measurements):
        current_rate = Fraction(cell["wins"], cell["trials"])
        if current_rate < target:
            continue
        if index == 0:
            return 1
        previous = measurements[index - 1]
        previous_rate = Fraction(previous["wins"], previous["trials"])
        lower, upper = ICON_VALUES[index - 1 : index + 1]
        position = (target - previous_rate) / (current_rate - previous_rate)
        return max(1, min(128, math.ceil(lower + position * (upper - lower))))
    return 128


# 人数、等级、品质、装备四维补正、科技等级、护院数；均满足正式带兵容量。
PROFILES = (
    (3, 10, "blue", 0, 0, 0),
    (3, 20, "blue", 5, 2, 300),
    (5, 30, "blue", 15, 3, 600),
    (5, 40, "blue", 25, 4, 800),
    (5, 50, "purple", 35, 5, 1000),
    (8, 60, "purple", 45, 6, 1600),
    (10, 70, "purple", 55, 7, 2500),
    (12, 80, "orange", 65, 8, 3000),
    (15, 90, "orange", 75, 9, 3750),
    (18, 100, "orange", 85, 10, 4500),
    (20, 100, "orange", 100, 10, 5000),
)


def read_yaml(relative):
    return yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))


def bootstrap():
    import django
    from django.conf import settings

    settings.configure(
        BASE_DIR=ROOT,
        USE_TZ=True,
        TIME_ZONE="Asia/Shanghai",
        SECRET_KEY="isolated-mission-difficulty-analysis",
        INSTALLED_APPS=[
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "accounts",
            "gameplay",
            "guests",
            "battle",
            "trade",
            "guilds",
        ],
        AUTH_USER_MODEL="accounts.User",
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
        MEDIA_URL="/media/",
        STATIC_URL="/static/",
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()
    from django.db import connection

    from battle.combatants_pkg import clear_guest_template_cache
    from battle.models import TroopTemplate
    from guests.management.commands.load_guest_templates import Command
    from guests.models import GuestTemplate, Skill

    assert connection.vendor == "sqlite" and connection.settings_dict["NAME"] == ":memory:"
    with connection.schema_editor() as editor:
        for model in (Skill, GuestTemplate, TroopTemplate):
            editor.create_model(model)
    fields = {field.name for field in Skill._meta.fields}
    for row in read_yaml("data/guest_skills.yaml")["skills"]:
        Skill.objects.create(**{key: value for key, value in row.items() if key in fields})

    missions = read_yaml("data/mission_templates.yaml")["missions"]
    required = {
        entry if isinstance(entry, str) else entry["key"]
        for mission in missions
        for entry in mission.get("enemy_guests", [])
    }
    profiles = read_yaml("data/guest_templates.yaml")["attribute_profiles"]
    command = Command()
    loaded = set()
    for path in sorted((ROOT / "data/guests").glob("*.yaml")):
        for rarity, entries in (yaml.safe_load(path.read_text()) or {}).get("heroes", {}).items():
            for entry in entries:
                if entry["key"] not in required:
                    continue
                archetype = entry.get("archetype", "military")
                built = command._build_template_from_stats(
                    entry, rarity, archetype, entry.get("custom_stats") or profiles[rarity][archetype]
                )
                skills = built.pop("skills")
                built.pop("avatar", None)
                template, _ = GuestTemplate.objects.update_or_create(key=built.pop("key"), defaults=built)
                template.initial_skills.set(Skill.objects.filter(key__in=skills))
                loaded.add(template.key)
    if loaded != required:
        raise AssertionError(f"missing enemy templates: {required - loaded}")
    for rarity in ("blue", "purple", "orange"):
        for archetype in ("military", "civil"):
            built = command._build_template_from_stats(
                {"key": f"difficulty_ref_{rarity}_{archetype}", "name": f"基准{rarity}{archetype}"},
                rarity,
                archetype,
                profiles[rarity][archetype],
            )
            built.pop("skills")
            built.pop("avatar", None)
            GuestTemplate.objects.create(**built)
    clear_guest_template_cache()
    troop_fields = {field.name for field in TroopTemplate._meta.fields}
    for row in read_yaml("data/troop_templates.yaml")["troops"]:
        TroopTemplate.objects.create(**{key: value for key, value in row.items() if key in troop_fields})
    return missions


@lru_cache(maxsize=2048)
def build_player(index, side, guest_only, seed):
    from battle.combatants_pkg import build_guest_combatants, build_named_ai_guests, build_troop_combatants
    from core.game_data.technology import build_uniform_tech_levels
    from guests.models import Skill
    from guests.services.skills import collect_unmet_skill_requirements

    count, level, rarity, equipment, technology, troop_count = PROFILES[index]
    archetypes = ["civil" if i % 3 == 2 else "military" for i in range(count)]
    with patch(
        "battle.combatants_pkg.ai_generator.allocate_ai_attribute_points",
        side_effect=lambda guest, total_points, *, rng: {
            "intellect" if guest.archetype == "civil" else "agility": total_points
        },
    ):
        guests = build_named_ai_guests(
            [f"difficulty_ref_{rarity}_{archetype}" for archetype in archetypes],
            level=level,
            rng=random.Random(seed + 900000),
        )
    for guest in guests:
        naked = dict(force=guest.force, intellect=guest.intellect, defense=guest.defense_stat, agility=guest.agility)
        attacks = (
            ("meteor_pierce_moon", "fatal_chain_sword", "thunder_control", "ice_curse")
            if guest.archetype == "military"
            else ("brahma_true_fire", "five_thunder_descent", "thunder_nine_heavens", "flower_rain", "ice_curse")
        )
        learned = []
        for key in attacks:
            if not collect_unmet_skill_requirements(guest, Skill.objects.get(key=key), non_equipment_attributes=naked):
                learned.append(key)
                break
        for key in ("iron_wall_heart", "steadfast_planning"):
            if not collect_unmet_skill_requirements(guest, Skill.objects.get(key=key), non_equipment_attributes=naked):
                learned.append(key)
        guest._override_skills = learned
        guest.force += equipment
        guest.intellect += equipment
        guest.defense_stat += equipment
        guest.agility += equipment
        guest.current_hp = guest.max_hp
    troop_count = 0 if guest_only else troop_count
    assert troop_count <= sum(guest.troop_capacity for guest in guests)
    troop_keys = (
        ("dao_ke", "qiang_ke", "jian_shi", "archer")
        if index < 4
        else (
            ("dao_jie", "qiang_hao", "jian_hao", "quan_ba", "divine_archer")
            if index < 7
            else ("dao_sheng", "qiang_wang", "jian_sheng", "quan_sheng", "arrow_god")
        )
    )
    loadout = {key: troop_count // len(troop_keys) for key in troop_keys}
    units = build_guest_combatants(guests, side, limit=count)
    units += build_troop_combatants(loadout, side, tech_levels=build_uniform_tech_levels(technology))
    return units


def evaluate(mission, index, seed):
    from battle.combatants_pkg import (
        assign_agility_based_priorities,
        build_guest_combatants,
        build_named_ai_guests,
        build_troop_combatants,
    )
    from battle.constants import get_battle_config
    from battle.random_context import RNG_STREAM_AI_GROWTH, RNG_STREAM_COMBAT, BattleRandomContext
    from battle.simulation import simulate_battle
    from core.game_data.technology import get_guest_stat_bonuses, resolve_enemy_tech_levels

    context = BattleRandomContext.create(seed)
    player_side, enemy_side = ("defender", "attacker") if mission.get("is_defense") else ("attacker", "defender")
    player = copy.deepcopy(build_player(index, player_side, mission.get("guest_only", False), seed))
    technology = mission.get("enemy_technology") or {}
    enemies = build_named_ai_guests(
        mission.get("enemy_guests", []),
        level=technology.get("guest_level", 50),
        rng=context.rng(RNG_STREAM_AI_GROWTH),
    )
    enemy = build_guest_combatants(
        enemies,
        enemy_side,
        limit=len(enemies),
        stat_bonuses=get_guest_stat_bonuses(technology),
        override_skill_keys=technology.get("guest_skills"),
    )
    enemy += build_troop_combatants(
        mission.get("enemy_troops") or {}, enemy_side, tech_levels=resolve_enemy_tech_levels(technology)
    )
    attacker, defender = (enemy, player) if mission.get("is_defense") else (player, enemy)
    assign_agility_based_priorities(attacker, defender)
    result = simulate_battle(
        attacker,
        defender,
        rng=context.rng(RNG_STREAM_COMBAT),
        seed=seed,
        travel_seconds=0,
        config=get_battle_config(mission.get("battle_type") or "task1"),
    )
    return dict(
        win=result.winner == player_side,
        rounds=len(result.rounds),
        survivors=sum(unit.hp > 0 for unit in player if unit.kind == "guest"),
    )


def export_ratings(report, report_path, ratings_output):
    if report["seeds"] < 40 or any(len(row["measurements"]) != len(PROFILES) for row in report["rows"]):
        raise ValueError("full difficulty ratings require at least 40 seeds across all 11 profiles")
    expected_keys = {mission["key"] for mission in read_yaml("data/mission_templates.yaml")["missions"]}
    if {row["key"] for row in report["rows"]} != expected_keys:
        raise ValueError("simulation report does not cover the current complete mission catalog")
    if [tuple(profile) for profile in report["profiles"]] != list(PROFILES):
        raise ValueError("simulation report uses different benchmark profiles")
    changed_sources = [
        source
        for source, digest in report["source_sha256"].items()
        if hashlib.sha256((ROOT / source).read_bytes()).hexdigest() != digest
    ]
    if changed_sources:
        raise ValueError(f"simulation sources have changed; rerun the simulation: {changed_sources}")
    report["methodology"] = METHODOLOGY
    (ROOT / report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    ratings = {}
    for row in report["rows"]:
        passed = next((cell for cell in row["measurements"] if cell["win_rate"] >= 0.7), None)
        index = passed["profile"] - 1 if passed else len(PROFILES) - 1
        value = calculate_difficulty_score(row["measurements"])
        ratings[row["key"]] = {
            "value": value,
            "label": describe_difficulty(value),
            "summary": "4颗星＝1个月亮，4个月亮＝1个太阳，4个太阳＝1个皇冠。评级来自战斗模拟，阵容与技能搭配会影响实战。",
            "profile": index + 1,
            "win_rate": passed["win_rate"] if passed else row["measurements"][-1]["win_rate"],
            "ceiling_exceeded": passed is None,
        }
    (ROOT / ratings_output).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "simulation_report": report_path,
                "simulation_battles": report["total_battles"],
                "seeds_per_profile": report["seeds"],
                "ratings": ratings,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    lines = [
        "# 个人任务模拟难度评级",
        "",
        f"共 {len(ratings)} 个任务，11 个基准梯度，每格 {report['seeds']} 个固定种子，合计 {report['total_battles']:,} 场正式引擎战斗。",
        "",
        "本次依据当前工作区配置文件，包含最新敌将属性、技能、真实兵力及科技；运行中游戏库的旧参数不参与评级。",
        "",
        "评级在首次达到 70% 胜率的相邻基准梯度间插值，并向上取整为 1～128 分。最高梯度仍未达标的任务记为 128 分，并在下表标记。",
        "",
        "图标按四进制累计：4 星＝1 月亮，4 月亮＝1 太阳，4 太阳＝1 皇冠。不同图标可以混合显示，例如 5 分＝1 月亮＋1 星，21 分＝1 太阳＋1 月亮＋1 星，128 分＝双皇冠。",
        "",
        "玩家采用基准科技，不计器械、帮会科技、城墙与箭塔。防守任务按同档固定守军比较；正式防守可使用全体闲置门客和库存护院，因此实际守军更充足时通关压力会降低。装备按四维属性补正建模；基准不是特定真实门客或装备推荐，评级仅供相对比较。",
        "",
        "| 梯度 | 人数 | 等级 | 品质 | 四维补正 | 科技 | 护院数 | 难度 |",
        "| --- | ---: | ---: | --- | ---: | ---: | ---: | --- |",
    ]
    for index, profile in enumerate(PROFILES):
        lines.append(
            f"| {index + 1} | "
            + " | ".join(str(value) for value in profile)
            + f" | {describe_difficulty(ICON_VALUES[index])} |"
        )
    lines += [
        "",
        "仅门客任务的玩家护院数为零。各梯度护院均不超过正式带兵容量，科技按玩家梯度设定，敌方科技按各任务原配置。",
        "",
        "| 任务 | 难度分 | 图标 | 达标或最高梯度胜率 | 备注 |",
        "| --- | ---: | --- | ---: | --- |",
    ]
    for row in report["rows"]:
        rating = ratings[row["key"]]
        note = "最高基准仍未达70%" if rating["ceiling_exceeded"] else ""
        lines.append(f"| {row['name']} | {rating['value']} | {rating['label']} | {rating['win_rate']:.1%} | {note} |")
    lines += [
        "",
        "完整数据：[模拟报告](mission_difficulty_simulation_20261021.json)。",
        "",
        f"复现：`python scripts/analysis/rate_mission_difficulty.py --seeds {report['seeds']} --seed-start {report['seed_start']}`。脚本仅使用独立内存 SQLite，不连接生产数据库。",
        "",
    ]
    (ROOT / "reports/mission_difficulty_ratings_20261021.md").write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=40)
    parser.add_argument("--seed-start", type=int, default=410000)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--mission", action="append")
    parser.add_argument("--output", default="reports/mission_difficulty_simulation_20261021.json")
    parser.add_argument("--from-report", action="store_true")
    parser.add_argument("--ratings-output", default="data/mission_difficulty_ratings.json")
    args = parser.parse_args()
    if args.from_report:
        report = json.loads((ROOT / args.output).read_text())
        export_ratings(report, args.output, args.ratings_output)
        return
    missions = bootstrap()
    if args.mission:
        missions = [mission for mission in missions if mission["key"] in args.mission]
    from battle.random_context import CURRENT_BATTLE_ENGINE_VERSION, CURRENT_RNG_VERSION

    started = time.monotonic()
    rows = []
    indices = (0, 5, 10) if args.pilot else range(len(PROFILES))
    seeds = 1 if args.pilot else args.seeds
    for mission in missions:
        measurements = []
        for index in indices:
            trials = [evaluate(mission, index, args.seed_start + offset) for offset in range(seeds)]
            measurements.append(
                dict(
                    profile=index + 1,
                    trials=seeds,
                    wins=sum(row["win"] for row in trials),
                    win_rate=mean(row["win"] for row in trials),
                    mean_rounds=round(mean(row["rounds"] for row in trials), 2),
                    mean_survivors=round(mean(row["survivors"] for row in trials), 2),
                )
            )
        row = dict(key=mission["key"], name=mission["name"], measurements=measurements)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    output = ROOT / args.output
    sources = [
        "data/mission_templates.yaml",
        "data/guest_skills.yaml",
        "data/guest_templates.yaml",
        "data/guest_growth_rules.yaml",
        "data/troop_templates.yaml",
        "data/technology_templates.yaml",
    ]
    sources += [str(path.relative_to(ROOT)) for path in sorted((ROOT / "data/guests").glob("*.yaml"))]
    sources += [str(path.relative_to(ROOT)) for path in sorted((ROOT / "battle").rglob("*.py"))]
    report = dict(
        schema_version=1,
        engine=CURRENT_BATTLE_ENGINE_VERSION,
        rng_version=CURRENT_RNG_VERSION,
        seed_start=args.seed_start,
        seeds=seeds,
        profiles=PROFILES,
        rows=rows,
        mission_inputs={mission["key"]: mission for mission in missions},
        methodology=METHODOLOGY,
        total_battles=len(rows) * len(indices) * seeds,
        elapsed_seconds=round(time.monotonic() - started, 2),
        source_sha256={source: hashlib.sha256((ROOT / source).read_bytes()).hexdigest() for source in sources},
    )
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    if not args.pilot and not args.mission:
        export_ratings(report, args.output, args.ratings_output)
    print(json.dumps(dict(output=str(output), battles=report["total_battles"], seconds=report["elapsed_seconds"])))


if __name__ == "__main__":
    main()
