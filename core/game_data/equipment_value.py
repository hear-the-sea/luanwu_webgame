"""Canonical equipment and guest power values.

This module is the single source of truth for how much a point of each
stat is worth in the arena/equipment budget.  It intentionally has no Django
imports so tests, analysis scripts and runtime code can all use it.

The values are expressed in "HP-equivalent" units (1 HP = 1).  Runtime
match-making uses ``power_points`` (1 point = 10 HP) to keep stored power
values in the same order of magnitude as the historical formula.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

Archetype = Literal["military", "civil", "neutral"]
Stage = Literal["early", "mid", "late"]
Scenario = Literal["pvp", "pve"]

POWER_POINTS_PER_HP = 10


@dataclass(frozen=True, slots=True)
class StatWeights:
    """HP-equivalent value of one point of each stat."""

    hp: float = 1.0
    force: float = 0.0
    intellect: float = 0.0
    defense: float = 0.0
    agility: float = 0.0
    luck: float = 0.0
    troop_capacity: float = 0.0
    attack_bonus: float = 0.0


# Mid-game (lv40) PvP values, derived from the combat formulas:
# - 1 force (military) = 0.7 attack = 42 HP
# - 1 intellect (civil) = 0.5 attack = 25 HP
# - 1 defense = 50 HP (max_hp) + 12 HP mitigation = 62 HP (PvP)
# - 1 luck = 45/25 HP with one active skill
# - 1 troop_capacity = 55 HP (team-level multiplier)
MILITARY_WEIGHTS = StatWeights(
    hp=1.0,
    force=42.0,
    intellect=15.0,
    defense=62.0,
    agility=3.0,
    luck=45.0,
    troop_capacity=55.0,
    attack_bonus=48.0,
)
CIVIL_WEIGHTS = StatWeights(
    hp=1.0,
    force=15.0,
    intellect=25.0,
    defense=57.0,
    agility=2.0,
    luck=25.0,
    troop_capacity=55.0,
    attack_bonus=29.0,
)
NEUTRAL_WEIGHTS = StatWeights(
    hp=1.0,
    force=(MILITARY_WEIGHTS.force + CIVIL_WEIGHTS.force) / 2,
    intellect=(MILITARY_WEIGHTS.intellect + CIVIL_WEIGHTS.intellect) / 2,
    defense=(MILITARY_WEIGHTS.defense + CIVIL_WEIGHTS.defense) / 2,
    agility=(MILITARY_WEIGHTS.agility + CIVIL_WEIGHTS.agility) / 2,
    luck=(MILITARY_WEIGHTS.luck + CIVIL_WEIGHTS.luck) / 2,
    troop_capacity=(MILITARY_WEIGHTS.troop_capacity + CIVIL_WEIGHTS.troop_capacity) / 2,
    attack_bonus=(MILITARY_WEIGHTS.attack_bonus + CIVIL_WEIGHTS.attack_bonus) / 2,
)

# PvE values use the lower defense constant (K=50 for guest-vs-troop), which
# makes defense much stronger at low defense totals.
PVE_DEFENSE_WEIGHTS = {
    "military": 85.0,
    "civil": 83.0,
    "neutral": (85.0 + 83.0) / 2,
}

# Stage multipliers are applied on top of the mid-game table.  Equipment
# level is mostly an access gate, so these are deliberately mild except for
# troop_capacity (which scales with the size of the army).
STAGE_MULTIPLIERS: dict[Stage, dict[str, float]] = {
    "early": {
        "hp": 1.0,
        "force": 0.9,
        "intellect": 0.9,
        "defense": 0.88,
        "agility": 0.8,
        "luck": 0.65,
        "troop_capacity": 0.64,
        "attack_bonus": 0.9,
    },
    "mid": {
        "hp": 1.0,
        "force": 1.0,
        "intellect": 1.0,
        "defense": 1.0,
        "agility": 1.0,
        "luck": 1.0,
        "troop_capacity": 1.0,
        "attack_bonus": 1.0,
    },
    "late": {
        "hp": 1.0,
        "force": 1.1,
        "intellect": 1.1,
        "defense": 1.1,
        "agility": 1.2,
        "luck": 1.2,
        "troop_capacity": 1.73,
        "attack_bonus": 1.05,
    },
}


def stage_for_level(level: int) -> Stage:
    """Return the budget stage for an equipment/guest level."""

    if level <= 15:
        return "early"
    if level <= 55:
        return "mid"
    return "late"


def weights_for(
    *,
    archetype: Archetype = "neutral",
    stage: Stage = "mid",
    scenario: Scenario = "pvp",
) -> StatWeights:
    """Return the canonical weights for a context.

    ``stage`` only affects the comparison frame; pass ``stage=None`` (via
    ``item_power``) when comparing raw item budgets without level scaling.
    """

    base = {
        "military": MILITARY_WEIGHTS,
        "civil": CIVIL_WEIGHTS,
        "neutral": NEUTRAL_WEIGHTS,
    }[archetype]
    multipliers = STAGE_MULTIPLIERS[stage]
    defense = base.defense
    if scenario == "pve":
        defense = PVE_DEFENSE_WEIGHTS[archetype]
    return StatWeights(
        hp=base.hp * multipliers["hp"],
        force=base.force * multipliers["force"],
        intellect=base.intellect * multipliers["intellect"],
        defense=defense * multipliers["defense"],
        agility=base.agility * multipliers["agility"],
        luck=base.luck * multipliers["luck"],
        troop_capacity=base.troop_capacity * multipliers["troop_capacity"],
        attack_bonus=base.attack_bonus * multipliers["attack_bonus"],
    )


def _numeric_items(stats: Mapping[str, Any]):
    for key, value in stats.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        yield str(key), float(value)


def item_power(
    stats: Mapping[str, Any],
    *,
    archetype: Archetype = "neutral",
    stage: Stage | None = "mid",
    scenario: Scenario = "pvp",
) -> float:
    """Return the HP-equivalent budget of one item's stat payload.

    ``attack`` is treated as ``attack_bonus`` because set bonuses and a few
    payloads use that key; ``troop_stat_bonus`` is intentionally ignored here
    (devices are evaluated separately because it is a multiplicative troop
    effect, not a flat stat).
    """

    # ``stage=None`` means "raw stat budget", so use the mid-game table
    # without applying any level multiplier.
    weights = weights_for(
        archetype=archetype,
        stage=stage or "mid",
        scenario=scenario,
    )
    weight_map = {
        "hp": weights.hp,
        "force": weights.force,
        "intellect": weights.intellect,
        "defense": weights.defense,
        "agility": weights.agility,
        "luck": weights.luck,
        "troop_capacity": weights.troop_capacity,
        "attack": weights.attack_bonus,
        "attack_bonus": weights.attack_bonus,
    }
    return sum(weight_map[key] * value for key, value in _numeric_items(stats) if key in weight_map)


def item_power_points(
    stats: Mapping[str, Any],
    *,
    archetype: Archetype = "neutral",
    stage: Stage | None = "mid",
    scenario: Scenario = "pvp",
) -> int:
    """Return the item budget in runtime power points (1 point = 10 HP)."""

    return int(round(item_power(stats, archetype=archetype, stage=stage, scenario=scenario) / POWER_POINTS_PER_HP))


def guest_power(
    *,
    force: int,
    intellect: int,
    defense: int,
    defense_stat: int | None = None,
    agility: int,
    hp_bonus: int,
    base_hp: int,
    level: int = 1,
    archetype: str = "",
    luck: int = 0,
    troop_capacity: int = 0,
    attack_bonus: int = 0,
    stage: Stage | None = None,
    scenario: Scenario = "pvp",
    include_troop_capacity: bool = True,
) -> float:
    """Return one guest's HP-equivalent combat power.

    ``max_hp`` already includes ``defense * 50``; the defense weight is
    therefore split into the 50 HP component (inside ``max_hp``) and the
    mitigation component.  This keeps defense from being counted twice.
    """

    normalized_force = max(0, int(force))
    normalized_intellect = max(0, int(intellect))
    normalized_defense = max(0, int(defense))
    normalized_agility = max(0, int(agility))
    normalized_luck = max(0, int(luck))
    normalized_capacity = max(0, int(troop_capacity))
    normalized_attack_bonus = max(0, int(attack_bonus))
    normalized_archetype: Archetype = (
        "civil" if str(archetype) == "civil" else ("military" if str(archetype) == "military" else "neutral")
    )
    resolved_stage = stage or stage_for_level(level)
    weights = weights_for(archetype=normalized_archetype, stage=resolved_stage, scenario=scenario)
    # The mid-game attack weight is 60 HP/attack for military and 50 for civil.
    # Attribute-derived attack and flat attack_bonus have different values:
    # 1 force = 0.7 attack = 42 HP, while 1 flat attack is worth 48/29 HP.
    attack_weight = 60.0 if normalized_archetype == "military" else (50.0 if normalized_archetype == "civil" else 55.0)
    attack_weight *= STAGE_MULTIPLIERS[resolved_stage]["force"]
    attack_bonus_weight = (
        48.0 if normalized_archetype == "military" else (29.0 if normalized_archetype == "civil" else 38.5)
    )
    attack_bonus_weight *= STAGE_MULTIPLIERS[resolved_stage]["attack_bonus"]
    attribute_attack = int(
        normalized_force * (0.7 if normalized_archetype == "military" else 0.5)
        + normalized_intellect * (0.3 if normalized_archetype == "military" else 0.5)
    )
    # ``defense`` is the effective value used for mitigation.  Max HP only
    # receives the base ``defense_stat`` component (see guest_rules.py), so
    # keep them separate to avoid granting 50 HP for defense_bonus gear.
    defense_for_hp = max(0, int(defense if defense_stat is None else defense_stat))
    max_hp = max(200, int(base_hp) + int(hp_bonus) + defense_for_hp * 50) + max(0, int(level) - 1) * 60
    non_defense_hp = max(0, max_hp - defense_for_hp * 50)
    capacity_value = normalized_capacity * weights.troop_capacity if include_troop_capacity else 0.0
    return (
        attribute_attack * attack_weight
        + normalized_attack_bonus * attack_bonus_weight
        + normalized_defense * weights.defense
        + non_defense_hp * weights.hp
        + normalized_agility * weights.agility
        + normalized_luck * weights.luck
        + capacity_value
    )


def guest_power_points(**kwargs: Any) -> int:
    """Return a guest's power in runtime points (1 point = 10 HP)."""

    return int(round(guest_power(**kwargs) / POWER_POINTS_PER_HP))
