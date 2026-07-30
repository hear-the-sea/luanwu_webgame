from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from gameplay.services.runtime_configs import (
    CalibrationRoute,
    RuntimeRoutingError,
    RuntimeRoutingSnapshot,
    read_virtual_player_routing,
)

from .config import VirtualPlayerConfigError, VirtualPlayerV2Config, load_virtual_player_v2_config
from .projection import ProjectionRuleError, ReferenceCandidate
from .reference_snapshot_catalog import ReferenceSnapshotCatalogError, load_configured_reference_snapshot
from .reference_snapshots import build_strength_summary

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ActiveCalibrationReference:
    route: CalibrationRoute
    profile_count: int
    candidates: tuple[ReferenceCandidate, ...]


def _candidate_from_profile(
    profile: Mapping[str, Any],
    *,
    prestige_band: str,
) -> ReferenceCandidate:
    strength = build_strength_summary(
        prestige=int(profile["prestige"]),
        core_building_level=int(profile["core_building_level"]),
        guest_count=int(profile["guest_count"]),
        max_guest_level=int(profile["max_guest_level"]),
        arena_lineup_power=int(profile["arena_lineup_power"]),
        troop_total=int(profile["troop_total"]),
    )
    return ReferenceCandidate(
        business_key=str(profile["business_key"]),
        prestige_band=prestige_band,
        strength=strength,
        features={
            "core_building_level": int(profile["core_building_level"]),
            "guest_count": int(profile["guest_count"]),
            "max_guest_level": int(profile["max_guest_level"]),
        },
    )


def _log_invalid_route(
    *,
    policy_version: int,
    prestige_band: str,
    reason: str,
) -> None:
    logger.warning(
        "Gate D2 calibration route ignored: policy_version=%s prestige_band=%s reason=%s",
        policy_version,
        prestige_band,
        reason,
        extra={
            "event": "virtual_player_calibration_route_ignored",
            "policy_version": policy_version,
            "prestige_band": prestige_band,
            "reason": reason,
        },
    )


def load_active_calibration_reference(
    *,
    policy_version: int,
    policy_checksum: str,
    prestige_band: str,
    config: VirtualPlayerV2Config | None = None,
    routing: RuntimeRoutingSnapshot | None = None,
    required_route: CalibrationRoute | None = None,
) -> ActiveCalibrationReference | None:
    """Resolve one active content-addressed calibration route without writes."""
    try:
        resolved_config = config or load_virtual_player_v2_config()
        if resolved_config is None:
            raise VirtualPlayerConfigError("virtual-player V2 configuration is unavailable")
        resolved_routing = routing or read_virtual_player_routing()
    except (RuntimeRoutingError, VirtualPlayerConfigError) as exc:
        _log_invalid_route(
            policy_version=policy_version,
            prestige_band=prestige_band,
            reason=str(exc),
        )
        return None

    matches = tuple(
        route
        for route in resolved_routing.calibration_routes
        if route.policy_version == int(policy_version) and route.prestige_band == prestige_band
    )
    if not matches:
        return None
    if len(matches) != 1:
        _log_invalid_route(
            policy_version=policy_version,
            prestige_band=prestige_band,
            reason="ambiguous active snapshot versions",
        )
        return None
    route = matches[0]
    if required_route is not None and route != required_route:
        _log_invalid_route(
            policy_version=policy_version,
            prestige_band=prestige_band,
            reason="activation proof changed",
        )
        return None

    try:
        policy = resolved_config.policy(policy_version)
        snapshot_entry = resolved_config.reference_snapshot_catalog[route.reference_snapshot_version]
        evidence_entry = snapshot_entry.gate_d2_evidence[(route.policy_version, route.prestige_band)]
    except (KeyError, VirtualPlayerConfigError) as exc:
        _log_invalid_route(
            policy_version=policy_version,
            prestige_band=prestige_band,
            reason=f"catalog identity is unavailable: {exc}",
        )
        return None

    proof_matches = (
        route.policy_checksum == policy.checksum == str(policy_checksum)
        and route.reference_snapshot_digest == snapshot_entry.digest
        and route.evidence_schema_version == evidence_entry.schema_version
        and route.evidence_digest == evidence_entry.digest
    )
    if not proof_matches:
        _log_invalid_route(
            policy_version=policy_version,
            prestige_band=prestige_band,
            reason="persisted activation proof does not match current catalog",
        )
        return None

    try:
        snapshot = load_configured_reference_snapshot(
            route.reference_snapshot_version,
            config=resolved_config,
        )
        if snapshot.digest != route.reference_snapshot_digest:
            raise ReferenceSnapshotCatalogError("loaded snapshot digest does not match the activation proof")
        band = snapshot.band(prestige_band)
        candidates = tuple(_candidate_from_profile(profile, prestige_band=prestige_band) for profile in band.profiles)
    except (ReferenceSnapshotCatalogError, ProjectionRuleError) as exc:
        _log_invalid_route(
            policy_version=policy_version,
            prestige_band=prestige_band,
            reason=str(exc),
        )
        return None
    if len(candidates) != band.profile_count:
        _log_invalid_route(
            policy_version=policy_version,
            prestige_band=prestige_band,
            reason="snapshot candidate count changed",
        )
        return None
    return ActiveCalibrationReference(
        route=route,
        profile_count=band.profile_count,
        candidates=candidates,
    )


__all__ = [
    "ActiveCalibrationReference",
    "load_active_calibration_reference",
]
