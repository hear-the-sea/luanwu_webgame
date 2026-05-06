from __future__ import annotations

import math
from typing import Any

from core.exceptions import GameError
from core.utils import require_positive_int

# ============ 战斗数值常量 ============

# 门客对小兵的屠戮倍率
# 设计理由：让门客对小兵有压倒性优势，配合单兵防御系统保持合理战斗节奏
# 效果示例：600伤害门客 vs 弓箭手(单位HP13)，击杀数从46人提升到461人
SLAUGHTER_MULTIPLIER = 10

# 小兵对门客的攻击除数
# 公式：effective_attack = unit_attack * (strength / divisor)
# 数值越小，小兵对门客伤害越高
# 从2.5改为2.0，进一步提高小兵对门客的威胁
TROOP_VS_GUEST_ATTACK_DIVISOR = 2.0

# 小兵对小兵的攻击除数
# 设为1.0表示直接乘以兵力数，大幅提高小兵互殴伤害
# 目的：压制拳系五气朝元恢复(25人/回合)，让战斗快速结束
TROOP_VS_TROOP_ATTACK_DIVISOR = 1.0

# 小兵防御平方根缩放除数
# 公式：defense_multiplier = sqrt(strength) / divisor
# 使用平方根缩放避免线性增长导致大规模部队防御过高
# 示例：1000兵力→倍率15.8，2000兵力→倍率22.4
TROOP_DEFENSE_SQRT_DIVISOR = 2.0


def _unit_strength(unit: Any) -> int:
    strength = getattr(unit, "initial_troop_strength", None)
    if strength is None:
        strength = getattr(unit, "troop_strength", None)
    return require_positive_int(strength, contract_name="battle troop strength")


def _current_strength(unit: Any) -> int:
    """
    使用当前兵力进行战斗相关的倍率计算；仅在缺失时退回初始值。
    保留 _unit_strength 供单位属性(单兵攻防/生命)计算使用。

    修复bug: 之前攻防倍率始终使用initial_troop_strength，导致只剩371血的
    1000弓箭手仍然按1000兵力计算伤害，造成"371血秒杀1000人"的bug。
    """
    strength = getattr(unit, "troop_strength", None)
    if strength is None:
        strength = getattr(unit, "initial_troop_strength", 0)
        return require_positive_int(strength, contract_name="battle current troop strength")
    if isinstance(strength, bool):
        raise GameError(f"invalid battle current troop strength: {strength!r}")
    try:
        parsed_strength = int(strength)
    except (TypeError, ValueError):
        raise GameError(f"invalid battle current troop strength: {strength!r}")
    if parsed_strength > 0:
        return parsed_strength
    return require_positive_int(
        getattr(unit, "initial_troop_strength", 0), contract_name="battle current troop strength"
    )


def _unit_attack_value(unit: Any) -> int:
    unit_attack = getattr(unit, "unit_attack", None)
    if unit_attack is not None:
        return require_positive_int(unit_attack, contract_name="battle unit_attack")
    strength = max(1, _unit_strength(unit))
    attack = require_positive_int(getattr(unit, "attack", None), contract_name="battle attack")
    return max(1, int(attack / strength))


def _unit_defense_value(unit: Any) -> int:
    unit_defense = getattr(unit, "unit_defense", None)
    if unit_defense is not None:
        return require_positive_int(unit_defense, contract_name="battle unit_defense")
    strength = max(1, _unit_strength(unit))
    defense = require_positive_int(getattr(unit, "defense", None), contract_name="battle defense")
    return max(1, int(defense / strength))


def troop_unit_hp(unit: Any) -> int:
    unit_hp = getattr(unit, "unit_hp", None)
    # 忽略无效/未初始化的 unit_hp（0 或负数），回退到平均血量计算
    if unit_hp is not None:
        if isinstance(unit_hp, bool):
            raise AssertionError(f"invalid battle unit_hp: {unit_hp!r}")
        try:
            parsed_unit_hp = int(unit_hp)
        except (TypeError, ValueError) as exc:
            raise AssertionError(f"invalid battle unit_hp: {unit_hp!r}") from exc
        if parsed_unit_hp > 0:
            return parsed_unit_hp
    strength = max(1, _unit_strength(unit))
    max_hp = require_positive_int(getattr(unit, "max_hp", strength), contract_name="battle max_hp")
    return max(1, int(max_hp / strength))


def calculate_slaughter_multiplier(attacker: Any, target: Any) -> float:
    """
    计算门客对小兵的屠戮倍率加成

    设计目标：让门客对小兵有明显优势，配合单兵防御系统保持合理战斗节奏

    计算公式：
    - 固定倍率：10倍
    - 在结算时将门客对小兵的最终伤害乘以该倍率
      （见 simulation_core.perform_attack），击杀数按
      kills = int(final_damage / per_unit_hp) 计算。

    效果示例（600伤害 vs 弓箭手单位HP13）：
    - 基础击杀：600 / 13 ≈ 46人
    - 屠戮加成：final_damage = 600 * 10 = 6000
      kills = 6000 / 13 ≈ 461人
    - 倍率效果：10x击杀速度

    战斗预期（配合单兵防御系统）：
    - 600攻击门客 vs 2000满科技小兵（单兵防御12）：约20回合
    - 门客有压倒性优势但需要合理回合数
    - 小兵防御提升（科技）能延长战斗时间
    """
    # 只对门客攻击小兵生效
    if getattr(attacker, "kind", "") != "guest":
        return 1.0
    if getattr(target, "kind", "") != "troop":
        return 1.0

    # 固定倍率，配合单兵防御系统提供合理的击杀速度
    return SLAUGHTER_MULTIPLIER


def effective_attack_value(actor: Any, target: Any | None = None) -> int:
    """
    计算有效攻击值，小兵攻击时根据当前兵力数量和目标类型使用不同的倍率。

    小兵攻击倍率设计：
    - 对门客：兵力/2.0（平衡调整，让小兵对门客有更大威胁）
    - 对小兵：兵力/1.0（极限倍率，大幅提高小兵互殴伤害）

    倍率差异原因：
    - 门客血量高（~15,000-20,000），但小兵数量优势应该能形成威胁
    - 小兵互殴需要极高伤害压制拳系五气朝元恢复（25人/回合）
    - 目标：让战斗快速结束，同时小兵配置有战术意义

    **平衡调整**: 小兵对门客倍率从/2.5改为/2.0，进一步提高伤害
    """
    if getattr(actor, "kind", "") != "troop":
        return int(getattr(actor, "attack", 0))
    strength = _current_strength(actor)  # 使用当前兵力而非初始兵力
    unit_attack = _unit_attack_value(actor)
    if target is not None and getattr(target, "kind", "") != "troop":
        # 小兵打门客：伤害适度提升，让小兵对门客有威胁
        multiplier = max(1.0, strength / TROOP_VS_GUEST_ATTACK_DIVISOR)
    else:
        # 小兵打小兵：极限倍率（直接×兵力）
        multiplier = max(1.0, strength / TROOP_VS_TROOP_ATTACK_DIVISOR)
    return max(1, int(unit_attack * multiplier))


def effective_defense_value(target: Any, attacker: Any | None = None) -> int:
    """
    小兵防御按当前兵力缩放，与攻击系统对齐，提升防御性价比。
    门客防御直接使用属性值。

    **BUG修复**: 使用_current_strength()替代_unit_strength()，让防御力随减员同步降低
    **平衡性修复**: 使用平方根缩放防御，避免大规模部队防御过高导致伤害为0

    防御倍率公式：sqrt(strength) / TROOP_DEFENSE_SQRT_DIVISOR
    - 1000兵力 → 倍率 15.8 （防御3 → 47）
    - 2000兵力 → 倍率 22.4 （防御6 → 134）
    - 10000兵力 → 倍率 50.0 （防御10 → 500）
    """
    if getattr(target, "kind", "") != "troop":
        return int(getattr(target, "defense", 0))
    unit_defense = _unit_defense_value(target)
    strength = _current_strength(target)  # 使用当前兵力而非初始兵力
    # 使用平方根缩放，避免线性增长导致的防御过高问题
    multiplier = max(1.0, math.sqrt(strength) / TROOP_DEFENSE_SQRT_DIVISOR)
    return max(1, int(unit_defense * multiplier))
