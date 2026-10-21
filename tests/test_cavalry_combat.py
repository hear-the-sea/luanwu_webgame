from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from battle.combatants_pkg.troop_builder import _bloodthirsty_cavalry_skills, build_troop_combatants
from battle.report_events import troop_phase_skill
from battle.simulation.attack_execution import perform_attack
from battle.simulation.battle_flow import _resolve_standard_round, resolve_priority_phases
from battle.simulation.turn_order import determine_turn_order
from battle.skills import trigger_skills
from battle.troops import load_troop_templates_from_yaml
from core.game_data.technology import build_uniform_tech_levels, get_troop_stat_bonuses_from_levels
from guilds.constants import normalize_guild_rules
from guilds.technology_tree import TECHNOLOGY_NODE_BY_KEY
from guilds.views.technology import _format_level_effect


class FixedRng(random.Random):
    def __init__(self, roll):
        super().__init__(1)
        self.roll = roll

    def random(self):
        return self.roll

    def uniform(self, _minimum, _maximum):
        return 1.0

    def choice(self, sequence):
        return sequence[0]

    def shuffle(self, sequence):
        return None


def test_cavalry_growth_caps_and_does_not_stack_personal_tech():
    bonuses = get_troop_stat_bonuses_from_levels(
        {"wolf_force": 99, "wolf_defense": 10, "wolf_hp": 10, "wolf_agility": 10, "qiang_attack": 10, "qiang_hp": 10},
        "bloodthirsty_cavalry",
    )
    assert bonuses == {"attack": 1.0, "defense": 1.0, "hp": 1.0, "agility": 0.5}
    assert get_troop_stat_bonuses_from_levels({"qiang_attack": 10}, "bloodthirsty_cavalry") == {}
    assert get_troop_stat_bonuses_from_levels({"wolf_force": -5}, "bloodthirsty_cavalry") == {}


@pytest.mark.parametrize("level", [0, 5, 10])
def test_cavalry_trades_attack_for_fastest_troop_turn_and_gun_survivability(monkeypatch, level):
    templates = load_troop_templates_from_yaml()
    monkeypatch.setattr("battle.combatants_pkg.troop_builder.load_troop_templates", lambda: templates)
    cavalry_tech = {key: level for key in ("wolf_force", "wolf_defense", "wolf_hp", "wolf_agility")}
    cavalry = build_troop_combatants({"bloodthirsty_cavalry": 100}, "attacker", tech_levels=cavalry_tech)[0]
    opponents = build_troop_combatants(
        {key: 100 for key in templates if key != "bloodthirsty_cavalry"},
        "defender",
        tech_levels=build_uniform_tech_levels(level),
    )
    gun = next(unit for unit in opponents if unit.template_key == "qiang_wang")
    assert cavalry.unit_defense == gun.unit_defense
    assert cavalry.unit_hp == gun.unit_hp
    assert cavalry.unit_attack < gun.unit_attack
    if level == 10:
        assert cavalry.unit_attack == 34
    assert cavalry.agility > max(unit.agility for unit in opponents)
    assert determine_turn_order([cavalry], opponents, FixedRng(0.99))[0] is cavalry


@pytest.mark.parametrize("hp", [1000, 500, 100, 1])
@pytest.mark.parametrize("roll, fires", [(0.499, True), (0.5, True), (0.501, False)])
def test_berserk_trigger_is_fixed_regardless_of_current_health(hp, roll, fires):
    actor = SimpleNamespace(
        luck=30, hp=hp, max_hp=1000, skills=_bloodthirsty_cavalry_skills({"wolf_berserk": 5}, "bloodthirsty_cavalry")
    )
    assert bool(trigger_skills(actor, FixedRng(roll))) is fires
    assert actor.skills[0]["trigger_chance"] == pytest.approx(0.5)
    assert "missing_hp_trigger_chance" not in actor.skills[0]


def test_explicit_none_disables_attacker_manor_technology(monkeypatch):
    import battle.execution as execution

    received_manors = []
    monkeypatch.setattr(execution, "build_guest_combatants", lambda *_args, **_kwargs: [])

    def capture_troops(_loadout, **kwargs):
        received_manors.append(kwargs["manor"])
        return []

    monkeypatch.setattr(execution, "build_troop_combatants", capture_troops)
    owner_manor = object()

    execution._build_attacker_units([], [], {}, execution.BattleOptions(attacker_manor=None), owner_manor)
    execution._build_attacker_units([], [], {}, execution.BattleOptions(), owner_manor)

    assert received_manors == [None, owner_manor]


def test_cached_troop_templates_are_backfilled_from_yaml(monkeypatch):
    import battle.troops as troop_service

    cached = {"legacy_troop": {"label": "旧兵种"}}
    refreshed_cache = {}
    monkeypatch.setattr(troop_service.cache, "get", lambda _key: cached)
    monkeypatch.setattr(
        troop_service.cache,
        "set",
        lambda _key, value, timeout: refreshed_cache.update(value),
    )
    monkeypatch.setattr(
        troop_service,
        "load_troop_templates_from_yaml",
        lambda: {"bloodthirsty_cavalry": {"label": "嗜血铁骑"}},
    )

    templates = troop_service.load_troop_templates_from_db()

    assert templates == {
        "legacy_troop": {"label": "旧兵种"},
        "bloodthirsty_cavalry": {"label": "嗜血铁骑"},
    }
    assert refreshed_cache == templates


def test_cavalry_skill_level_limits_and_unresearched_state():
    assert _bloodthirsty_cavalry_skills({}, "bloodthirsty_cavalry") == []
    assert _bloodthirsty_cavalry_skills({"wolf_berserk": 5}, "qiang_wang") == []
    capped = _bloodthirsty_cavalry_skills({"wolf_berserk": 99}, "bloodthirsty_cavalry")
    assert capped == _bloodthirsty_cavalry_skills({"wolf_berserk": 5}, "bloodthirsty_cavalry")
    assert capped[0]["damage_multiplier"] == pytest.approx(0.7)
    assert capped[0]["single_target_damage_multiplier"] == pytest.approx(1.2)


@pytest.mark.parametrize("target_count, dead_count, multiplier", [(2, 0, 0.7), (3, 0, 0.7), (1, 0, 1.2), (2, 1, 1.2)])
def test_berserk_damage_depends_on_living_enemy_count(monkeypatch, target_count, dead_count, multiplier):
    monkeypatch.setattr("battle.combatants_pkg.troop_builder.load_troop_templates", load_troop_templates_from_yaml)
    actor = build_troop_combatants({"bloodthirsty_cavalry": 100}, "attacker", tech_levels={"wolf_berserk": 5})[0]
    targets = [build_troop_combatants({"dao_sheng": 10000}, "defender", tech_levels={})[0] for _ in range(target_count)]
    for index, target in enumerate(targets):
        target.name = f"敌军{index}"
        if index >= target_count - dead_count:
            target.hp = 0
            target.troop_strength = 0
    normal_actor = build_troop_combatants({"bloodthirsty_cavalry": 100}, "attacker", tech_levels={})[0]
    normal_target = build_troop_combatants({"dao_sheng": 10000}, "defender", tech_levels={})[0]
    normal = perform_attack(normal_actor, [normal_actor], [normal_target], FixedRng(0.25))
    assert normal is not None
    event = perform_attack(actor, [actor], targets, FixedRng(0.25))
    assert event is not None
    hits = [event, *event["additional_targets"]]
    assert len(hits) == min(2, target_count - dead_count)
    assert len({hit["target"] for hit in hits}) == len(hits)
    assert all(hit["damage"] == pytest.approx(normal["damage"] * multiplier, abs=1) for hit in hits)
    assert event["skills"] == ["铁骑冲阵"]
    assert actor.skills[0]["damage_multiplier"] == 0.7


def test_berserk_does_not_switch_to_single_target_bonus_mid_attack(monkeypatch):
    monkeypatch.setattr("battle.combatants_pkg.troop_builder.load_troop_templates", load_troop_templates_from_yaml)
    actor = build_troop_combatants({"bloodthirsty_cavalry": 100}, "attacker", tech_levels={"wolf_berserk": 5})[0]
    targets = [build_troop_combatants({"dao_sheng": 10000}, "defender", tech_levels={})[0] for _ in range(2)]
    targets[0].hp = targets[0].troop_strength = 1
    normal_actor = build_troop_combatants({"bloodthirsty_cavalry": 100}, "attacker", tech_levels={})[0]
    normal_target = build_troop_combatants({"dao_sheng": 10000}, "defender", tech_levels={})[0]
    normal = perform_attack(normal_actor, [normal_actor], [normal_target], FixedRng(0.25))
    event = perform_attack(actor, [actor], targets, FixedRng(0.25))
    assert normal is not None and event is not None and event["target_defeated"]
    assert event["additional_targets"][0]["damage"] == pytest.approx(normal["damage"] * 0.7, abs=1)


@pytest.mark.parametrize("target_count", [1, 2])
def test_untriggered_berserk_keeps_normal_attack_damage(monkeypatch, target_count):
    monkeypatch.setattr("battle.combatants_pkg.troop_builder.load_troop_templates", load_troop_templates_from_yaml)
    damages = []
    for tech in ({}, {"wolf_berserk": 5}):
        actor = build_troop_combatants({"bloodthirsty_cavalry": 100}, "attacker", tech_levels=tech)[0]
        targets = [
            build_troop_combatants({"dao_sheng": 10000}, "defender", tech_levels={})[0] for _ in range(target_count)
        ]
        event = perform_attack(actor, [actor], targets, FixedRng(0.99))
        assert event is not None and event["skills"] == [] and event["additional_targets"] == []
        damages.append(event["damage"])
    assert damages[0] == damages[1]


def test_technology_display_and_costs_cover_every_new_level():
    from guilds.services.technology import calculate_tech_upgrade_cost

    assert _format_level_effect(TECHNOLOGY_NODE_BY_KEY["wolf_force"], 10) == "嗜血铁骑攻击 +100%"
    assert _format_level_effect(TECHNOLOGY_NODE_BY_KEY["wolf_agility"], 10) == "嗜血铁骑敏捷 +50%"
    assert TECHNOLOGY_NODE_BY_KEY["wolf_berserk"]["name"] == "铁骑冲阵"
    assert "铁骑冲阵" in _format_level_effect(TECHNOLOGY_NODE_BY_KEY["wolf_berserk"], 5)
    assert "固定触发率 50%" in _format_level_effect(TECHNOLOGY_NODE_BY_KEY["wolf_berserk"], 5)
    assert "双目标各 70%" in _format_level_effect(TECHNOLOGY_NODE_BY_KEY["wolf_berserk"], 5)
    assert "仅剩一个目标时 120%" in _format_level_effect(TECHNOLOGY_NODE_BY_KEY["wolf_berserk"], 5)
    for key in ("wolf_force", "wolf_defense", "wolf_hp", "wolf_agility", "wolf_berserk"):
        for level in range(TECHNOLOGY_NODE_BY_KEY[key]["max_level"]):
            assert all(value > 0 for value in calculate_tech_upgrade_cost(key, level).values())


def test_cavalry_rule_normalization_never_allows_free_or_negative_recruitment():
    rules = normalize_guild_rules(
        {
            "cavalry_recruitment": {
                "silver_per_unit": -1,
                "grain_per_unit": 0,
                "contribution_per_unit": 0,
                "max_batch": -4,
            }
        }
    )
    assert all(value >= 1 for value in rules["cavalry_recruitment"].values())


def test_cavalry_attacks_in_preemptive_and_ordinary_rounds_but_not_ranged_phase(monkeypatch):
    templates = load_troop_templates_from_yaml()
    # 兼容数据库旧模板或YAML回退未保留负优先级的情况。
    templates = {key: dict(value) for key, value in templates.items()}
    templates["bloodthirsty_cavalry"]["priority"] = 0
    monkeypatch.setattr("battle.combatants_pkg.troop_builder.load_troop_templates", lambda: templates)
    attacker = build_troop_combatants({"bloodthirsty_cavalry": 100}, "attacker", tech_levels={})
    defender = build_troop_combatants({"qiang_wang": 10000, "archer": 1}, "defender", tech_levels={"gong_range": 1})
    cavalry = attacker[0]
    assert cavalry.priority == -1
    phases, next_round = resolve_priority_phases(attacker, defender, FixedRng(0.99))
    assert [phase["priority"] for phase in phases] == [-2, -1]
    assert not any(event.get("actor") == cavalry.name and "damage" in event for event in phases[0]["events"])
    assert sum(event.get("actor") == cavalry.name and "damage" in event for event in phases[1]["events"]) == 1
    ordinary = _resolve_standard_round(attacker, defender, FixedRng(0.99), next_round)
    assert sum(event.get("actor") == cavalry.name and "damage" in event for event in ordinary["events"]) == 1


@pytest.mark.parametrize("side", ["attacker", "defender"])
@pytest.mark.parametrize("count", [100, 3000])
@pytest.mark.parametrize("target_count", [1, 2])
def test_cavalry_preemptive_attack_is_normal_and_ordinary_round_can_charge(monkeypatch, side, count, target_count):
    monkeypatch.setattr("battle.combatants_pkg.troop_builder.load_troop_templates", load_troop_templates_from_yaml)
    actor = build_troop_combatants({"bloodthirsty_cavalry": count}, side, tech_levels={"wolf_berserk": 5})[0]
    enemy_side = "defender" if side == "attacker" else "attacker"
    targets = [
        build_troop_combatants({"qiang_wang": 1000000}, enemy_side, tech_levels={})[0] for _ in range(target_count)
    ]
    for index, target in enumerate(targets):
        target.name = f"敌军{index}"
    attacker, defender = ([actor], targets) if side == "attacker" else (targets, [actor])
    phases, next_round = resolve_priority_phases(attacker, defender, FixedRng(0.25))
    event = next(row for row in phases[0]["events"] if row.get("actor") == actor.name and "damage" in row)
    for hit in [event, *event.get("additional_attacks", [])]:
        assert hit["skills"] == []
        assert hit["additional_targets"] == []
        assert hit["actor_template_key"] == "bloodthirsty_cavalry"
        assert troop_phase_skill(hit, event, -1) == ""
    # 同一个单位继续进入普通回合，技能不被先攻回合永久清除。
    actor.current_round = next_round
    ordinary = perform_attack(actor, attacker, defender, FixedRng(0.25))
    assert ordinary is not None
    for hit in [ordinary, *ordinary.get("additional_attacks", [])]:
        assert hit["skills"] == ["铁骑冲阵"]
        assert len(hit["additional_targets"]) == target_count - 1
