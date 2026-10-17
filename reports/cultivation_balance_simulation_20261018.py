"""修行任务战斗复核：仅使用内存SQLite，运行需在仓库根目录。"""

# isort: skip_file
# ruff: noqa: E402
import os
import sys
import json
import random
import statistics
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest  # noqa: E402, F401 -- 激活项目隔离测试配置

os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
import django  # noqa: E402 -- Django 隔离初始化后加载

django.setup()
from django.conf import settings  # noqa: E402 -- Django 隔离初始化后加载

settings.DATABASES["default"]["NAME"] = ":memory:"

assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
from django.db import connection  # noqa: E402 -- Django 隔离初始化后加载
from guests.models import GuestTemplate, Skill  # noqa: E402 -- Django 隔离初始化后加载
from battle.models import TroopTemplate  # noqa: E402 -- Django 隔离初始化后加载
import yaml  # noqa: E402 -- Django 隔离初始化后加载

with connection.schema_editor() as ed:
    for model in [Skill, GuestTemplate, TroopTemplate]:
        ed.create_model(model)
for row in yaml.safe_load(open("data/guest_skills.yaml"))["skills"]:
    valid = {f.name for f in Skill._meta.fields}
    Skill.objects.create(**{k: v for k, v in row.items() if k in valid})
profiles = yaml.safe_load(open("data/guest_templates.yaml"))["attribute_profiles"]
for rarity in ["blue", "purple", "orange"]:
    for archetype in ["military", "civil"]:
        stats = profiles[rarity][archetype]
        GuestTemplate.objects.create(
            key=f"std_{rarity}_{archetype}",
            name=f"标准{rarity}{archetype}",
            rarity=rarity,
            archetype=archetype,
            base_attack=stats["force"],
            base_intellect=stats["intellect"],
            base_defense=stats["defense"],
            base_agility=stats["agility"],
            base_luck=stats["luck"],
            base_hp={"blue": 600, "purple": 800, "orange": 1000}[rarity],
        )
for rarity, rows in yaml.safe_load(open("data/guests/special.yaml"))["heroes"].items():
    for row in rows:
        if not row.get("custom_stats"):
            continue
        stats = row["custom_stats"]
        GuestTemplate.objects.create(
            key=row["key"],
            name=row["name"],
            rarity=rarity,
            archetype=row["archetype"],
            recruitable=False,
            base_attack=stats["force"],
            base_intellect=stats["intellect"],
            base_defense=stats["defense"],
            base_agility=stats["agility"],
            base_luck=stats["luck"],
            base_hp=stats.get(
                "base_hp",
                {"black": 100, "gray": 250, "green": 400, "red": 450, "blue": 600, "purple": 800, "orange": 1000}[
                    rarity
                ],
            ),
        )
for rarity, rows in yaml.safe_load(open("data/guests/cultivation.yaml"))["heroes"].items():
    for row in rows:
        stats = row["custom_stats"]
        GuestTemplate.objects.create(
            key=row["key"],
            name=row["name"],
            rarity=rarity,
            archetype=row["archetype"],
            recruitable=False,
            base_attack=stats["force"],
            base_intellect=stats["intellect"],
            base_defense=stats["defense"],
            base_agility=stats["agility"],
            base_luck=stats["luck"],
            base_hp=stats["base_hp"],
        )
        GuestTemplate.objects.get(key=row["key"]).initial_skills.set(
            Skill.objects.filter(key__in=row.get("skills", []))
        )
for rarity, rows in yaml.safe_load(open("data/guests/special.yaml"))["heroes"].items():
    for row in rows:
        if row.get("custom_stats"):
            GuestTemplate.objects.get(key=row["key"]).initial_skills.set(
                Skill.objects.filter(key__in=row.get("skills", []))
            )
from battle.troops import (  # noqa: E402
    load_troop_templates_from_yaml,
    TROOP_TEMPLATES_CACHE_KEY,
)
from django.core.cache import cache  # noqa: E402 -- Django 隔离初始化后加载

cache.set(TROOP_TEMPLATES_CACHE_KEY, load_troop_templates_from_yaml())
print("LOADED", flush=True)
from battle.combatants_pkg import (  # noqa: E402 -- Django 隔离初始化后加载
    build_named_ai_guests,
    build_guest_combatants,
    build_troop_combatants,
    assign_agility_based_priorities,
)
from battle.simulation_core import simulate_battle  # noqa: E402 -- Django 隔离初始化后加载
from gameplay.services.technology import build_uniform_tech_levels  # noqa: E402 -- Django 隔离初始化后加载
from core.game_data.technology import get_guest_stat_bonuses  # noqa: E402 -- Django 隔离初始化后加载

missions = {m["key"]: m for m in yaml.safe_load(open("data/mission_templates.yaml"))["missions"]}
# No database changes beyond isolated in-memory SQLite.
# Pick standard recruitable templates with typical archetype weights; avoid unique/special stat templates.
keys = {}
for rarity in ["blue", "purple", "orange"]:
    for archetype in ["military", "civil"]:
        ts = GuestTemplate.objects.filter(rarity=rarity, archetype=archetype, recruitable=True).order_by("key")
        t = next(
            (t for t in ts if t.attribute_weights in ({}, None) and t.base_attack < 100 and t.base_intellect < 100),
            ts.first(),
        )
        keys[(rarity, archetype)] = t.key
print("PLAYER_TEMPLATES", keys, flush=True)


def make_party(m, seed, rarity, level, composition="mixed", troops=False, equip=0):
    rng = random.Random(seed)
    ar = ["military", "military", "civil"] if composition == "mixed" else [composition] * 3
    # Fixed three-guest roster; modest gear modeled as explicit core attribute increment.
    import battle.combatants_pkg.ai_generator as aig

    old_allocate = aig.allocate_ai_attribute_points

    def allocated(g, total_points, *, rng):
        return {"agility" if g.archetype == "military" else "intellect": total_points}

    aig.allocate_ai_attribute_points = allocated
    try:
        gs = build_named_ai_guests([keys[(rarity, a)] for a in ar], level=level, rng=rng)
    finally:
        aig.allocate_ai_attribute_points = old_allocate
    from guests.services.skills import collect_unmet_skill_requirements

    for g, a in zip(gs, ar):
        naked = {"force": g.force, "intellect": g.intellect, "defense": g.defense_stat, "agility": g.agility}
        g.force += equip
        g.intellect += equip
        g.defense_stat += equip
        g.agility += equip
        g.current_hp = g.max_hp
        options = (
            ["meteor_pierce_moon", "fatal_chain_sword", "thunder_control", "ice_curse"]
            if a == "military"
            else [
                "brahma_true_fire",
                "five_thunder_descent",
                "thunder_nine_heavens",
                "tangseng_sermon",
                "red_lotus_dance",
                "flower_rain",
                "taiyi_wind",
                "ice_curse",
            ]
        )
        learned = []
        for k in options:
            sk = Skill.objects.get(key=k)
            if not collect_unmet_skill_requirements(g, sk, non_equipment_attributes=naked):
                learned.append(k)
                break
        for k in ["iron_wall_heart", "steadfast_planning"]:
            sk = Skill.objects.get(key=k)
            if not collect_unmet_skill_requirements(g, sk, non_equipment_attributes=naked):
                learned.append(k)
        if PROFILE == "weak":
            learned = ["flower_rain"] if a == "civil" else ["meteor_pierce_moon"]
        if PROFILE == "no_passives":
            learned = learned[:1]
        if PROFILE == "tank" and g is gs[0]:
            learned = ["iron_wall_heart", "draw_enemy_blades", "meteor_pierce_moon"]
        g._override_skills = learned
    attackers = build_guest_combatants(gs, "attacker")
    tech = m["enemy_technology"]
    egs = build_named_ai_guests(m["enemy_guests"], level=tech["guest_level"], rng=rng)
    defenders = build_guest_combatants(egs, "defender", stat_bonuses=get_guest_stat_bonuses(tech))
    defenders += build_troop_combatants(
        loadout=m["enemy_troops"], side="defender", manor=None, tech_levels=build_uniform_tech_levels(tech["level"])
    )
    if troops:
        load = [
            {"qiang_ke": 120},
            {"jian_hao": 180, "archer": 100},
            {"jian_sheng": 250, "quan_sheng": 200, "arrow_god": 100},
        ][["blue", "purple", "orange"].index(rarity)]
        load = {k: int(v * float(troops)) for k, v in load.items()}
        attackers += build_troop_combatants(
            loadout=load, side="attacker", manor=None, tech_levels=build_uniform_tech_levels(tech["level"])
        )
    assign_agility_based_priorities(attackers, defenders)
    return attackers, defenders


PROFILE = "strong"
scenario = sys.argv[1] if len(sys.argv) > 1 else "matrix"

if scenario == "matrix":
    results = []

    for mk, rarity, level, eq in [
        ("yaolu_hujing", "blue", 45, 35),
        ("xisui_yougu", "purple", 70, 50),
        ("yijin_mijing", "orange", 95, 65),
    ]:
        for comp in ["military", "mixed", "civil"]:
            for troops in [False, True]:
                wins = 0
                rounds = []
                for seed in range(1, 21):
                    a, d = make_party(missions[mk], seed, rarity, level, comp, troops, eq)
                    if seed == 1 and comp == "mixed" and not troops:
                        print(
                            "STATS",
                            mk,
                            [
                                (
                                    u.name,
                                    u.force_attr,
                                    u.intellect_attr,
                                    u.defense,
                                    u.agility,
                                    u.max_hp,
                                    [v["key"] for v in u.skills],
                                )
                                for u in a + d
                                if u.kind == "guest"
                            ],
                            flush=True,
                        )
                    r = simulate_battle(a, d, rng=random.Random(seed + 1000), seed=seed, travel_seconds=None, config={})
                    wins += r.winner == "attacker"
                    rounds.append(len(r.rounds))
                row = dict(
                    mission=mk,
                    variant="proposed",
                    composition=comp,
                    troops=troops,
                    wins=wins,
                    n=20,
                    meanround=statistics.mean(rounds),
                )
                results.append(row)
                print(json.dumps(row), flush=True)
    open("/tmp/cultivation_balance_legal.json", "w").write(json.dumps(results, indent=2))

if scenario == "endpoints":
    results = []

    for mk, rarity, level, eq in [
        ("yaolu_hujing", "blue", 40, 35),
        ("yaolu_hujing", "blue", 50, 35),
        ("xisui_yougu", "purple", 65, 50),
        ("xisui_yougu", "purple", 75, 50),
        ("yijin_mijing", "orange", 90, 65),
        ("yijin_mijing", "orange", 100, 65),
        ("dongtian_fudi", "orange", 95, 65),
        ("zhuiji_manzu", "orange", 95, 65),
        ("jiufeng_feihuan", "purple", 70, 50),
        ("wulongshan", "blue", 45, 35),
    ]:
        for troops in [False, True]:
            wins = 0
            rounds = []
            for seed in range(1, 21):
                a, d = make_party(missions[mk], seed, rarity, level, "mixed", troops, eq)
                r = simulate_battle(a, d, rng=random.Random(seed + 1000), seed=seed, travel_seconds=None, config={})
                wins += r.winner == "attacker"
                rounds.append(len(r.rounds))
            row = dict(
                mission=mk,
                level=level,
                composition="mixed",
                troops=troops,
                wins=wins,
                n=20,
                meanround=statistics.mean(rounds),
            )
            results.append(row)
            print(json.dumps(row), flush=True)
    open("/tmp/cultivation_balance_endpoints.json", "w").write(json.dumps(results, indent=2))

if scenario == "strategy":
    results = []

    for PROFILE in ["strong", "weak", "no_passives", "tank"]:
        for troops in [False, 0.5, True]:
            wins = 0
            rounds = []
            for seed in range(1, 41):
                a, d = make_party(missions["yijin_mijing"], seed, "orange", 95, "mixed", troops, 65)
                r = simulate_battle(a, d, rng=random.Random(seed + 1000), seed=seed, travel_seconds=None, config={})
                wins += r.winner == "attacker"
                rounds.append(len(r.rounds))
            row = dict(
                mission="yijin_mijing",
                level=95,
                profile=PROFILE,
                troops=troops,
                wins=wins,
                n=40,
                meanround=statistics.mean(rounds),
            )
            results.append(row)
            print(json.dumps(row), flush=True)
    open("/tmp/cultivation_balance_strategy.json", "w").write(json.dumps(results, indent=2))
