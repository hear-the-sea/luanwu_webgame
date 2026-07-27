"""
战斗模拟器模块

负责构造战斗单位、参数覆盖、执行战斗模拟。
"""

from __future__ import annotations

import logging
import math
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

from .config import BattleConfig, GuestConfig, PartyConfig

logger = logging.getLogger(__name__)


# ============ 参数覆盖机制 ============


def _patch_slaughter_multiplier(cm, params: Dict[str, Any], originals: Dict[str, Any]) -> None:
    if "slaughter_multiplier" not in params:
        return
    originals["slaughter"] = cm.calculate_slaughter_multiplier
    multiplier = params["slaughter_multiplier"]

    def custom_slaughter(attacker, target):
        if getattr(attacker, "kind", "") != "guest":
            return 1.0
        if getattr(target, "kind", "") != "troop":
            return 1.0
        return multiplier

    cm.calculate_slaughter_multiplier = custom_slaughter


def _patch_attack_value(cm, params: Dict[str, Any], originals: Dict[str, Any]) -> None:
    if "troop_attack_divisor_vs_guest" not in params and "troop_attack_divisor_vs_troop" not in params:
        return

    originals["attack"] = cm.effective_attack_value
    div_guest = params.get("troop_attack_divisor_vs_guest", 4.0)
    div_troop = params.get("troop_attack_divisor_vs_troop", 1.0)
    orig_attack = cm.effective_attack_value

    def custom_attack(actor, target=None):
        if getattr(actor, "kind", "") != "troop":
            return orig_attack(actor, target)

        strength = cm._current_strength(actor)
        unit_attack = cm._unit_attack_value(actor)
        if target is not None and getattr(target, "kind", "") != "troop":
            multiplier = max(1.0, strength / div_guest)
        else:
            multiplier = max(1.0, strength / div_troop)
        return max(1, int(unit_attack * multiplier))

    cm.effective_attack_value = custom_attack


def _patch_defense_value(cm, params: Dict[str, Any], originals: Dict[str, Any]) -> None:
    if "troop_defense_divisor" not in params:
        return

    originals["defense"] = cm.effective_defense_value
    divisor = params["troop_defense_divisor"]
    orig_defense = cm.effective_defense_value

    def custom_defense(target, attacker=None):
        if getattr(target, "kind", "") != "troop":
            return orig_defense(target, attacker)

        unit_defense = cm._unit_defense_value(target)
        strength = cm._current_strength(target)
        multiplier = max(1.0, math.sqrt(strength) / divisor)
        return max(1, int(unit_defense * multiplier))

    cm.effective_defense_value = custom_defense


def _patch_crit_chance(sc, params: Dict[str, Any], originals: Dict[str, Any]) -> None:
    if "crit_chance" not in params:
        return

    originals["crit"] = sc.calculate_crit_chance
    chance = params["crit_chance"]

    def custom_crit(actor):
        return chance

    sc.calculate_crit_chance = custom_crit


def _patch_priority_target_weight(sc, params: Dict[str, Any], originals: Dict[str, Any]) -> None:
    if "priority_target_weight" not in params:
        return

    originals["priority_weight"] = sc.PRIORITY_TARGET_WEIGHT
    sc.PRIORITY_TARGET_WEIGHT = params["priority_target_weight"]


def _apply_battle_param_patches(cm, sc, params: Dict[str, Any], originals: Dict[str, Any]) -> None:
    _patch_slaughter_multiplier(cm, params, originals)
    _patch_attack_value(cm, params, originals)
    _patch_defense_value(cm, params, originals)
    _patch_crit_chance(sc, params, originals)
    _patch_priority_target_weight(sc, params, originals)


def _restore_battle_param_patches(cm, sc, originals: Dict[str, Any]) -> None:
    restore_handlers = {
        "slaughter": lambda value: setattr(cm, "calculate_slaughter_multiplier", value),
        "attack": lambda value: setattr(cm, "effective_attack_value", value),
        "defense": lambda value: setattr(cm, "effective_defense_value", value),
        "crit": lambda value: setattr(sc, "calculate_crit_chance", value),
        "priority_weight": lambda value: setattr(sc, "PRIORITY_TARGET_WEIGHT", value),
    }
    for key, value in originals.items():
        handler = restore_handlers.get(key)
        if handler is not None:
            handler(value)


@contextmanager
def patch_battle_params(params: Dict[str, Any]):
    """
    临时覆盖战斗参数（Monkey Patch）

    使用上下文管理器确保参数在使用后自动恢复。

    支持的参数：
    - slaughter_multiplier: 屠戮倍率
    - troop_attack_divisor_vs_guest: 小兵打门客的攻击除数
    - troop_attack_divisor_vs_troop: 小兵打小兵的攻击除数
    - troop_defense_divisor: 小兵防御缩放除数
    - guest_vs_troop_reduction_coeff: 门客打小兵减伤系数
    - guest_vs_troop_reduction_cap: 门客打小兵减伤上限
    - counter_multiplier: 五行相克倍率
    - crit_chance: 暴击率
    - crit_multiplier: 暴击倍率
    - preemptive_penalty: 先锋门客伤害惩罚
    - priority_target_weight: 优先目标选择权重
    """
    import battle.combat_math as cm
    import battle.simulation_core as sc

    originals: Dict[str, Any] = {}
    _apply_battle_param_patches(cm, sc, params, originals)

    try:
        yield
    finally:
        _restore_battle_param_patches(cm, sc, originals)


# ============ 战斗模拟器 ============


class BattleSimulator:
    """战斗模拟器"""

    def __init__(self, config: BattleConfig):
        """
        初始化模拟器

        Args:
            config: 战斗配置
        """
        self.config = config

    def run_battle(self, seed: Optional[int] = None) -> dict:
        """
        运行战斗模拟

        Args:
            seed: 随机种子，None表示随机生成

        Returns:
            战斗报告字典，包含：
            - seed: 实际使用的种子
            - winner: 胜者（"attacker" 或 "defender"）
            - rounds: 回合数
            - losses: 损失统计
            - combat_log: 战斗日志
        """
        from battle.combatants import assign_agility_based_priorities
        from battle.random_context import RNG_STREAM_AI_GROWTH, RNG_STREAM_COMBAT, BattleRandomContext
        from battle.simulation_core import simulate_battle

        random_context = BattleRandomContext.create(seed)
        ai_rng = random_context.rng(RNG_STREAM_AI_GROWTH)

        # 构造双方战斗单位
        attacker_guests, attacker_troops = self._build_party(self.config.attacker, "attacker", ai_rng)
        defender_guests, defender_troops = self._build_party(self.config.defender, "defender", ai_rng)

        # 合并单位列表
        attacker_units = attacker_guests + attacker_troops
        defender_units = defender_guests + defender_troops

        # 根据敏捷分配门客优先级（前25%快的门客获得先攻）
        assign_agility_based_priorities(attacker_units, defender_units)

        # 生成随机数生成器
        actual_seed = random_context.base_seed
        rng = random_context.rng(RNG_STREAM_COMBAT)

        # 在参数覆盖上下文中运行战斗
        with patch_battle_params(self.config.tunable_params):
            result = simulate_battle(
                attacker_units=attacker_units,
                defender_units=defender_units,
                rng=rng,
                seed=actual_seed,
                travel_seconds=None,
                config={},
                drop_table=None,
            )

        # 返回格式化的报告
        return {
            "seed": actual_seed,
            "winner": result.winner,
            "rounds": result.rounds,
            "losses": result.losses,
            "drops": result.drops,
            "combat_log": result.rounds,  # 详细战斗日志
        }

    def _build_party(self, party_cfg: PartyConfig, side: str, rng) -> Tuple[List, List]:
        """
        构造阵营的战斗单位

        Args:
            party_cfg: 阵营配置
            side: 阵营标识（"attacker" 或 "defender"）

        Returns:
            (门客列表, 小兵列表)
        """
        guest_combatants = []
        troop_combatants = []

        # 构造门客
        if party_cfg.guests:
            guest_combatants = self._build_guests(party_cfg.guests, side, rng)

        # 构造小兵
        if party_cfg.troops:
            troop_combatants = self._build_troops(
                party_cfg.troops, party_cfg.technology_level, party_cfg.technology_levels, side
            )

        return guest_combatants, troop_combatants

    def _build_guests(self, guests_cfg: List[GuestConfig], side: str, rng) -> List:
        """构造门客战斗单位"""
        from battle.combatants import Combatant, build_named_ai_guests
        from guests.models import Skill

        guest_combatants = []

        for guest_cfg in guests_cfg:
            ai_guests = build_named_ai_guests(
                guest_keys=[guest_cfg.template],
                level=guest_cfg.level,
                rng=rng,
            )

            if not ai_guests:
                logger.warning(f"无法创建门客 {guest_cfg.template}")
                continue

            guest = ai_guests[0]

            # 应用自定义属性（如果配置中指定了）
            if guest_cfg.force is not None:
                guest.force = guest_cfg.force
            if guest_cfg.intellect is not None:
                guest.intellect = guest_cfg.intellect
            if guest_cfg.defense is not None:
                guest.defense_stat = guest_cfg.defense
            if guest_cfg.agility is not None:
                guest.agility = guest_cfg.agility
            if guest_cfg.luck is not None:
                guest.luck = guest_cfg.luck

            # 转换为Combatant

            # 创建临时列表并转换
            combatant = Combatant(
                name=guest.template.name,
                kind="guest",
                side=side,
                guest_id=guest.id,
                template_key=guest.template.key,
                rarity=guest.rarity,
                level=guest.level,
                force_attr=guest.force,
                intellect_attr=guest.intellect,
                defense=guest.defense_stat,
                defense_attr=guest.defense_stat,
                agility=guest.agility,
                luck=guest.luck,
                attack=self._calculate_attack(guest, guest_cfg.archetype),
                max_hp=self._calculate_hp(guest),
                hp=0,  # 临时值
                current_round=0,
                has_acted_this_round=False,
                last_round_acted=0,
                priority=0,
                skills=[],
                status_effects={},
                troop_class="",
                troop_strength=0,
                unit_attack=0,
                unit_defense=0,
                unit_hp=0,
                initial_troop_strength=0,
                tech_effects={},
            )

            # 设置HP
            combatant.hp = combatant.max_hp

            # 设置技能
            if guest_cfg.skills:
                skill_objs = list(Skill.objects.filter(key__in=guest_cfg.skills))
                combatant.skills = [
                    {
                        "name": s.name,
                        "key": s.key,
                        "power": s.base_power,
                        "probability": s.base_probability,
                        "kind": s.kind,
                        "status_effect": s.status_effect,
                        "status_probability": s.status_probability,
                        "status_duration": s.status_duration,
                        "damage_formula": s.damage_formula,
                        "targets": s.targets,
                    }
                    for s in skill_objs
                ]

            guest_combatants.append(combatant)

        return guest_combatants

    def _build_troops(
        self, troops_cfg: Dict[str, int], tech_level: int, tech_levels: Dict[str, int], side: str
    ) -> List:
        """构造小兵战斗单位"""
        from battle.combatants import build_troop_combatants

        if not troops_cfg:
            return []

        # 构造科技等级字典
        final_tech_levels = self._build_tech_levels(tech_level, tech_levels)

        # 构造小兵单位（新API：使用 loadout, side, tech_levels）
        troop_units = build_troop_combatants(
            loadout=troops_cfg, side=side, manor=None, tech_levels=final_tech_levels  # 不使用Manor，使用tech_levels
        )

        return troop_units

    def _build_tech_levels(self, tech_level: int, tech_levels: Dict[str, int]) -> Dict[str, int]:
        """
        构造科技等级字典

        Args:
            tech_level: 统一科技等级
            tech_levels: 精细科技等级字典

        Returns:
            科技等级字典
        """
        # 如果有精细配置，直接使用
        if tech_levels:
            return tech_levels

        # 否则使用统一等级
        if tech_level > 0:
            from gameplay.services.technology import build_uniform_tech_levels

            return build_uniform_tech_levels(tech_level)

        return {}

    def _calculate_attack(self, guest, archetype: Optional[str] = None) -> int:
        """
        计算门客攻击力

        Args:
            guest: 门客对象
            archetype: 门客类型（civil/military），None时从guest获取

        Returns:
            攻击力
        """
        force = guest.force
        intellect = guest.intellect

        # 确定类型
        if archetype is None:
            archetype = guest.archetype

        if archetype == "civil":
            # 文官：40%武力 + 60%智力
            return int(force * 0.4 + intellect * 0.6)
        else:
            # 武将：70%武力 + 30%智力
            return int(force * 0.7 + intellect * 0.3)

    def _calculate_hp(self, guest) -> int:
        """
        计算门客HP

        Args:
            guest: 门客对象

        Returns:
            HP值
        """
        # 获取基础HP（从模板）
        base_hp = guest.template.base_hp

        # 获取防御
        defense = guest.defense_stat

        # HP = 基础HP + 防御×50
        hp = int(base_hp + defense * 50)

        return max(200, hp)
