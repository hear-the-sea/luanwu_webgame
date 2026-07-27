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
    actor_state: NotRequired[dict[str, Any]]
    target_state: NotRequired[dict[str, Any]]
    raw_damage: NotRequired[int]
    applied_damage: NotRequired[int]
    overkill_damage: NotRequired[int]
    target_hp_before: NotRequired[int]
    target_hp_after: NotRequired[int]
    target_strength_before: NotRequired[int]
    target_strength_after: NotRequired[int]
    reflect_applied_damage: NotRequired[int]
    reflect_overkill_damage: NotRequired[int]
    counter_applied_damage: NotRequired[int]
    counter_overkill_damage: NotRequired[int]


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
class _UnitDamageApplication:
    """单个单位的一次伤害状态转换。"""

    raw_damage: int
    applied_damage: int
    overkill_damage: int
    kills: int
    defeated: bool
    hp_before: int
    hp_after: int
    strength_before: int
    strength_after: int


@dataclass(frozen=True, slots=True)
class _DamageApplication:
    """一次攻击及其反伤、反击产生的完整状态转换。"""

    target: _UnitDamageApplication
    reflect: _UnitDamageApplication
    counter: _UnitDamageApplication

    @property
    def display_damage(self) -> int:
        """兼容旧战报：`damage` 继续展示理论伤害。"""

        return self.target.raw_damage

    @property
    def kills(self) -> int:
        return self.target.kills

    @property
    def target_defeated(self) -> bool:
        return self.target.defeated

    @property
    def actor_defeated(self) -> bool:
        return self.reflect.defeated or self.counter.defeated

    @property
    def reflect_damage(self) -> int:
        return self.reflect.raw_damage

    @property
    def reflect_kills(self) -> int:
        return self.reflect.kills

    @property
    def reflect_defeated(self) -> bool:
        return self.reflect.defeated

    @property
    def counter_damage(self) -> int:
        return self.counter.raw_damage

    @property
    def counter_kills(self) -> int:
        return self.counter.kills

    @property
    def counter_defeated(self) -> bool:
        return self.counter.defeated
