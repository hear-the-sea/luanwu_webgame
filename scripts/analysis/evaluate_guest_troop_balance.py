#!/usr/bin/env python3
"""使用正式引擎离线比较门客/护院平衡候选，仅创建隔离内存数据库。"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from statistics import mean
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.analysis import rate_mission_difficulty as reference  # noqa: E402

VARIANTS = {
    "production_v8": {"engine": "8"},
    "production_v7": {"engine": "7"},
    "production_v6": {"engine": "6"},
    "production_v5": {"engine": "5"},
    "baseline": {},
    "clear_20_3": {"clear": True},
    "screen_050": {"screen": 0.5},
    "screen_025": {"screen": 0.25},
    "softcap_1000": {"softcap": 1000},
    "soft_clear": {"softcap": 1000, "clear": True},
    "screen050_clear": {"screen": 0.5, "clear": True},
    "screen025_clear": {"screen": 0.25, "clear": True},
    "screen050_soft_clear": {"screen": 0.5, "softcap": 1000, "clear": True},
    "intercept50_clear": {"intercept": 0.5, "clear": True},
    "intercept75": {"intercept": 0.75},
    "intercept75_clear": {"intercept": 0.75, "clear": True},
    "intercept50_soft_clear": {"intercept": 0.5, "softcap": 1000, "clear": True},
}
SCENARIOS = [
    ("purple_1000", 4, 4, None, None),
    ("purple_2500", 6, 6, None, None),
    ("orange_5000", 10, 10, None, None),
    ("orange_single_5000", 10, 10, "qiang_wang", None),
    ("orange_archer_5000", 10, 10, "arrow_god", None),
    ("strong_vs_weak", 10, 8, None, None),
    ("weak_vs_strong", 8, 10, None, None),
    ("li_elai", 8, None, None, "surname_legend_li"),
    ("zhanlong_first", 8, None, None, "zhanlong_yanshan_shizi"),
    ("zhanlong_wuhu", 10, None, None, "zhanlong_wuhu_shizhen"),
]


def player(index, side, seed, mono=None):
    from battle.combatants_pkg import build_guest_combatants, build_named_ai_guests, build_troop_combatants
    from core.game_data.technology import build_uniform_tech_levels
    from guests.models import Skill
    from guests.services.skills import collect_unmet_skill_requirements

    count, level, rarity, equipment, technology, strength = reference.PROFILES[index]
    guests = build_named_ai_guests(
        [f"difficulty_ref_{rarity}_{'civil' if i % 3 == 2 else 'military'}" for i in range(count)],
        level=level,
        rng=random.Random(seed),
    )
    for guest in guests:
        naked = dict(force=guest.force, intellect=guest.intellect, defense=guest.defense_stat, agility=guest.agility)
        choices = (
            ["meteor_pierce_moon", "fatal_chain_sword", "thunder_control", "ice_curse"]
            if guest.archetype == "military"
            else ["brahma_true_fire", "five_thunder_descent", "thunder_nine_heavens", "flower_rain", "ice_curse"]
        )
        learned = []
        for key in choices:
            if not collect_unmet_skill_requirements(guest, Skill.objects.get(key=key), non_equipment_attributes=naked):
                learned.append(key)
                break
        for key in ["iron_wall_heart", "steadfast_planning"]:
            if not collect_unmet_skill_requirements(guest, Skill.objects.get(key=key), non_equipment_attributes=naked):
                learned.append(key)
        guest._override_skills = learned
        guest.force += equipment
        guest.intellect += equipment
        guest.defense_stat += equipment
        guest.agility += equipment
        guest.current_hp = guest.max_hp
    assert strength <= sum(g.troop_capacity for g in guests)
    keys = (
        ["dao_jie", "qiang_hao", "jian_hao", "quan_ba", "divine_archer"]
        if index < 7
        else ["dao_sheng", "qiang_wang", "jian_sheng", "quan_sheng", "arrow_god"]
    )
    loadout = {mono: strength} if mono else {key: strength // len(keys) for key in keys}
    return build_guest_combatants(guests, side, limit=count) + build_troop_combatants(
        loadout, side, tech_levels=build_uniform_tech_levels(technology)
    )


@contextmanager
def candidate(options):
    from battle import combat_math
    from battle.simulation import damage_calculation as damage
    from battle.simulation import target_selection as targeting

    # 固定版本 4 为原始基线，避免正式引擎升级后把新规则当成旧规则重复叠加。
    original_choose = targeting._select_target_without_screening
    original_attack = combat_math.effective_attack_value

    def choose(actor, opponents, rng):
        if actor.kind != "troop" or not any(u.kind == "troop" and u.hp > 0 for u in opponents):
            return original_choose(actor, opponents, rng)
        if options.get("intercept"):
            target = original_choose(actor, opponents, rng)
            if target.kind == "guest" and rng.random() < options["intercept"]:
                return original_choose(actor, [u for u in opponents if u.kind == "troop" and u.hp > 0], rng)
            return target
        changed = [
            (u, "target_weight_multiplier" in u.battle_modifiers, u.battle_modifiers.get("target_weight_multiplier"))
            for u in opponents
            if u.kind == "guest"
        ]
        try:
            for unit, _, previous in changed:
                unit.battle_modifiers["target_weight_multiplier"] = float(previous or 1.0) * options["screen"]
            return original_choose(actor, opponents, rng)
        finally:
            for unit, existed, previous in changed:
                if existed:
                    unit.battle_modifiers["target_weight_multiplier"] = previous
                else:
                    unit.battle_modifiers.pop("target_weight_multiplier", None)

    def attack(actor, target=None, *, troop_attack_strength=None):
        value = original_attack(actor, target, troop_attack_strength=troop_attack_strength)
        if actor.kind == "troop" and getattr(target, "kind", None) == "guest":
            total = max(1, actor.troop_strength)
            threshold = options["softcap"]
            if total > threshold:
                value *= math.sqrt(threshold / total)
        return value

    with ExitStack() as stack:
        if options.get("clear"):
            stack.enter_context(patch.object(combat_math, "LEGACY_SLAUGHTER_MULTIPLIER", 20))
            stack.enter_context(patch.object(damage, "LEGACY_GUEST_SKILL_VS_TROOP_MULTIPLIER", 3.0))
        if options.get("screen") or options.get("intercept"):
            stack.enter_context(patch.object(targeting, "_select_target_without_screening", choose))
        if options.get("softcap"):
            stack.enter_context(patch.object(combat_math, "effective_attack_value", attack))
        yield


def trial(teams, seed, options):
    from battle.combatants_pkg import assign_agility_based_priorities
    from battle.simulation import battle_flow

    attackers, defenders = copy.deepcopy(teams)
    for side, team in [("attacker", attackers), ("defender", defenders)]:
        for i, unit in enumerate(team):
            unit.name = f"{side}:{i}:{unit.name}"
    assign_agility_based_priorities(attackers, defenders)
    all_units = attackers + defenders
    lookup = {u.name: u for u in all_units}
    guests = [u for u in all_units if u.kind == "guest"]
    observed = {
        "guest_damage": 0,
        "all_damage": 0,
        "troop_guest_damage": 0,
        "guest_guest_damage": 0,
        "pure_troop_rounds": 0,
    }
    acted = set()
    actions = {u.name: 0 for u in guests}
    guestless_start = None
    original_perform = battle_flow._perform_versioned_attack
    original_standard = battle_flow._resolve_standard_round

    def perform(actor, *args, **kwargs):
        if actor.kind == "guest":
            acted.add(actor.name)
            actions[actor.name] += 1
        return original_perform(actor, *args, **kwargs)

    def standard(*args, **kwargs):
        nonlocal guestless_start
        if not any(u.kind == "guest" and u.hp > 0 for u in all_units):
            observed["pure_troop_rounds"] += 1
            if guestless_start is None:
                remaining = sum(u.troop_strength for u in all_units if u.kind == "troop")
                initial = sum(u.initial_troop_strength for u in all_units if u.kind == "troop")
                guestless_start = remaining / max(1, initial)
        return original_standard(*args, **kwargs)

    def entries(events):
        for event in events:
            if "damage" in event:
                yield event
            yield from entries(event.get("additional_targets") or [])
            yield from entries(event.get("additional_attacks") or [])

    with (
        candidate(options),
        patch.object(battle_flow, "_perform_versioned_attack", perform),
        patch.object(battle_flow, "_resolve_standard_round", standard),
    ):
        result = battle_flow.simulate_battle(
            attackers,
            defenders,
            random.Random(seed),
            seed,
            0,
            {},
            max_rounds=60,
            battle_engine_version=options.get("engine", "4"),
        )
    for round_ in result.rounds:
        for event in entries(round_["events"]):
            # 与历史样本保持同一口径：直接伤害占比不计反伤/反击等次生伤害。
            if event.get("reaction_type") == "counter":
                continue
            actor = lookup.get(event.get("actor"))
            target = lookup.get(event.get("target"))
            dealt = event.get("applied_damage", event.get("damage", 0))
            observed["all_damage"] += dealt
            if actor and actor.kind == "guest":
                observed["guest_damage"] += dealt
            if actor and target and target.kind == "guest":
                observed["troop_guest_damage" if actor.kind == "troop" else "guest_guest_damage"] += dealt
    player_guests = [u for u in attackers if u.kind == "guest"]
    result_row = {
        "attacker_win": int(result.winner == "attacker"),
        "defender_win": int(result.winner == "defender"),
        "draw": int(result.winner not in ["attacker", "defender"]),
        "round_limit_reached": int(any(u.hp > 0 for u in attackers) and any(u.hp > 0 for u in defenders)),
        "rounds": len(result.rounds),
        "guest_death_before_action_pct": 100 * sum(u.hp <= 0 and u.name not in acted for u in guests) / len(guests),
        "guest_death_pct": 100 * sum(u.hp <= 0 for u in guests) / len(guests),
        "player_guest_survival_pct": 100 * sum(u.hp > 0 for u in player_guests) / len(player_guests),
        "guest_actions": mean(actions.values()),
        "pure_troop_rounds": observed["pure_troop_rounds"],
        "pure_troop_tail": int(observed["pure_troop_rounds"] > 0),
        "troops_left_when_all_guests_dead_pct": 100 * (guestless_start or 0),
        "guest_damage_share_pct": 100 * observed["guest_damage"] / max(1, observed["all_damage"]),
        "troop_share_of_guest_incoming_pct": 100
        * observed["troop_guest_damage"]
        / max(1, observed["troop_guest_damage"] + observed["guest_guest_damage"]),
    }
    return result_row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("--variants", nargs="+", choices=list(VARIANTS), default=list(VARIANTS))
    parser.add_argument("--scenarios", nargs="+", choices=[s[0] for s in SCENARIOS])
    parser.add_argument("--output", type=Path, default=ROOT / "reports/guest_troop_balance_simulation_20261022.json")
    args = parser.parse_args()
    if args.seeds < 1:
        parser.error("--seeds 必须大于零")
    selected_variants = {key: VARIANTS[key] for key in args.variants}
    selected_scenarios = [s for s in SCENARIOS if not args.scenarios or s[0] in args.scenarios]
    missions = {m["key"]: m for m in reference.bootstrap()}
    from battle.combatants_pkg import build_guest_combatants, build_named_ai_guests, build_troop_combatants
    from core.game_data.technology import get_guest_stat_bonuses, resolve_enemy_tech_levels

    rows = []
    raw_trials = []
    started = time.monotonic()
    for name, profile_a, profile_b, mono, mission_key in selected_scenarios:
        cases = []
        for seed in range(1, args.seeds + 1):
            attacker = player(profile_a, "attacker", seed + 710000, mono)
            if mission_key:
                mission = missions[mission_key]
                tech = mission["enemy_technology"]
                ai = build_named_ai_guests(
                    mission["enemy_guests"], level=tech["guest_level"], rng=random.Random(seed + 720000)
                )
                defender = build_guest_combatants(
                    ai, "defender", limit=len(ai), stat_bonuses=get_guest_stat_bonuses(tech)
                )
                defender += build_troop_combatants(
                    mission.get("enemy_troops", {}), "defender", tech_levels=resolve_enemy_tech_levels(tech)
                )
            else:
                defender = player(profile_b, "defender", seed + 720000, mono)
            cases.append((attacker, defender))
        for variant, options in selected_variants.items():
            results = [trial(teams, 730000 + seed, options) for seed, teams in enumerate(cases)]
            raw_trials.extend(
                {"scenario": name, "variant": variant, "combat_seed": 730000 + i, **result}
                for i, result in enumerate(results)
            )
            row = {
                "scenario": name,
                "variant": variant,
                "trials": args.seeds,
                **{key: round(mean(r[key] for r in results), 3) for key in results[0]},
            }
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    sources = [
        "battle/random_context.py",
        "battle/combat_math.py",
        "battle/simulation/damage_calculation.py",
        "battle/simulation/turn_order.py",
        "battle/simulation/target_selection.py",
        "battle/simulation/attack_execution.py",
        "battle/simulation/damage_application.py",
        "battle/simulation/constants.py",
        "battle/simulation/types.py",
        "battle/simulation/battle_flow.py",
        "battle/status_manager.py",
        "battle/utils/status_effects.py",
        "battle/modifier_lifecycle.py",
        "battle/skills.py",
        "battle/active_config.py",
        "battle/passives.py",
        "battle/passive_effects.py",
        "battle/passive_conditions.py",
        "battle/passive_transformations.py",
        "battle/combatants_pkg/core.py",
        "battle/combatants_pkg/ai_generator.py",
        "battle/combatants_pkg/guest_builder.py",
        "battle/combatants_pkg/troop_builder.py",
        "battle/combatants_pkg/tech_effects.py",
        "battle/combatants_pkg/cache.py",
        "battle/troops.py",
        "guests/guest_combat_stats.py",
        "core/game_data/technology.py",
        "scripts/analysis/rate_mission_difficulty.py",
        "scripts/analysis/evaluate_guest_troop_balance.py",
        "data/mission_templates.yaml",
        "data/guest_skills.yaml",
        "data/troop_templates.yaml",
        "data/technology_templates.yaml",
        "data/guest_templates.yaml",
    ]
    sources.extend(str(path.relative_to(ROOT)) for path in sorted((ROOT / "data/guests").glob("*.yaml")))
    report = {
        "total_battles": len(rows) * args.seeds,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "seeds_per_cell": args.seeds,
        "variants": selected_variants,
        "scenarios": selected_scenarios,
        "profiles": reference.PROFILES,
        "methodology": "正式simulate_battle；隔离内存SQLite；正常属性成长、合法技能与带兵容量；同阵容同seed比较，随机分支变化不保证后续随机抽样逐次相同；无器械/城防/帮会；双方对称应用候选。",
        "limits": "非玩家战报抽样；装备为四维等额补正；分队软阈值按当前整队人数分摊避免1000以下分队绕过；统计为战斗内血量归零，不等同永久死亡。",
        "source_sha256": {s: hashlib.sha256((ROOT / s).read_bytes()).hexdigest() for s in sources},
        "rows": rows,
        "raw_trials": raw_trials,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
