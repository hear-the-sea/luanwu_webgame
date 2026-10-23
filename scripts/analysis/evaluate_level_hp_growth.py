#!/usr/bin/env python3
"""用独立内存数据库和正式引擎比较等级生命成长，不修改游戏规则或运行库。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import sys
import time
from pathlib import Path
from statistics import mean
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.analysis import rate_mission_difficulty as reference  # noqa: E402

VARIANTS = ("baseline", "fixed_50", "random_40_60", "fixed_60")
SCENARIOS = (
    ("pvp_50_with_troops", 4, None, False),
    ("pvp_70_with_troops", 6, None, False),
    ("pvp_100_with_troops", 9, None, False),
    ("pvp_70_guest_only", 6, None, True),
    ("pvp_100_guest_only", 9, None, True),
    ("surname_li", 8, "surname_legend_li", False),
    ("tianpeng_2", 8, "tianpeng_dijiao_2", False),
    ("huashan_advanced", 9, "huashan_lunjian_advanced", False),
    ("yijin_mijing", 7, "yijin_mijing", False),
    ("zhanlong_1", 9, "zhanlong_yanshan_shizi", False),
    ("zhanlong_2", 9, "zhanlong_changban_tuwei", False),
    ("zhanlong_3", 10, "zhanlong_changban_duanqiao", False),
    ("zhanlong_4", 10, "zhanlong_wuhu_shizhen", False),
)


def hp_increment(variant, level, seed, side, slot, template_key):
    levels = max(0, level - 1)
    if variant == "baseline":
        return 0
    if variant in ("fixed_50", "fixed_60"):
        return levels * (50 if variant == "fixed_50" else 60)
    if variant != "random_40_60":
        raise ValueError(variant)
    # 升级随机数使用独立种子；同一门客的成长不会随战斗次数重新抽取。
    material = f"level-hp:{seed}:{side}:{slot}:{template_key}".encode()
    rng = random.Random(int.from_bytes(hashlib.sha256(material).digest(), "big"))
    return sum(rng.randint(40, 60) for _ in range(levels))


def flatten_hits(events):
    for event in events:
        if "target_hp_before" in event:
            yield event
        yield from flatten_hits(event.get("additional_targets") or [])
        yield from flatten_hits(event.get("additional_attacks") or [])


def trial(profile, mission, guest_only, seed, variant):
    from battle.combatants_pkg import (
        assign_agility_based_priorities,
        build_named_ai_guests,
        build_troop_combatants,
        guest_builder,
    )
    from battle.constants import get_battle_config
    from battle.random_context import RNG_STREAM_AI_GROWTH, RNG_STREAM_COMBAT, BattleRandomContext
    from battle.simulation import battle_flow
    from core.game_data.technology import get_guest_stat_bonuses, resolve_enemy_tech_levels

    context = BattleRandomContext.create(seed)
    player_side = "defender" if mission and mission.get("is_defense") else "attacker"
    enemy_side = "attacker" if player_side == "defender" else "defender"
    increments = []

    def build_with_growth(guests, side, *args, **kwargs):
        for slot, guest in enumerate(guests):
            bonus = hp_increment(variant, guest.level, seed, side, slot, guest.template.key)
            guest.hp_bonus += bonus
            guest.current_hp = guest.max_hp
            increments.append(bonus)
        return guest_builder.build_guest_combatants(guests, side, *args, **kwargs)

    with patch("battle.combatants_pkg.build_guest_combatants", side_effect=build_with_growth):
        # 缓存不能跨越生命方案，避免将前一方案的战斗对象复用到下一方案。
        reference.build_player.cache_clear()
        player = copy.deepcopy(reference.build_player(profile, player_side, guest_only, seed))
        if mission:
            technology = mission.get("enemy_technology") or {}
            guests = build_named_ai_guests(
                mission.get("enemy_guests", []),
                level=technology.get("guest_level", 50),
                rng=context.rng(RNG_STREAM_AI_GROWTH),
            )
            enemy = build_with_growth(
                guests,
                enemy_side,
                limit=len(guests),
                stat_bonuses=get_guest_stat_bonuses(technology),
                override_skill_keys=technology.get("guest_skills"),
            )
            enemy += build_troop_combatants(
                mission.get("enemy_troops") or {}, enemy_side, tech_levels=resolve_enemy_tech_levels(technology)
            )
        else:
            enemy = copy.deepcopy(reference.build_player(profile, enemy_side, guest_only, seed + 100000))
        reference.build_player.cache_clear()
    attacker, defender = (player, enemy) if player_side == "attacker" else (enemy, player)
    for side, team in (("attacker", attacker), ("defender", defender)):
        for slot, unit in enumerate(team):
            unit.name = f"{side}:{slot}:{unit.name}"
            unit.combatant_slot = slot
    all_units = attacker + defender
    player_guests = [unit for unit in player if unit.kind == "guest"]
    guest_names = {unit.name for unit in player_guests}
    lookup = {unit.name: unit for unit in all_units}
    initial_player_hp = mean(unit.max_hp for unit in player_guests)
    acted = set()
    original = battle_flow._perform_versioned_attack

    def perform(actor, *args, **kwargs):
        if actor.kind == "guest":
            acted.add(actor.name)
        return original(actor, *args, **kwargs)

    assign_agility_based_priorities(attacker, defender)
    with patch.object(battle_flow, "_perform_versioned_attack", side_effect=perform):
        result = battle_flow.simulate_battle(
            attacker,
            defender,
            rng=context.rng(RNG_STREAM_COMBAT),
            seed=seed,
            travel_seconds=0,
            config=get_battle_config(mission.get("battle_type") or "task1" if mission else "task1"),
        )
    full_hp_hits = full_hp_kills = 0
    first_death = None
    for phase, round_ in enumerate(result.rounds, 1):
        for hit in flatten_hits(round_["events"]):
            target = lookup.get(hit.get("target"))
            if target and target.name in guest_names:
                if hit.get("target_defeated") and first_death is None:
                    first_death = phase
                if not hit.get("is_dodge") and hit["target_hp_before"] == target.max_hp:
                    full_hp_hits += 1
                    full_hp_kills += int(hit.get("target_hp_after") == 0)
            if hit.get("actor") in guest_names and hit.get("actor_defeated") and first_death is None:
                first_death = phase
    alive_guests = sum(unit.hp > 0 for unit in player_guests)
    return dict(
        win=int(result.winner == player_side),
        phases=len(result.rounds),
        standard_rounds=sum("priority" not in round_ for round_ in result.rounds),
        player_guest_survival_pct=100 * alive_guests / len(player_guests),
        player_surviving_guests=alive_guests,
        death_before_attack_pct=100
        * sum(unit.hp <= 0 and unit.name not in acted for unit in player_guests)
        / len(player_guests),
        first_direct_guest_death_phase=first_death,
        full_hp_hits=full_hp_hits,
        full_hp_kills=full_hp_kills,
        full_hp_kill_battle=int(full_hp_kills > 0),
        round_limit_reached=int(any(unit.hp > 0 for unit in attacker) and any(unit.hp > 0 for unit in defender)),
        initial_mean_player_hp=initial_player_hp,
        mean_added_hp_before_technology=mean(increments),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("--seed-start", type=int, default=810000)
    parser.add_argument("--scenario", action="append", choices=[scenario[0] for scenario in SCENARIOS])
    parser.add_argument("--output", type=Path, default=ROOT / "reports/level_hp_growth_simulation_20261022.json")
    args = parser.parse_args()
    if args.seeds < 1:
        parser.error("--seeds必须大于零")
    missions = {mission["key"]: mission for mission in reference.bootstrap()}
    from battle.random_context import CURRENT_BATTLE_ENGINE_VERSION

    selected = [case for case in SCENARIOS if not args.scenario or case[0] in args.scenario]
    started = time.monotonic()
    trials = []
    rows = []
    for name, profile, mission_key, guest_only in selected:
        mission = missions[mission_key] if mission_key else None
        guest_only = mission.get("guest_only", False) if mission else guest_only
        for variant in VARIANTS:
            cells = [
                trial(profile, mission, guest_only, args.seed_start + offset, variant) for offset in range(args.seeds)
            ]
            trials.extend(
                dict(scenario=name, variant=variant, seed=args.seed_start + i, **cell) for i, cell in enumerate(cells)
            )
            row = dict(scenario=name, variant=variant, trials=args.seeds)
            for field in (
                "win",
                "phases",
                "standard_rounds",
                "player_guest_survival_pct",
                "player_surviving_guests",
                "death_before_attack_pct",
                "round_limit_reached",
                "initial_mean_player_hp",
                "mean_added_hp_before_technology",
                "full_hp_kill_battle",
            ):
                row[field] = round(mean(cell[field] for cell in cells), 4)
            deaths = [
                cell["first_direct_guest_death_phase"]
                for cell in cells
                if cell["first_direct_guest_death_phase"] is not None
            ]
            row["mean_first_direct_guest_death_phase_when_observed"] = round(mean(deaths), 4) if deaths else None
            row["first_death_observed_trials"] = len(deaths)
            hits = sum(cell["full_hp_hits"] for cell in cells)
            kills = sum(cell["full_hp_kills"] for cell in cells)
            row.update(full_hp_hits=hits, full_hp_kills=kills, full_hp_hit_kill_pct=100 * kills / hits if hits else 0)
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    sources = {Path(__file__), ROOT / "scripts/analysis/rate_mission_difficulty.py", ROOT / "core/config.py"}
    for directory, pattern in (("battle", "*.py"), ("guests", "*.py"), ("core/game_data", "*.py"), ("data", "*.yaml")):
        sources.update((ROOT / directory).rglob(pattern))
    report = dict(
        engine=CURRENT_BATTLE_ENGINE_VERSION,
        seed_start=args.seed_start,
        seeds=args.seeds,
        total_battles=len(trials),
        elapsed_seconds=round(time.monotonic() - started, 2),
        scenarios=[
            dict(name=name, profile=profile + 1, mission_key=key, guest_only=only)
            for name, profile, key, only in selected
        ],
        profiles=reference.PROFILES,
        variants=VARIANTS,
        rows=rows,
        trials=trials,
        mission_inputs={key: missions[key] for _, _, key, _ in selected if key},
        methodology={
            "hp": "从1级起每次升级增加；固定50/60或独立均匀整数40~60。玩家与任务AI都适用，AI新增生命照常享受任务科技倍率",
            "random": "各战斗种子代表一套独立养成阵容；成长用独立随机流，同一阵容在重复战斗中不重新抽取，战斗RNG不受成长抽样消耗影响",
            "controls": "同场景同种子的基础属性、技能、装备补正、兵力、科技、战斗随机种子一致；当前代码默认引擎，默认回合上限",
            "phases": "phases是战报阶段数，含先攻；standard_rounds只统计普通回合",
            "one_hit": "满血门客一次直接伤害结算被击倒次数/满血门客受到的直接命中数；不等同于整次行动的技能加普攻合计秒杀率",
            "first_death": "首个直接命中或其反伤/反击造成玩家门客阵亡的战报阶段；没有观察到的样本不纳入均值，并单列观察数",
            "limits": "标准合法阵容，无器械/帮会科技/城防；PvP两侧同档但独立成长；结果用于比较，不能代表所有玩家阵容",
        },
        source_sha256={
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(sources)
        },
    )
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
