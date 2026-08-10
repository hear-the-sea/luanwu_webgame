"""Versioned Arena strength envelopes for normal virtual-player supply."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from gameplay.services.virtual_player_core.config import (
    VirtualPlayerV2Config,
    load_virtual_player_v2_config,
    policy_checksum,
)
from gameplay.services.virtual_player_core.random_context import canonical_json_bytes

from .virtual_lineups import MIN_LINEUP_POWER_PERCENT

ARENA_TRAINING_POLICY_SCHEMA_VERSION = 2


class ArenaTrainingPolicyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ArenaStrengthEnvelope:
    segment: str
    ready_power_min: int
    ready_power_max: int | None
    supply_prestige_band_priority: tuple[str, ...]

    def matches(self, ready_power: int) -> bool:
        return int(ready_power) >= self.ready_power_min and (
            self.ready_power_max is None or int(ready_power) <= self.ready_power_max
        )

    def payload(self) -> dict[str, object]:
        return {
            "segment": self.segment,
            "ready_power_min": self.ready_power_min,
            "ready_power_max": self.ready_power_max,
            "supply_prestige_band_priority": list(self.supply_prestige_band_priority),
        }

    @property
    def digest(self) -> str:
        return sha256(canonical_json_bytes(self.payload())).hexdigest()

    @property
    def supply_prestige_band(self) -> str:
        """Return the primary band used for population materialization."""

        return self.supply_prestige_band_priority[0]


@dataclass(frozen=True, slots=True)
class ArenaTrainingPolicy:
    version: int
    checksum: str
    envelopes: tuple[ArenaStrengthEnvelope, ...]

    def envelope_for_ready_power(self, ready_power: int) -> ArenaStrengthEnvelope | None:
        for envelope in self.envelopes:
            if envelope.matches(ready_power):
                return envelope
        return None


@dataclass(frozen=True, slots=True)
class ArenaTrainingPolicyDecision:
    policy_version: int
    policy_checksum: str
    required_ready_power: int
    envelope: ArenaStrengthEnvelope | None
    supply_prestige: int
    reason: str = ""

    @property
    def available(self) -> bool:
        return self.envelope is not None and not self.reason

    @property
    def strength_segment(self) -> str:
        return "" if self.envelope is None else self.envelope.segment

    @property
    def envelope_digest(self) -> str:
        return "" if self.envelope is None else self.envelope.digest

    @property
    def supply_prestige_band(self) -> str:
        return "" if self.envelope is None else self.envelope.supply_prestige_band

    @property
    def supply_prestige_band_priority(self) -> tuple[str, ...]:
        return () if self.envelope is None else self.envelope.supply_prestige_band_priority


def _mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ArenaTrainingPolicyError(f"{field} must be a mapping")
    return {str(key): item for key, item in value.items()}


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ArenaTrainingPolicyError(f"{field} must be a positive integer")
    return int(value)


def _non_negative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ArenaTrainingPolicyError(f"{field} must be a non-negative integer")
    return int(value)


def _required_ready_power(target_team_power: int) -> int:
    target = _positive_int(target_team_power, field="target_team_power")
    return (target * MIN_LINEUP_POWER_PERCENT + 99) // 100


def parse_arena_training_policy(
    value: object,
    *,
    config: VirtualPlayerV2Config,
) -> ArenaTrainingPolicy:
    raw = _mapping(value, field="arena_training_policy")
    expected_fields = frozenset({"schema_version", "version", "checksum", "envelopes"})
    if frozenset(raw) != expected_fields:
        raise ArenaTrainingPolicyError("arena_training_policy has invalid fields")
    if raw["schema_version"] != ARENA_TRAINING_POLICY_SCHEMA_VERSION:
        raise ArenaTrainingPolicyError("unsupported arena training policy schema version")
    version = _positive_int(raw["version"], field="arena_training_policy.version")
    checksum = raw["checksum"]
    if not isinstance(checksum, str) or len(checksum) != 64:
        raise ArenaTrainingPolicyError("arena_training_policy.checksum must be a SHA-256 digest")
    calculated_checksum = policy_checksum(raw)
    if checksum != calculated_checksum:
        raise ArenaTrainingPolicyError("arena_training_policy checksum does not match canonical payload")
    raw_envelopes = _mapping(raw["envelopes"], field="arena_training_policy.envelopes")
    if not raw_envelopes:
        raise ArenaTrainingPolicyError("arena_training_policy.envelopes must not be empty")

    available_bands = {band.name: band for band in config.bands}
    envelopes: list[ArenaStrengthEnvelope] = []
    for segment, raw_envelope in raw_envelopes.items():
        normalized_segment = str(segment).strip()
        if not normalized_segment:
            raise ArenaTrainingPolicyError("arena training envelope segment must be non-empty")
        envelope_config = _mapping(raw_envelope, field=f"arena_training_policy.envelopes.{normalized_segment}")
        expected_envelope_fields = frozenset({"ready_power_range", "supply_prestige_band_priority"})
        if frozenset(envelope_config) != expected_envelope_fields:
            raise ArenaTrainingPolicyError(f"arena training envelope {normalized_segment!r} has invalid fields")
        raw_range = envelope_config["ready_power_range"]
        if not isinstance(raw_range, (list, tuple)) or len(raw_range) != 2:
            raise ArenaTrainingPolicyError(
                f"arena training envelope {normalized_segment!r} ready_power_range must have two values"
            )
        lower = _non_negative_int(raw_range[0], field=f"{normalized_segment}.ready_power_range[0]")
        upper = raw_range[1]
        if upper is not None:
            upper = _non_negative_int(upper, field=f"{normalized_segment}.ready_power_range[1]")
            if upper < lower:
                raise ArenaTrainingPolicyError(
                    f"arena training envelope {normalized_segment!r} ready power range is inverted"
                )
        raw_supply_priority = envelope_config["supply_prestige_band_priority"]
        if (
            isinstance(raw_supply_priority, str)
            or not isinstance(raw_supply_priority, (list, tuple))
            or not raw_supply_priority
        ):
            raise ArenaTrainingPolicyError(
                f"arena training envelope {normalized_segment!r} must define a non-empty supply prestige priority"
            )
        supply_priority = tuple(str(item).strip() for item in raw_supply_priority)
        if any(not band or band not in available_bands for band in supply_priority) or len(set(supply_priority)) != len(
            supply_priority
        ):
            raise ArenaTrainingPolicyError(
                f"arena training envelope {normalized_segment!r} has an invalid supply prestige priority"
            )
        envelopes.append(
            ArenaStrengthEnvelope(
                segment=normalized_segment,
                ready_power_min=lower,
                ready_power_max=upper,
                supply_prestige_band_priority=supply_priority,
            )
        )

    envelopes.sort(
        key=lambda item: (
            item.ready_power_min,
            item.ready_power_max is None,
            0 if item.ready_power_max is None else item.ready_power_max,
            item.segment,
        )
    )
    previous_upper: int | None = None
    for index, strength_envelope in enumerate(envelopes):
        if previous_upper is None and index != 0:
            raise ArenaTrainingPolicyError("only the final arena training envelope may be open ended")
        if previous_upper is not None and strength_envelope.ready_power_min <= previous_upper:
            raise ArenaTrainingPolicyError("arena training envelope ranges must not overlap")
        previous_upper = strength_envelope.ready_power_max
    return ArenaTrainingPolicy(
        version=version,
        checksum=checksum,
        envelopes=tuple(envelopes),
    )


def resolve_configured_arena_training_policy(*, target_team_power: int) -> ArenaTrainingPolicyDecision:
    required_ready_power = _required_ready_power(target_team_power)
    config = load_virtual_player_v2_config()
    if config is None or config.arena_training_policy is None:
        return ArenaTrainingPolicyDecision(
            policy_version=0,
            policy_checksum="",
            required_ready_power=required_ready_power,
            envelope=None,
            supply_prestige=0,
            reason="arena_training_policy_unavailable",
        )
    try:
        policy = parse_arena_training_policy(config.arena_training_policy, config=config)
    except ArenaTrainingPolicyError:
        return ArenaTrainingPolicyDecision(
            policy_version=0,
            policy_checksum="",
            required_ready_power=required_ready_power,
            envelope=None,
            supply_prestige=0,
            reason="arena_training_policy_unavailable",
        )
    envelope = policy.envelope_for_ready_power(required_ready_power)
    if envelope is None:
        return ArenaTrainingPolicyDecision(
            policy_version=policy.version,
            policy_checksum=policy.checksum,
            required_ready_power=required_ready_power,
            envelope=None,
            supply_prestige=0,
            reason="arena_strength_envelope_mismatch",
        )
    supply_band = next(band for band in config.bands if band.name == envelope.supply_prestige_band)
    return ArenaTrainingPolicyDecision(
        policy_version=policy.version,
        policy_checksum=policy.checksum,
        required_ready_power=required_ready_power,
        envelope=envelope,
        supply_prestige=supply_band.lower_inclusive,
    )


def demand_uses_arena_training_policy(demand: object) -> bool:
    return int(getattr(demand, "arena_training_policy_version", 0) or 0) >= 1


def demand_supply_prestige_band(demand: object) -> str | None:
    if not demand_uses_arena_training_policy(demand):
        return None
    value = str(getattr(demand, "arena_supply_prestige_band", "") or "").strip()
    return value or None


def demand_supply_prestige_band_priority(demand: object) -> tuple[str, ...] | None:
    """Read the immutable per-demand borrowing order without consulting live config."""

    if not demand_uses_arena_training_policy(demand):
        return None
    raw_priority = getattr(demand, "arena_supply_prestige_band_priority", None)
    if isinstance(raw_priority, str) or not isinstance(raw_priority, (list, tuple)) or not raw_priority:
        return None
    priority = tuple(str(item).strip() for item in raw_priority)
    primary_band = demand_supply_prestige_band(demand)
    if (
        any(not band for band in priority)
        or len(set(priority)) != len(priority)
        or primary_band is None
        or priority[0] != primary_band
    ):
        return None
    return priority


def demand_supply_prestige(demand: object) -> int | None:
    if not demand_uses_arena_training_policy(demand):
        return None
    value = getattr(demand, "arena_supply_prestige", None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return int(value)


__all__ = [
    "ARENA_TRAINING_POLICY_SCHEMA_VERSION",
    "ArenaStrengthEnvelope",
    "ArenaTrainingPolicy",
    "ArenaTrainingPolicyDecision",
    "ArenaTrainingPolicyError",
    "demand_supply_prestige",
    "demand_supply_prestige_band",
    "demand_supply_prestige_band_priority",
    "demand_uses_arena_training_policy",
    "parse_arena_training_policy",
    "resolve_configured_arena_training_policy",
]
