from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction
from types import MappingProxyType
from typing import Final

from core.config import GUEST

from .contracts import calculate_positive_growth_bps
from .random_context import RandomContext

PRESTIGE_BANDS: Final[tuple[str, ...]] = (
    "newbie",
    "junior",
    "middle",
    "senior",
    "veteran",
    "elite",
    "legend",
    "mythic",
)
CANONICAL_STRENGTH_COMPONENTS: Final[tuple[str, ...]] = (
    "arena_lineup_power",
    "core_building_level",
    "guest_count",
    "max_guest_level",
    "prestige",
    "troop_total",
)


class ProjectionRuleError(ValueError):
    pass


class SampleTier(str, Enum):
    NO_REFERENCE = "no_reference"
    SPARSE = "sparse"
    LIMITED = "limited"
    SUFFICIENT = "sufficient"


class ReferenceSource(str, Enum):
    LOCAL = "local"
    GLOBAL_SAME_BAND = "global_same_band"
    STARTER = "starter"


def _finite_number(value: object, *, field: str, non_negative: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        qualifier = " non-negative" if non_negative else ""
        raise ProjectionRuleError(f"{field} must be a finite{qualifier} number")
    normalized = float(value)
    if not math.isfinite(normalized) or (non_negative and normalized < 0):
        qualifier = " non-negative" if non_negative else ""
        raise ProjectionRuleError(f"{field} must be a finite{qualifier} number")
    return normalized


def _business_key(value: object, *, field: str = "business_key") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectionRuleError(f"{field} must be a non-empty string")
    return value.strip()


def _prestige_band(value: object, *, field: str = "prestige_band") -> str:
    normalized = _business_key(value, field=field)
    if normalized not in PRESTIGE_BANDS:
        raise ProjectionRuleError(f"{field} must be one of: {', '.join(PRESTIGE_BANDS)}")
    return normalized


def _freeze_numeric_mapping(
    values: Mapping[str, int | float],
    *,
    field: str,
    allow_empty: bool,
) -> Mapping[str, float]:
    if not isinstance(values, Mapping):
        raise ProjectionRuleError(f"{field} must be a mapping")
    normalized: dict[str, float] = {}
    for raw_key, raw_value in values.items():
        key = _business_key(raw_key, field=f"{field} key")
        if key in normalized:
            raise ProjectionRuleError(f"{field} keys must be unique after normalization")
        normalized[key] = _finite_number(raw_value, field=f"{field}[{key!r}]", non_negative=True)
    if not normalized and not allow_empty:
        raise ProjectionRuleError(f"{field} must not be empty")
    return MappingProxyType(dict(sorted(normalized.items())))


@dataclass(frozen=True, slots=True)
class StrengthSummary:
    composite: float
    components: Mapping[str, float]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "composite",
            _finite_number(self.composite, field="composite", non_negative=True),
        )
        object.__setattr__(
            self,
            "components",
            _freeze_numeric_mapping(self.components, field="components", allow_empty=True),
        )


def calculate_guest_arena_power(
    *,
    force: int,
    intellect: int,
    defense: int,
    hp_bonus: int,
    archetype: str,
    base_hp: int,
) -> int:
    """计算真人快照与 V2 发展预测共用的单门客竞技强度。"""
    normalized_force = max(0, int(force))
    normalized_intellect = max(0, int(intellect))
    normalized_defense = max(0, int(defense))
    if str(archetype) == "civil":
        attack = int(normalized_force * GUEST.CIVIL_FORCE_WEIGHT + normalized_intellect * GUEST.CIVIL_INTELLECT_WEIGHT)
    else:
        attack = int(
            normalized_force * GUEST.MILITARY_FORCE_WEIGHT + normalized_intellect * GUEST.MILITARY_INTELLECT_WEIGHT
        )
    max_hp = max(
        int(GUEST.MIN_HP_FLOOR),
        max(0, int(base_hp)) + max(0, int(hp_bonus)) + normalized_defense * int(GUEST.DEFENSE_TO_HP_MULTIPLIER),
    )
    return max(1, attack) + max(1, normalized_defense) + max_hp // 10


@dataclass(frozen=True, slots=True)
class ReferenceCandidate:
    business_key: str
    prestige_band: str
    strength: StrengthSummary
    features: Mapping[str, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "business_key", _business_key(self.business_key))
        object.__setattr__(self, "prestige_band", _prestige_band(self.prestige_band))
        if not isinstance(self.strength, StrengthSummary):
            raise ProjectionRuleError("strength must be a StrengthSummary")
        object.__setattr__(
            self,
            "features",
            _freeze_numeric_mapping(self.features, field="features", allow_empty=False),
        )


@dataclass(frozen=True, slots=True)
class StrengthSafetyRule:
    sample_tier: SampleTier
    cap_quantile: str
    composite_cap_ratio: float
    component_cap_ratio: float
    positive_jitter_bps_max: int
    strength_increasing_actions_per_24h_max: int
    composite_growth_bps_per_24h_max: int

    @property
    def tier(self) -> SampleTier:
        return self.sample_tier

    @property
    def positive_jitter_bps(self) -> int:
        return self.positive_jitter_bps_max

    @property
    def actions_per_24h(self) -> int:
        return self.strength_increasing_actions_per_24h_max

    @property
    def growth_bps_per_24h(self) -> int:
        return self.composite_growth_bps_per_24h_max


STRENGTH_SAFETY_RULES: Final[Mapping[SampleTier, StrengthSafetyRule]] = MappingProxyType(
    {
        SampleTier.NO_REFERENCE: StrengthSafetyRule(
            sample_tier=SampleTier.NO_REFERENCE,
            cap_quantile="starter",
            composite_cap_ratio=0.90,
            component_cap_ratio=0.90,
            positive_jitter_bps_max=0,
            strength_increasing_actions_per_24h_max=0,
            composite_growth_bps_per_24h_max=0,
        ),
        SampleTier.SPARSE: StrengthSafetyRule(
            sample_tier=SampleTier.SPARSE,
            cap_quantile="p50",
            composite_cap_ratio=1.05,
            component_cap_ratio=1.10,
            positive_jitter_bps_max=0,
            strength_increasing_actions_per_24h_max=1,
            composite_growth_bps_per_24h_max=300,
        ),
        SampleTier.LIMITED: StrengthSafetyRule(
            sample_tier=SampleTier.LIMITED,
            cap_quantile="p75",
            composite_cap_ratio=1.10,
            component_cap_ratio=1.15,
            positive_jitter_bps_max=200,
            strength_increasing_actions_per_24h_max=2,
            composite_growth_bps_per_24h_max=500,
        ),
        SampleTier.SUFFICIENT: StrengthSafetyRule(
            sample_tier=SampleTier.SUFFICIENT,
            cap_quantile="p95",
            composite_cap_ratio=1.15,
            component_cap_ratio=1.20,
            positive_jitter_bps_max=500,
            strength_increasing_actions_per_24h_max=4,
            composite_growth_bps_per_24h_max=1000,
        ),
    }
)


@dataclass(frozen=True, slots=True)
class ReferenceSelection:
    prestige_band: str
    tier: SampleTier
    source: ReferenceSource
    local_sample_count: int
    anchor: ReferenceCandidate | None
    cap: StrengthSummary
    nearest_candidate_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "prestige_band", _prestige_band(self.prestige_band))
        try:
            tier = SampleTier(self.tier)
            source = ReferenceSource(self.source)
        except ValueError as exc:
            raise ProjectionRuleError("reference selection has an unknown tier or source") from exc
        object.__setattr__(self, "tier", tier)
        object.__setattr__(self, "source", source)
        if (
            isinstance(self.local_sample_count, bool)
            or not isinstance(self.local_sample_count, int)
            or self.local_sample_count < 0
        ):
            raise ProjectionRuleError("local_sample_count must be a non-negative integer")
        if tier is not sample_tier_for_count(self.local_sample_count):
            raise ProjectionRuleError("tier must match local_sample_count")
        if not isinstance(self.cap, StrengthSummary):
            raise ProjectionRuleError("cap must be a StrengthSummary")

        keys = tuple(_business_key(key, field="nearest_candidate_keys entry") for key in self.nearest_candidate_keys)
        if len(keys) != len(set(keys)):
            raise ProjectionRuleError("nearest_candidate_keys must be unique")
        object.__setattr__(self, "nearest_candidate_keys", keys)

        if source is ReferenceSource.LOCAL and self.local_sample_count == 0:
            raise ProjectionRuleError("a local reference requires a local sample")
        if source is not ReferenceSource.LOCAL and self.local_sample_count > 0:
            raise ProjectionRuleError("a local sample may not be replaced by a non-local reference")
        if source is ReferenceSource.STARTER:
            if self.anchor is not None or keys:
                raise ProjectionRuleError("a starter reference cannot contain an anchor")
            return
        if not isinstance(self.anchor, ReferenceCandidate):
            raise ProjectionRuleError("a cohort reference requires an anchor")
        if self.anchor.prestige_band != self.prestige_band:
            raise ProjectionRuleError("reference anchor must be in the target prestige band")
        if self.anchor.business_key not in keys:
            raise ProjectionRuleError("reference anchor must be present in nearest_candidate_keys")

    @property
    def strength_cap(self) -> StrengthSummary:
        return self.cap


def _non_negative_integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProjectionRuleError(f"{field} must be a non-negative integer")
    return value


def _freeze_integer_mapping(
    values: Mapping[str, int],
    *,
    field: str,
    allow_empty: bool,
) -> Mapping[str, int]:
    if not isinstance(values, Mapping):
        raise ProjectionRuleError(f"{field} must be a mapping")
    normalized = {
        _business_key(key, field=f"{field} key"): _non_negative_integer(
            value,
            field=f"{field}[{key!r}]",
        )
        for key, value in values.items()
    }
    if not normalized and not allow_empty:
        raise ProjectionRuleError(f"{field} must not be empty")
    return MappingProxyType(dict(sorted(normalized.items())))


def _freeze_business_keys(
    values: Iterable[str],
    *,
    field: str,
) -> tuple[str, ...]:
    normalized = tuple(_business_key(value, field=f"{field} entry") for value in values)
    if len(normalized) != len(set(normalized)):
        raise ProjectionRuleError(f"{field} entries must be unique")
    return normalized


@dataclass(frozen=True, slots=True)
class BootstrapGuestTarget:
    ordinal: int
    template_key: str
    level: int
    investment_tier: str
    gear_template_keys: tuple[str, ...] = ()
    skill_keys: tuple[str, ...] = ()
    created_day_offset: int = 0
    gear_acquired_day_offsets: tuple[int, ...] = ()
    skill_learned_day_offsets: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if _non_negative_integer(self.ordinal, field="ordinal") < 1:
            raise ProjectionRuleError("ordinal must be a positive integer")
        object.__setattr__(self, "template_key", _business_key(self.template_key))
        if isinstance(self.level, bool) or not isinstance(self.level, int) or self.level < 1:
            raise ProjectionRuleError("guest level must be a positive integer")
        tier = _business_key(self.investment_tier, field="investment_tier")
        if tier not in {"core", "secondary", "bench"}:
            raise ProjectionRuleError("investment_tier must be core, secondary, or bench")
        object.__setattr__(self, "investment_tier", tier)
        object.__setattr__(
            self,
            "gear_template_keys",
            _freeze_business_keys(
                self.gear_template_keys,
                field="gear_template_keys",
            ),
        )
        object.__setattr__(
            self,
            "skill_keys",
            _freeze_business_keys(self.skill_keys, field="skill_keys"),
        )
        _non_negative_integer(
            self.created_day_offset,
            field="created_day_offset",
        )
        gear_offsets = tuple(self.gear_acquired_day_offsets) or tuple(
            self.created_day_offset for _key in self.gear_template_keys
        )
        skill_offsets = tuple(self.skill_learned_day_offsets) or tuple(
            self.created_day_offset for _key in self.skill_keys
        )
        if len(gear_offsets) != len(self.gear_template_keys):
            raise ProjectionRuleError("gear_acquired_day_offsets must align with gear_template_keys")
        if len(skill_offsets) != len(self.skill_keys):
            raise ProjectionRuleError("skill_learned_day_offsets must align with skill_keys")
        for index, offset in enumerate(gear_offsets):
            normalized = _non_negative_integer(
                offset,
                field=f"gear_acquired_day_offsets[{index}]",
            )
            if normalized < self.created_day_offset:
                raise ProjectionRuleError("gear acquisition cannot predate its guest")
        for index, offset in enumerate(skill_offsets):
            normalized = _non_negative_integer(
                offset,
                field=f"skill_learned_day_offsets[{index}]",
            )
            if normalized < self.created_day_offset:
                raise ProjectionRuleError("skill learning cannot predate its guest")
        object.__setattr__(self, "gear_acquired_day_offsets", gear_offsets)
        object.__setattr__(self, "skill_learned_day_offsets", skill_offsets)


@dataclass(frozen=True, slots=True)
class BootstrapInventoryTarget:
    template_key: str
    quantity: int
    storage_location: str = "warehouse"
    acquired_day_offset: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "template_key", _business_key(self.template_key))
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int) or self.quantity < 1:
            raise ProjectionRuleError("inventory quantity must be a positive integer")
        location = _business_key(self.storage_location, field="storage_location")
        if location not in {"warehouse", "treasury"}:
            raise ProjectionRuleError("storage_location must be warehouse or treasury")
        object.__setattr__(self, "storage_location", location)
        _non_negative_integer(
            self.acquired_day_offset,
            field="acquired_day_offset",
        )


@dataclass(frozen=True, slots=True)
class BootstrapAssetTargets:
    building_levels: Mapping[str, int]
    technology_levels: Mapping[str, int]
    guests: tuple[BootstrapGuestTarget, ...]
    retainer_count: int
    troop_counts: Mapping[str, int]
    inventory: tuple[BootstrapInventoryTarget, ...]
    silver: int
    grain: int
    catalog_digest: str
    building_created_day_offsets: Mapping[str, int] = field(default_factory=dict)
    technology_reached_day_offsets: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "building_levels",
            _freeze_integer_mapping(
                self.building_levels,
                field="building_levels",
                allow_empty=False,
            ),
        )
        object.__setattr__(
            self,
            "technology_levels",
            _freeze_integer_mapping(
                self.technology_levels,
                field="technology_levels",
                allow_empty=True,
            ),
        )
        building_offsets = self.building_created_day_offsets or {key: 0 for key in self.building_levels}
        technology_offsets = self.technology_reached_day_offsets or {key: 0 for key in self.technology_levels}
        object.__setattr__(
            self,
            "building_created_day_offsets",
            _freeze_integer_mapping(
                building_offsets,
                field="building_created_day_offsets",
                allow_empty=False,
            ),
        )
        object.__setattr__(
            self,
            "technology_reached_day_offsets",
            _freeze_integer_mapping(
                technology_offsets,
                field="technology_reached_day_offsets",
                allow_empty=True,
            ),
        )
        if set(self.building_created_day_offsets) != set(self.building_levels):
            raise ProjectionRuleError("building history offsets must match building level keys")
        if set(self.technology_reached_day_offsets) != set(self.technology_levels):
            raise ProjectionRuleError("technology history offsets must match technology level keys")
        guests = tuple(self.guests)
        if any(not isinstance(guest, BootstrapGuestTarget) for guest in guests):
            raise ProjectionRuleError("guests must contain BootstrapGuestTarget values")
        if tuple(guest.ordinal for guest in guests) != tuple(range(1, len(guests) + 1)):
            raise ProjectionRuleError("guest ordinals must be contiguous and start at one")
        object.__setattr__(self, "guests", guests)
        _non_negative_integer(self.retainer_count, field="retainer_count")
        object.__setattr__(
            self,
            "troop_counts",
            _freeze_integer_mapping(
                self.troop_counts,
                field="troop_counts",
                allow_empty=True,
            ),
        )
        inventory = tuple(self.inventory)
        if any(not isinstance(item, BootstrapInventoryTarget) for item in inventory):
            raise ProjectionRuleError("inventory must contain BootstrapInventoryTarget values")
        inventory_keys = tuple((item.template_key, item.storage_location) for item in inventory)
        if len(inventory_keys) != len(set(inventory_keys)):
            raise ProjectionRuleError("inventory template and storage pairs must be unique")
        object.__setattr__(self, "inventory", inventory)
        _non_negative_integer(self.silver, field="silver")
        _non_negative_integer(self.grain, field="grain")
        digest = _business_key(self.catalog_digest, field="catalog_digest")
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ProjectionRuleError("catalog_digest must be a lowercase SHA-256 digest")
        object.__setattr__(self, "catalog_digest", digest)


@dataclass(frozen=True, slots=True)
class BootstrapBlueprint:
    business_key: str
    prestige_band: str
    historical_age_days: int
    target_strength: StrengthSummary
    reference_selection: ReferenceSelection
    assets: BootstrapAssetTargets

    def __post_init__(self) -> None:
        object.__setattr__(self, "business_key", _business_key(self.business_key))
        object.__setattr__(self, "prestige_band", _prestige_band(self.prestige_band))
        if (
            isinstance(self.historical_age_days, bool)
            or not isinstance(self.historical_age_days, int)
            or self.historical_age_days < 0
        ):
            raise ProjectionRuleError("historical_age_days must be a non-negative integer")
        if not isinstance(self.target_strength, StrengthSummary):
            raise ProjectionRuleError("target_strength must be a StrengthSummary")
        if not isinstance(self.reference_selection, ReferenceSelection):
            raise ProjectionRuleError("reference_selection must be a ReferenceSelection")
        if not isinstance(self.assets, BootstrapAssetTargets):
            raise ProjectionRuleError("assets must be BootstrapAssetTargets")
        if self.reference_selection.prestige_band != self.prestige_band:
            raise ProjectionRuleError("blueprint and reference selection prestige bands must match")
        validate_strength_within_cap(self.target_strength, self.reference_selection.cap)
        if (
            any(guest.created_day_offset > self.historical_age_days for guest in self.assets.guests)
            or any(item.acquired_day_offset > self.historical_age_days for item in self.assets.inventory)
            or any(
                offset > self.historical_age_days
                for guest in self.assets.guests
                for offset in (
                    *guest.gear_acquired_day_offsets,
                    *guest.skill_learned_day_offsets,
                )
            )
            or any(offset > self.historical_age_days for offset in self.assets.building_created_day_offsets.values())
            or any(offset > self.historical_age_days for offset in self.assets.technology_reached_day_offsets.values())
        ):
            raise ProjectionRuleError("asset history offsets cannot exceed historical_age_days")
        components = self.target_strength.components
        if "guest_count" in components and len(self.assets.guests) != components["guest_count"]:
            raise ProjectionRuleError("guest assets must match target guest_count")
        if "max_guest_level" in components:
            actual_max_guest_level = max(
                (guest.level for guest in self.assets.guests),
                default=0,
            )
            if actual_max_guest_level != components["max_guest_level"]:
                raise ProjectionRuleError("guest assets must match target max_guest_level")
        if "core_building_level" in components and (
            max(self.assets.building_levels.values(), default=0) != components["core_building_level"]
        ):
            raise ProjectionRuleError("building assets must match target core_building_level")
        if "troop_total" in components and sum(self.assets.troop_counts.values()) != components["troop_total"]:
            raise ProjectionRuleError("troop assets must match target troop_total")


@dataclass(frozen=True, slots=True)
class DevelopmentIntent:
    business_key: str
    action_kind: str
    source_prestige_band: str
    target_prestige_band: str
    strength_before: StrengthSummary
    strength_after: StrengthSummary
    utility_score: float
    constraint_violations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "business_key", _business_key(self.business_key))
        object.__setattr__(self, "action_kind", _business_key(self.action_kind, field="action_kind"))
        object.__setattr__(
            self,
            "source_prestige_band",
            _business_key(self.source_prestige_band, field="source_prestige_band"),
        )
        object.__setattr__(
            self,
            "target_prestige_band",
            _business_key(self.target_prestige_band, field="target_prestige_band"),
        )
        if not isinstance(self.strength_before, StrengthSummary) or not isinstance(
            self.strength_after,
            StrengthSummary,
        ):
            raise ProjectionRuleError("strength_before and strength_after must be StrengthSummary values")
        if self.strength_before.components.keys() != self.strength_after.components.keys():
            raise ProjectionRuleError("strength_before and strength_after component keys must match")
        object.__setattr__(
            self,
            "utility_score",
            _finite_number(self.utility_score, field="utility_score", non_negative=False),
        )
        violations = tuple(
            sorted({_business_key(value, field="constraint_violations entry") for value in self.constraint_violations})
        )
        object.__setattr__(self, "constraint_violations", violations)

    @property
    def is_legal(self) -> bool:
        return not self.constraint_violations and crosses_at_most_one_band(
            self.source_prestige_band,
            self.target_prestige_band,
        )


@dataclass(frozen=True, slots=True)
class GuestHealingCandidate:
    guest_id: int
    item_id: int
    item_key: str
    investment_tier: str
    is_injured: bool
    current_hp: int
    max_hp: int

    def __post_init__(self) -> None:
        for field_name in ("guest_id", "item_id"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ProjectionRuleError(f"{field_name} must be a positive integer")
        object.__setattr__(
            self,
            "item_key",
            _business_key(self.item_key, field="item_key"),
        )
        tier = _business_key(self.investment_tier, field="investment_tier")
        if tier not in {"core", "secondary", "bench"}:
            raise ProjectionRuleError("investment_tier must be core, secondary, or bench")
        object.__setattr__(self, "investment_tier", tier)
        if not isinstance(self.is_injured, bool):
            raise ProjectionRuleError("is_injured must be a boolean")
        if isinstance(self.current_hp, bool) or not isinstance(self.current_hp, int) or self.current_hp < 0:
            raise ProjectionRuleError("current_hp must be a non-negative integer")
        if (
            isinstance(self.max_hp, bool)
            or not isinstance(self.max_hp, int)
            or self.max_hp < 1
            or self.current_hp >= self.max_hp
        ):
            raise ProjectionRuleError("max_hp must be positive and exceed current_hp")

    @property
    def business_key(self) -> str:
        return f"guest_healing:guest:{self.guest_id}:item:{self.item_key}"

    @property
    def missing_hp_ratio(self) -> Fraction:
        return Fraction(self.max_hp - self.current_hp, self.max_hp)


_HEALING_TIER_PRIORITY: Final[Mapping[str, int]] = MappingProxyType({"core": 2, "secondary": 1, "bench": 0})


def select_guest_healing_candidate(
    candidates: Iterable[GuestHealingCandidate],
    *,
    context: RandomContext,
) -> GuestHealingCandidate | None:
    """严格按重伤、投资层级、缺失 HP 排序，仅在完全同分时稳定随机。"""
    normalized = tuple(candidates)
    if any(not isinstance(candidate, GuestHealingCandidate) for candidate in normalized):
        raise ProjectionRuleError("guest healing candidates must be GuestHealingCandidate values")
    keys = tuple(candidate.business_key for candidate in normalized)
    if len(keys) != len(set(keys)):
        raise ProjectionRuleError("guest healing candidate business_key values must be unique")
    if not normalized:
        return None

    def priority(candidate: GuestHealingCandidate) -> tuple[bool, int, Fraction]:
        return (
            candidate.is_injured,
            _HEALING_TIER_PRIORITY[candidate.investment_tier],
            candidate.missing_hp_ratio,
        )

    best_priority = max(priority(candidate) for candidate in normalized)
    tied = tuple(
        sorted(
            (candidate for candidate in normalized if priority(candidate) == best_priority),
            key=lambda candidate: candidate.business_key,
        )
    )
    if len(tied) == 1:
        return tied[0]
    randomizer = context.random(
        domain="roster",
        discriminator={
            "candidates": [candidate.business_key for candidate in tied],
            "purpose": "guest-healing-tie",
        },
    )
    return tied[randomizer.randrange(len(tied))]


def project_guest_healing_development_intent(
    *,
    candidate: GuestHealingCandidate,
    prestige_band: str,
    strength_before: StrengthSummary,
) -> DevelopmentIntent:
    """把恢复既有 HP 的治疗动作投影为不增加永久强度的 intent。"""
    if not isinstance(candidate, GuestHealingCandidate):
        raise ProjectionRuleError("candidate must be a GuestHealingCandidate")
    if not isinstance(strength_before, StrengthSummary):
        raise ProjectionRuleError("strength_before must be a StrengthSummary")
    utility_score = (
        (100.0 if candidate.is_injured else 0.0)
        + 10.0 * _HEALING_TIER_PRIORITY[candidate.investment_tier]
        + float(candidate.missing_hp_ratio)
    )
    return DevelopmentIntent(
        business_key=candidate.business_key,
        action_kind="guest_healing",
        source_prestige_band=prestige_band,
        target_prestige_band=prestige_band,
        strength_before=strength_before,
        strength_after=strength_before,
        utility_score=utility_score,
    )


def project_training_development_intent(
    *,
    guest_id: int,
    prestige_band: str,
    strength_before: StrengthSummary,
    guest_level_after: int,
    guest_arena_power_before: int,
    guest_arena_power_after: int,
    utility_score: float,
    constraint_violations: tuple[str, ...] = (),
) -> DevelopmentIntent:
    """把一次确定性培养结果投影为标准 V2 发展 intent。"""
    if isinstance(guest_id, bool) or not isinstance(guest_id, int) or guest_id < 1:
        raise ProjectionRuleError("guest_id must be a positive integer")
    if not isinstance(strength_before, StrengthSummary):
        raise ProjectionRuleError("strength_before must be a StrengthSummary")
    if set(strength_before.components) != set(CANONICAL_STRENGTH_COMPONENTS):
        raise ProjectionRuleError("training projection requires the canonical strength components")
    if isinstance(guest_level_after, bool) or not isinstance(guest_level_after, int) or guest_level_after < 1:
        raise ProjectionRuleError("guest_level_after must be a positive integer")
    for field_name, value in (
        ("guest_arena_power_before", guest_arena_power_before),
        ("guest_arena_power_after", guest_arena_power_after),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ProjectionRuleError(f"{field_name} must be a non-negative integer")
    if guest_arena_power_after < guest_arena_power_before:
        raise ProjectionRuleError("training must not reduce guest arena power")

    components_after = dict(strength_before.components)
    lineup_power_before = components_after["arena_lineup_power"]
    if lineup_power_before < guest_arena_power_before:
        raise ProjectionRuleError("guest arena power exceeds the current lineup power component")
    components_after["arena_lineup_power"] = lineup_power_before - guest_arena_power_before + guest_arena_power_after
    components_after["max_guest_level"] = max(
        components_after["max_guest_level"],
        guest_level_after,
    )
    strength_after = StrengthSummary(
        composite=float(components_after["arena_lineup_power"] + 2 * components_after["troop_total"]),
        components=components_after,
    )
    return DevelopmentIntent(
        business_key=f"training:guest:{guest_id}",
        action_kind="training",
        source_prestige_band=prestige_band,
        target_prestige_band=prestige_band,
        strength_before=strength_before,
        strength_after=strength_after,
        utility_score=utility_score,
        constraint_violations=constraint_violations,
    )


def project_troop_recruitment_development_intent(
    *,
    troop_key: str,
    quantity: int,
    prestige_band: str,
    strength_before: StrengthSummary,
    utility_score: float,
    constraint_violations: tuple[str, ...] = (),
) -> DevelopmentIntent:
    """把一次同步护院募兵投影为标准 V2 发展 intent。"""
    normalized_troop_key = _business_key(troop_key, field="troop_key")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise ProjectionRuleError("quantity must be a positive integer")
    if not isinstance(strength_before, StrengthSummary):
        raise ProjectionRuleError("strength_before must be a StrengthSummary")
    if set(strength_before.components) != set(CANONICAL_STRENGTH_COMPONENTS):
        raise ProjectionRuleError("troop recruitment projection requires the canonical strength components")

    components_after = dict(strength_before.components)
    components_after["troop_total"] += quantity
    strength_after = StrengthSummary(
        composite=float(components_after["arena_lineup_power"] + 2 * components_after["troop_total"]),
        components=components_after,
    )
    return DevelopmentIntent(
        business_key=(f"troop_recruitment:{normalized_troop_key}:{quantity}"),
        action_kind="troop_recruitment",
        source_prestige_band=prestige_band,
        target_prestige_band=prestige_band,
        strength_before=strength_before,
        strength_after=strength_after,
        utility_score=utility_score,
        constraint_violations=constraint_violations,
    )


def sample_tier_for_count(sample_count: int) -> SampleTier:
    if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 0:
        raise ProjectionRuleError("sample_count must be a non-negative integer")
    if sample_count == 0:
        return SampleTier.NO_REFERENCE
    if sample_count <= 4:
        return SampleTier.SPARSE
    if sample_count <= 29:
        return SampleTier.LIMITED
    return SampleTier.SUFFICIENT


def safety_rule_for_sample_count(sample_count: int) -> StrengthSafetyRule:
    return STRENGTH_SAFETY_RULES[sample_tier_for_count(sample_count)]


def nearest_rank_quantile(values: Sequence[int | float], quantile: float) -> float:
    if not values:
        raise ProjectionRuleError("quantile requires at least one value")
    normalized_quantile = _finite_number(quantile, field="quantile", non_negative=True)
    if normalized_quantile > 1:
        raise ProjectionRuleError("quantile must be between 0 and 1")
    ordered = sorted(_finite_number(value, field="quantile value", non_negative=True) for value in values)
    index = max(0, min(len(ordered) - 1, math.ceil(normalized_quantile * len(ordered)) - 1))
    return ordered[index]


def clip_p5_p95(value: int | float, reference_values: Sequence[int | float]) -> float:
    normalized = _finite_number(value, field="value", non_negative=True)
    lower = nearest_rank_quantile(reference_values, 0.05)
    upper = nearest_rank_quantile(reference_values, 0.95)
    return min(upper, max(lower, normalized))


def _candidates_for_band(
    candidates: Iterable[ReferenceCandidate],
    *,
    prestige_band: str,
) -> tuple[ReferenceCandidate, ...]:
    selected: list[ReferenceCandidate] = []
    for candidate in candidates:
        if not isinstance(candidate, ReferenceCandidate):
            raise ProjectionRuleError("reference candidates must be ReferenceCandidate values")
        if candidate.prestige_band == prestige_band:
            selected.append(candidate)
    ordered = tuple(sorted(selected, key=lambda candidate: candidate.business_key))
    keys = tuple(candidate.business_key for candidate in ordered)
    if len(keys) != len(set(keys)):
        raise ProjectionRuleError("reference candidate business_key values must be unique within a prestige band")
    return ordered


def _nearest_candidates(
    candidates: tuple[ReferenceCandidate, ...],
    *,
    target_features: Mapping[str, float],
    nearest_k: int,
) -> tuple[ReferenceCandidate, ...]:
    feature_names = tuple(target_features)
    for candidate in candidates:
        if tuple(candidate.features) != feature_names:
            raise ProjectionRuleError("reference candidate feature keys must match target_features")

    bounds: dict[str, tuple[float, float]] = {}
    for feature_name in feature_names:
        values = [candidate.features[feature_name] for candidate in candidates]
        bounds[feature_name] = (
            nearest_rank_quantile(values, 0.05),
            nearest_rank_quantile(values, 0.95),
        )

    distances: list[tuple[float, str, ReferenceCandidate]] = []
    for candidate in candidates:
        squared_distance = 0.0
        for feature_name in feature_names:
            lower, upper = bounds[feature_name]
            if upper == lower:
                continue
            target = min(upper, max(lower, target_features[feature_name]))
            observed = min(upper, max(lower, candidate.features[feature_name]))
            squared_distance += ((observed - target) / (upper - lower)) ** 2
        distances.append((math.sqrt(squared_distance), candidate.business_key, candidate))
    distances.sort(key=lambda row: (row[0], row[1]))
    return tuple(row[2] for row in distances[:nearest_k])


def _strength_quantile_cap(
    candidates: tuple[ReferenceCandidate, ...],
    *,
    rule: StrengthSafetyRule,
) -> StrengthSummary:
    quantiles = {"p50": 0.50, "p75": 0.75, "p95": 0.95}
    quantile = quantiles.get(rule.cap_quantile)
    if quantile is None:
        raise ProjectionRuleError("cohort strength cap requires a percentile rule")
    component_names = tuple(candidates[0].strength.components)
    for candidate in candidates:
        if tuple(candidate.strength.components) != component_names:
            raise ProjectionRuleError("reference candidate strength component keys must match")
    return StrengthSummary(
        composite=nearest_rank_quantile(
            [candidate.strength.composite for candidate in candidates],
            quantile,
        )
        * rule.composite_cap_ratio,
        components={
            component_name: nearest_rank_quantile(
                [candidate.strength.components[component_name] for candidate in candidates],
                quantile,
            )
            * rule.component_cap_ratio
            for component_name in component_names
        },
    )


def _scaled_strength(summary: StrengthSummary, ratio: float) -> StrengthSummary:
    components = {key: value * ratio for key, value in summary.components.items()}
    core_building_level = summary.components.get("core_building_level")
    if core_building_level is not None and core_building_level >= 1:
        # A core building is indivisible; discounting level 1 must not turn a
        # structurally valid reference into an impossible bootstrap cap.
        components["core_building_level"] = max(
            1.0,
            components["core_building_level"],
        )
    return StrengthSummary(
        composite=summary.composite * ratio,
        components=components,
    )


def minimum_strength_cap(first: StrengthSummary, second: StrengthSummary) -> StrengthSummary:
    if first.components.keys() != second.components.keys():
        raise ProjectionRuleError("strength component keys must match when combining caps")
    return StrengthSummary(
        composite=min(first.composite, second.composite),
        components={key: min(first.components[key], second.components[key]) for key in first.components},
    )


def select_reference(
    *,
    context: RandomContext,
    prestige_band: str,
    target_features: Mapping[str, int | float],
    starter_strength: StrengthSummary,
    local_candidates: Iterable[ReferenceCandidate],
    local_sample_count: int | None = None,
    global_candidates: Iterable[ReferenceCandidate] = (),
    global_same_band_cap: StrengthSummary | None = None,
    nearest_k: int = 3,
) -> ReferenceSelection:
    band = _prestige_band(prestige_band)
    if not isinstance(starter_strength, StrengthSummary):
        raise ProjectionRuleError("starter_strength must be a StrengthSummary")
    if isinstance(nearest_k, bool) or not isinstance(nearest_k, int) or nearest_k <= 0:
        raise ProjectionRuleError("nearest_k must be a positive integer")
    normalized_target = _freeze_numeric_mapping(
        target_features,
        field="target_features",
        allow_empty=False,
    )
    if global_same_band_cap is not None and not isinstance(global_same_band_cap, StrengthSummary):
        raise ProjectionRuleError("global_same_band_cap must be a StrengthSummary")
    local = _candidates_for_band(local_candidates, prestige_band=band)
    loaded_local_count = len(local)
    if local_sample_count is None:
        effective_local_count = loaded_local_count
    else:
        if (
            isinstance(local_sample_count, bool)
            or not isinstance(local_sample_count, int)
            or local_sample_count < loaded_local_count
            or (local_sample_count > 0 and loaded_local_count == 0)
        ):
            raise ProjectionRuleError("local_sample_count must be an integer covering the loaded local candidates")
        effective_local_count = local_sample_count
    tier = sample_tier_for_count(effective_local_count)
    rule = STRENGTH_SAFETY_RULES[tier]

    if local:
        cohort = local
        source = ReferenceSource.LOCAL
        cap = _strength_quantile_cap(cohort, rule=rule)
    else:
        global_cohort = _candidates_for_band(global_candidates, prestige_band=band)
        cap = _scaled_strength(starter_strength, rule.composite_cap_ratio)
        if global_cohort and global_same_band_cap is not None:
            cohort = global_cohort
            source = ReferenceSource.GLOBAL_SAME_BAND
            cap = minimum_strength_cap(
                cap,
                _scaled_strength(global_same_band_cap, rule.composite_cap_ratio),
            )
        else:
            cohort = ()
            source = ReferenceSource.STARTER

    if not cohort:
        return ReferenceSelection(
            prestige_band=band,
            tier=tier,
            source=source,
            local_sample_count=effective_local_count,
            anchor=None,
            cap=cap,
            nearest_candidate_keys=(),
        )

    nearest = _nearest_candidates(
        cohort,
        target_features=normalized_target,
        nearest_k=min(nearest_k, len(cohort)),
    )
    nearest_keys = tuple(candidate.business_key for candidate in nearest)
    anchor_index = context.bucket(
        domain="reference_anchor",
        discriminator={
            "candidate_keys": list(nearest_keys),
            "prestige_band": band,
            "source": source.value,
            "target_features": dict(normalized_target),
        },
        bucket_count=len(nearest),
    )
    anchor = nearest[anchor_index % len(nearest)]
    return ReferenceSelection(
        prestige_band=band,
        tier=tier,
        source=source,
        local_sample_count=effective_local_count,
        anchor=anchor,
        cap=cap,
        nearest_candidate_keys=nearest_keys,
    )


def validate_strength_within_cap(strength: StrengthSummary, cap: StrengthSummary) -> None:
    if strength.components.keys() != cap.components.keys():
        raise ProjectionRuleError("target strength component keys must match the strength cap")
    if strength.composite > cap.composite:
        raise ProjectionRuleError("target composite strength exceeds the reference cap")
    exceeded = [key for key in strength.components if strength.components[key] > cap.components[key]]
    if exceeded:
        raise ProjectionRuleError(f"target strength components exceed the reference cap: {', '.join(exceeded)}")


def crosses_at_most_one_band(source_band: str, target_band: str) -> bool:
    try:
        source_index = PRESTIGE_BANDS.index(source_band)
        target_index = PRESTIGE_BANDS.index(target_band)
    except ValueError:
        return False
    return abs(target_index - source_index) <= 1


def validate_controlled_band_transition(source_band: str, target_band: str) -> None:
    if not crosses_at_most_one_band(source_band, target_band):
        raise ProjectionRuleError("a controlled action may cross at most one adjacent prestige band")


def composite_growth_bps(before: int | float, after: int | float) -> int:
    return calculate_positive_growth_bps(pre_score=before, post_score=after, score_floor=1.0)


def select_development_intent(
    candidates: Iterable[DevelopmentIntent],
    *,
    context: RandomContext,
    optimization_bias: float,
    top_k: int = 3,
) -> DevelopmentIntent | None:
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise ProjectionRuleError("top_k must be a positive integer")
    bias = _finite_number(optimization_bias, field="optimization_bias", non_negative=True)
    if bias > 1:
        raise ProjectionRuleError("optimization_bias must be between 0 and 1")

    normalized: list[DevelopmentIntent] = []
    for candidate in candidates:
        if not isinstance(candidate, DevelopmentIntent):
            raise ProjectionRuleError("development candidates must be DevelopmentIntent values")
        normalized.append(candidate)
    keys = tuple(candidate.business_key for candidate in normalized)
    if len(keys) != len(set(keys)):
        raise ProjectionRuleError("development intent business_key values must be unique")

    eligible = sorted(
        (candidate for candidate in normalized if candidate.is_legal),
        key=lambda candidate: (-candidate.utility_score, candidate.business_key),
    )
    if not eligible:
        return None
    shortlist = eligible[:top_k]
    randomizer = context.random(
        domain="schedule",
        discriminator={
            "candidates": [
                {
                    "action_kind": candidate.action_kind,
                    "business_key": candidate.business_key,
                    "utility_score": candidate.utility_score,
                }
                for candidate in shortlist
            ],
            "optimization_bias": bias,
        },
    )
    if bias == 1 or len(shortlist) == 1 or randomizer.random() < bias:
        return shortlist[0]
    return shortlist[randomizer.randrange(len(shortlist))]


__all__ = [
    "CANONICAL_STRENGTH_COMPONENTS",
    "PRESTIGE_BANDS",
    "STRENGTH_SAFETY_RULES",
    "BootstrapAssetTargets",
    "BootstrapBlueprint",
    "BootstrapGuestTarget",
    "BootstrapInventoryTarget",
    "DevelopmentIntent",
    "GuestHealingCandidate",
    "ProjectionRuleError",
    "ReferenceCandidate",
    "ReferenceSelection",
    "ReferenceSource",
    "SampleTier",
    "StrengthSafetyRule",
    "StrengthSummary",
    "calculate_guest_arena_power",
    "clip_p5_p95",
    "composite_growth_bps",
    "crosses_at_most_one_band",
    "minimum_strength_cap",
    "nearest_rank_quantile",
    "project_guest_healing_development_intent",
    "project_troop_recruitment_development_intent",
    "project_training_development_intent",
    "safety_rule_for_sample_count",
    "sample_tier_for_count",
    "select_development_intent",
    "select_guest_healing_candidate",
    "select_reference",
    "validate_controlled_band_transition",
    "validate_strength_within_cap",
]
