"""指定关卡难度成对复核；仅使用内存SQLite。"""

# isort: skip_file
# ruff: noqa: E402
# 显式复用隔离初始化的命名空间，避免向当前模块隐式注入名称。
from pathlib import Path
import os
import sys
import json
import random
import copy
import statistics

os.chdir(Path(__file__).resolve().parents[1])
sys.path.insert(0, str(Path.cwd()))
# Reuse isolated source-table bootstrap, never the old probing loop.
bootstrap_path = Path("reports/cultivation_balance_simulation_20261018.py")
src = bootstrap_path.read_text()
bootstrap = {"__file__": str(bootstrap_path.resolve())}
exec(compile(src[: src.index("def make_party(")], str(bootstrap_path), "exec"), bootstrap)
yaml = bootstrap["yaml"]
profiles = bootstrap["profiles"]
GuestTemplate = bootstrap["GuestTemplate"]
Skill = bootstrap["Skill"]
missions = bootstrap["missions"]
keys = bootstrap["keys"]
build_named_ai_guests = bootstrap["build_named_ai_guests"]
build_guest_combatants = bootstrap["build_guest_combatants"]
build_troop_combatants = bootstrap["build_troop_combatants"]
build_uniform_tech_levels = bootstrap["build_uniform_tech_levels"]
get_guest_stat_bonuses = bootstrap["get_guest_stat_bonuses"]
assign_agility_based_priorities = bootstrap["assign_agility_based_priorities"]
simulate_battle = bootstrap["simulate_battle"]
from guests.management.commands.load_guest_templates import Command  # noqa: E402 -- Django 隔离初始化后加载

cmd = Command()
required = {
    "hist_suitang_0162_zhairang",
    "hero_qin_shubao",
    "hist_suitang_0164_shanxiongxin",
    "hist_suitang_0165_wangbodang",
    "hero_luo_cheng",
    "hero_cheng_yaojin",
}
for f in Path("data/guests").glob("*.yaml"):
    for rarity, rows in (yaml.safe_load(f.read_text()).get("heroes") or {}).items():
        for row in rows:
            if row["key"] not in required:
                continue
            built = cmd._build_template_from_stats(
                row, rarity, row["archetype"], row.get("custom_stats") or profiles[rarity][row["archetype"]]
            )
            skillkeys = built.pop("skills")
            built.pop("avatar", None)
            tpl, _ = GuestTemplate.objects.update_or_create(key=row["key"], defaults=built)
            tpl.initial_skills.set(Skill.objects.filter(key__in=skillkeys))
live = json.loads(Path(__file__).with_name("cultivation_reference_live_20261018.json").read_text())
for row in live["templates"]:
    row = dict(row)
    skillkeys = row.pop("skills")
    tpl, _ = GuestTemplate.objects.update_or_create(key=row.pop("key"), defaults=row)
    tpl.initial_skills.set(Skill.objects.filter(key__in=skillkeys))
missions.update({m["key"]: m for m in live["missions"]})
from battle.random_context import (  # noqa: E402
    BattleRandomContext,
    RNG_STREAM_AI_GROWTH,
    RNG_STREAM_COMBAT,
)
from guests.services.skills import collect_unmet_skill_requirements  # noqa: E402 -- Django 隔离初始化后加载
from types import SimpleNamespace  # noqa: E402 -- Django 隔离初始化后加载
from battle.city_defense import (  # noqa: E402
    build_city_defense_combatants,
    city_defense_max_hp_for_key,
)
from core.config import BUILDING_KEYS  # noqa: E402 -- Django 隔离初始化后加载
from django.utils import timezone  # noqa: E402 -- Django 隔离初始化后加载


def city(level):
    class Buildings:
        def select_related(self, *a):
            return self

        def filter(self, **kw):
            return self

        def order_by(self, *a):
            return self

        def __iter__(self):
            for key in [BUILDING_KEYS.WALL, BUILDING_KEYS.ARROW_TOWER]:
                yield SimpleNamespace(
                    building_type=SimpleNamespace(key=key),
                    level=level,
                    current_hp=city_defense_max_hp_for_key(key, level),
                    hp_updated_at=timezone.now(),
                )

    return build_city_defense_combatants(SimpleNamespace(buildings=Buildings()), side="defender")


def player(seed, n, level, rarity, equip, load, tech, side):
    ar = ["military" if i % 3 != 2 else "civil" for i in range(n)]
    rng = random.Random(seed + 5000)
    import battle.combatants_pkg.ai_generator as aig

    orig = aig.allocate_ai_attribute_points
    aig.allocate_ai_attribute_points = lambda g, total_points, *, rng: {
        "agility" if g.archetype == "military" else "intellect": total_points
    }
    try:
        gs = build_named_ai_guests([keys[(rarity, a)] for a in ar], level=level, rng=rng)
    finally:
        aig.allocate_ai_attribute_points = orig
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
                learned = [k]
                break
        for k in ["iron_wall_heart", "steadfast_planning"]:
            if not collect_unmet_skill_requirements(g, Skill.objects.get(key=k), non_equipment_attributes=naked):
                learned.append(k)
        g._override_skills = learned
    return build_guest_combatants(gs, side, limit=n) + build_troop_combatants(
        loadout=load, side=side, manor=None, tech_levels=build_uniform_tech_levels(tech)
    )


def combat(m, seed, n, level, rarity, equip, load, tech, wall=0):
    ctx = BattleRandomContext.create(seed)
    reverse = m.get("is_defense", False)
    pside = "defender" if reverse else "attacker"
    eside = "attacker" if reverse else "defender"
    p = player(seed, n, level, rarity, equip, load, tech, pside)
    ec = m["enemy_technology"]
    eg = build_named_ai_guests(m["enemy_guests"], level=ec["guest_level"], rng=ctx.rng(RNG_STREAM_AI_GROWTH))
    e = build_guest_combatants(
        eg, eside, limit=len(eg), stat_bonuses=get_guest_stat_bonuses(ec)
    ) + build_troop_combatants(
        loadout=m.get("enemy_troops") or {}, side=eside, manor=None, tech_levels=build_uniform_tech_levels(ec["level"])
    )
    if reverse and wall:
        p += city(wall)
    a, d = (e, p) if reverse else (p, e)
    assign_agility_based_priorities(a, d)
    ph = sum(u.hp for u in p if u.kind == "guest")
    eh = sum(u.hp for u in e if u.kind == "guest")
    r = simulate_battle(a, d, rng=ctx.rng(RNG_STREAM_COMBAT), seed=seed, travel_seconds=None, config={})
    return dict(
        win=r.winner == pside,
        rounds=len(r.rounds),
        player_hp=sum(max(0, u.hp) for u in p if u.kind == "guest") / ph,
        enemy_hp=sum(max(0, u.hp) for u in e if u.kind == "guest") / eh,
        guest_alive=sum(u.hp > 0 for u in p if u.kind == "guest"),
        troop_remaining=sum(u.troop_strength for u in p if u.kind == "troop"),
    )


def evaluate(m, label, n, level, rarity, equip, load, tech, wall=0, count=12):
    rows = [combat(m, seed, n, level, rarity, equip, load, tech, wall) for seed in range(1, count + 1)]
    out = dict(
        label=label,
        mission=m["key"],
        n=n,
        level=level,
        rarity=rarity,
        equip=equip,
        load=load,
        tech=tech,
        wall=wall,
        wins=sum(r["win"] for r in rows),
        seeds=count,
    )
    out.update(
        {
            k: round(statistics.mean(r[k] for r in rows), 3)
            for k in ["rounds", "player_hp", "enemy_hp", "guest_alive", "troop_remaining"]
        }
    )
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return out


def configure():
    changes = {
        "task_cultivation_road_blade": (70, 40, 60, 45, 50, 2000),
        "task_cultivation_bandit_leader": (100, 35, 55, 75, 65, 2400),
        "task_cultivation_valley_swordsman": (80, 45, 70, 75, 65, 1600),
        "task_cultivation_valley_healer": (45, 100, 55, 70, 75, 1600),
        "task_cultivation_vajra_guard": (150, 65, 155, 75, 70, 100000),
        "task_cultivation_mind_guard": (65, 175, 100, 100, 100, 100000),
        "task_cultivation_demon_guard": (170, 60, 95, 155, 80, 90000),
    }
    for key, vals in changes.items():
        GuestTemplate.objects.filter(key=key).update(
            **dict(zip(["base_attack", "base_intellect", "base_defense", "base_agility", "base_luck", "base_hp"], vals))
        )
    cand = {k: copy.deepcopy(missions[k]) for k in ["yaolu_hujing", "xisui_yougu", "yijin_mijing"]}
    c = cand["yaolu_hujing"]
    c["enemy_guests"] = [dict(key="task_cultivation_road_blade", skills=["iron_wall_heart"])] * 6 + [
        dict(key="task_cultivation_bandit_leader", skills=[])
    ]
    c["enemy_troops"] = {}
    c["enemy_technology"] = {"level": 7, "guest_level": 35, "guest_bonus": 0.12}
    c = cand["xisui_yougu"]
    c["enemy_guests"] = (
        [dict(key="task_cultivation_valley_swordsman", skills=["iron_wall_heart"])] * 2
        + [dict(key="task_cultivation_valley_swordsman", skills=["iron_wall_heart", "fatal_chain_sword"])]
        + [dict(key="task_cultivation_valley_healer", skills=["flower_rain", "steadfast_planning"])] * 3
    )
    c["enemy_troops"] = {"arrow_god": 800, "qiang_wang": 800, "quan_sheng": 800}
    c["enemy_technology"] = {"level": 10, "guest_level": 85, "guest_bonus": 0.3}
    c = cand["yijin_mijing"]
    c["enemy_troops"] = {"arrow_god": 300, "quan_sheng": 700}
    c["enemy_technology"] = {"level": 9, "guest_level": 80, "guest_bonus": 0.26}
    return cand


if __name__ == "__main__":
    candidates = configure()
    out = []
    cases = [
        ("jiguanshou_chuxian", "yaolu_hujing", 5, "blue", 45, 35, 7, {"jian_hao": 150, "archer": 150}),
        ("jiguanshou_chuxian", "yaolu_hujing", 5, "blue", 50, 35, 7, {}),
        ("jiguanshou_chuxian", "yaolu_hujing", 5, "blue", 50, 35, 7, {"jian_hao": 300, "archer": 300}),
        ("jiguanshou_chuxian", "yaolu_hujing", 8, "blue", 45, 35, 7, {}),
        ("jiguanshou_chuxian", "yaolu_hujing", 10, "blue", 40, 35, 7, {}),
        (
            "wagangzhai_nixi",
            "xisui_yougu",
            5,
            "orange",
            100,
            65,
            10,
            {"jian_sheng": 500, "quan_sheng": 500, "arrow_god": 500},
        ),
        (
            "wagangzhai_nixi",
            "xisui_yougu",
            8,
            "orange",
            100,
            65,
            10,
            {"jian_sheng": 300, "quan_sheng": 300, "arrow_god": 300},
        ),
        (
            "wagangzhai_nixi",
            "xisui_yougu",
            10,
            "orange",
            100,
            65,
            10,
            {"jian_sheng": 300, "quan_sheng": 300, "arrow_god": 300},
        ),
        (
            "wagangzhai_nixi",
            "xisui_yougu",
            8,
            "purple",
            100,
            50,
            10,
            {"jian_sheng": 800, "quan_sheng": 800, "arrow_god": 800},
        ),
        (
            "dongtian_fudi",
            "yijin_mijing",
            5,
            "orange",
            100,
            65,
            10,
            {"jian_sheng": 500, "quan_sheng": 500, "arrow_god": 500},
        ),
        (
            "dongtian_fudi",
            "yijin_mijing",
            8,
            "orange",
            100,
            65,
            10,
            {"jian_sheng": 300, "quan_sheng": 300, "arrow_god": 300},
        ),
        (
            "dongtian_fudi",
            "yijin_mijing",
            10,
            "orange",
            100,
            65,
            10,
            {"jian_sheng": 300, "quan_sheng": 300, "arrow_god": 300},
        ),
        ("dongtian_fudi", "yijin_mijing", 15, "orange", 100, 65, 10, {}),
    ]
    for base, target, n, rar, lv, eq, tech, load in cases:
        for m in [missions[base], candidates[target]]:
            out.append(evaluate(m, "paired_v2", n, lv, rar, eq, load, tech, count=40))
    for n, count in [(5, 500), (8, 300)]:
        load = {"jian_sheng": count, "quan_sheng": count, "arrow_god": count}
        out.append(
            evaluate(missions["wagangzhai_nixi"], "city_level5", n, 100, "orange", 65, load, 10, wall=5, count=40)
        )
    Path("/tmp/cultivation_calibrate_paired_v2.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    Path("/tmp/cultivation_calibrate_candidate_v2.json").write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2)
    )
    Path("/tmp/cultivation_calibrate_npcs_v2.json").write_text(
        json.dumps(
            list(
                GuestTemplate.objects.filter(key__startswith="task_cultivation_").values(
                    "key",
                    "rarity",
                    "archetype",
                    "base_attack",
                    "base_intellect",
                    "base_defense",
                    "base_agility",
                    "base_luck",
                    "base_hp",
                    "growth_range",
                )
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
