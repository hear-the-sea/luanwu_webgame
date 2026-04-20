"""
Troop combatant builder.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from battle.troops import default_troop_loadout, load_troop_templates

from .core import Combatant
from .tech_effects import build_tech_effects
from .troop_device_bonuses import TroopStatBonuses, apply_troop_device_bonus


def normalize_troop_loadout(
    loadout: Dict[str, int] | None,
    *,
    default_if_empty: bool = True,
) -> Dict[str, int]:
    templates = load_troop_templates()
    if not templates:
        return {}
    if not loadout:
        return default_troop_loadout() if default_if_empty else {}
    normalized: Dict[str, int] = {}
    for key in templates.keys():
        value = int(loadout.get(key, 0) or 0)
        normalized[key] = max(0, value)
    if not any(normalized.values()):
        return default_troop_loadout() if default_if_empty else {}
    return normalized


def build_troop_combatants(
    loadout: Dict[str, int],
    side: str,
    manor=None,
    tech_levels: Optional[Dict[str, int]] = None,
    device_bonuses: TroopStatBonuses | None = None,
) -> List[Combatant]:
    """
    Build troop combatant list.

    Args:
        loadout: Troop config {troop_key: count}
        side: Side ("attacker" or "defender")
        manor: Manor instance (optional, for tech bonuses)
        tech_levels: Optional tech levels dict (for enemy)

    Returns:
        Troop combatant list
    """
    from core.game_data.technology import get_troop_class_for_key, get_troop_stat_bonuses_from_levels

    templates = load_troop_templates()
    troops: List[Combatant] = []

    effective_levels = tech_levels
    if effective_levels is None and manor is not None:
        effective_levels = {t.tech_key: t.level for t in manor.technologies.all()}

    for key, count in loadout.items():
        if count <= 0:
            continue
        definition = templates.get(key)
        if not definition:
            continue

        troop_class = get_troop_class_for_key(key) or ""

        bonuses: Dict[str, float] = {}
        if effective_levels is not None:
            bonuses = get_troop_stat_bonuses_from_levels(effective_levels, key)

        base_attack = apply_troop_device_bonus(
            base_value=definition.get("base_attack", 30),
            troop_class=troop_class,
            stat="attack",
            device_bonuses=device_bonuses,
        )
        base_defense = apply_troop_device_bonus(
            base_value=definition.get("base_defense", 20),
            troop_class=troop_class,
            stat="defense",
            device_bonuses=device_bonuses,
        )
        base_hp = apply_troop_device_bonus(
            base_value=definition.get("base_hp", 80),
            troop_class=troop_class,
            stat="hp",
            device_bonuses=device_bonuses,
        )
        base_agility = apply_troop_device_bonus(
            base_value=definition.get("speed_bonus", 0),
            troop_class=troop_class,
            stat="agility",
            device_bonuses=device_bonuses,
        )

        attack_mult = 1.0 + bonuses.get("attack", 0)
        defense_mult = 1.0 + bonuses.get("defense", 0)
        hp_mult = 1.0 + bonuses.get("hp", 0)
        agility_mult = 1.0 + bonuses.get("agility", 0)

        unit_attack = int(base_attack * attack_mult)
        unit_defense = int(base_defense * defense_mult)
        unit_hp = int(base_hp * hp_mult)
        agility = int(base_agility * agility_mult) if base_agility > 0 else int(base_agility)

        attack = unit_attack * count
        defense = unit_defense * count
        hp = unit_hp * count

        tech_effects_dict: Dict[str, float] = {}
        if effective_levels is not None and troop_class:
            tech_effects_dict = build_tech_effects(troop_class, tech_levels=effective_levels)

        priority = int(definition["priority"])

        if troop_class == "jian" and tech_effects_dict.get("preemptive_damage", 0) > 0:
            priority = -1

        if troop_class == "gong" and tech_effects_dict.get("extra_range_damage", 0) > 0:
            priority = -2

        troops.append(
            Combatant(
                name=definition["label"],
                attack=attack,
                defense=defense,
                hp=hp,
                max_hp=hp,
                side=side,
                rarity="troop",
                luck=30,
                agility=agility,
                priority=priority,
                kind="troop",
                troop_strength=count,
                initial_troop_strength=count,
                initial_hp=hp,
                unit_attack=unit_attack,
                unit_defense=unit_defense,
                unit_hp=unit_hp,
                template_key=key,
                skills=[],
                troop_class=troop_class,
                tech_effects=tech_effects_dict,
            )
        )
    return troops
