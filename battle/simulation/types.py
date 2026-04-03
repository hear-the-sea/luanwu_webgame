"""
战斗模拟类型定义
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, NotRequired, TypedDict

AttackSkill = Dict[str, Any]
AttackType = Literal["ranged", "melee"]
PassiveLogEntry = Dict[str, Any]


class AttackLogEntry(TypedDict):
    """
    单次攻击（或闪避）的战报结构。

    注意：
    - 战报是对外协议数据，字段名/含义需保持向后兼容。
    - 闪避时不包含反伤/反击等技术字段（这些字段为可选）。
    """

    actor: str
    target: str
    damage: int
    is_dodge: bool
    is_crit: bool
    side: str
    skills: List[str]
    agility: int
    kind: str
    priority: int
    status_inflicted: List[str]
    index: int
    kills: int
    target_defeated: bool

    additional_targets: NotRequired[List["AttackLogEntry"]]
    passive_events_before: NotRequired[List[PassiveLogEntry]]
    passive_events_after: NotRequired[List[PassiveLogEntry]]

    # 武艺/技术特殊效果（命中时才会记录）
    is_double_strike: NotRequired[bool]
    reflect_damage: NotRequired[int]
    reflect_kills: NotRequired[int]
    reflect_defeated: NotRequired[bool]
    counter_damage: NotRequired[int]
    counter_kills: NotRequired[int]
    counter_defeated: NotRequired[bool]
    attack_type: NotRequired[AttackType]
    actor_defeated: NotRequired[bool]
    actor_guest_id: NotRequired[int | None]
    actor_owner_entry_id: NotRequired[int | None]
    actor_combatant_slot: NotRequired[int | None]
    target_guest_id: NotRequired[int | None]
    target_owner_entry_id: NotRequired[int | None]
    target_combatant_slot: NotRequired[int | None]
    target_template_key: NotRequired[str | None]
    target_is_boss: NotRequired[bool]


@dataclass(frozen=True, slots=True)
class _SelectedAttackTargets:
    """一次行动所涉及的攻击目标列表及其对应的技能触发结果。"""

    engaged_targets: List[Any]  # List[Combatant]
    skills: List[AttackSkill]


@dataclass(frozen=True, slots=True)
class _DamageCalculation:
    """命中后对目标造成的最终伤害（尚未应用到目标/攻击者），以及关键标记。"""

    damage: int
    is_crit: bool
    is_double_strike: bool


@dataclass(frozen=True, slots=True)
class _DamageApplication:
    """将伤害应用到单位后产生的结算结果（击杀、反伤、反击等）。"""

    display_damage: int
    kills: int
    target_defeated: bool
    actor_defeated: bool
    reflect_damage: int
    reflect_kills: int
    reflect_defeated: bool
    counter_damage: int
    counter_kills: int
    counter_defeated: bool
