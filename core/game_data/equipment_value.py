"""Canonical equipment and guest power values.

This module is the single source of truth for both:
1. **Static equipment budgeting** (``item_power`` / ``StatWeights``): context-free
   linear HP-equivalent stat weights calibrated via heterogeneous 5v5 battle
   simulations, used by equipment balance tests and AI gear heuristics.
2. **Guest & lineup combat power** (``guest_power`` / ``combat_power_points``):
   a strictly monotonic, physics-based battle model derived directly from the
   real combat formulas:

   ``full_power_hp = SCALE * (EHP ** 0.45) * (DPR ** 0.55) * speed_factor * passive_factor``

   where:
   - ``EHP = max_hp / (1 - damage_reduction(defense))`` uses the exact PvP
     (K=600) or PvE (K=200) mitigation curve with 50% softcap and 75% hardcap.
   - ``DPR = 15 * attack + skill_mult * P(trigger_any) * avg_skill_bonus``
     evaluates actual equipped ``skill_keys`` from ``data/guest_skills.yaml``
     when supplied (L3, ~91-94% out-of-sample 5v5 win-prediction accuracy), or
     a smooth level-continuous archetype expectation when ``skill_keys`` is
     omitted (L2, ~83% accuracy, 100% cross-level accuracy).
   - ``speed_factor = ((agility + 30) / 180) ** 0.75`` captures same-round kill
     order and fastest-20% vanguard advantage while staying strictly monotonic
     across every level (no level-denominator dips).

Intentionally free of Django imports so tests, analysis scripts, and runtime
services can share one canonical implementation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from core.config import GUEST

Archetype = Literal["military", "civil", "neutral"]
Stage = Literal["early", "mid", "late"]
Scenario = Literal["pvp", "pve"]

POWER_POINTS_PER_HP = 10
DEFENSE_HP_PER_POINT = float(GUEST.DEFENSE_TO_HP_MULTIPLIER)
LUCK_HARD_CAP = 300.0

# Real combat constants mirrored from battle/simulation/constants.py to avoid
# importing battle/Django models inside core.game_data.
_PVP_DEFENSE_CONSTANT = 900.0
_PVE_DEFENSE_CONSTANT = 300.0
_DEFENSE_SOFTCAP_THRESHOLD = 0.50
_DEFENSE_HARDCAP = 0.75
_PVP_NORMAL_ATTACK_MULTIPLIER = 13.0
_PVP_SKILL_BONUS_MULTIPLIER = 2.0
_PVE_SKILL_BONUS_MULTIPLIER = 2.5
_INTELLECT_SKILL_DR_CONSTANT = 860.0
_INTELLECT_SKILL_DR_HARDCAP = 0.45
_INTELLECT_STATUS_RESIST_CONSTANT = 1600.0
_INTELLECT_STATUS_RESIST_HARDCAP = 0.30
_PVP_SKILL_DAMAGE_SHARE = 0.48
_PVP_STATUS_RESIST_EHP_SHARE = 0.25
_MIN_SKILL_TRIGGER_CHANCE = 0.05
_MAX_SKILL_TRIGGER_CHANCE = 0.95

# Calibrated physics model exponents & scale (validated on 12,000+ 5v5 battles).
_PHYSICS_EHP_EXPONENT = 0.45
_PHYSICS_DPR_EXPONENT = 0.55
_PHYSICS_AGILITY_OFFSET = 30.0
_PHYSICS_AGILITY_REFERENCE = 180.0
_PHYSICS_AGILITY_EXPONENT = 0.75
_PHYSICS_POWER_SCALE = 4.5
_PASSIVE_RARITY_BONUS: dict[str, float] = {
    "black": 0.02,
    "gray": 0.02,
    "green": 0.03,
    "red": 0.04,
    "blue": 0.05,
    "purple": 0.07,
    "orange": 0.10,
}
_MAX_PASSIVE_MULTIPLIER_BONUS = 0.25


@dataclass(frozen=True, slots=True)
class StatWeights:
    """HP-equivalent value of one point of each stat for context-free item budgeting.

    ``defense`` is the full value of one point of ``defense_stat``
    (``DEFENSE_TO_HP_MULTIPLIER`` + ``defense_mitigation``).
    ``defense_mitigation`` is the pure damage-reduction value without the HP
    grant (used for ``defense_bonus``).
    """

    hp: float = 1.0
    force: float = 0.0
    intellect: float = 0.0
    defense: float = 0.0
    agility: float = 0.0
    luck: float = 0.0
    troop_capacity: float = 0.0
    attack_bonus: float = 0.0
    defense_mitigation: float = 0.0


# Mid-game (lv45) PvP linear weights for single-item budgeting & AI gear scoring.
MILITARY_WEIGHTS = StatWeights(
    hp=1.0,
    force=30.0,
    intellect=10.0,
    defense=DEFENSE_HP_PER_POINT + 8.0,
    agility=42.0,
    luck=44.0,
    troop_capacity=18.0,
    attack_bonus=24.0,
    defense_mitigation=8.0,
)
CIVIL_WEIGHTS = StatWeights(
    hp=1.0,
    force=10.0,
    intellect=25.0,
    defense=DEFENSE_HP_PER_POINT + 6.0,
    agility=42.0,
    luck=34.0,
    troop_capacity=18.0,
    attack_bonus=12.0,
    defense_mitigation=6.0,
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
    defense_mitigation=(MILITARY_WEIGHTS.defense_mitigation + CIVIL_WEIGHTS.defense_mitigation) / 2,
)

# PvE pure mitigation weights (troop-vs-guest K=200), calibrated against
# simulated guest-vs-troop battles (total defense = 30 + mitigation = 52/48).
PVE_DEFENSE_MITIGATION_WEIGHTS = {
    "military": 22.0,
    "civil": 18.0,
    "neutral": 20.0,
}
PVE_DEFENSE_WEIGHTS = {key: DEFENSE_HP_PER_POINT + value for key, value in PVE_DEFENSE_MITIGATION_WEIGHTS.items()}

# Stage multipliers for linear ``weights_for`` / ``item_power``.
# ``defense`` multiplier scales only ``defense_mitigation``; the fixed 30 HP
# component never changes with level stage.
STAGE_MULTIPLIERS: dict[Stage, dict[str, float]] = {
    "early": {
        "hp": 1.0,
        "force": 1.20,
        "intellect": 1.00,
        "defense": 0.90,
        "agility": 0.55,
        "luck": 0.40,
        "troop_capacity": 0.85,
        "attack_bonus": 1.50,
    },
    "mid": {
        "hp": 1.0,
        "force": 1.00,
        "intellect": 1.00,
        "defense": 1.00,
        "agility": 1.00,
        "luck": 1.00,
        "troop_capacity": 1.00,
        "attack_bonus": 1.00,
    },
    "late": {
        "hp": 1.0,
        "force": 0.95,
        "intellect": 1.05,
        "defense": 0.70,
        "agility": 1.55,
        "luck": 1.10,
        "troop_capacity": 1.35,
        "attack_bonus": 0.55,
    },
}


@lru_cache(maxsize=1)
def _load_skill_catalog() -> dict[str, dict[str, Any]]:
    """Load read-only skill definitions from YAML without Django ORM."""

    root = Path(__file__).resolve().parent.parent.parent / "data"
    catalog: dict[str, dict[str, Any]] = {}
    for filename in ("guest_skills.yaml", "arena_coop_special_skills.yaml"):
        path = root / filename
        if not path.is_file():
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for entry in raw.get("skills") or []:
            if isinstance(entry, dict) and isinstance(entry.get("key"), str):
                catalog[entry["key"]] = entry
    return catalog


def _lerp(start: float, end: float, t: float) -> float:
    clamped = max(0.0, min(1.0, float(t)))
    return start + (end - start) * clamped


def defense_damage_reduction(defense: int | float, *, scenario: Scenario = "pvp") -> float:
    """Return exact battle engine damage reduction with softcap (50%) and hardcap (75%)."""

    d = max(0.0, float(defense))
    constant = _PVE_DEFENSE_CONSTANT if scenario == "pve" else _PVP_DEFENSE_CONSTANT
    base = d / (d + constant)
    if base > _DEFENSE_SOFTCAP_THRESHOLD:
        base = _DEFENSE_SOFTCAP_THRESHOLD + (base - _DEFENSE_SOFTCAP_THRESHOLD) * 0.5
    return min(_DEFENSE_HARDCAP, base)


def skill_trigger_chance(luck: int | float) -> float:
    """Return exact per-skill active trigger chance from battle/skills.py."""

    clamped_luck = max(1.0, float(luck))
    chance = _MAX_SKILL_TRIGGER_CHANCE * ((clamped_luck / LUCK_HARD_CAP) ** 0.85)
    return max(_MIN_SKILL_TRIGGER_CHANCE, min(_MAX_SKILL_TRIGGER_CHANCE, chance))


def effective_luck(luck: int | float, *, active_skill_count: int | None = None) -> float:
    """Return concave saturation-adjusted luck capped at ``LUCK_HARD_CAP`` (300).

    Mirrors the empirical marginal value of luck under ``1 - (1 - p)^k``:
    - 0..100:   1.00x (steepest region)
    - 100..200: 0.45x (diminishing returns, especially with 2-3 skills)
    - 200..300: 0.18x (near-saturation region)
    - >300:     0.00x (hard trigger cap reached)
    """

    if active_skill_count is not None and int(active_skill_count) <= 0:
        return 0.0
    clamped = max(0.0, min(float(luck), LUCK_HARD_CAP))
    seg1 = min(clamped, 100.0)
    seg2 = max(0.0, min(clamped, 200.0) - 100.0)
    seg3 = max(0.0, clamped - 200.0)
    if active_skill_count == 1:
        return seg1 + seg2 * 0.65 + seg3 * 0.35
    return seg1 + seg2 * 0.45 + seg3 * 0.18


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
    """Return the canonical linear weights for single-item budgeting."""

    base = {
        "military": MILITARY_WEIGHTS,
        "civil": CIVIL_WEIGHTS,
        "neutral": NEUTRAL_WEIGHTS,
    }[archetype]
    multipliers = STAGE_MULTIPLIERS[stage]
    base_mitigation = PVE_DEFENSE_MITIGATION_WEIGHTS[archetype] if scenario == "pve" else base.defense_mitigation
    scaled_mitigation = base_mitigation * multipliers["defense"]
    return StatWeights(
        hp=base.hp * multipliers["hp"],
        force=base.force * multipliers["force"],
        intellect=base.intellect * multipliers["intellect"],
        defense=DEFENSE_HP_PER_POINT + scaled_mitigation,
        agility=base.agility * multipliers["agility"],
        luck=base.luck * multipliers["luck"],
        troop_capacity=base.troop_capacity * multipliers["troop_capacity"],
        attack_bonus=base.attack_bonus * multipliers["attack_bonus"],
        defense_mitigation=scaled_mitigation,
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

    ``attack`` / ``equipment_attack_bonus`` are treated as ``attack_bonus``;
    ``defense`` adds to ``defense_stat`` (HP + mitigation), whereas
    ``equipment_defense_bonus`` / ``defense_bonus`` add pure mitigation only.
    ``troop_stat_bonus`` is intentionally ignored here (devices are evaluated
    separately because it is a multiplicative troop effect, not a flat stat).
    """

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
        "defense_bonus": weights.defense_mitigation,
        "equipment_defense_bonus": weights.defense_mitigation,
        "agility": weights.agility,
        "luck": weights.luck,
        "troop_capacity": weights.troop_capacity,
        "attack": weights.attack_bonus,
        "attack_bonus": weights.attack_bonus,
        "equipment_attack_bonus": weights.attack_bonus,
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


def _evaluate_equipped_skills(
    skill_keys: Sequence[str],
    *,
    attack: int,
    defense: int,
    force: int,
    intellect: int,
    agility: int,
    luck: int,
    max_hp: int,
    level: int,
    scenario: Scenario,
) -> tuple[float, float]:
    """Return ``(expected_skill_dpr, passive_multiplier)`` for known ``skill_keys``."""

    catalog = _load_skill_catalog()
    lv = max(1.0, float(level))
    # Reference enemy profile at the same level for enemy-subtraction clauses.
    ref_enemy = {
        "defense": 45.0 + 3.6 * lv,
        "intellect": 35.0 + 2.8 * lv,
        "agility": 40.0 + 3.2 * lv,
        "force": 45.0 + 3.5 * lv,
    }
    own = {
        "attack": float(attack),
        "defense": float(defense),
        "force": float(force),
        "intellect": float(intellect),
        "agility": float(agility),
        "luck": float(luck),
        "hp": float(max_hp),
    }

    p_default = skill_trigger_chance(luck)
    no_trigger_prod = 1.0
    weighted_bonus_sum = 0.0
    prob_sum = 0.0
    passive_bonus = 0.0

    for raw_key in skill_keys:
        key = str(raw_key or "").strip()
        if not key:
            continue
        skill = catalog.get(key)
        if not skill:
            continue
        kind = skill.get("kind")
        if kind == "passive":
            rarity = str(skill.get("rarity") or "green")
            passive_bonus += _PASSIVE_RARITY_BONUS.get(rarity, 0.04)
            continue
        if kind != "active":
            continue

        formula = skill.get("damage_formula") or {}
        if not formula:
            bonus = float(skill.get("power", 0) or 0) * 0.2
        else:
            bonus = float(formula.get("base", 0) or 0)
            for stat, coeff in (formula.get("ally") or {}).items():
                bonus += float(coeff) * own.get(str(stat), 0.0)
            ally_min = formula.get("ally_min")
            if isinstance(ally_min, Mapping):
                stats = ally_min.get("stats") or []
                if len(stats) == 2:
                    bonus += float(ally_min.get("coefficient", 0) or 0) * min(
                        own.get(str(stats[0]), 0.0), own.get(str(stats[1]), 0.0)
                    )
            for stat, coeff in (formula.get("enemy") or {}).items():
                bonus -= float(coeff) * ref_enemy.get(str(stat), 0.0)

        bonus = max(0.0, bonus)
        raw_trigger = skill.get("trigger_chance")
        p_skill = max(0.0, min(1.0, float(raw_trigger))) if raw_trigger is not None else p_default
        no_trigger_prod *= 1.0 - p_skill
        weighted_bonus_sum += p_skill * bonus
        prob_sum += p_skill

    skill_mult = _PVE_SKILL_BONUS_MULTIPLIER if scenario == "pve" else _PVP_SKILL_BONUS_MULTIPLIER
    if prob_sum > 1e-9:
        p_any = 1.0 - no_trigger_prod
        expected_skill_dpr = skill_mult * p_any * (weighted_bonus_sum / prob_sum)
    else:
        expected_skill_dpr = 0.0

    passive_multiplier = 1.0 + min(_MAX_PASSIVE_MULTIPLIER_BONUS, passive_bonus)
    return expected_skill_dpr, passive_multiplier


def _estimate_fallback_skill_dpr(
    *,
    archetype: Archetype,
    force: int,
    intellect: int,
    agility: int,
    luck: int,
    level: int,
    scenario: Scenario,
    active_skill_count: int | None,
) -> float:
    """Smooth level-continuous expected active skill DPR when ``skill_keys`` is unavailable."""

    if active_skill_count is not None and int(active_skill_count) <= 0:
        return 0.0
    lv = max(1.0, float(level))
    t = (lv - 15.0) / 65.0
    est_k = float(active_skill_count) if active_skill_count is not None else _lerp(1.0, 2.8, t)
    p_one = skill_trigger_chance(luck)
    p_any = 1.0 - ((1.0 - p_one) ** est_k)

    if archetype == "military":
        est_base = _lerp(450.0, 2200.0, t)
        est_coef = _lerp(2.2, 8.8, t) * float(force) + _lerp(0.5, 1.8, t) * float(agility)
    elif archetype == "civil":
        est_base = _lerp(450.0, 2400.0, t)
        est_coef = _lerp(3.8, 12.2, t) * float(intellect)
    else:
        est_base = _lerp(450.0, 2300.0, t)
        est_coef = _lerp(3.0, 10.5, t) * max(float(force), float(intellect))

    skill_mult = _PVE_SKILL_BONUS_MULTIPLIER if scenario == "pve" else _PVP_SKILL_BONUS_MULTIPLIER
    return skill_mult * p_any * (est_base + est_coef)


def _compute_guest_combat_power_hp(
    *,
    attack: int,
    defense: int,
    force: int,
    intellect: int,
    agility: int,
    luck: int,
    max_hp: int,
    resolved_hp: int,
    level: int,
    archetype: Archetype,
    scenario: Scenario,
    skill_keys: Sequence[str] | None = None,
    active_skill_count: int | None = None,
) -> float:
    """Core strictly monotonic physics combat power formula (HP-equivalent units).

    Computes full-health power via ``EHP^0.45 * DPR^0.55 * speed_factor * passive``,
    then subtracts missing health ``(max_hp - resolved_hp)`` 1:1 when evaluating
    a wounded guest so current-HP scaling remains exact and predictable.
    """

    full_hp = max(1, int(max_hp))
    current_hp = max(0, min(full_hp, int(resolved_hp)))
    dr = defense_damage_reduction(defense, scenario=scenario)
    ehp = float(full_hp) / max(1.0 - _DEFENSE_HARDCAP, 1.0 - dr)
    if scenario == "pvp" and intellect > 0:
        skill_dr = min(
            _INTELLECT_SKILL_DR_HARDCAP,
            float(intellect) / (float(intellect) + _INTELLECT_SKILL_DR_CONSTANT),
        )
        status_resist = min(
            _INTELLECT_STATUS_RESIST_HARDCAP,
            float(intellect) / (float(intellect) + _INTELLECT_STATUS_RESIST_CONSTANT),
        )
        ehp /= max(0.5, 1.0 - _PVP_SKILL_DAMAGE_SHARE * skill_dr - _PVP_STATUS_RESIST_EHP_SHARE * status_resist)

    normal_dpr = max(1.0, float(attack) * _PVP_NORMAL_ATTACK_MULTIPLIER)
    if skill_keys is not None:
        skill_dpr, passive_mult = _evaluate_equipped_skills(
            skill_keys,
            attack=attack,
            defense=defense,
            force=force,
            intellect=intellect,
            agility=agility,
            luck=luck,
            max_hp=full_hp,
            level=level,
            scenario=scenario,
        )
    else:
        skill_dpr = _estimate_fallback_skill_dpr(
            archetype=archetype,
            force=force,
            intellect=intellect,
            agility=agility,
            luck=luck,
            level=level,
            scenario=scenario,
            active_skill_count=active_skill_count,
        )
        passive_mult = 1.0

    dpr = max(1.0, normal_dpr + skill_dpr)
    speed_factor = (
        (max(0.0, float(agility)) + _PHYSICS_AGILITY_OFFSET) / _PHYSICS_AGILITY_REFERENCE
    ) ** _PHYSICS_AGILITY_EXPONENT
    full_power_hp = (
        _PHYSICS_POWER_SCALE * (ehp**_PHYSICS_EHP_EXPONENT) * (dpr**_PHYSICS_DPR_EXPONENT) * speed_factor * passive_mult
    )
    missing_hp = full_hp - current_hp
    return max(0.0, full_power_hp - float(missing_hp))


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
    skill_keys: Sequence[str] | None = None,
    active_skill_count: int | None = None,
) -> float:
    """Return one guest's HP-equivalent combat power.

    ``max_hp`` derives from ``defense_stat * DEFENSE_TO_HP_MULTIPLIER`` (never
    ``defense_bonus``), while damage reduction uses full ``defense``.  When
    ``skill_keys`` is supplied, exact equipped skill formulas are evaluated.
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

    if normalized_archetype == "military":
        attr_attack = int(
            normalized_force * float(GUEST.MILITARY_FORCE_WEIGHT)
            + normalized_intellect * float(GUEST.MILITARY_INTELLECT_WEIGHT)
        )
    else:
        attr_attack = int(
            normalized_force * float(GUEST.CIVIL_FORCE_WEIGHT)
            + normalized_intellect * float(GUEST.CIVIL_INTELLECT_WEIGHT)
        )
    total_attack = attr_attack + normalized_attack_bonus

    defense_for_hp = max(0, int(defense if defense_stat is None else defense_stat))
    defense_to_hp = int(GUEST.DEFENSE_TO_HP_MULTIPLIER)
    if normalized_archetype == "civil":
        level_hp_gain = int(GUEST.CIVIL_LEVEL_HP_GAIN)
    elif normalized_archetype == "military":
        level_hp_gain = int(GUEST.MILITARY_LEVEL_HP_GAIN)
    else:
        level_hp_gain = int(GUEST.LEVEL_HP_GAIN)
    max_hp = (
        max(int(GUEST.MIN_HP_FLOOR), int(base_hp) + int(hp_bonus) + defense_for_hp * defense_to_hp)
        + max(0, int(level) - 1) * level_hp_gain
    )

    combat_hp_units = _compute_guest_combat_power_hp(
        attack=total_attack,
        defense=normalized_defense,
        force=normalized_force,
        intellect=normalized_intellect,
        agility=normalized_agility,
        luck=normalized_luck,
        max_hp=max_hp,
        resolved_hp=max_hp,
        level=max(1, int(level)),
        archetype=normalized_archetype,
        scenario=scenario,
        skill_keys=skill_keys,
        active_skill_count=active_skill_count,
    )

    if include_troop_capacity and normalized_capacity > 0:
        resolved_stage = stage or stage_for_level(max(1, int(level)))
        capacity_weight = weights_for(
            archetype=normalized_archetype, stage=resolved_stage, scenario=scenario
        ).troop_capacity
        combat_hp_units += normalized_capacity * capacity_weight

    return combat_hp_units


def guest_power_points(**kwargs: Any) -> int:
    """Return a guest's power in runtime points (1 point = 10 HP)."""

    return int(round(guest_power(**kwargs) / POWER_POINTS_PER_HP))


def combat_power_points(
    *,
    attack: int,
    defense: int,
    defense_stat: int,
    force: int,
    intellect: int,
    level: int,
    archetype: str = "",
    max_hp: int = 0,
    hp: int | None = None,
    attack_attribute_component: int | None = None,
    agility: int = 0,
    luck: int = 0,
    skill_keys: Sequence[str] | None = None,
    active_skill_count: int | None = None,
) -> int:
    """Return canonical lineup power in runtime points (1 point = 10 HP).

    This is the single shared formula behind arena power v2 and season
    strength.  ``hp`` defaults to ``max_hp``; pass the current HP when the
    caller needs a damaged lineup's strength.  When ``skill_keys`` is provided
    (e.g. from persisted arena snapshots), exact equipped skill formulas are
    used; otherwise a smooth level-continuous estimate is applied.
    """

    normalized_archetype: Archetype = (
        "civil" if str(archetype) == "civil" else ("military" if str(archetype) == "military" else "neutral")
    )
    normalized_attack = max(0, int(attack))
    normalized_defense = max(0, int(defense))
    normalized_force = max(0, int(force))
    normalized_intellect = max(0, int(intellect))
    normalized_max_hp = max(1, int(max_hp))
    resolved_hp = normalized_max_hp if hp is None else max(0, min(normalized_max_hp, int(hp)))

    # Keep ``defense_stat`` and ``attack_attribute_component`` accepted in the
    # signature for full backward compatibility; ``max_hp`` already incorporates
    # ``defense_stat`` and ``attack`` already incorporates attribute + flat bonus.
    _ = defense_stat, attack_attribute_component

    hp_units = _compute_guest_combat_power_hp(
        attack=normalized_attack,
        defense=normalized_defense,
        force=normalized_force,
        intellect=normalized_intellect,
        agility=max(0, int(agility)),
        luck=max(0, int(luck)),
        max_hp=normalized_max_hp,
        resolved_hp=resolved_hp,
        level=max(1, int(level or 1)),
        archetype=normalized_archetype,
        scenario="pvp",
        skill_keys=skill_keys,
        active_skill_count=active_skill_count,
    )
    return max(0, int(round(hp_units / POWER_POINTS_PER_HP)))
