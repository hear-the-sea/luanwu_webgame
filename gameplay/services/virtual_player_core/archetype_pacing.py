"""Deterministic, typed pacing rules for ordinary V2 virtual players."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Any

from common.constants.virtual_players import VIRTUAL_PLAYER_BUILDING_TARGET_KEYS, VIRTUAL_PLAYER_TECHNOLOGY_TARGET_KEYS

ARCHETYPE_PACING_SCHEMA_VERSION = 1
SUPPORTED_ARCHETYPES = ("balanced", "rich", "dojo", "guard", "abandoned")
SUPPORTED_RECRUITMENT_POOLS = ("dianshi", "xiangshi", "cunmu")
HIGH_COST_ACTION_KINDS = frozenset(
    {
        "building_upgrade",
        "technology_upgrade",
        "training",
        "troop_recruitment",
    }
)


class ArchetypePacingError(ValueError):
    """Raised when a typed archetype pacing payload is malformed."""


_ARCHETYPE_BUDGET_RESOURCES = ("silver", "grain")


def _canonical_budget_entries(value: Mapping[str, Any], *, field: str) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, Mapping):
        raise ArchetypePacingError(f"{field} must be a mapping")
    unknown = set(value) - set(_ARCHETYPE_BUDGET_RESOURCES)
    if unknown:
        raise ArchetypePacingError(f"{field} contains unknown resources: {sorted(unknown)!r}")
    return tuple(
        (
            resource,
            _bounded_int(value.get(resource, 0), field=f"{field}.{resource}", minimum=0),
        )
        for resource in _ARCHETYPE_BUDGET_RESOURCES
    )


@dataclass(frozen=True, slots=True)
class ArchetypeBudgetState:
    """Immutable cycle budget baseline and committed resource usage.

    The baseline is captured from the post-salary spendable ledger at the first
    slot.  Every later slot consumes from the same baseline, so the configured
    ratio is a cycle ceiling rather than a fresh per-slot allowance.
    """

    baseline: tuple[tuple[str, int], ...]
    spent: tuple[tuple[str, int], ...] = (("silver", 0), ("grain", 0))

    def __post_init__(self) -> None:
        expected = _ARCHETYPE_BUDGET_RESOURCES
        if tuple(resource for resource, _amount in self.baseline) != expected:
            raise ArchetypePacingError("budget baseline must use canonical resource order")
        if tuple(resource for resource, _amount in self.spent) != expected:
            raise ArchetypePacingError("budget spent must use canonical resource order")
        if any(
            isinstance(amount, bool) or not isinstance(amount, int) or amount < 0
            for _resource, amount in (*self.baseline, *self.spent)
        ):
            raise ArchetypePacingError("budget values must be non-negative integers")
        baseline = dict(self.baseline)
        if any(amount > baseline[resource] for resource, amount in self.spent):
            raise ArchetypePacingError("budget spent cannot exceed the cycle baseline")

    @classmethod
    def from_spendable_resources(
        cls,
        spendable_resources: Mapping[str, Any] | Sequence[tuple[str, Any]],
    ) -> "ArchetypeBudgetState":
        if isinstance(spendable_resources, Mapping):
            raw = spendable_resources
        else:
            raw = dict(spendable_resources)
        return cls(
            baseline=_canonical_budget_entries(raw, field="budget baseline"),
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> "ArchetypeBudgetState | None":
        if payload is None:
            return None
        if not isinstance(payload, Mapping):
            raise ArchetypePacingError("archetype budget payload must be a mapping")
        if "baseline" not in payload:
            return None
        raw_baseline = payload.get("baseline")
        if not isinstance(raw_baseline, Mapping):
            raise ArchetypePacingError("budget baseline must be a mapping")
        raw_spent = payload.get("spent") or {}
        if not isinstance(raw_spent, Mapping):
            raise ArchetypePacingError("budget spent must be a mapping")
        baseline = _canonical_budget_entries(raw_baseline, field="budget baseline")
        spent = _canonical_budget_entries(raw_spent, field="budget spent")
        return cls(baseline=baseline, spent=spent)

    def baseline_for(self, resource: str) -> int:
        return int(dict(self.baseline).get(str(resource), 0))

    def spent_for(self, resource: str) -> int:
        return int(dict(self.spent).get(str(resource), 0))

    def remaining_limits(self, pacing: "ArchetypePacing") -> tuple[tuple[str, int], ...]:
        ratios = {
            "silver": pacing.silver_budget_ratio,
            "grain": pacing.grain_budget_ratio,
        }
        return tuple(
            (
                resource,
                max(0, int(self.baseline_for(resource) * ratios[resource]) - self.spent_for(resource)),
            )
            for resource in _ARCHETYPE_BUDGET_RESOURCES
        )

    def consume(self, costs: Mapping[str, Any] | Sequence[tuple[str, Any]]) -> "ArchetypeBudgetState":
        raw_costs = costs if isinstance(costs, Mapping) else dict(costs)
        normalized_costs = dict(_canonical_budget_entries(raw_costs, field="budget costs"))
        spent = {
            resource: self.spent_for(resource) + normalized_costs[resource] for resource in _ARCHETYPE_BUDGET_RESOURCES
        }
        return type(self)(baseline=self.baseline, spent=tuple(spent.items()))

    def to_payload(self) -> dict[str, dict[str, int]]:
        return {
            "baseline": dict(self.baseline),
            "spent": dict(self.spent),
        }


def _bounded_int(value: Any, *, field: str, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ArchetypePacingError(f"{field} must be an integer")
    normalized = int(value)
    if normalized < minimum or (maximum is not None and normalized > maximum):
        bound = f"between {minimum} and {maximum}" if maximum is not None else f">= {minimum}"
        raise ArchetypePacingError(f"{field} must be {bound}")
    return normalized


def _bounded_ratio(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArchetypePacingError(f"{field} must be a finite ratio")
    normalized = float(value)
    if not isfinite(normalized) or normalized < 0.0 or normalized > 1.0:
        raise ArchetypePacingError(f"{field} must be a finite ratio between 0 and 1")
    return round(normalized, 6)


def _string_tuple(value: Any, *, field: str, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ArchetypePacingError(f"{field} must be a list")
    normalized = tuple(str(item).strip() for item in value)
    if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
        raise ArchetypePacingError(f"{field} must contain unique non-empty strings")
    if not allow_empty and not normalized:
        raise ArchetypePacingError(f"{field} must not be empty")
    return normalized


def _pool_weights(value: Any) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, Mapping):
        raise ArchetypePacingError("recruitment_pool_weights must be a mapping")
    normalized: dict[str, int] = {}
    for pool_key in SUPPORTED_RECRUITMENT_POOLS:
        if pool_key not in value:
            raise ArchetypePacingError(f"recruitment_pool_weights is missing {pool_key}")
        normalized[pool_key] = _bounded_int(
            value[pool_key],
            field=f"recruitment_pool_weights.{pool_key}",
            minimum=1,
            maximum=100,
        )
    unknown = set(value) - set(SUPPORTED_RECRUITMENT_POOLS)
    if unknown:
        raise ArchetypePacingError(
            "recruitment_pool_weights contains unsupported pools: " + ", ".join(sorted(map(str, unknown)))
        )
    return tuple((pool_key, normalized[pool_key]) for pool_key in SUPPORTED_RECRUITMENT_POOLS)


@dataclass(frozen=True, slots=True)
class ArchetypePacing:
    """Frozen pacing contract copied into every newly opened ordinary cycle."""

    archetype: str
    slot_interval_minutes: tuple[int, int]
    silver_budget_ratio: float
    grain_budget_ratio: float
    max_parallel_training: int
    high_cost_actions_per_cycle: int
    building_targets: tuple[str, ...]
    technology_targets: tuple[str, ...]
    recruitment_pool_weights: tuple[tuple[str, int], ...]
    schema_version: int = ARCHETYPE_PACING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        archetype = str(self.archetype).strip()
        if archetype not in SUPPORTED_ARCHETYPES:
            raise ArchetypePacingError(f"unsupported virtual-player archetype: {archetype!r}")
        if self.schema_version != ARCHETYPE_PACING_SCHEMA_VERSION:
            raise ArchetypePacingError(f"unsupported archetype pacing schema: {self.schema_version!r}")
        if (
            not isinstance(self.slot_interval_minutes, tuple)
            or len(self.slot_interval_minutes) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in self.slot_interval_minutes)
        ):
            raise ArchetypePacingError("slot_interval_minutes must be a two-item integer tuple")
        minimum, maximum = self.slot_interval_minutes
        if minimum < 10 or maximum > 15 or minimum > maximum:
            raise ArchetypePacingError("slot_interval_minutes must stay within 10..15 minutes")
        object.__setattr__(self, "archetype", archetype)
        object.__setattr__(
            self, "silver_budget_ratio", _bounded_ratio(self.silver_budget_ratio, field="silver_budget_ratio")
        )
        object.__setattr__(
            self, "grain_budget_ratio", _bounded_ratio(self.grain_budget_ratio, field="grain_budget_ratio")
        )
        object.__setattr__(
            self,
            "max_parallel_training",
            _bounded_int(self.max_parallel_training, field="max_parallel_training", minimum=0, maximum=8),
        )
        object.__setattr__(
            self,
            "high_cost_actions_per_cycle",
            _bounded_int(self.high_cost_actions_per_cycle, field="high_cost_actions_per_cycle", minimum=0, maximum=16),
        )
        building_targets = _string_tuple(self.building_targets, field="building_targets")
        unknown_buildings = set(building_targets) - set(VIRTUAL_PLAYER_BUILDING_TARGET_KEYS)
        if unknown_buildings:
            raise ArchetypePacingError(f"building_targets contains unknown keys: {sorted(unknown_buildings)!r}")
        object.__setattr__(self, "building_targets", building_targets)
        technology_targets = _string_tuple(self.technology_targets, field="technology_targets")
        unknown_technologies = set(technology_targets) - set(VIRTUAL_PLAYER_TECHNOLOGY_TARGET_KEYS)
        if unknown_technologies:
            raise ArchetypePacingError(f"technology_targets contains unknown keys: {sorted(unknown_technologies)!r}")
        object.__setattr__(
            self,
            "technology_targets",
            technology_targets,
        )
        object.__setattr__(self, "recruitment_pool_weights", _pool_weights(dict(self.recruitment_pool_weights)))

    @classmethod
    def from_mapping(
        cls,
        archetype: str,
        value: Mapping[str, Any],
        *,
        fallback: "ArchetypePacing | None" = None,
    ) -> "ArchetypePacing":
        if not isinstance(value, Mapping):
            raise ArchetypePacingError("archetype pacing entry must be a mapping")
        base = fallback or DEFAULT_ARCHETYPE_PACING["balanced"]
        interval = value.get("slot_interval_minutes", list(base.slot_interval_minutes))
        if not isinstance(interval, Sequence) or isinstance(interval, (str, bytes)) or len(interval) != 2:
            raise ArchetypePacingError("slot_interval_minutes must be a two-item list")
        return cls(
            archetype=str(archetype),
            slot_interval_minutes=(int(interval[0]), int(interval[1])),
            silver_budget_ratio=value.get("silver_budget_ratio", base.silver_budget_ratio),
            grain_budget_ratio=value.get("grain_budget_ratio", base.grain_budget_ratio),
            max_parallel_training=value.get("max_parallel_training", base.max_parallel_training),
            high_cost_actions_per_cycle=value.get(
                "high_cost_actions_per_cycle",
                base.high_cost_actions_per_cycle,
            ),
            building_targets=value.get("building_targets", list(base.building_targets)),
            technology_targets=value.get("technology_targets", list(base.technology_targets)),
            recruitment_pool_weights=value.get(
                "recruitment_pool_weights",
                dict(base.recruitment_pool_weights),
            ),
            schema_version=value.get("schema_version", ARCHETYPE_PACING_SCHEMA_VERSION),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "archetype": self.archetype,
            "slot_interval_minutes": list(self.slot_interval_minutes),
            "silver_budget_ratio": self.silver_budget_ratio,
            "grain_budget_ratio": self.grain_budget_ratio,
            "max_parallel_training": self.max_parallel_training,
            "high_cost_actions_per_cycle": self.high_cost_actions_per_cycle,
            "building_targets": list(self.building_targets),
            "technology_targets": list(self.technology_targets),
            "recruitment_pool_weights": dict(self.recruitment_pool_weights),
        }


DEFAULT_ARCHETYPE_PACING: dict[str, ArchetypePacing] = {
    "balanced": ArchetypePacing(
        archetype="balanced",
        slot_interval_minutes=(10, 15),
        silver_budget_ratio=0.60,
        grain_budget_ratio=0.60,
        max_parallel_training=1,
        high_cost_actions_per_cycle=4,
        building_targets=("farm", "granary", "tax_office"),
        technology_targets=("architecture", "farming"),
        recruitment_pool_weights=(
            ("dianshi", 1),
            ("xiangshi", 1),
            ("cunmu", 1),
        ),
    ),
    "rich": ArchetypePacing(
        archetype="rich",
        slot_interval_minutes=(12, 15),
        silver_budget_ratio=0.80,
        grain_budget_ratio=0.75,
        max_parallel_training=1,
        high_cost_actions_per_cycle=5,
        building_targets=("tax_office", "silver_vault", "tavern", "treasury"),
        technology_targets=("architecture", "farming"),
        recruitment_pool_weights=(("dianshi", 3), ("xiangshi", 2), ("cunmu", 1)),
    ),
    "dojo": ArchetypePacing(
        archetype="dojo",
        slot_interval_minutes=(10, 12),
        silver_budget_ratio=0.45,
        grain_budget_ratio=0.45,
        max_parallel_training=2,
        high_cost_actions_per_cycle=5,
        building_targets=("forge", "smithy"),
        technology_targets=("forging", "smelting"),
        recruitment_pool_weights=(("dianshi", 1), ("xiangshi", 3), ("cunmu", 2)),
    ),
    "guard": ArchetypePacing(
        archetype="guard",
        slot_interval_minutes=(11, 14),
        silver_budget_ratio=0.50,
        grain_budget_ratio=0.65,
        max_parallel_training=1,
        high_cost_actions_per_cycle=5,
        building_targets=("wall", "arrow_tower", "jiadingfang"),
        technology_targets=("dao_defense", "qiang_defense", "gong_defense"),
        recruitment_pool_weights=(("dianshi", 2), ("xiangshi", 1), ("cunmu", 3)),
    ),
    "abandoned": ArchetypePacing(
        archetype="abandoned",
        slot_interval_minutes=(14, 15),
        silver_budget_ratio=0.20,
        grain_budget_ratio=0.20,
        max_parallel_training=0,
        high_cost_actions_per_cycle=1,
        building_targets=("farm", "granary"),
        technology_targets=("farming",),
        recruitment_pool_weights=(("dianshi", 1), ("xiangshi", 1), ("cunmu", 1)),
    ),
}


def resolve_archetype_pacing(config: Mapping[str, Any], archetype: str) -> ArchetypePacing:
    normalized_archetype = str(archetype).strip()
    try:
        fallback = DEFAULT_ARCHETYPE_PACING[normalized_archetype]
    except KeyError as exc:
        raise ArchetypePacingError(f"unsupported virtual-player archetype: {normalized_archetype!r}") from exc
    projection = config.get("projection") if isinstance(config, Mapping) else None
    raw_by_archetype = projection.get("archetype_pacing") if isinstance(projection, Mapping) else None
    raw = raw_by_archetype.get(normalized_archetype) if isinstance(raw_by_archetype, Mapping) else None
    return fallback if raw is None else ArchetypePacing.from_mapping(normalized_archetype, raw, fallback=fallback)


def pacing_from_cycle_payload(
    payload: Mapping[str, Any] | None,
    *,
    fallback_archetype: str = "balanced",
    config: Mapping[str, Any] | None = None,
) -> ArchetypePacing:
    normalized_fallback = str(fallback_archetype).strip() or "balanced"
    fallback = DEFAULT_ARCHETYPE_PACING.get(normalized_fallback, DEFAULT_ARCHETYPE_PACING["balanced"])
    if config is not None:
        fallback = resolve_archetype_pacing(config, normalized_fallback)
    raw = payload.get("archetype_pacing") if isinstance(payload, Mapping) else None
    if not isinstance(raw, Mapping):
        return fallback
    archetype = str(raw.get("archetype") or normalized_fallback)
    if config is not None:
        fallback = resolve_archetype_pacing(config, archetype)
    else:
        fallback = DEFAULT_ARCHETYPE_PACING.get(archetype, fallback)
    return ArchetypePacing.from_mapping(archetype, raw, fallback=fallback)


__all__ = [
    "ARCHETYPE_PACING_SCHEMA_VERSION",
    "ArchetypeBudgetState",
    "ArchetypePacing",
    "ArchetypePacingError",
    "DEFAULT_ARCHETYPE_PACING",
    "HIGH_COST_ACTION_KINDS",
    "SUPPORTED_ARCHETYPES",
    "SUPPORTED_RECRUITMENT_POOLS",
    "pacing_from_cycle_payload",
    "resolve_archetype_pacing",
]
