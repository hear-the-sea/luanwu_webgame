"""Deterministic virtual-player asset projections.

The scheduled V2 engine must not manufacture warehouse rows just to make a
bot look developed.  This module therefore contains only value objects and
pure selection/projection helpers.  The maintenance transaction owns the
small adapter that turns an accepted projection into a real Guest/GearItem or
GuestSkill row.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence


class VirtualAssetError(ValueError):
    """Raised when a virtual asset definition or projection is invalid."""


VIRTUAL_INVENTORY_BATCH_QUANTITIES = MappingProxyType(
    {
        "balanced": 4,
        "rich": 5,
        "dojo": 3,
        "guard": 3,
        "abandoned": 4,
    }
)

VIRTUAL_PRESTIGE_BANDS = (
    "newbie",
    "junior",
    "middle",
    "senior",
    "veteran",
    "elite",
    "legend",
    "mythic",
)

VIRTUAL_RARITY_ORDER = ("black", "gray", "green", "red", "blue", "purple", "orange")
VIRTUAL_RARITY_RANK = {rarity: index for index, rarity in enumerate(VIRTUAL_RARITY_ORDER)}
VIRTUAL_RARE_COLORS = frozenset({"red", "purple", "orange"})


@dataclass(frozen=True, slots=True)
class VirtualAssetCandidate:
    """A serializable candidate before any database mutation."""

    key: str
    kind: str
    weight: float
    rarity: str = "gray"
    slot: str = ""
    role: str = ""
    template_id: int | None = None
    payload: Mapping[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        for field_name in ("key", "kind"):
            value = str(getattr(self, field_name)).strip()
            if not value:
                raise VirtualAssetError(f"{field_name} must be non-empty")
            object.__setattr__(self, field_name, value)
        weight = float(self.weight)
        if weight <= 0 or weight != weight or weight in {float("inf"), float("-inf")}:
            raise VirtualAssetError("candidate weight must be finite and positive")
        object.__setattr__(self, "weight", weight)
        rarity = str(self.rarity).strip().lower()
        if rarity not in VIRTUAL_RARITY_RANK:
            raise VirtualAssetError(f"unsupported virtual rarity: {rarity}")
        object.__setattr__(self, "rarity", rarity)
        object.__setattr__(self, "slot", str(self.slot).strip())
        object.__setattr__(self, "role", str(self.role).strip())
        if self.template_id is not None:
            if isinstance(self.template_id, bool) or not isinstance(self.template_id, int) or self.template_id < 1:
                raise VirtualAssetError("template_id must be positive when present")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class VirtualTrainingProjection:
    guest_id: int
    current_level: int
    target_level: int
    remaining_seconds: int

    def __post_init__(self) -> None:
        if self.guest_id <= 0 or self.current_level < 1 or self.target_level <= self.current_level:
            raise VirtualAssetError("training projection must advance one positive guest level")
        if self.target_level != self.current_level + 1:
            raise VirtualAssetError("virtual training may advance only one level at a time")
        if self.remaining_seconds <= 0:
            raise VirtualAssetError("training duration must be positive")


@dataclass(frozen=True, slots=True)
class VirtualInventoryBatch:
    archetype: str
    requested_quantity: int
    candidates: tuple[VirtualAssetCandidate, ...]
    rare_count: int

    @property
    def applied_quantity(self) -> int:
        return len(self.candidates)

    @property
    def is_no_action(self) -> bool:
        return not self.candidates


def inventory_batch_quantity(archetype: str) -> int:
    try:
        return int(VIRTUAL_INVENTORY_BATCH_QUANTITIES[str(archetype).strip()])
    except KeyError as exc:
        raise VirtualAssetError(f"unsupported virtual-player archetype: {archetype!r}") from exc


def _rarity_cap_for_stage(
    *,
    prestige_band: str,
    growth_stage: int,
    stage_caps: Mapping[int | str, str] | None,
) -> str:
    if isinstance(growth_stage, bool) or not isinstance(growth_stage, int) or growth_stage < 0:
        raise VirtualAssetError("growth_stage must be a non-negative integer")
    if str(prestige_band).strip() not in VIRTUAL_PRESTIGE_BANDS:
        raise VirtualAssetError(f"unsupported prestige band: {prestige_band!r}")
    if growth_stage <= 6:
        default_rarity = "green"
    elif growth_stage <= 10:
        default_rarity = "blue"
    elif growth_stage <= 15:
        default_rarity = "purple"
    else:
        default_rarity = "orange"
    cap_index = VIRTUAL_RARITY_RANK[default_rarity]
    if stage_caps is not None:
        for raw_stage, raw_rarity in stage_caps.items():
            try:
                threshold = int(raw_stage)
            except (TypeError, ValueError):
                continue
            rarity = str(raw_rarity).strip().lower()
            if threshold <= growth_stage and rarity in VIRTUAL_RARITY_RANK:
                cap_index = max(cap_index, VIRTUAL_RARITY_RANK[rarity])
    return VIRTUAL_RARITY_ORDER[min(cap_index, len(VIRTUAL_RARITY_ORDER) - 1)]


def _is_rare(candidate: VirtualAssetCandidate) -> bool:
    return candidate.rarity in VIRTUAL_RARE_COLORS


def _normalized_color_weights(
    *,
    prestige_band: str,
    color_weights_by_band: Mapping[str, Mapping[str, float]] | None,
    available_colors: Sequence[str],
) -> tuple[tuple[str, float], ...]:
    configured = color_weights_by_band.get(str(prestige_band), {}) if isinstance(color_weights_by_band, Mapping) else {}
    weights: list[tuple[str, float]] = []
    for color in available_colors:
        raw_weight = configured.get(color, 1.0) if isinstance(configured, Mapping) else 1.0
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError) as exc:
            raise VirtualAssetError(f"inventory color weight is invalid for {color!r}") from exc
        if weight < 0 or weight != weight or weight in {float("inf"), float("-inf")}:
            raise VirtualAssetError(f"inventory color weight must be finite and non-negative for {color!r}")
        if weight > 0:
            weights.append((color, weight))
    if not weights:
        raise VirtualAssetError("inventory color weights have no legal color")
    return tuple(weights)


def _weighted_without_replacement(
    candidates: Sequence[VirtualAssetCandidate],
    *,
    quantity: int,
    seed: int,
    used_keys: set[str],
    rare_cap_remaining: int | None,
    prestige_band: str,
    color_weights_by_band: Mapping[str, Mapping[str, float]] | None,
    rare_colors: frozenset[str],
) -> tuple[VirtualAssetCandidate, ...]:
    pool = [candidate for candidate in candidates if candidate.key not in used_keys]
    selected: list[VirtualAssetCandidate] = []
    rng = random.Random(int(seed))
    while pool and len(selected) < quantity:
        eligible = [
            candidate
            for candidate in pool
            if rare_cap_remaining is None or candidate.rarity not in rare_colors or rare_cap_remaining > 0
        ]
        if not eligible:
            break
        by_color: dict[str, list[VirtualAssetCandidate]] = {}
        for candidate in eligible:
            by_color.setdefault(candidate.rarity, []).append(candidate)
        available_colors = tuple(color for color in VIRTUAL_RARITY_ORDER if color in by_color)
        color_weights = _normalized_color_weights(
            prestige_band=prestige_band,
            color_weights_by_band=color_weights_by_band,
            available_colors=available_colors,
        )
        chosen_color = rng.choices(
            [color for color, _weight in color_weights],
            weights=[weight for _color, weight in color_weights],
            k=1,
        )[0]
        same_color = sorted(
            by_color[chosen_color],
            key=lambda candidate: (candidate.key, candidate.template_id or 0),
        )
        chosen = rng.choices(
            same_color,
            weights=[candidate.weight for candidate in same_color],
            k=1,
        )[0]
        selected.append(chosen)
        pool.remove(chosen)
        used_keys.add(chosen.key)
        if rare_cap_remaining is not None and chosen.rarity in rare_colors:
            rare_cap_remaining -= 1
    return tuple(selected)


def draw_inventory_batch(
    candidates: Sequence[VirtualAssetCandidate],
    *,
    archetype: str,
    prestige_band: str,
    growth_stage: int,
    seed: int,
    used_keys: set[str] | None = None,
    rare_count_today: int = 0,
    rare_daily_cap: int = 20,
    stage_caps: Mapping[int | str, str] | None = None,
    color_weights_by_band: Mapping[str, Mapping[str, float]] | None = None,
    rare_colors: Sequence[str] = tuple(sorted(VIRTUAL_RARE_COLORS)),
) -> VirtualInventoryBatch:
    """Draw a deterministic, non-grain batch without using item price.

    Invalid, duplicate, over-cap, and over-rarity candidates are treated as
    redraws.  If the eligible pool is exhausted, the batch is intentionally
    underfilled; an empty result is a deterministic ``NO_ACTION``.
    """

    quantity = inventory_batch_quantity(archetype)
    if isinstance(rare_count_today, bool) or not isinstance(rare_count_today, int) or rare_count_today < 0:
        raise VirtualAssetError("rare_count_today must be a non-negative integer")
    if isinstance(rare_daily_cap, bool) or not isinstance(rare_daily_cap, int) or rare_daily_cap < 0:
        raise VirtualAssetError("rare_daily_cap must be a non-negative integer")
    normalized_rare_colors = frozenset(str(color).strip().lower() for color in rare_colors)
    if not normalized_rare_colors.issubset(VIRTUAL_RARITY_RANK):
        raise VirtualAssetError("rare_colors contains an unsupported color")
    cap = VIRTUAL_RARITY_RANK[
        _rarity_cap_for_stage(
            prestige_band=prestige_band,
            growth_stage=growth_stage,
            stage_caps=stage_caps,
        )
    ]
    normalized: list[VirtualAssetCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, VirtualAssetCandidate):
            raise VirtualAssetError("inventory candidates must be VirtualAssetCandidate values")
        if candidate.kind == "grain" or candidate.key == "grain":
            continue
        if VIRTUAL_RARITY_RANK[candidate.rarity] > cap or candidate.key in seen:
            continue
        seen.add(candidate.key)
        normalized.append(candidate)
    # A color-specific daily cap is applied to actual selected items.  Keep
    # the helper's old call shape deterministic when callers omit the new
    # policy payload, while still using the configured rare-color set.
    selected = _weighted_without_replacement(
        normalized,
        quantity=quantity,
        seed=seed,
        used_keys=set() if used_keys is None else used_keys,
        rare_cap_remaining=max(0, rare_daily_cap - rare_count_today) if normalized_rare_colors else None,
        prestige_band=str(prestige_band).strip(),
        color_weights_by_band=color_weights_by_band,
        rare_colors=normalized_rare_colors,
    )
    return VirtualInventoryBatch(
        archetype=str(archetype).strip(),
        requested_quantity=quantity,
        candidates=selected,
        rare_count=sum(1 for candidate in selected if candidate.rarity in normalized_rare_colors),
    )


def resolve_skill_book_definition(
    item_template: object,
    skills_by_key: Mapping[str, object],
) -> object:
    """Require the DB item-template contract for a virtual skill candidate."""

    effect_type = str(getattr(item_template, "effect_type", "")).strip()
    if effect_type != "skill_book":
        raise VirtualAssetError("virtual skill candidates require effect_type=skill_book")
    payload = getattr(item_template, "effect_payload", None)
    if not isinstance(payload, Mapping):
        raise VirtualAssetError("virtual skill book payload must be a mapping")
    skill_key = payload.get("skill_key")
    if not isinstance(skill_key, str) or not skill_key.strip():
        raise VirtualAssetError("virtual skill book requires effect_payload.skill_key")
    try:
        return skills_by_key[skill_key.strip()]
    except KeyError as exc:
        raise VirtualAssetError(f"virtual skill book references missing skill {skill_key!r}") from exc


def skill_candidate_weight(
    *,
    skill: object,
    guest: object,
    current_skill_count: int,
    preferred_kind: bool = False,
    preferred_role: bool = False,
) -> float:
    """Weight skills by type, level, attributes, role, power and requirements."""

    base_power = max(1, int(getattr(skill, "base_power", 0) or 0))
    level = max(1, int(getattr(guest, "level", 1) or 1))
    attribute_total = sum(
        max(0, int(getattr(guest, field, 0) or 0)) for field in ("force", "intellect", "defense_stat", "agility")
    )
    requirements = sum(
        max(0, int(getattr(skill, field, 0) or 0))
        for field in ("required_level", "required_force", "required_intellect", "required_defense", "required_agility")
    )
    kind_factor = 1.25 if preferred_kind else 1.0
    role_factor = 1.2 if preferred_role else 1.0
    slot_factor = max(0.25, 1.0 - 0.12 * max(0, int(current_skill_count)))
    return max(
        0.001,
        (base_power * (1.0 + level / 100.0) * (1.0 + attribute_total / 1_000.0) * kind_factor * role_factor)
        / (1.0 + requirements / 100.0)
        * slot_factor,
    )


def equipment_candidate_weight(
    *,
    template: object,
    guest: object,
    growth_stage: int,
    preferred_role: bool = False,
) -> float:
    """Weight equipment by power, stage, role fit, and slot availability inputs."""

    power = max(
        1,
        int(getattr(template, "attack_bonus", 0) or 0)
        + int(getattr(template, "defense_bonus", 0) or 0)
        + sum(
            max(0, int(value or 0))
            for value in (getattr(template, "extra_stats", {}) or {}).values()
            if isinstance(value, (int, float))
        ),
    )
    stage_factor = 1.0 + min(2.0, max(0, int(growth_stage)) / 20.0)
    role_factor = 1.35 if preferred_role else 1.0
    guest_power = max(
        1,
        sum(max(0, int(getattr(guest, field, 0) or 0)) for field in ("force", "intellect", "defense_stat", "agility")),
    )
    return max(0.001, power * stage_factor * role_factor * (1.0 + guest_power / 1_000.0))


def training_target_for_guest(*, guest_id: int, current_level: int, stage_cap: int) -> VirtualTrainingProjection | None:
    if guest_id <= 0 or current_level < 1 or stage_cap < 1 or current_level >= stage_cap:
        return None
    # The caller supplies the formal training duration.  The projection keeps
    # the completion contract explicit without ever changing the level here.
    return VirtualTrainingProjection(
        guest_id=int(guest_id),
        current_level=int(current_level),
        target_level=int(current_level) + 1,
        remaining_seconds=max(1, 3600 + int(current_level) * 60),
    )


def reduce_training_seconds(*, remaining_seconds: int, reduction_seconds: int) -> int:
    if remaining_seconds < 0 or reduction_seconds < 0:
        raise VirtualAssetError("training seconds must be non-negative")
    return max(0, int(remaining_seconds) - int(reduction_seconds))


def project_positive_troop_increment(
    *,
    current_count: int,
    requested_increment: int,
    target_count: int | None = None,
) -> int:
    """Project only a positive troop delta; no inventory/retainer mutation."""

    current = max(0, int(current_count))
    requested = max(0, int(requested_increment))
    if target_count is not None:
        requested = min(requested, max(0, int(target_count) - current))
    return max(0, requested)


def free_arena_shadow_cost(*, silver: int = 0, grain: int = 0, wage: int = 0, medicine: int = 0) -> dict[str, int]:
    values = {"silver": silver, "grain": grain, "wage": wage, "medicine": medicine}
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values.values()):
        raise VirtualAssetError("arena shadow costs must be non-negative integers")
    return {**values, "subsidized": 1}


__all__ = [
    "VIRTUAL_INVENTORY_BATCH_QUANTITIES",
    "VIRTUAL_PRESTIGE_BANDS",
    "VIRTUAL_RARE_COLORS",
    "VIRTUAL_RARITY_ORDER",
    "VIRTUAL_RARITY_RANK",
    "VirtualAssetCandidate",
    "VirtualAssetError",
    "VirtualInventoryBatch",
    "VirtualTrainingProjection",
    "draw_inventory_batch",
    "equipment_candidate_weight",
    "free_arena_shadow_cost",
    "inventory_batch_quantity",
    "project_positive_troop_increment",
    "reduce_training_seconds",
    "resolve_skill_book_definition",
    "skill_candidate_weight",
    "training_target_for_guest",
]
