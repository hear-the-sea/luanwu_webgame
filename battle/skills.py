from __future__ import annotations

import random
from typing import Dict, List

from .utils.battle_calculator import calculate_skill_bonus
from .utils.status_effects import CONTROL_STATUS_EFFECTS, apply_status_effect, get_status_label


def trigger_skills(attacker, rng: random.Random) -> List[Dict]:
    """
    Roll and select skills for the given attacker.

    Ensures at most one active skill fires per turn while passives accumulate.

    触发率公式：chance = 0.95 * (luck / 300) ^ 0.85，上限95%，下限5%
    - 技能不再有独立触发率，统一由运势决定
    - 运势50（武将典型）：20.2% 触发率
    - 运势100：36.2% 触发率
    - 运势150（文官典型）：52.6% 触发率
    - 运势180：60.8% 触发率
    - 运势300及以上：达到95%上限

    设计理念：
    - 武将依赖普攻和兵力优势，技能为辅助
    - 文官通过高运势获得频繁技能释放，弥补武力不足
    - 文官触发率约为武将的3倍，体现职业差异
    """
    triggered: List[Dict] = []
    triggered_active: List[Dict] = []

    # 运势决定触发率，所有技能共用同一个触发概率
    # 幂函数公式：低运势差距大，高运势收敛到95%上限
    # 安全修复：设置最小触发率5%，防止luck=1时触发率过低(0.03%)
    MIN_SKILL_CHANCE = 0.05  # 最小触发率5%
    MAX_SKILL_CHANCE = 0.95  # 最大触发率95%
    luck = max(1, getattr(attacker, "luck", 50))  # 防止负数或零导致数学错误
    base_chance = MAX_SKILL_CHANCE * pow(luck / 300, 0.85)
    chance = max(MIN_SKILL_CHANCE, min(MAX_SKILL_CHANCE, base_chance))

    for skill in attacker.skills:
        if not _is_active_skill(skill):
            triggered.append(skill)
            continue
        if rng.random() > chance:
            continue
        triggered_active.append(skill)
    if triggered_active:
        triggered.append(rng.choice(triggered_active))
    return triggered


def skill_damage_bonus(skills: List[Dict], attacker, target) -> int:
    return sum(calculate_skill_bonus(skill, attacker, target) for skill in skills)


def apply_skill_statuses(skills: List[Dict], target, rng: random.Random) -> List[str]:
    """
    对目标施加技能状态效果（如眩晕）。

    小兵单位对控制类状态有特殊处理：
    - 眩晕等控制效果 → 转换为"士气低落"（伤害降低30%）
    - 原因：小兵代表一群士兵，被震慑后士气下降但不会完全停止行动
    """
    is_troop = getattr(target, "kind", "") == "troop"

    inflicted: List[str] = []
    for skill in skills:
        status_effect = skill.get("status_effect")
        chance = float(skill.get("status_probability") or 0.0)
        duration = int(skill.get("status_duration") or 0)
        if not status_effect or chance <= 0 or duration <= 0:
            continue
        if rng.random() <= chance:
            already_acted = getattr(target, "has_acted_this_round", False)

            # 小兵单位：控制效果转换为削弱效果
            if is_troop and status_effect in CONTROL_STATUS_EFFECTS:
                apply_status_effect(target, "weakened", duration, defer=already_acted)
                inflicted.append(get_status_label("weakened"))
            else:
                apply_status_effect(target, status_effect, duration, defer=already_acted)
                inflicted.append(get_status_label(status_effect))
    return inflicted


def _is_active_skill(skill: Dict) -> bool:
    kind = skill.get("kind", "active")
    return str(kind) == "active"
