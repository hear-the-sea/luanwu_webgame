from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

from django.conf import settings
from yaml import YAMLError

from core.utils.yaml_loader import load_yaml_data
from core.utils.yaml_validators.virtual_players import validate_virtual_players

from .random_context import canonical_json_bytes

logger = logging.getLogger(__name__)


class VirtualPlayerConfigError(ValueError):
    def __init__(self, message: str, *, errors: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.errors = errors


class RoutingTransitionError(VirtualPlayerConfigError):
    pass


class BootstrapMode(str, Enum):
    LEGACY_BEFORE_GATE = "legacy_before_gate"
    V2_ACTIVE = "v2_active"
    V2_PAUSED = "v2_paused"


class MaintenanceMode(str, Enum):
    LEGACY_BEFORE_GATE = "legacy_before_gate"
    V2_CUTOVER = "v2_cutover"
    V2_ACTIVE = "v2_active"
    V2_PAUSED = "v2_paused"


V2_PRESTIGE_BAND_NAMES = (
    "newbie",
    "junior",
    "middle",
    "senior",
    "veteran",
    "elite",
    "legend",
    "mythic",
)


@dataclass(frozen=True, slots=True)
class PrestigeBandConfig:
    name: str
    lower_inclusive: int
    upper_exclusive: int | None

    def contains(self, prestige: int) -> bool:
        normalized = int(prestige)
        return normalized >= self.lower_inclusive and (
            self.upper_exclusive is None or normalized < self.upper_exclusive
        )


@dataclass(frozen=True, slots=True)
class V2RoutingConfig:
    activation_mode: str
    bootstrap_mode: BootstrapMode
    maintenance_mode: MaintenanceMode


@dataclass(frozen=True, slots=True)
class PolicyRolloutConfig:
    target_version: int
    enabled: bool
    rollout_percent: int


@dataclass(frozen=True, slots=True)
class GateD2EvidenceCatalogEntry:
    policy_version: int
    reference_snapshot_version: int
    prestige_band: str
    schema_version: int
    digest: str


@dataclass(frozen=True, slots=True)
class ReferenceSnapshotCatalogEntry:
    reference_snapshot_version: int
    schema_version: int
    digest: str
    artifact_path: str
    gate_d2_evidence: Mapping[tuple[int, str], GateD2EvidenceCatalogEntry] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True, slots=True)
class BotDevelopmentPolicy:
    version: int
    checksum: str
    payload: Mapping[str, Any]

    @property
    def max_development_actions(self) -> int:
        return int(self.payload["max_development_actions"])

    @property
    def anchor_k(self) -> int:
        return int(self.payload["anchor_k"])

    @property
    def reference_calibration_min_profiles_per_band(self) -> int:
        return int(self.payload["reference_calibration_min_profiles_per_band"])

    @property
    def reference_calibration_thresholds(self) -> Mapping[str, Any]:
        value = self.payload.get("reference_calibration_thresholds")
        if not isinstance(value, Mapping):
            raise VirtualPlayerConfigError("policy reference_calibration_thresholds must be a mapping")
        return value

    @property
    def reference_calibration_archetype_effects(self) -> Mapping[str, Any]:
        value = self.payload.get("reference_calibration_archetype_effects")
        if not isinstance(value, Mapping):
            raise VirtualPlayerConfigError("policy reference_calibration_archetype_effects must be a mapping")
        return value

    @property
    def reference_calibration_abandoned_features(self) -> Mapping[str, Any]:
        value = self.payload.get("reference_calibration_abandoned_features")
        if not isinstance(value, Mapping):
            raise VirtualPlayerConfigError("policy reference_calibration_abandoned_features must be a mapping")
        return value


@dataclass(frozen=True, slots=True)
class VirtualPlayerV2Config:
    environment_mode: str
    engine_version: int
    rng_version: int
    plan_schema_version: int
    band_schema_version: int
    bands: tuple[PrestigeBandConfig, ...]
    routing: V2RoutingConfig
    policy_rollout: PolicyRolloutConfig
    reference_snapshot_catalog: Mapping[int, ReferenceSnapshotCatalogEntry]
    policies: Mapping[int, BotDevelopmentPolicy]

    def policy(self, version: int | None = None) -> BotDevelopmentPolicy:
        normalized_version = self.policy_rollout.target_version if version is None else int(version)
        try:
            return self.policies[normalized_version]
        except KeyError as exc:
            raise VirtualPlayerConfigError(f"Virtual-player policy {normalized_version} is not configured") from exc

    def band_for_prestige(self, prestige: int) -> PrestigeBandConfig:
        normalized = int(prestige)
        if normalized < 0:
            raise VirtualPlayerConfigError("prestige must be non-negative")
        for band in self.bands:
            if band.contains(normalized):
                return band
        raise VirtualPlayerConfigError(f"No V2 prestige band contains {normalized}")


_BOOTSTRAP_MODE_TRANSITIONS = {
    BootstrapMode.LEGACY_BEFORE_GATE: frozenset({BootstrapMode.LEGACY_BEFORE_GATE, BootstrapMode.V2_ACTIVE}),
    BootstrapMode.V2_ACTIVE: frozenset({BootstrapMode.V2_ACTIVE, BootstrapMode.V2_PAUSED}),
    BootstrapMode.V2_PAUSED: frozenset({BootstrapMode.V2_PAUSED, BootstrapMode.V2_ACTIVE}),
}
_MAINTENANCE_MODE_TRANSITIONS = {
    MaintenanceMode.LEGACY_BEFORE_GATE: frozenset({MaintenanceMode.LEGACY_BEFORE_GATE, MaintenanceMode.V2_CUTOVER}),
    MaintenanceMode.V2_CUTOVER: frozenset(
        {MaintenanceMode.V2_CUTOVER, MaintenanceMode.V2_ACTIVE, MaintenanceMode.V2_PAUSED}
    ),
    MaintenanceMode.V2_ACTIVE: frozenset({MaintenanceMode.V2_ACTIVE, MaintenanceMode.V2_PAUSED}),
    MaintenanceMode.V2_PAUSED: frozenset(
        {MaintenanceMode.V2_PAUSED, MaintenanceMode.V2_CUTOVER, MaintenanceMode.V2_ACTIVE}
    ),
}


def _plain_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json_value(item) for item in value]
    return value


def _freeze_config_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_config_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_config_value(item) for item in value)
    return value


def canonical_policy_payload(policy: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _plain_json_value(value) for key, value in policy.items() if key != "checksum"}


def canonical_policy_bytes(policy: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(canonical_policy_payload(policy))


def policy_checksum(policy: Mapping[str, Any]) -> str:
    from hashlib import sha256

    return sha256(canonical_policy_bytes(policy)).hexdigest()


def parse_bot_development_v2(
    value: Mapping[str, Any],
    *,
    source: str = "bot_development_v2",
) -> VirtualPlayerV2Config:
    validated_root = _validated_config({"bot_development_v2": dict(value)}, source=source)
    raw = validated_root["bot_development_v2"]
    segmentation = raw["prestige_segmentation"]
    bands = tuple(
        PrestigeBandConfig(
            name=str(name),
            lower_inclusive=int(bounds[0]),
            upper_exclusive=None if bounds[1] is None else int(bounds[1]),
        )
        for name, bounds in segmentation["v2_bands"].items()
    )
    raw_routing = raw["routing"]
    routing = V2RoutingConfig(
        activation_mode=str(raw_routing["activation_mode"]),
        bootstrap_mode=BootstrapMode(raw_routing["bootstrap_mode"]),
        maintenance_mode=MaintenanceMode(raw_routing["maintenance_mode"]),
    )
    raw_rollout = raw["policy_rollout"]
    rollout = PolicyRolloutConfig(
        target_version=int(raw_rollout["target_version"]),
        enabled=bool(raw_rollout["enabled"]),
        rollout_percent=int(raw_rollout["rollout_percent"]),
    )
    reference_snapshot_catalog: dict[int, ReferenceSnapshotCatalogEntry] = {}
    for raw_version, raw_entry in raw["reference_snapshot_catalog"].items():
        snapshot_version = int(raw_version)
        evidence_entries: dict[tuple[int, str], GateD2EvidenceCatalogEntry] = {}
        for raw_policy_version, raw_bands in raw_entry.get("gate_d2_evidence", {}).items():
            policy_version = int(raw_policy_version)
            for prestige_band, raw_evidence in raw_bands.items():
                evidence_entries[(policy_version, str(prestige_band))] = GateD2EvidenceCatalogEntry(
                    policy_version=policy_version,
                    reference_snapshot_version=snapshot_version,
                    prestige_band=str(prestige_band),
                    schema_version=int(raw_evidence["schema_version"]),
                    digest=str(raw_evidence["digest"]),
                )
        reference_snapshot_catalog[snapshot_version] = ReferenceSnapshotCatalogEntry(
            reference_snapshot_version=snapshot_version,
            schema_version=int(raw_entry["schema_version"]),
            digest=str(raw_entry["digest"]),
            artifact_path=str(raw_entry["artifact_path"]),
            gate_d2_evidence=MappingProxyType(evidence_entries),
        )
    policies: dict[int, BotDevelopmentPolicy] = {}
    for raw_version, raw_policy in raw["policies"].items():
        version = int(raw_version)
        checksum = str(raw_policy["checksum"])
        if policy_checksum(raw_policy) != checksum:
            raise VirtualPlayerConfigError(f"Virtual-player policy {version} checksum changed after validation")
        policies[version] = BotDevelopmentPolicy(
            version=version,
            checksum=checksum,
            payload=_freeze_config_value(canonical_policy_payload(raw_policy)),
        )
    return VirtualPlayerV2Config(
        environment_mode=str(raw["environment_mode"]),
        engine_version=int(raw["engine_version"]),
        rng_version=int(raw["rng_version"]),
        plan_schema_version=int(raw["plan_schema_version"]),
        band_schema_version=int(segmentation["band_schema_version"]),
        bands=bands,
        routing=routing,
        policy_rollout=rollout,
        reference_snapshot_catalog=MappingProxyType(reference_snapshot_catalog),
        policies=MappingProxyType(policies),
    )


def validate_routing_transition(current: V2RoutingConfig, proposed: V2RoutingConfig) -> None:
    if proposed.activation_mode != current.activation_mode:
        raise RoutingTransitionError("activation_mode is immutable")
    if (
        current.bootstrap_mode is BootstrapMode.LEGACY_BEFORE_GATE
        and proposed.maintenance_mode is not current.maintenance_mode
    ):
        raise RoutingTransitionError("Maintenance routing cannot advance before Bootstrap exits Gate D1")
    if proposed.bootstrap_mode not in _BOOTSTRAP_MODE_TRANSITIONS[current.bootstrap_mode]:
        raise RoutingTransitionError(
            f"Illegal Bootstrap routing transition: {current.bootstrap_mode.value} -> {proposed.bootstrap_mode.value}"
        )
    if proposed.maintenance_mode not in _MAINTENANCE_MODE_TRANSITIONS[current.maintenance_mode]:
        raise RoutingTransitionError(
            f"Illegal Maintenance routing transition: {current.maintenance_mode.value} -> {proposed.maintenance_mode.value}"
        )


DEFAULT_VIRTUAL_PLAYER_CONFIG: dict[str, Any] = {
    "enabled": True,
    "population": {
        "active_window_days": 7,
        "region_floor": 8,
        "region_active_multiplier": 8,
        "global_floor": 32,
        "global_active_multiplier": 20,
        "exploration_supply": 0,
        "min_attackable_per_band": 4,
        "rolling_batch_size": [3, 12],
    },
    "prestige_bands": {
        "newbie": [0, 500],
        "junior": [500, 2000],
        "middle": [2000, 8000],
        "senior": [8000, 30000],
        "veteran": [30000, None],
    },
    "lifecycle": {
        "active_days": [30, 90],
        "abandoned_days": [14, 45],
        "next_growth_hours": [2, 18],
        "empty_hit_stale_threshold": 3,
        "empty_hit_window_hours": 24,
        "stale_no_interaction_days": 30,
    },
    "growth": {
        "catch_up_ratio": 0.25,
        "slowing_ratio_multiplier": 0.5,
        "max_building_step": 2,
        "max_guest_level_step": 3,
        "max_prestige_step": 500,
        "stage_caps": {
            "newbie": 3,
            "junior": 6,
            "middle": 10,
            "senior": 15,
            "veteran": 20,
        },
    },
    "resources": {
        "balanced": [0.25, 0.55],
        "rich": [0.55, 0.85],
        "dojo": [0.15, 0.40],
        "guard": [0.20, 0.45],
        "abandoned": [0.65, 0.95],
    },
    "projection": {
        "guest_template_keys": [],
        "gear_template_keys": [],
        "extra_skill_keys": [],
        "extra_skills_per_guest": [0, 0],
        "high_tier_skill_keys": [],
        "high_tier_skill_chance": 0.0,
        "high_tier_skills_per_guest": [1, 1],
        "early_stage_skill_max": 6,
        "early_stage_skill_count": [0, 1],
        "multi_skill_passive_focus_chance": 0.75,
        "troop_template_keys": [],
        "technology_keys": [],
        "gear_max_rarity_by_stage": {
            1: "green",
            7: "blue",
            11: "purple",
            16: "orange",
        },
        "guest_max_rarity_by_stage": {
            1: "green",
            4: "red",
            7: "blue",
            11: "purple",
            16: "orange",
        },
        "real_projection_sample_size": 25,
        "active_sample_days": 30,
        "regional_min_sample_size": 5,
        "strength_quantile_weights": {"p25": 25, "p50": 50, "p75": 25},
        "real_projection_jitter_bps": 500,
        "inventory_template_slots_by_archetype": {
            "balanced": 4,
            "rich": 5,
            "dojo": 3,
            "guard": 3,
            "abandoned": 4,
        },
        "inventory_effect_type_weights": {
            "balanced": {
                "resource_pack": 3,
                "resource": 3,
                "experience_items": 2,
                "medicine": 2,
                "tool": 1,
            },
            "rich": {
                "resource_pack": 4,
                "resource": 5,
                "experience_items": 1,
                "medicine": 1,
                "tool": 1,
            },
            "dojo": {
                "resource_pack": 1,
                "resource": 1,
                "experience_items": 4,
                "medicine": 2,
                "tool": 1,
            },
            "guard": {
                "resource_pack": 2,
                "resource": 2,
                "experience_items": 1,
                "medicine": 4,
                "tool": 1,
            },
            "abandoned": {
                "resource_pack": 3,
                "resource": 3,
                "experience_items": 1,
                "medicine": 1,
                "tool": 1,
            },
        },
        "loot_budget_daily": 2_000_000,
        "loot_limits": {
            "real_attacker_daily_resource_cap": 2_000_000,
        },
        "rare_item_daily_global_cap": 20,
        "powerful_item_daily_global_cap": 5,
        "powerful_item_min_price": 100_000,
        "powerful_item_min_growth_stage": 5,
        "low_stage_powerful_item_chance": 0.03,
        "powerful_item_prestige_chance": [
            {"min_prestige": 0, "chance": 0.0},
            {"min_prestige": 500, "chance": 0.01},
            {"min_prestige": 2000, "chance": 0.03},
            {"min_prestige": 8000, "chance": 0.06},
            {"min_prestige": 30000, "chance": 0.12},
        ],
    },
    "combat_personas": {
        "balanced": {
            "guest_level_multiplier": 1.0,
            "guest_count_multiplier": 1.0,
            "troop_multiplier": 1.0,
        },
        "rich": {
            "guest_level_multiplier": 0.85,
            "guest_count_multiplier": 0.85,
            "troop_multiplier": 0.8,
        },
        "dojo": {
            "guest_level_multiplier": 1.15,
            "guest_count_multiplier": 1.0,
            "troop_multiplier": 0.75,
        },
        "guard": {
            "guest_level_multiplier": 0.85,
            "guest_count_multiplier": 0.85,
            "troop_multiplier": 1.35,
        },
        "abandoned": {
            "guest_level_multiplier": 0.75,
            "guest_count_multiplier": 0.75,
            "troop_multiplier": 0.6,
        },
    },
    "lifecycle_personas": {
        "tourist": {"weight": 15, "active_days": [7, 21], "abandoned_days": [7, 14]},
        "casual": {"weight": 45, "active_days": [30, 90], "abandoned_days": [14, 45]},
        "committed": {
            "weight": 30,
            "active_days": [90, 180],
            "abandoned_days": [30, 60],
        },
        "veteran": {
            "weight": 10,
            "active_days": [180, 360],
            "abandoned_days": [45, 90],
        },
    },
}

VIRTUAL_PLAYER_CONFIG_PATH = Path(settings.BASE_DIR) / "data" / "virtual_players.yaml"


def _deep_merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = {key: dict(value) if isinstance(value, dict) else value for key, value in base.items()}
    for section, values in override.items():
        if isinstance(values, dict) and isinstance(merged.get(section), dict):
            merged[section] = _deep_merge_config(merged[section], values)
        else:
            merged[section] = values
    return merged


def _validated_config(config: Any, *, source: str) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise VirtualPlayerConfigError(f"{source} must contain a mapping")
    result = validate_virtual_players(config, file=source)
    if result.is_valid:
        return config
    errors = tuple(str(error) for error in result.errors)
    raise VirtualPlayerConfigError(
        f"Invalid virtual-player config from {source}: {'; '.join(errors)}",
        errors=errors,
    )


@lru_cache(maxsize=1)
def _load_virtual_player_config_from_disk() -> dict[str, Any]:
    try:
        raw = load_yaml_data(
            VIRTUAL_PLAYER_CONFIG_PATH,
            logger=logger,
            context="virtual player config",
            default={},
            raise_on_error=True,
        )
    except (OSError, UnicodeDecodeError, YAMLError) as exc:
        raise VirtualPlayerConfigError(
            f"Unable to load virtual-player config from {VIRTUAL_PLAYER_CONFIG_PATH}"
        ) from exc
    merged = _deep_merge_config(
        DEFAULT_VIRTUAL_PLAYER_CONFIG, _validated_config(raw, source=str(VIRTUAL_PLAYER_CONFIG_PATH))
    )
    return _validated_config(merged, source=str(VIRTUAL_PLAYER_CONFIG_PATH))


def clear_virtual_player_config_cache() -> None:
    _load_virtual_player_config_from_disk.cache_clear()
    from .bootstrap_catalog import clear_bootstrap_catalog_cache

    clear_bootstrap_catalog_cache()


def load_virtual_player_config() -> dict[str, Any]:
    config = _load_virtual_player_config_from_disk()
    configured = getattr(settings, "VIRTUAL_PLAYER_CONFIG", None)
    if configured is None or configured == {}:
        return config
    if not isinstance(configured, dict):
        raise VirtualPlayerConfigError("settings.VIRTUAL_PLAYER_CONFIG must be a mapping")
    merged = _deep_merge_config(config, configured)
    return _validated_config(merged, source="settings.VIRTUAL_PLAYER_CONFIG")


def load_virtual_player_v2_config() -> VirtualPlayerV2Config | None:
    raw = load_virtual_player_config().get("bot_development_v2")
    if raw is None:
        return None
    return parse_bot_development_v2(raw, source=str(VIRTUAL_PLAYER_CONFIG_PATH))


__all__ = [
    "BootstrapMode",
    "BotDevelopmentPolicy",
    "DEFAULT_VIRTUAL_PLAYER_CONFIG",
    "MaintenanceMode",
    "PolicyRolloutConfig",
    "PrestigeBandConfig",
    "ReferenceSnapshotCatalogEntry",
    "RoutingTransitionError",
    "V2RoutingConfig",
    "VirtualPlayerConfigError",
    "VirtualPlayerV2Config",
    "V2_PRESTIGE_BAND_NAMES",
    "canonical_policy_bytes",
    "canonical_policy_payload",
    "clear_virtual_player_config_cache",
    "load_virtual_player_config",
    "load_virtual_player_v2_config",
    "parse_bot_development_v2",
    "policy_checksum",
    "validate_routing_transition",
]
