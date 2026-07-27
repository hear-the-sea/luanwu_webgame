from __future__ import annotations

import random
from itertools import chain
from typing import Any, Dict, Iterable, List

from .combat_math import troop_unit_hp
from .modifier_lifecycle import clear_round_and_action_modifiers
from .simulation.report_state import snapshot_unit_state
from .utils.status_effects import cleanup_status_effects

# ============ 战斗恢复常量 ============

# 五气朝元默认恢复比例（当前生命的10%）
DEFAULT_BATTLE_HEAL_RATIO = 0.10
# 防守方拳系【五气朝元】恢复比例（当前生命的13%）
DEFENDER_QUAN_BATTLE_HEAL_RATIO = 0.13


def prepare_combatants_for_round(
    attacker_team: Iterable,
    defender_team: Iterable,
    round_no: int,
    promote_pending: bool = True,
) -> None:
    """
    Reset combatants' round flags and optionally promote pending status effects.
    """
    for unit in chain(attacker_team, defender_team):
        unit.has_acted_this_round = False
        unit.current_round = round_no
        clear_round_and_action_modifiers(unit)
        if not getattr(unit, "status_effects", None):
            continue
        for status, payload in list(unit.status_effects.items()):
            active = max(0, payload.get("active", 0))
            pending = max(0, payload.get("pending", 0))
            if promote_pending and pending > 0:
                active = max(active, pending)
                pending = 0
            unit.status_effects[status] = {"active": active, "pending": pending}
        cleanup_status_effects(unit)


def apply_battle_heal(
    units: Iterable,
    rng: random.Random,
) -> List[Dict[str, Any]]:
    """
    应用战斗恢复效果（五气朝元）。

    设计变更：五气朝元改为在单位行动前触发，而非回合开始统一触发。
    这使得敏捷高的拳类单位能够优先回血，增加策略深度。
    同时允许对手通过集火在敌人回血前将其击杀。

    门客：恢复当前生命值的10%
    小兵：按当前血量恢复兵力（当前HP × 10% / 单位HP = 恢复兵力数）
    """
    heals: List[Dict[str, Any]] = []
    for unit in units:
        if unit.hp <= 0:
            continue
        tech_effects = getattr(unit, "tech_effects", None)
        if not tech_effects:
            continue
        heal_chance = tech_effects.get("battle_heal_chance", 0)
        if heal_chance <= 0:
            continue
        if rng.random() < heal_chance:
            # 拳系恢复比例：从tech_effects读取，默认10%
            # 配合小兵vs小兵攻击倍率1.0，让治疗效果可通过科技升级调整
            heal_amount_ratio = float(tech_effects.get("battle_heal_amount", DEFAULT_BATTLE_HEAL_RATIO))
            # 防守方强化：拳系【五气朝元】恢复比例提升至13%
            if getattr(unit, "side", "") == "defender" and getattr(unit, "troop_class", "") == "quan":
                heal_amount_ratio = max(heal_amount_ratio, DEFENDER_QUAN_BATTLE_HEAL_RATIO)

            # 小兵：按当前血量恢复兵力
            if getattr(unit, "kind", "") == "troop":
                # 计算应恢复的HP量
                heal_hp_amount = int(unit.hp * heal_amount_ratio)

                # 转换为恢复兵力数
                per_unit_hp = troop_unit_hp(unit)
                heal_strength = max(1, int(heal_hp_amount / per_unit_hp))

                # 不能超过初始兵力
                initial_strength = getattr(unit, "initial_troop_strength", unit.troop_strength)
                current_strength = getattr(unit, "troop_strength", 0)
                heal_strength = min(heal_strength, initial_strength - current_strength)

                if heal_strength > 0:
                    # 恢复兵力
                    unit.troop_strength = current_strength + heal_strength

                    # 同时恢复对应的HP
                    healed_hp = heal_strength * per_unit_hp
                    max_hp = getattr(unit, "max_hp", unit.hp)
                    unit.hp = min(max_hp, unit.hp + healed_hp)

                    heals.append(
                        {
                            "unit": unit.name,
                            "side": unit.side,
                            "healed": healed_hp,
                            "heal_strength": heal_strength,
                            "new_hp": unit.hp,
                            "new_strength": unit.troop_strength,
                            "effect": "五气朝元",
                            "unit_state": snapshot_unit_state(unit),
                        }
                    )
            # 门客：恢复HP
            else:
                healed = int(unit.hp * heal_amount_ratio)
                max_hp = getattr(unit, "max_hp", unit.hp)
                unit.hp = min(max_hp, unit.hp + healed)
                heals.append(
                    {
                        "unit": unit.name,
                        "side": unit.side,
                        "healed": healed,
                        "new_hp": unit.hp,
                        "effect": "五气朝元",
                        "unit_state": snapshot_unit_state(unit),
                    }
                )
    return heals


def try_trigger_battle_heal_on_action(
    unit,
    rng: random.Random,
) -> Dict[str, Any] | None:
    """
    尝试在单位行动前触发战斗恢复效果（五气朝元）。

    这个函数用于单个单位的五气朝元触发判定，在该单位行动前调用。

    **设计要点：**
    1. 在控制状态判定之后调用，被眩晕/冻结的单位无法触发回血
    2. 让敏捷高的单位优先获得回血机会
    3. 允许对手通过集火在敌人回血前将其击杀
    4. 回血量基于当前血量而非回合开始时的血量

    **与回合开始统一触发的区别：**
    - 旧机制：回合开始时所有拳类单位一起触发，保底回血
    - 新机制：按行动顺序触发，增加策略性和互动性

    Args:
        unit: 战斗单位（门客或护院）
        rng: 随机数生成器

    Returns:
        如果触发回血，返回回血事件字典；否则返回None
    """
    if not unit or unit.hp <= 0:
        return None

    heal_events = apply_battle_heal([unit], rng)
    return heal_events[0] if heal_events else None
