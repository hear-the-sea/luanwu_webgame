"""
目标选择逻辑
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Callable

from .constants import PRIORITY_TARGET_WEIGHT, TROOP_COUNTERS
from .types import AttackSkill, _SelectedAttackTargets
from .utils import alive

if TYPE_CHECKING:
    from ..combatants_pkg.core import Combatant


def _active_attack_skills(skills: list[AttackSkill]) -> list[AttackSkill]:
    return [skill for skill in skills if str(skill.get("kind", "active")) == "active"]


def is_ranged_attack(actor: "Combatant", round_priority: int) -> bool:
    """判断是否为远程攻击（弓箭手在先攻/先锋回合视为远程攻击）"""
    if actor.troop_class != "gong":
        return False
    # 弓箭手在先锋回合(-2)和先攻回合(-1)视为远程攻击
    return round_priority < 0


def select_target_with_priority(actor: "Combatant", opponents: list["Combatant"], rng: random.Random) -> "Combatant":
    """
    加权概率选择攻击目标。

    护院优先攻击克制的敌方兵种（五行相克），门客优先攻击敌方门客。
    通过 PRIORITY_TARGET_WEIGHT 权重平衡策略性和随机性。

    Args:
        actor: 攻击者
        opponents: 可选敌方目标列表（已排除已阵亡单位）
        rng: 随机数生成器

    Returns:
        选中的攻击目标

    Examples:
        >>> # 刀兵有70%概率优先攻击剑兵
        >>> # 门客有70%概率优先攻击敌方门客
        >>> # 30%概率随机选择，增加战斗不确定性
    """
    priority_targets: list["Combatant"] = []

    # 护院：优先攻击克制的兵种
    if actor.kind == "troop":
        counter_class = TROOP_COUNTERS.get(actor.troop_class)
        if counter_class:
            priority_targets = [unit for unit in opponents if unit.troop_class == counter_class]

    # 门客：优先攻击敌方门客
    elif actor.kind == "guest":
        priority_targets = [unit for unit in opponents if unit.kind == "guest"]

    # 加权概率判定
    if priority_targets and rng.random() < PRIORITY_TARGET_WEIGHT:
        return rng.choice(priority_targets)

    # 无优先目标或随机判定：完全随机选择
    return rng.choice(opponents)


def select_attack_targets(
    actor: "Combatant",
    attacker_team: list["Combatant"],
    defender_team: list["Combatant"],
    rng: random.Random,
    trigger_attack_skills_fn: Callable[["Combatant", random.Random], list[AttackSkill]],
) -> _SelectedAttackTargets | None:
    """
    选择本次行动的攻击目标（含多目标技能的扩展目标）。

    目标选择规则：
    - 先根据阵营选择存活的对手列表。
    - 优先目标选择由 `select_target_with_priority()` 决定：
      - 护院优先选择五行克制兵种；
      - 门客优先选择敌方门客；
      - 通过 `PRIORITY_TARGET_WEIGHT` 在"策略性"和"随机性"之间做权衡。
    - 随后触发技能，读取技能的 `targets` 字段决定最大目标数；扩展目标从剩余对手中随机抽取。

    返回：
    - 若无可攻击目标，返回 None（调用方需负责设置 `has_acted_this_round` 等结算字段）。
    - 否则返回 (engaged_targets, skills) 元组。
    """
    opponents = alive(defender_team) if actor.side == "attacker" else alive(attacker_team)
    if not opponents:
        return None

    # 关键：保持 RNG 消耗顺序——先选主目标，再触发技能（历史实现依赖此顺序保证可复现）
    primary_target = select_target_with_priority(actor, opponents, rng)
    skills = _active_attack_skills(trigger_attack_skills_fn(actor, rng))

    multi_targets = max(1, max(int(skill.get("targets", 1)) for skill in skills) if skills else 1)
    engaged_targets = [primary_target]

    available_opponents = [unit for unit in opponents if unit is not primary_target]
    rng.shuffle(available_opponents)
    for extra in available_opponents[: multi_targets - 1]:
        engaged_targets.append(extra)

    return _SelectedAttackTargets(engaged_targets=engaged_targets, skills=skills)
