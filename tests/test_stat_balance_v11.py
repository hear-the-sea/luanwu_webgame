"""版本 11 四维平衡回归测试：智力策防与抗控、先攻伤害与控制折减。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from battle.combatants_pkg.core import Combatant
from battle.random_context import CURRENT_BATTLE_ENGINE_VERSION
from battle.simulation.damage_calculation import (
    _intellect_skill_damage_reduction,
    _status_chance_multiplier,
    calculate_attack_damage,
    process_status_effects,
)
from core.config import GUEST
from guests.guest_rules import compute_guest_attack_attribute_component


class _FixedRng:
    def __init__(self, value: float = 0.5):
        self.value = value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2.0

    def random(self) -> float:
        return self.value


def _make_guest(
    *,
    name: str,
    side: str,
    attack: int = 200,
    defense: int = 200,
    intellect: int = 100,
    hp: int = 15000,
    priority: int = 0,
) -> Combatant:
    return Combatant(
        name=name,
        side=side,
        kind="guest",
        rarity="orange",
        troop_strength=0,
        attack=attack,
        defense=defense,
        hp=hp,
        max_hp=hp,
        agility=150,
        luck=100,
        priority=priority,
        force_attr=200,
        intellect_attr=intellect,
        defense_attr=defense,
    )


def test_attack_weights_separate_military_and_civil_normal_attacks():
    assert GUEST.MILITARY_FORCE_WEIGHT == pytest.approx(0.85)
    assert GUEST.MILITARY_INTELLECT_WEIGHT == pytest.approx(0.15)
    assert GUEST.CIVIL_FORCE_WEIGHT == pytest.approx(0.85)
    assert GUEST.CIVIL_INTELLECT_WEIGHT == pytest.approx(0.15)
    military = SimpleNamespace(archetype="military", force=520, intellect=140)
    civil = SimpleNamespace(archetype="civil", force=220, intellect=520)
    assert compute_guest_attack_attribute_component(military) == 463
    assert compute_guest_attack_attribute_component(civil) == 265


def test_v11_intellect_reduces_skill_burst_damage_and_preserves_v10_replay():
    attacker = _make_guest(name="攻方", side="attacker", attack=300, intellect=100)
    low_int_target = _make_guest(name="低智门客", side="defender", defense=240, intellect=0)
    high_int_target = _make_guest(name="高智门客", side="defender", defense=240, intellect=400)
    skill = [{"name": "火计", "kind": "active", "damage_formula": {"base": 2000}}]

    assert CURRENT_BATTLE_ENGINE_VERSION == "11"
    assert _intellect_skill_damage_reduction(high_int_target, "10") == 0.0
    assert _intellect_skill_damage_reduction(high_int_target, "11") == pytest.approx(400 / 1260)
    # 约 704 智触及 45% 上限
    capped_target = _make_guest(name="极智门客", side="defender", intellect=704)
    assert _intellect_skill_damage_reduction(capped_target, "11") == pytest.approx(0.45)

    # 版本 10 历史回放不受智力策防影响
    v10_low = calculate_attack_damage(
        attacker,
        low_int_target,
        skill,
        _FixedRng(0.9),
        round_priority=0,
        battle_engine_version="10",
        damage_component="burst",
    )
    v10_high = calculate_attack_damage(
        attacker,
        high_int_target,
        skill,
        _FixedRng(0.9),
        round_priority=0,
        battle_engine_version="10",
        damage_component="burst",
    )
    assert v10_low.damage == v10_high.damage

    # 版本 11 高智目标受到的技能额外伤害显著降低（400 智减伤 400/1260）
    v11_low = calculate_attack_damage(
        attacker,
        low_int_target,
        skill,
        _FixedRng(0.9),
        round_priority=0,
        battle_engine_version="11",
        damage_component="burst",
    )
    v11_high = calculate_attack_damage(
        attacker,
        high_int_target,
        skill,
        _FixedRng(0.9),
        round_priority=0,
        battle_engine_version="11",
        damage_component="burst",
    )
    assert v11_low.damage == pytest.approx(v10_low.damage, abs=1)
    assert v11_high.damage == pytest.approx(v11_low.damage * (1.0 - 400 / 1260), abs=2)


def test_v11_preemptive_damage_and_status_chance_are_scaled_with_intellect_resist():
    vanguard = _make_guest(name="先攻门客", side="attacker", attack=300, priority=-1)
    main_force = _make_guest(name="主力门客", side="attacker", attack=300, priority=0)
    target = _make_guest(name="目标门客", side="defender", defense=200, intellect=400)

    # 门客 PvP 普攻倍率：版本 10 为 15x，版本 11 为 13x；先攻伤害：版本 10 为 0.8，版本 11 为 0.7
    normal_v10 = calculate_attack_damage(
        main_force, target, [], _FixedRng(0.9), round_priority=0, battle_engine_version="10"
    )
    normal_v11 = calculate_attack_damage(
        main_force, target, [], _FixedRng(0.9), round_priority=0, battle_engine_version="11"
    )
    preempt_v10 = calculate_attack_damage(
        vanguard, target, [], _FixedRng(0.9), round_priority=-1, battle_engine_version="10"
    )
    preempt_v11 = calculate_attack_damage(
        vanguard, target, [], _FixedRng(0.9), round_priority=-1, battle_engine_version="11"
    )
    assert normal_v11.damage == pytest.approx(normal_v10.damage * (13.0 / 15.0), abs=2)
    assert preempt_v10.damage == pytest.approx(normal_v10.damage * 0.8, abs=2)
    assert preempt_v11.damage == pytest.approx(normal_v11.damage * 0.7, abs=2)

    # 控制概率折减：先攻 0.7 × 目标 400 智抗控 (1 - 400/2000 = 0.80) = 0.56
    assert _status_chance_multiplier(vanguard, target, "10") == 1.0
    assert _status_chance_multiplier(vanguard, target, "11") == pytest.approx(0.7 * 0.80)
    # 约 686 智触及 30% 抗控上限
    capped_target = _make_guest(name="高抗门客", side="defender", intellect=686)
    assert _status_chance_multiplier(main_force, capped_target, "11") == pytest.approx(0.70)

    stun_skill = [
        {"name": "震慑", "kind": "active", "status_effect": "stunned", "status_probability": 1.0, "status_duration": 1}
    ]
    # rng=0.60：版本 10 必中；版本 11 下 1.0 * 0.56 = 0.56 < 0.60，被抗住
    assert (
        process_status_effects(
            vanguard, target, stun_skill, _FixedRng(0.60), phase="inflict", battle_engine_version="10"
        )
        != []
    )
    target_fresh = _make_guest(name="目标门客2", side="defender", defense=200, intellect=400)
    assert (
        process_status_effects(
            vanguard, target_fresh, stun_skill, _FixedRng(0.60), phase="inflict", battle_engine_version="11"
        )
        == []
    )
