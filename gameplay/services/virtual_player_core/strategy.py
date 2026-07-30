from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from common.constants.virtual_players import VIRTUAL_PLAYER_ARCHETYPES

from .random_context import RandomContext, canonical_json_bytes

PLAN_SCHEMA_VERSION_V1 = 1
SUPPORTED_PLAN_SCHEMA_VERSIONS = frozenset({PLAN_SCHEMA_VERSION_V1})

_PLAN_V1_GUEST_ARCHETYPES = ("civil", "military")
_PLAN_V1_TROOP_CLASSES = ("dao", "gong", "jian", "qiang", "quan")
_PLAN_V1_GEAR_STATS = ("agility", "defense", "force", "hp", "intellect")
_PLAN_V1_SKILL_KINDS = ("active", "passive")
_PLAN_V1_BUILDING_KEYS = (
    "arrow_tower",
    "bathhouse",
    "citang",
    "farm",
    "forge",
    "granary",
    "jiadingfang",
    "jail",
    "juxianzhuang",
    "latrine",
    "lianggongchang",
    "oath_grove",
    "ranch",
    "silver_vault",
    "smithy",
    "stable",
    "tavern",
    "tax_office",
    "treasury",
    "wall",
    "youxibaota",
)
_PLAN_V1_TECHNOLOGY_KEYS = (
    "animal_husbandry",
    "architecture",
    "dao_agility",
    "dao_attack",
    "dao_defense",
    "dao_double_strike",
    "dao_hp",
    "dao_recruit",
    "farming",
    "forging",
    "gong_agility",
    "gong_attack",
    "gong_defense",
    "gong_hp",
    "gong_melee",
    "gong_range",
    "gong_recruit",
    "horsemanship",
    "jian_agility",
    "jian_attack",
    "jian_defense",
    "jian_hp",
    "jian_preempt",
    "jian_recruit",
    "jian_reflect",
    "march_art",
    "qiang_agility",
    "qiang_attack",
    "qiang_counter",
    "qiang_defense",
    "qiang_hp",
    "qiang_recruit",
    "qiang_siege",
    "quan_agility",
    "quan_attack",
    "quan_defense",
    "quan_heal",
    "quan_hp",
    "quan_recruit",
    "quan_vs_ranged",
    "scout_art",
    "smelting",
)

_PLAN_FIELDS = frozenset(
    {
        "schema_version",
        "optimization_bias",
        "inertia_bias",
        "roster_focus",
        "preferred_guest_archetypes",
        "primary_troop_class",
        "secondary_troop_class",
        "troop_mix",
        "preferred_gear_stats",
        "preferred_skill_kinds",
        "building_focuses",
        "technology_focuses",
    }
)

_ARCHETYPE_BIAS_RANGES: dict[str, tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] = {
    "balanced": ((0.45, 0.70), (0.35, 0.60), (0.45, 0.65)),
    "rich": ((0.25, 0.50), (0.45, 0.75), (0.55, 0.80)),
    "dojo": ((0.65, 0.90), (0.45, 0.70), (0.65, 0.90)),
    "guard": ((0.50, 0.75), (0.60, 0.85), (0.40, 0.65)),
    "abandoned": ((0.15, 0.35), (0.75, 0.95), (0.20, 0.45)),
}


class DevelopmentPlanError(ValueError):
    pass


class UnsupportedPlanSchemaError(DevelopmentPlanError):
    pass


class InvalidDevelopmentPlanError(DevelopmentPlanError):
    pass


def _canonical_strings(values: Iterable[str], *, field: str, minimum: int = 1) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise InvalidDevelopmentPlanError(f"{field} entries must be non-empty strings")
        normalized.append(value.strip())
    unique = tuple(sorted(set(normalized)))
    if len(unique) < minimum:
        raise InvalidDevelopmentPlanError(f"{field} requires at least {minimum} distinct entries")
    return unique


@dataclass(frozen=True, slots=True)
class DevelopmentPlanCatalog:
    guest_archetypes: tuple[str, ...]
    troop_classes: tuple[str, ...]
    gear_stats: tuple[str, ...]
    skill_kinds: tuple[str, ...]
    building_keys: tuple[str, ...]
    technology_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "guest_archetypes",
            _canonical_strings(self.guest_archetypes, field="guest_archetypes"),
        )
        object.__setattr__(
            self,
            "troop_classes",
            _canonical_strings(self.troop_classes, field="troop_classes", minimum=2),
        )
        object.__setattr__(self, "gear_stats", _canonical_strings(self.gear_stats, field="gear_stats"))
        object.__setattr__(self, "skill_kinds", _canonical_strings(self.skill_kinds, field="skill_kinds"))
        object.__setattr__(self, "building_keys", _canonical_strings(self.building_keys, field="building_keys"))
        object.__setattr__(
            self,
            "technology_keys",
            _canonical_strings(self.technology_keys, field="technology_keys"),
        )


def development_plan_catalog_v1() -> DevelopmentPlanCatalog:
    """Return the frozen candidate catalog owned by plan schema v1."""
    return DevelopmentPlanCatalog(
        guest_archetypes=_PLAN_V1_GUEST_ARCHETYPES,
        troop_classes=_PLAN_V1_TROOP_CLASSES,
        gear_stats=_PLAN_V1_GEAR_STATS,
        skill_kinds=_PLAN_V1_SKILL_KINDS,
        building_keys=_PLAN_V1_BUILDING_KEYS,
        technology_keys=_PLAN_V1_TECHNOLOGY_KEYS,
    )


def _bounded_float(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidDevelopmentPlanError(f"{field} must be a finite number between 0 and 1")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0 or normalized > 1:
        raise InvalidDevelopmentPlanError(f"{field} must be a finite number between 0 and 1")
    return round(normalized, 6)


def _plan_strings(values: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(values, list) or not values:
        raise InvalidDevelopmentPlanError(f"{field} must be a non-empty list")
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise InvalidDevelopmentPlanError(f"{field} entries must be non-empty strings")
        normalized.append(value.strip())
    if len(set(normalized)) != len(normalized):
        raise InvalidDevelopmentPlanError(f"{field} entries must be unique")
    return tuple(normalized)


def _validated_plan_strings(values: tuple[str, ...], *, field: str) -> tuple[str, ...]:
    return _plan_strings(list(values), field=field)


def _troop_mix(value: Any) -> tuple[tuple[str, float], ...]:
    if not isinstance(value, list) or len(value) < 2:
        raise InvalidDevelopmentPlanError("troop_mix must contain at least two entries")
    entries: list[tuple[str, float]] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, list) or len(entry) != 2:
            raise InvalidDevelopmentPlanError(f"troop_mix[{index}] must be a two-item list")
        troop_class, raw_ratio = entry
        if not isinstance(troop_class, str) or not troop_class.strip():
            raise InvalidDevelopmentPlanError(f"troop_mix[{index}] class must be a non-empty string")
        ratio = _bounded_float(raw_ratio, field=f"troop_mix[{index}] ratio")
        if ratio <= 0:
            raise InvalidDevelopmentPlanError(f"troop_mix[{index}] ratio must be positive")
        entries.append((troop_class.strip(), ratio))
    classes = [troop_class for troop_class, _ in entries]
    if len(set(classes)) != len(classes):
        raise InvalidDevelopmentPlanError("troop_mix classes must be unique")
    if not math.isclose(sum(ratio for _, ratio in entries), 1.0, abs_tol=1e-6):
        raise InvalidDevelopmentPlanError("troop_mix ratios must sum to 1")
    return tuple(entries)


@dataclass(frozen=True, slots=True)
class BotDevelopmentPlan:
    schema_version: int
    optimization_bias: float
    inertia_bias: float
    roster_focus: float
    preferred_guest_archetypes: tuple[str, ...]
    primary_troop_class: str
    secondary_troop_class: str
    troop_mix: tuple[tuple[str, float], ...]
    preferred_gear_stats: tuple[str, ...]
    preferred_skill_kinds: tuple[str, ...]
    building_focuses: tuple[str, ...]
    technology_focuses: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version not in SUPPORTED_PLAN_SCHEMA_VERSIONS:
            raise UnsupportedPlanSchemaError(f"Unsupported BotDevelopmentPlan schema: {self.schema_version!r}")
        object.__setattr__(
            self,
            "optimization_bias",
            _bounded_float(self.optimization_bias, field="optimization_bias"),
        )
        object.__setattr__(self, "inertia_bias", _bounded_float(self.inertia_bias, field="inertia_bias"))
        object.__setattr__(self, "roster_focus", _bounded_float(self.roster_focus, field="roster_focus"))
        for field in (
            "preferred_guest_archetypes",
            "preferred_gear_stats",
            "preferred_skill_kinds",
            "building_focuses",
            "technology_focuses",
        ):
            object.__setattr__(self, field, _validated_plan_strings(getattr(self, field), field=field))
        primary = self.primary_troop_class
        secondary = self.secondary_troop_class
        if not isinstance(primary, str) or not primary.strip():
            raise InvalidDevelopmentPlanError("primary_troop_class must be a non-empty string")
        if not isinstance(secondary, str) or not secondary.strip():
            raise InvalidDevelopmentPlanError("secondary_troop_class must be a non-empty string")
        primary = primary.strip()
        secondary = secondary.strip()
        if primary == secondary:
            raise InvalidDevelopmentPlanError("primary and secondary troop classes must differ")
        object.__setattr__(self, "primary_troop_class", primary)
        object.__setattr__(self, "secondary_troop_class", secondary)
        normalized_mix = _troop_mix([[troop_class, ratio] for troop_class, ratio in self.troop_mix])
        mix_classes = {troop_class for troop_class, _ in normalized_mix}
        if primary not in mix_classes or secondary not in mix_classes:
            raise InvalidDevelopmentPlanError("troop_mix must include primary and secondary troop classes")
        object.__setattr__(self, "troop_mix", normalized_mix)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "optimization_bias": self.optimization_bias,
            "inertia_bias": self.inertia_bias,
            "roster_focus": self.roster_focus,
            "preferred_guest_archetypes": list(self.preferred_guest_archetypes),
            "primary_troop_class": self.primary_troop_class,
            "secondary_troop_class": self.secondary_troop_class,
            "troop_mix": [[troop_class, ratio] for troop_class, ratio in self.troop_mix],
            "preferred_gear_stats": list(self.preferred_gear_stats),
            "preferred_skill_kinds": list(self.preferred_skill_kinds),
            "building_focuses": list(self.building_focuses),
            "technology_focuses": list(self.technology_focuses),
        }


def _strict_schema_version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidDevelopmentPlanError("schema_version must be an integer")
    if value not in SUPPORTED_PLAN_SCHEMA_VERSIONS:
        raise UnsupportedPlanSchemaError(f"Unsupported BotDevelopmentPlan schema: {value}")
    return value


def parse_development_plan(
    payload: Mapping[str, Any],
    *,
    catalog: DevelopmentPlanCatalog | None = None,
) -> BotDevelopmentPlan:
    if not isinstance(payload, Mapping):
        raise InvalidDevelopmentPlanError("development profile must be a mapping")
    payload_keys = set(payload)
    missing = sorted(_PLAN_FIELDS - payload_keys)
    unknown = sorted(payload_keys - _PLAN_FIELDS)
    if missing:
        raise InvalidDevelopmentPlanError(f"development profile missing fields: {', '.join(missing)}")
    if unknown:
        raise InvalidDevelopmentPlanError(f"development profile has unknown fields: {', '.join(unknown)}")

    plan = BotDevelopmentPlan(
        schema_version=_strict_schema_version(payload["schema_version"]),
        optimization_bias=_bounded_float(payload["optimization_bias"], field="optimization_bias"),
        inertia_bias=_bounded_float(payload["inertia_bias"], field="inertia_bias"),
        roster_focus=_bounded_float(payload["roster_focus"], field="roster_focus"),
        preferred_guest_archetypes=_plan_strings(
            payload["preferred_guest_archetypes"],
            field="preferred_guest_archetypes",
        ),
        primary_troop_class=payload["primary_troop_class"],
        secondary_troop_class=payload["secondary_troop_class"],
        troop_mix=_troop_mix(payload["troop_mix"]),
        preferred_gear_stats=_plan_strings(payload["preferred_gear_stats"], field="preferred_gear_stats"),
        preferred_skill_kinds=_plan_strings(payload["preferred_skill_kinds"], field="preferred_skill_kinds"),
        building_focuses=_plan_strings(payload["building_focuses"], field="building_focuses"),
        technology_focuses=_plan_strings(payload["technology_focuses"], field="technology_focuses"),
    )
    if catalog is not None:
        validate_development_plan_references(plan, catalog=catalog)
    return plan


def validate_development_plan_references(plan: BotDevelopmentPlan, *, catalog: DevelopmentPlanCatalog) -> None:
    references = (
        ("preferred_guest_archetypes", plan.preferred_guest_archetypes, catalog.guest_archetypes),
        ("preferred_gear_stats", plan.preferred_gear_stats, catalog.gear_stats),
        ("preferred_skill_kinds", plan.preferred_skill_kinds, catalog.skill_kinds),
        ("building_focuses", plan.building_focuses, catalog.building_keys),
        ("technology_focuses", plan.technology_focuses, catalog.technology_keys),
    )
    for field, selected, allowed in references:
        invalid = sorted(set(selected) - set(allowed))
        if invalid:
            raise InvalidDevelopmentPlanError(f"{field} has unknown references: {', '.join(invalid)}")
    troop_references = {
        plan.primary_troop_class,
        plan.secondary_troop_class,
        *(troop_class for troop_class, _ in plan.troop_mix),
    }
    invalid_troops = sorted(troop_references - set(catalog.troop_classes))
    if invalid_troops:
        raise InvalidDevelopmentPlanError(f"troop fields have unknown references: {', '.join(invalid_troops)}")


def _ranked_values(
    context: RandomContext,
    *,
    domain: str,
    discriminator: str,
    values: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            values,
            key=lambda value: (
                context.digest(
                    domain=domain,
                    discriminator={"plan_component": discriminator, "value": value},
                ),
                value,
            ),
        )
    )


def _selected_values(
    context: RandomContext,
    *,
    domain: str,
    discriminator: str,
    values: tuple[str, ...],
    maximum: int,
) -> tuple[str, ...]:
    count = min(
        len(values), 1 + context.bucket(domain=domain, discriminator=f"{discriminator}:count", bucket_count=maximum)
    )
    return _ranked_values(context, domain=domain, discriminator=discriminator, values=values)[:count]


def _sample_bias(context: RandomContext, *, field: str, bounds: tuple[float, float]) -> float:
    random_stream = context.random(domain="bootstrap", discriminator=f"development-plan:{field}")
    return round(random_stream.uniform(*bounds), 6)


def _generate_troop_mix(
    context: RandomContext,
    *,
    troop_classes: tuple[str, ...],
) -> tuple[str, str, tuple[tuple[str, float], ...]]:
    ranked = _ranked_values(context, domain="troops", discriminator="troop-mix", values=troop_classes)
    primary, secondary = ranked[:2]
    if len(ranked) == 2:
        primary_ratio = round(
            context.random(domain="troops", discriminator="troop-mix:primary-ratio").uniform(0.60, 0.75),
            6,
        )
        return primary, secondary, ((primary, primary_ratio), (secondary, round(1.0 - primary_ratio, 6)))

    tertiary = ranked[2]
    primary_ratio = round(
        context.random(domain="troops", discriminator="troop-mix:primary-ratio").uniform(0.55, 0.72),
        6,
    )
    secondary_ratio = round(
        context.random(domain="troops", discriminator="troop-mix:secondary-ratio").uniform(0.18, 0.28),
        6,
    )
    tertiary_ratio = round(1.0 - primary_ratio - secondary_ratio, 6)
    return (
        primary,
        secondary,
        (
            (primary, primary_ratio),
            (secondary, secondary_ratio),
            (tertiary, tertiary_ratio),
        ),
    )


def generate_development_plan(
    *,
    context: RandomContext,
    archetype: str,
    catalog: DevelopmentPlanCatalog,
) -> BotDevelopmentPlan:
    normalized_archetype = str(archetype).strip()
    if normalized_archetype not in VIRTUAL_PLAYER_ARCHETYPES:
        raise InvalidDevelopmentPlanError(f"Unsupported virtual-player archetype: {normalized_archetype!r}")
    if context.plan_schema_version != PLAN_SCHEMA_VERSION_V1:
        raise UnsupportedPlanSchemaError(f"Unsupported BotDevelopmentPlan schema: {context.plan_schema_version}")

    optimization_range, inertia_range, roster_range = _ARCHETYPE_BIAS_RANGES[normalized_archetype]
    primary, secondary, troop_mix = _generate_troop_mix(context, troop_classes=catalog.troop_classes)
    plan = BotDevelopmentPlan(
        schema_version=context.plan_schema_version,
        optimization_bias=_sample_bias(
            context, field=f"{normalized_archetype}:optimization", bounds=optimization_range
        ),
        inertia_bias=_sample_bias(context, field=f"{normalized_archetype}:inertia", bounds=inertia_range),
        roster_focus=_sample_bias(context, field=f"{normalized_archetype}:roster", bounds=roster_range),
        preferred_guest_archetypes=_selected_values(
            context,
            domain="roster",
            discriminator=f"{normalized_archetype}:guest-archetypes",
            values=catalog.guest_archetypes,
            maximum=3,
        ),
        primary_troop_class=primary,
        secondary_troop_class=secondary,
        troop_mix=troop_mix,
        preferred_gear_stats=_selected_values(
            context,
            domain="gear",
            discriminator=f"{normalized_archetype}:gear-stats",
            values=catalog.gear_stats,
            maximum=3,
        ),
        preferred_skill_kinds=_selected_values(
            context,
            domain="skills",
            discriminator=f"{normalized_archetype}:skill-kinds",
            values=catalog.skill_kinds,
            maximum=3,
        ),
        building_focuses=_selected_values(
            context,
            domain="buildings",
            discriminator=f"{normalized_archetype}:building-focuses",
            values=catalog.building_keys,
            maximum=3,
        ),
        technology_focuses=_selected_values(
            context,
            domain="technology",
            discriminator=f"{normalized_archetype}:technology-focuses",
            values=catalog.technology_keys,
            maximum=3,
        ),
    )
    validate_development_plan_references(plan, catalog=catalog)
    return plan


def canonical_development_plan_bytes(plan: BotDevelopmentPlan) -> bytes:
    return canonical_json_bytes(plan.to_payload())


def development_plan_checksum(plan: BotDevelopmentPlan) -> str:
    return sha256(canonical_development_plan_bytes(plan)).hexdigest()


def upgrade_development_plan(
    payload: Mapping[str, Any],
    *,
    target_schema_version: int,
    catalog: DevelopmentPlanCatalog | None = None,
) -> BotDevelopmentPlan:
    plan = parse_development_plan(payload, catalog=catalog)
    if target_schema_version != plan.schema_version:
        raise UnsupportedPlanSchemaError(
            f"No BotDevelopmentPlan upgrade path from {plan.schema_version} to {target_schema_version}"
        )
    return plan


def load_development_plan_json(
    serialized: str,
    *,
    catalog: DevelopmentPlanCatalog | None = None,
) -> BotDevelopmentPlan:
    try:
        payload = json.loads(serialized)
    except (TypeError, json.JSONDecodeError) as exc:
        raise InvalidDevelopmentPlanError("development profile is not valid JSON") from exc
    return parse_development_plan(payload, catalog=catalog)


__all__ = [
    "PLAN_SCHEMA_VERSION_V1",
    "SUPPORTED_PLAN_SCHEMA_VERSIONS",
    "BotDevelopmentPlan",
    "DevelopmentPlanCatalog",
    "DevelopmentPlanError",
    "InvalidDevelopmentPlanError",
    "UnsupportedPlanSchemaError",
    "canonical_development_plan_bytes",
    "development_plan_checksum",
    "development_plan_catalog_v1",
    "generate_development_plan",
    "load_development_plan_json",
    "parse_development_plan",
    "upgrade_development_plan",
    "validate_development_plan_references",
]
