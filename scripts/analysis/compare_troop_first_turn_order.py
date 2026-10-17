#!/usr/bin/env python3
"""离线比较战斗引擎 3/4 的护院先攻；只读取 YAML，不连接数据库。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def setup_offline_django():
    import django
    from django.conf import settings

    # 使用 dummy backend，任何意外数据库调用都会失败，且不会读取生产配置。
    settings.configure(
        BASE_DIR=ROOT,
        USE_TZ=True,
        SECRET_KEY="offline-turn-order-analysis",
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
        DATABASES={"default": {"ENGINE": "django.db.backends.dummy"}},
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    )
    django.setup()


def load_yaml(relative):
    import yaml

    return yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))


PROFILES = {
    "门客为主": (5, {"dao_ke": 25, "qiang_ke": 25, "jian_shi": 25, "archer": 25}),
    "护院为主": (2, {"dao_ke": 100, "qiang_ke": 100, "jian_shi": 100, "archer": 100}),
    "混编": (3, {"dao_ke": 75, "qiang_ke": 75, "jian_shi": 75, "archer": 75}),
}
PAIRS = [
    ("门客镜像", "门客为主", "门客为主", False),
    ("护院镜像", "护院为主", "护院为主", False),
    ("混编镜像", "混编", "混编", False),
    ("门客对混编", "门客为主", "混编", False),
    ("护院对混编", "护院为主", "混编", False),
    ("带城防混编", "混编", "混编", True),
]


def make_team(profile, side, identity, level):
    from battle.combatants_pkg import build_guest_combatants, build_troop_combatants
    from guests.guest_rules import build_guest_stat_block

    entry = load_yaml("data/guests/base.yaml")["heroes"]["red"][0]
    base = load_yaml("data/guest_templates.yaml")["attribute_profiles"]["red"]["military"]
    hp_base = load_yaml("data/guest_growth_rules.yaml")["rarity_hp_profiles"]["red"]["base"]
    growth = mean(entry["growth_range"]) * (level - 1)
    count, loadout = PROFILES[profile]
    guests = []
    for index in range(count):
        attrs = {key: base[key] + int(growth * weight / 100) for key, weight in entry["attribute_weights"].items()}
        guest = SimpleNamespace(
            name=f"{identity}-门客{index + 1}",
            display_name=f"{identity}-门客{index + 1}",
            rarity="red",
            archetype="military",
            force=attrs["force"],
            intellect=attrs["intellect"],
            defense_stat=attrs["defense"],
            agility=attrs["agility"] + index,
            luck=base["luck"],
            level=level,
            template=SimpleNamespace(key=entry["key"], base_hp=hp_base),
        )
        guest.stat_block = lambda guest=guest: build_guest_stat_block(guest)
        guests.append(guest)
    team = build_guest_combatants(guests, side, limit=5)
    team.extend(build_troop_combatants(loadout, side, tech_levels={}))
    for unit in team[count:]:
        unit.name = f"{identity}-{unit.name}"
    return team


class OfflineBuildings:
    """仅实现 city builder 所需的只读列表协议。"""

    def __init__(self, buildings):
        self.buildings = buildings

    def select_related(self, *args):
        return self

    def filter(self, **kwargs):
        return self

    def order_by(self, *args):
        return self.buildings


def make_city_defenses():
    from django.utils import timezone

    from battle.city_defense import build_city_defense_combatants

    now = timezone.now()
    buildings = [
        SimpleNamespace(
            building_type=SimpleNamespace(key=key),
            level=5,
            current_hp=hp,
            hp_updated_at=now,
        )
        for key, hp in (("wall", 15000), ("arrow_tower", 7500))
    ]
    return build_city_defense_combatants(
        SimpleNamespace(buildings=OfflineBuildings(buildings)), side="defender", now=now
    )


def attack_entries(events):
    for event in events:
        if "damage" in event:
            yield event
        yield from attack_entries(event.get("additional_targets") or [])
        yield from attack_entries(event.get("additional_attacks") or [])


def one_battle(profile_a, profile_b, swap, city, seed, version, level):
    from battle.combatants_pkg import assign_agility_based_priorities
    from battle.simulation import battle_flow
    from battle.simulation.utils import build_rng

    attacker = make_team(profile_b if swap else profile_a, "attacker", "B" if swap else "A", level)
    defender = make_team(profile_a if swap else profile_b, "defender", "A" if swap else "B", level)
    # 城防永远属于防守方；交换攻守是在相同城防条件下比较进攻阵容。
    if city:
        defender.extend(make_city_defenses())
    assign_agility_based_priorities(attacker, defender)
    units = attacker + defender
    guests = [unit for unit in units if unit.kind == "guest"]
    troops = [unit for unit in units if unit.kind == "troop"]
    initial_strength = sum(unit.troop_strength for unit in troops)
    acted = set()
    first = {"troop_damage": 0, "troop_strength_after": 0, "ordinary_round_present": 0}
    original_attack = battle_flow._perform_versioned_attack
    original_round = battle_flow._resolve_standard_round

    def observed_attack(actor, *args, **kwargs):
        acted.add(actor.name)
        return original_attack(actor, *args, **kwargs)

    def observed_round(*args, **kwargs):
        result = original_round(*args, **kwargs)
        if not first["ordinary_round_present"]:
            assert "priority" not in result
            first.update(
                ordinary_round_present=1,
                troop_damage=sum(
                    event.get("applied_damage", event["damage"])
                    for event in attack_entries(result["events"])
                    if event.get("kind") == "troop"
                ),
                troop_strength_after=sum(unit.troop_strength for unit in troops),
            )
        return result

    _, rng = build_rng(seed)
    with (
        patch.object(battle_flow, "_perform_versioned_attack", observed_attack),
        patch.object(battle_flow, "_resolve_standard_round", observed_round),
    ):
        result = battle_flow.simulate_battle(
            attacker, defender, rng, seed, 0, {"max_rounds": 100}, battle_engine_version=version
        )
    winner_a = "defender" if swap else "attacker"
    if not first["ordinary_round_present"]:
        # 开战阶段结束战斗时，存活兵力取终局，输出为零，并额外记录缺失比例。
        first["troop_strength_after"] = sum(unit.troop_strength for unit in troops)
    return {
        "a_win": int(result.winner == winner_a),
        "attacker_win": int(result.winner == "attacker"),
        "defender_win": int(result.winner == "defender"),
        "draw": int(result.winner not in ("attacker", "defender")),
        "rounds": len(result.rounds),
        "guest_dead_before_action_pct": 100
        * sum(unit.hp <= 0 and unit.name not in acted for unit in guests)
        / len(guests),
        "troop_survival_first_pct": 100 * first["troop_strength_after"] / initial_strength,
        "troop_combat_loss_pct": 100 * (1 - sum(unit.troop_strength for unit in troops) / initial_strength),
        "guest_combat_death_pct": 100 * sum(unit.hp <= 0 for unit in guests) / len(guests),
        **first,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("--level", type=int, default=60)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/troop_first_turn_order_comparison.json")
    args = parser.parse_args()
    if args.seeds < 1 or args.level < 1:
        parser.error("seeds 和 level 必须为正整数")
    setup_offline_django()
    from battle.troops import load_troop_templates_from_yaml

    templates = load_troop_templates_from_yaml()
    rows = []
    with patch("battle.combatants_pkg.troop_builder.load_troop_templates", return_value=templates):
        for scenario, profile_a, profile_b, city in PAIRS:
            for swap in (False, True):
                for version in ("3", "4"):
                    battles = [
                        one_battle(profile_a, profile_b, swap, city, seed, version, args.level)
                        for seed in range(1, args.seeds + 1)
                    ]
                    rows.append(
                        {
                            "scenario": scenario,
                            "profile_a": profile_a,
                            "profile_b": profile_b,
                            "swap": swap,
                            "engine": version,
                            "battles": len(battles),
                            **{key: mean(item[key] for item in battles) for key in battles[0]},
                        }
                    )
            print(f"完成 {scenario}", flush=True)
    payload = {
        "seeds": [1, args.seeds],
        "level": args.level,
        "total_battles": args.seeds * len(PAIRS) * 2 * 2,
        "profiles": PROFILES,
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
