from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import BooleanField, Exists, Value

from gameplay.models import BotRuntimeRoutingState
from gameplay.services.virtual_player_core.config import (
    V2_PRESTIGE_BAND_NAMES,
    BootstrapMode,
    MaintenanceMode,
    PolicyRolloutConfig,
    V2RoutingConfig,
    VirtualPlayerConfigError,
    load_virtual_player_v2_config,
    validate_routing_transition,
)


class RuntimeRoutingError(ValueError):
    pass


class RuntimeRoutingUnavailable(RuntimeRoutingError):
    pass


class RuntimeRoutingConflict(RuntimeRoutingError):
    pass


class RuntimeRoutingGateBlocked(RuntimeRoutingError):
    pass


@dataclass(frozen=True, order=True, slots=True)
class CalibrationRouteTarget:
    policy_version: int
    reference_snapshot_version: int
    prestige_band: str

    def to_payload(self) -> dict[str, int | str]:
        return {
            "policy_version": self.policy_version,
            "reference_snapshot_version": self.reference_snapshot_version,
            "prestige_band": self.prestige_band,
        }


@dataclass(frozen=True, order=True, slots=True)
class CalibrationRoute:
    policy_version: int
    reference_snapshot_version: int
    prestige_band: str
    policy_checksum: str
    reference_snapshot_digest: str
    evidence_schema_version: int
    evidence_digest: str

    @property
    def target(self) -> CalibrationRouteTarget:
        return CalibrationRouteTarget(
            policy_version=self.policy_version,
            reference_snapshot_version=self.reference_snapshot_version,
            prestige_band=self.prestige_band,
        )

    def to_payload(self) -> dict[str, int | str]:
        return {
            **self.target.to_payload(),
            "policy_checksum": self.policy_checksum,
            "reference_snapshot_digest": self.reference_snapshot_digest,
            "evidence_schema_version": self.evidence_schema_version,
            "evidence_digest": self.evidence_digest,
        }


@dataclass(frozen=True, slots=True)
class RuntimeRoutingSnapshot:
    bootstrap_mode: BootstrapMode
    maintenance_mode: MaintenanceMode
    calibration_routes: tuple[CalibrationRoute, ...]
    revision: int | None
    last_hourly_safety_window_end_at: datetime | None
    last_daily_safety_window_end_at: datetime | None
    last_pause_window_id: str
    pause_reason: str
    paused_from_maintenance_mode: str
    persisted: bool


def runtime_routing_guard_expression(
    snapshot: RuntimeRoutingSnapshot,
) -> Exists | Value:
    if not snapshot.persisted or snapshot.revision is None:
        return Value(False, output_field=BooleanField())
    return Exists(
        BotRuntimeRoutingState.objects.filter(
            key=BotRuntimeRoutingState.GLOBAL_KEY,
            bootstrap_mode=snapshot.bootstrap_mode.value,
            maintenance_mode=snapshot.maintenance_mode.value,
            calibration_routes=[route.to_payload() for route in snapshot.calibration_routes],
            revision=snapshot.revision,
        )
    )


@dataclass(frozen=True, slots=True)
class RuntimeRoutingTransitionResult:
    snapshot: RuntimeRoutingSnapshot
    changed: bool
    initialized: bool


@dataclass(frozen=True, slots=True)
class RuntimeRoutingOperationSummary:
    scanned: int
    locked: int
    changed: int
    skipped: int
    failed: int
    reasons: tuple[str, ...]
    snapshot: RuntimeRoutingSnapshot


@dataclass(frozen=True, slots=True)
class SafetyRoutingDecisionResult:
    snapshot: RuntimeRoutingSnapshot
    consumed: bool
    paused: bool
    window_id: str
    window_kind: str
    resumed: bool = False


@dataclass(frozen=True, slots=True)
class PolicyRolloutSnapshot:
    target_version: int
    enabled: bool
    rollout_percent: int
    revision: int | None
    persisted: bool


@dataclass(frozen=True, slots=True)
class PolicyRolloutTransitionResult:
    snapshot: PolicyRolloutSnapshot
    changed: bool


@dataclass(frozen=True, slots=True)
class PolicyRolloutOperationSummary:
    scanned: int
    locked: int
    changed: int
    skipped: int
    failed: int
    reasons: tuple[str, ...]
    snapshot: PolicyRolloutSnapshot


_CALIBRATION_ROUTE_TARGET_FIELDS = frozenset({"policy_version", "reference_snapshot_version", "prestige_band"})
_CALIBRATION_ROUTE_FIELDS = frozenset(
    {
        *_CALIBRATION_ROUTE_TARGET_FIELDS,
        "policy_checksum",
        "reference_snapshot_digest",
        "evidence_schema_version",
        "evidence_digest",
    }
)
_BAND_ORDINAL = {band: index for index, band in enumerate(V2_PRESTIGE_BAND_NAMES)}
_SAFETY_WINDOW_KINDS = frozenset({"hourly", "daily"})


def _strict_positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RuntimeRoutingError(f"{field} must be a positive integer")
    return value


def normalize_policy_rollout(
    *,
    target_version: Any,
    enabled: Any,
    rollout_percent: Any,
) -> PolicyRolloutConfig:
    normalized_target = _strict_positive_int(
        target_version,
        field="policy_rollout.target_version",
    )
    if not isinstance(enabled, bool):
        raise RuntimeRoutingError("policy_rollout.enabled must be a boolean")
    if (
        isinstance(rollout_percent, bool)
        or not isinstance(rollout_percent, int)
        or rollout_percent < 0
        or rollout_percent > 100
    ):
        raise RuntimeRoutingError("policy_rollout.rollout_percent must be between 0 and 100")
    if enabled and rollout_percent == 0:
        raise RuntimeRoutingError("policy_rollout.rollout_percent must be positive while enabled")
    if not enabled and rollout_percent != 0:
        raise RuntimeRoutingError("policy_rollout.rollout_percent must be 0 while disabled")
    return PolicyRolloutConfig(
        target_version=normalized_target,
        enabled=enabled,
        rollout_percent=rollout_percent,
    )


def _route_sort_key(
    route: CalibrationRoute | CalibrationRouteTarget,
) -> tuple[int, int, int]:
    return (
        route.policy_version,
        route.reference_snapshot_version,
        _BAND_ORDINAL[route.prestige_band],
    )


def normalize_calibration_routes(value: Any) -> tuple[CalibrationRouteTarget, ...]:
    if not isinstance(value, (list, tuple)):
        raise RuntimeRoutingError("calibration_routes must be a list")
    routes: list[CalibrationRouteTarget] = []
    seen: set[tuple[int, int, str]] = set()
    for index, raw in enumerate(value):
        if isinstance(raw, CalibrationRoute):
            route = raw.target
        elif isinstance(raw, CalibrationRouteTarget):
            route = raw
        else:
            if not isinstance(raw, Mapping):
                raise RuntimeRoutingError(f"calibration_routes[{index}] must be a mapping")
            fields = set(raw)
            if fields != _CALIBRATION_ROUTE_TARGET_FIELDS:
                missing = sorted(_CALIBRATION_ROUTE_TARGET_FIELDS - fields)
                unknown = sorted(fields - _CALIBRATION_ROUTE_TARGET_FIELDS)
                details = []
                if missing:
                    details.append(f"missing {', '.join(missing)}")
                if unknown:
                    details.append(f"unknown {', '.join(unknown)}")
                raise RuntimeRoutingError(f"calibration_routes[{index}] has {'; '.join(details)}")
            band = raw["prestige_band"]
            if not isinstance(band, str) or band not in _BAND_ORDINAL:
                raise RuntimeRoutingError(f"calibration_routes[{index}].prestige_band is invalid")
            route = CalibrationRouteTarget(
                policy_version=_strict_positive_int(
                    raw["policy_version"],
                    field=f"calibration_routes[{index}].policy_version",
                ),
                reference_snapshot_version=_strict_positive_int(
                    raw["reference_snapshot_version"],
                    field=f"calibration_routes[{index}].reference_snapshot_version",
                ),
                prestige_band=band,
            )
        key = (
            route.policy_version,
            route.reference_snapshot_version,
            route.prestige_band,
        )
        if key in seen:
            raise RuntimeRoutingError(f"calibration_routes contains duplicate {key!r}")
        seen.add(key)
        routes.append(route)
    return tuple(sorted(routes, key=_route_sort_key))


def _lower_sha256(value: Any, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RuntimeRoutingError(f"{field} must be a lowercase SHA-256 digest")
    return value


def parse_calibration_routes(value: Any) -> tuple[CalibrationRoute, ...]:
    if not isinstance(value, list):
        raise RuntimeRoutingError("persisted calibration_routes must be a JSON list")
    routes: list[CalibrationRoute] = []
    seen: set[tuple[int, int, str]] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise RuntimeRoutingError(f"persisted calibration_routes[{index}] must be a mapping")
        fields = set(raw)
        if fields != _CALIBRATION_ROUTE_FIELDS:
            missing = sorted(_CALIBRATION_ROUTE_FIELDS - fields)
            unknown = sorted(fields - _CALIBRATION_ROUTE_FIELDS)
            details: list[str] = []
            if missing:
                details.append(f"missing {', '.join(missing)}")
            if unknown:
                details.append(f"unknown {', '.join(unknown)}")
            raise RuntimeRoutingError(f"persisted calibration_routes[{index}] has {'; '.join(details)}")
        prestige_band = raw["prestige_band"]
        if not isinstance(prestige_band, str) or prestige_band not in _BAND_ORDINAL:
            raise RuntimeRoutingError(f"persisted calibration_routes[{index}].prestige_band is invalid")
        route = CalibrationRoute(
            policy_version=_strict_positive_int(
                raw["policy_version"],
                field=f"persisted calibration_routes[{index}].policy_version",
            ),
            reference_snapshot_version=_strict_positive_int(
                raw["reference_snapshot_version"],
                field=("persisted calibration_routes" f"[{index}].reference_snapshot_version"),
            ),
            prestige_band=prestige_band,
            policy_checksum=_lower_sha256(
                raw["policy_checksum"],
                field=f"persisted calibration_routes[{index}].policy_checksum",
            ),
            reference_snapshot_digest=_lower_sha256(
                raw["reference_snapshot_digest"],
                field=("persisted calibration_routes" f"[{index}].reference_snapshot_digest"),
            ),
            evidence_schema_version=_strict_positive_int(
                raw["evidence_schema_version"],
                field=("persisted calibration_routes" f"[{index}].evidence_schema_version"),
            ),
            evidence_digest=_lower_sha256(
                raw["evidence_digest"],
                field=f"persisted calibration_routes[{index}].evidence_digest",
            ),
        )
        key = (
            route.policy_version,
            route.reference_snapshot_version,
            route.prestige_band,
        )
        if key in seen:
            raise RuntimeRoutingError(f"persisted calibration_routes contains duplicate {key!r}")
        seen.add(key)
        routes.append(route)
    routes = sorted(routes, key=_route_sort_key)
    if [route.to_payload() for route in routes] != value:
        raise RuntimeRoutingError("persisted calibration_routes is not in canonical order")
    return tuple(routes)


def _snapshot(state: BotRuntimeRoutingState) -> RuntimeRoutingSnapshot:
    try:
        bootstrap_mode = BootstrapMode(state.bootstrap_mode)
        maintenance_mode = MaintenanceMode(state.maintenance_mode)
    except ValueError as exc:
        raise RuntimeRoutingUnavailable("persisted virtual-player routing mode is invalid") from exc
    try:
        routes = parse_calibration_routes(state.calibration_routes)
    except RuntimeRoutingError as exc:
        raise RuntimeRoutingUnavailable(str(exc)) from exc
    return RuntimeRoutingSnapshot(
        bootstrap_mode=bootstrap_mode,
        maintenance_mode=maintenance_mode,
        calibration_routes=routes,
        revision=int(state.revision),
        last_hourly_safety_window_end_at=state.last_hourly_safety_window_end_at,
        last_daily_safety_window_end_at=state.last_daily_safety_window_end_at,
        last_pause_window_id=state.last_pause_window_id,
        pause_reason=state.pause_reason,
        paused_from_maintenance_mode=state.paused_from_maintenance_mode,
        persisted=True,
    )


def _policy_rollout_snapshot(
    state: BotRuntimeRoutingState,
) -> PolicyRolloutSnapshot:
    try:
        rollout = normalize_policy_rollout(
            target_version=state.policy_rollout_target_version,
            enabled=state.policy_rollout_enabled,
            rollout_percent=state.policy_rollout_percent,
        )
    except RuntimeRoutingError as exc:
        raise RuntimeRoutingUnavailable(f"persisted virtual-player {exc}") from exc
    return PolicyRolloutSnapshot(
        target_version=rollout.target_version,
        enabled=rollout.enabled,
        rollout_percent=rollout.rollout_percent,
        revision=int(state.revision),
        persisted=True,
    )


def read_virtual_player_routing() -> RuntimeRoutingSnapshot:
    state = BotRuntimeRoutingState.objects.filter(key=BotRuntimeRoutingState.GLOBAL_KEY).first()
    if state is not None:
        return _snapshot(state)
    return _missing_virtual_player_routing_snapshot()


def read_virtual_player_policy_rollout() -> PolicyRolloutSnapshot:
    state = BotRuntimeRoutingState.objects.filter(key=BotRuntimeRoutingState.GLOBAL_KEY).first()
    if state is not None:
        return _policy_rollout_snapshot(state)
    _missing_virtual_player_routing_snapshot()
    config = load_virtual_player_v2_config()
    target_version = 1 if config is None else config.policy_rollout.target_version
    return PolicyRolloutSnapshot(
        target_version=target_version,
        enabled=False,
        rollout_percent=0,
        revision=None,
        persisted=False,
    )


def lock_virtual_player_routing() -> RuntimeRoutingSnapshot:
    """Lock persisted routing for a write transaction and return its snapshot."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("lock_virtual_player_routing must be called inside transaction.atomic()")
    state = BotRuntimeRoutingState.objects.select_for_update().filter(key=BotRuntimeRoutingState.GLOBAL_KEY).first()
    if state is not None:
        return _snapshot(state)
    return _missing_virtual_player_routing_snapshot()


def lock_virtual_player_policy_rollout() -> PolicyRolloutSnapshot:
    """Lock the shared routing row before a policy-rollout write batch."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("lock_virtual_player_policy_rollout must be called inside transaction.atomic()")
    state = BotRuntimeRoutingState.objects.select_for_update().filter(key=BotRuntimeRoutingState.GLOBAL_KEY).first()
    if state is None:
        _missing_virtual_player_routing_snapshot()
        raise RuntimeRoutingUnavailable("routing state must be initialized before policy rollout")
    return _policy_rollout_snapshot(state)


def _missing_virtual_player_routing_snapshot() -> RuntimeRoutingSnapshot:
    from gameplay.services.virtual_player_core.profile_store import any_v2_profiles_exist

    if any_v2_profiles_exist():
        raise RuntimeRoutingUnavailable("routing state is missing after V2 enrollment")
    return RuntimeRoutingSnapshot(
        bootstrap_mode=BootstrapMode.LEGACY_BEFORE_GATE,
        maintenance_mode=MaintenanceMode.LEGACY_BEFORE_GATE,
        calibration_routes=(),
        revision=None,
        last_hourly_safety_window_end_at=None,
        last_daily_safety_window_end_at=None,
        last_pause_window_id="",
        pause_reason="",
        paused_from_maintenance_mode="",
        persisted=False,
    )


def _assert_expected_state(
    state: BotRuntimeRoutingState,
    *,
    expected_revision: int,
    expected_bootstrap_mode: BootstrapMode | None,
    expected_maintenance_mode: MaintenanceMode | None,
) -> None:
    if state.revision != expected_revision:
        raise RuntimeRoutingConflict(f"routing revision changed: expected {expected_revision}, found {state.revision}")
    if expected_bootstrap_mode is not None and state.bootstrap_mode != expected_bootstrap_mode.value:
        raise RuntimeRoutingConflict(
            f"bootstrap mode changed: expected {expected_bootstrap_mode.value}, found {state.bootstrap_mode}"
        )
    if expected_maintenance_mode is not None and state.maintenance_mode != expected_maintenance_mode.value:
        raise RuntimeRoutingConflict(
            f"maintenance mode changed: expected {expected_maintenance_mode.value}, found {state.maintenance_mode}"
        )


def _preflight_new_calibration_routes(
    proposed_targets: tuple[CalibrationRouteTarget, ...],
    *,
    expected_revision: int | None,
) -> tuple[Any | None, dict[CalibrationRouteTarget, Any]]:
    """Validate content-addressed evidence before the routing row is locked."""
    if not proposed_targets or expected_revision is None:
        return None, {}
    state = BotRuntimeRoutingState.objects.filter(key=BotRuntimeRoutingState.GLOBAL_KEY).first()
    if state is None or state.revision != expected_revision:
        return None, {}
    current_targets = {route.target for route in _snapshot(state).calibration_routes}
    newly_enabled = tuple(target for target in proposed_targets if target not in current_targets)
    if not newly_enabled:
        return None, {}

    from gameplay.services.virtual_player_core.calibration import CalibrationUnit
    from gameplay.services.virtual_player_core.gate_d2_acceptance_workflow import (
        GateD2AcceptanceError,
        evaluate_gate_d2_acceptance,
    )

    try:
        v2_config = load_virtual_player_v2_config()
        if v2_config is None:
            raise VirtualPlayerConfigError("virtual-player V2 configuration is unavailable")
    except VirtualPlayerConfigError as exc:
        raise RuntimeRoutingGateBlocked(str(exc)) from exc

    prepared: dict[CalibrationRouteTarget, Any] = {}
    for target in newly_enabled:
        unit = CalibrationUnit(
            policy_version=target.policy_version,
            reference_snapshot_version=target.reference_snapshot_version,
            prestige_band=target.prestige_band,
        )
        try:
            acceptance = evaluate_gate_d2_acceptance(unit, config=v2_config)
        except (GateD2AcceptanceError, VirtualPlayerConfigError) as exc:
            raise RuntimeRoutingGateBlocked(
                "calibration route lacks valid Gate D2 evidence: " f"{target.to_payload()}: {exc}"
            ) from exc
        if not acceptance.passed:
            reasons = ", ".join(acceptance.verdict.reason_codes) or (acceptance.verdict.status.value)
            raise RuntimeRoutingGateBlocked(
                "calibration route Gate D2 evidence did not pass: " f"{target.to_payload()}: {reasons}"
            )
        prepared[target] = acceptance
    return v2_config, prepared


@transaction.atomic
def _transition_virtual_player_routing(
    *,
    expected_revision: int | None,
    bootstrap_mode: BootstrapMode | str,
    maintenance_mode: MaintenanceMode | str,
    calibration_routes: Iterable[CalibrationRoute | CalibrationRouteTarget | Mapping[str, Any]] = (),
    expected_bootstrap_mode: BootstrapMode | str | None = None,
    expected_maintenance_mode: MaintenanceMode | str | None = None,
    gate_d1_ready: bool = False,
    gate_e_ready: bool = False,
    pause_reason: str = "",
    apply: bool,
) -> RuntimeRoutingTransitionResult:
    proposed_bootstrap = BootstrapMode(bootstrap_mode)
    proposed_maintenance = MaintenanceMode(maintenance_mode)
    proposed_targets = normalize_calibration_routes(list(calibration_routes))
    expected_bootstrap = None if expected_bootstrap_mode is None else BootstrapMode(expected_bootstrap_mode)
    expected_maintenance = None if expected_maintenance_mode is None else MaintenanceMode(expected_maintenance_mode)
    preflight_config, preflight_acceptances = _preflight_new_calibration_routes(
        proposed_targets,
        expected_revision=expected_revision,
    )

    state = BotRuntimeRoutingState.objects.select_for_update().filter(key=BotRuntimeRoutingState.GLOBAL_KEY).first()
    if state is None:
        if expected_revision is not None:
            raise RuntimeRoutingConflict("routing state is absent")
        if proposed_bootstrap is not BootstrapMode.LEGACY_BEFORE_GATE:
            raise RuntimeRoutingGateBlocked("initial routing bootstrap mode must be legacy_before_gate")
        if proposed_maintenance is not MaintenanceMode.LEGACY_BEFORE_GATE:
            raise RuntimeRoutingGateBlocked("initial routing maintenance mode must be legacy_before_gate")
        if proposed_targets:
            raise RuntimeRoutingGateBlocked("initial routing cannot enable calibration routes")
        from gameplay.services.virtual_player_core.profile_store import any_v2_profiles_exist

        if any_v2_profiles_exist():
            raise RuntimeRoutingUnavailable("cannot initialize missing routing after V2 enrollment")
        if apply:
            state = BotRuntimeRoutingState.objects.create(
                key=BotRuntimeRoutingState.GLOBAL_KEY,
                bootstrap_mode=proposed_bootstrap.value,
                maintenance_mode=proposed_maintenance.value,
                calibration_routes=[],
                revision=0,
                pause_reason=str(pause_reason),
            )
            snapshot = _snapshot(state)
        else:
            snapshot = RuntimeRoutingSnapshot(
                bootstrap_mode=proposed_bootstrap,
                maintenance_mode=proposed_maintenance,
                calibration_routes=(),
                revision=0,
                last_hourly_safety_window_end_at=None,
                last_daily_safety_window_end_at=None,
                last_pause_window_id="",
                pause_reason=str(pause_reason),
                paused_from_maintenance_mode="",
                persisted=False,
            )
        return RuntimeRoutingTransitionResult(
            snapshot=snapshot,
            changed=True,
            initialized=True,
        )

    if expected_revision is None:
        raise RuntimeRoutingConflict("expected_revision is required for persisted routing")
    _assert_expected_state(
        state,
        expected_revision=expected_revision,
        expected_bootstrap_mode=expected_bootstrap,
        expected_maintenance_mode=expected_maintenance,
    )
    current = _snapshot(state)
    validate_routing_transition(
        V2RoutingConfig(
            activation_mode="direct_after_gate",
            bootstrap_mode=current.bootstrap_mode,
            maintenance_mode=current.maintenance_mode,
        ),
        V2RoutingConfig(
            activation_mode="direct_after_gate",
            bootstrap_mode=proposed_bootstrap,
            maintenance_mode=proposed_maintenance,
        ),
    )
    if (
        current.bootstrap_mode is BootstrapMode.LEGACY_BEFORE_GATE
        and proposed_bootstrap is BootstrapMode.V2_ACTIVE
        and not gate_d1_ready
    ):
        raise RuntimeRoutingGateBlocked("Gate D1 evidence is required before Bootstrap V2 activation")
    if (
        current.maintenance_mode is MaintenanceMode.LEGACY_BEFORE_GATE
        and proposed_maintenance is MaintenanceMode.V2_CUTOVER
        and not gate_e_ready
    ):
        raise RuntimeRoutingGateBlocked("Gate E readiness evidence is required before cutover")
    if current.maintenance_mode is MaintenanceMode.V2_CUTOVER and proposed_maintenance is MaintenanceMode.V2_ACTIVE:
        from gameplay.services.virtual_player_core.profile_store import runtime_eligible_v1_profile_count

        if not gate_e_ready or runtime_eligible_v1_profile_count() != 0:
            raise RuntimeRoutingGateBlocked(
                "Maintenance V2 activation requires Gate E evidence and zero eligible V1 profiles"
            )

    normalized_pause_reason = str(pause_reason)
    current_targets = tuple(route.target for route in current.calibration_routes)
    changed = (
        state.bootstrap_mode != proposed_bootstrap.value
        or state.maintenance_mode != proposed_maintenance.value
        or current_targets != proposed_targets
        or state.pause_reason != normalized_pause_reason
    )
    if not changed:
        return RuntimeRoutingTransitionResult(
            snapshot=current,
            changed=False,
            initialized=False,
        )
    current_policy_versions = {route.policy_version for route in current.calibration_routes}
    proposed_policy_versions = {target.policy_version for target in proposed_targets}
    if current_policy_versions != proposed_policy_versions:
        from gameplay.services.virtual_player_core.policy_registry import (
            PolicyRegistryError,
            update_routing_policy_references,
        )

        try:
            update_routing_policy_references(
                added_versions=proposed_policy_versions - current_policy_versions,
                removed_versions=current_policy_versions - proposed_policy_versions,
                apply=apply,
            )
        except PolicyRegistryError as exc:
            raise RuntimeRoutingGateBlocked(str(exc)) from exc
    current_by_target = {route.target: route for route in current.calibration_routes}
    newly_enabled = tuple(target for target in proposed_targets if target not in current_by_target)
    if newly_enabled:
        from gameplay.services.virtual_player_core.policy_registry import (
            PolicyRegistryError,
            lock_assignable_policy_release,
        )

        if preflight_config is None or any(target not in preflight_acceptances for target in newly_enabled):
            raise RuntimeRoutingConflict("routing changed while Gate D2 evidence was being validated")
        for target in newly_enabled:
            try:
                policy = preflight_config.policy(target.policy_version)
                lock_assignable_policy_release(
                    version=policy.version,
                    expected_checksum=policy.checksum,
                )
            except (PolicyRegistryError, VirtualPlayerConfigError) as exc:
                raise RuntimeRoutingGateBlocked(
                    "calibration route lacks valid Gate D2 evidence: " f"{target.to_payload()}: {exc}"
                ) from exc
    proposed_routes = tuple(
        sorted(
            (
                (
                    current_by_target[target]
                    if target in current_by_target
                    else CalibrationRoute(
                        policy_version=target.policy_version,
                        reference_snapshot_version=target.reference_snapshot_version,
                        prestige_band=target.prestige_band,
                        policy_checksum=preflight_acceptances[target].policy_checksum,
                        reference_snapshot_digest=(preflight_acceptances[target].reference_snapshot_digest),
                        evidence_schema_version=(preflight_acceptances[target].evidence_schema_version),
                        evidence_digest=preflight_acceptances[target].evidence_digest,
                    )
                )
                for target in proposed_targets
            ),
            key=_route_sort_key,
        )
    )
    proposed_payload = [route.to_payload() for route in proposed_routes]
    if apply:
        state.bootstrap_mode = proposed_bootstrap.value
        state.maintenance_mode = proposed_maintenance.value
        state.calibration_routes = proposed_payload
        state.pause_reason = normalized_pause_reason
        state.paused_from_maintenance_mode = ""
        state.revision += 1
        state.save(
            update_fields=[
                "bootstrap_mode",
                "maintenance_mode",
                "calibration_routes",
                "pause_reason",
                "paused_from_maintenance_mode",
                "revision",
                "updated_at",
            ]
        )
        snapshot = _snapshot(state)
    else:
        snapshot = RuntimeRoutingSnapshot(
            bootstrap_mode=proposed_bootstrap,
            maintenance_mode=proposed_maintenance,
            calibration_routes=proposed_routes,
            revision=int(current.revision or 0) + 1,
            last_hourly_safety_window_end_at=current.last_hourly_safety_window_end_at,
            last_daily_safety_window_end_at=current.last_daily_safety_window_end_at,
            last_pause_window_id=current.last_pause_window_id,
            pause_reason=normalized_pause_reason,
            paused_from_maintenance_mode="",
            persisted=True,
        )
    return RuntimeRoutingTransitionResult(
        snapshot=snapshot,
        changed=True,
        initialized=False,
    )


def transition_virtual_player_routing(
    *,
    expected_revision: int | None,
    bootstrap_mode: BootstrapMode | str,
    maintenance_mode: MaintenanceMode | str,
    calibration_routes: Iterable[CalibrationRoute | Mapping[str, Any]] = (),
    expected_bootstrap_mode: BootstrapMode | str | None = None,
    expected_maintenance_mode: MaintenanceMode | str | None = None,
    pause_reason: str = "",
) -> RuntimeRoutingSnapshot:
    return _transition_virtual_player_routing(
        expected_revision=expected_revision,
        bootstrap_mode=bootstrap_mode,
        maintenance_mode=maintenance_mode,
        calibration_routes=calibration_routes,
        expected_bootstrap_mode=expected_bootstrap_mode,
        expected_maintenance_mode=expected_maintenance_mode,
        gate_d1_ready=False,
        gate_e_ready=False,
        pause_reason=pause_reason,
        apply=True,
    ).snapshot


def transition_virtual_player_routing_operation(
    *,
    expected_revision: int | None,
    bootstrap_mode: BootstrapMode | str,
    maintenance_mode: MaintenanceMode | str,
    calibration_routes: Iterable[CalibrationRoute | Mapping[str, Any]] = (),
    expected_bootstrap_mode: BootstrapMode | str | None = None,
    expected_maintenance_mode: MaintenanceMode | str | None = None,
    pause_reason: str = "",
    apply: bool = False,
) -> RuntimeRoutingOperationSummary:
    result = _transition_virtual_player_routing(
        expected_revision=expected_revision,
        bootstrap_mode=bootstrap_mode,
        maintenance_mode=maintenance_mode,
        calibration_routes=calibration_routes,
        expected_bootstrap_mode=expected_bootstrap_mode,
        expected_maintenance_mode=expected_maintenance_mode,
        gate_d1_ready=False,
        gate_e_ready=False,
        pause_reason=pause_reason,
        apply=apply,
    )
    return RuntimeRoutingOperationSummary(
        scanned=1,
        locked=0,
        changed=int(result.changed),
        skipped=int(not result.changed),
        failed=0,
        reasons=() if result.changed else ("routing_unchanged",),
        snapshot=result.snapshot,
    )


def _normalize_safety_window_contract(
    *,
    expected_revision: int,
    window_id: str,
    window_kind: str,
    window_end_at: datetime,
    should_pause: bool,
    pause_reason: str,
    resume_if_healthy: bool = False,
    expected_pause_reason: str = "",
) -> tuple[int, str, str, datetime, str, bool, str]:
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        raise RuntimeRoutingError("expected_revision must be a non-negative integer")
    if not isinstance(window_id, str) or not window_id.strip():
        raise RuntimeRoutingError("window_id must be a non-empty string")
    normalized_window_id = window_id.strip()
    if len(normalized_window_id) > 128:
        raise RuntimeRoutingError("window_id must contain at most 128 characters")
    if not isinstance(window_kind, str) or window_kind not in _SAFETY_WINDOW_KINDS:
        raise RuntimeRoutingError("window_kind must be 'hourly' or 'daily'")
    if not isinstance(window_end_at, datetime):
        raise RuntimeRoutingError("window_end_at must be a datetime")
    if window_end_at.tzinfo is None or window_end_at.utcoffset() is None:
        raise RuntimeRoutingError("window_end_at must be timezone-aware UTC")
    if window_end_at.utcoffset() != timedelta(0):
        raise RuntimeRoutingError("window_end_at must use UTC")
    normalized_window_end = window_end_at.astimezone(UTC)
    if any(
        (
            normalized_window_end.minute,
            normalized_window_end.second,
            normalized_window_end.microsecond,
        )
    ):
        raise RuntimeRoutingError("safety window end must be aligned to an hour")
    if window_kind == "daily" and normalized_window_end.hour != 0:
        raise RuntimeRoutingError("daily safety window end must be aligned to UTC midnight")
    if not isinstance(should_pause, bool):
        raise RuntimeRoutingError("should_pause must be a boolean")
    if not isinstance(resume_if_healthy, bool):
        raise RuntimeRoutingError("resume_if_healthy must be a boolean")
    if not isinstance(pause_reason, str):
        raise RuntimeRoutingError("pause_reason must be a string")
    if not isinstance(expected_pause_reason, str):
        raise RuntimeRoutingError("expected_pause_reason must be a string")
    normalized_reason = pause_reason.strip()
    normalized_expected_pause_reason = expected_pause_reason.strip()
    if should_pause and not normalized_reason:
        raise RuntimeRoutingError("pause_reason is required when should_pause is true")
    if not should_pause and normalized_reason:
        raise RuntimeRoutingError("pause_reason requires should_pause=true")
    if should_pause and resume_if_healthy:
        raise RuntimeRoutingError("resume_if_healthy cannot be combined with should_pause=true")
    if resume_if_healthy and not normalized_expected_pause_reason:
        raise RuntimeRoutingError("expected_pause_reason is required when resume_if_healthy is true")
    return (
        expected_revision,
        normalized_window_id,
        window_kind,
        normalized_window_end,
        normalized_reason,
        resume_if_healthy,
        normalized_expected_pause_reason,
    )


def is_recoverable_safety_pause_reason(reason: str) -> bool:
    """Return whether a pause can recover after consecutive complete safety windows."""
    normalized = str(reason).strip()
    reasons = tuple(part.strip() for part in normalized.split(","))
    if not reasons or any(not part for part in reasons):
        return False

    # One scheduler outage can make every heartbeat stream incomplete in the
    # same window. Keep that operational pause recoverable as a unit, while
    # refusing to auto-resume when any independent safety reason is present.
    if all(part.startswith("heartbeat_incomplete:") and bool(part.partition(":")[2].strip()) for part in reasons):
        return True

    if len(reasons) != 1:
        return False
    return reasons[0].startswith(("arena_shortage_baseline_missing:", "arena_shortage_baseline_expired:")) or reasons[
        0
    ] in {
        "missing_finalized_hourly_window",
        "missing_finalized_daily_window",
    }


def _is_recoverable_safety_pause_reason(reason: str) -> bool:
    """Compatibility wrapper for the routing transition implementation."""
    return is_recoverable_safety_pause_reason(reason)


@transaction.atomic
def apply_virtual_player_safety_decision(
    *,
    expected_revision: int,
    window_id: str,
    window_kind: str,
    window_end_at: datetime,
    should_pause: bool,
    pause_reason: str = "",
    resume_if_healthy: bool = False,
    expected_pause_reason: str = "",
) -> SafetyRoutingDecisionResult:
    """Consume one finalized safety window and atomically apply its routing decision."""
    (
        normalized_revision,
        normalized_window_id,
        normalized_kind,
        normalized_window_end,
        normalized_reason,
        normalized_resume_if_healthy,
        normalized_expected_pause_reason,
    ) = _normalize_safety_window_contract(
        expected_revision=expected_revision,
        window_id=window_id,
        window_kind=window_kind,
        window_end_at=window_end_at,
        should_pause=should_pause,
        pause_reason=pause_reason,
        resume_if_healthy=resume_if_healthy,
        expected_pause_reason=expected_pause_reason,
    )
    state = BotRuntimeRoutingState.objects.select_for_update().filter(key=BotRuntimeRoutingState.GLOBAL_KEY).first()
    if state is None:
        raise RuntimeRoutingUnavailable("routing state is absent")

    cursor_field = (
        "last_hourly_safety_window_end_at" if normalized_kind == "hourly" else "last_daily_safety_window_end_at"
    )
    current_cursor = getattr(state, cursor_field)
    if current_cursor is not None and current_cursor >= normalized_window_end:
        snapshot = _snapshot(state)
        return SafetyRoutingDecisionResult(
            snapshot=snapshot,
            consumed=False,
            paused=(
                should_pause
                and snapshot.maintenance_mode is MaintenanceMode.V2_PAUSED
                and snapshot.last_pause_window_id == normalized_window_id
            ),
            window_id=normalized_window_id,
            window_kind=normalized_kind,
        )

    _assert_expected_state(
        state,
        expected_revision=normalized_revision,
        expected_bootstrap_mode=None,
        expected_maintenance_mode=None,
    )
    setattr(state, cursor_field, normalized_window_end)
    update_fields = [cursor_field, "revision", "updated_at"]
    resumed = False
    if should_pause:
        state.safety_clean_window_streak = 0
        state.safety_clean_window_kind = normalized_kind
        if state.maintenance_mode in {
            MaintenanceMode.V2_ACTIVE.value,
            MaintenanceMode.V2_CUTOVER.value,
        }:
            state.paused_from_maintenance_mode = state.maintenance_mode
            state.maintenance_mode = MaintenanceMode.V2_PAUSED.value
            update_fields.append("maintenance_mode")
        state.last_pause_window_id = normalized_window_id
        state.pause_reason = normalized_reason
        update_fields.extend(
            [
                "last_pause_window_id",
                "pause_reason",
                "safety_clean_window_streak",
                "safety_clean_window_kind",
                "paused_from_maintenance_mode",
            ]
        )
    elif normalized_resume_if_healthy:
        if (
            state.maintenance_mode == MaintenanceMode.V2_PAUSED.value
            and state.pause_reason == normalized_expected_pause_reason
            and _is_recoverable_safety_pause_reason(state.pause_reason)
            and state.safety_clean_window_kind == normalized_kind
            and state.paused_from_maintenance_mode == MaintenanceMode.V2_ACTIVE.value
        ):
            state.safety_clean_window_streak = min(255, int(state.safety_clean_window_streak) + 1)
            if state.safety_clean_window_streak >= int(settings.VIRTUAL_PLAYER_SAFETY_AUTO_RESUME_CLEAN_WINDOWS):
                state.maintenance_mode = MaintenanceMode.V2_ACTIVE.value
                state.pause_reason = ""
                state.safety_clean_window_streak = 0
                state.safety_clean_window_kind = ""
                state.paused_from_maintenance_mode = ""
                resumed = True
                update_fields.extend(
                    [
                        "maintenance_mode",
                        "pause_reason",
                        "safety_clean_window_kind",
                        "paused_from_maintenance_mode",
                    ]
                )
            update_fields.append("safety_clean_window_streak")
        else:
            state.safety_clean_window_streak = 0
            update_fields.append("safety_clean_window_streak")
    else:
        state.safety_clean_window_streak = 0
        update_fields.append("safety_clean_window_streak")
    state.revision += 1
    state.save(update_fields=update_fields)
    snapshot = _snapshot(state)
    return SafetyRoutingDecisionResult(
        snapshot=snapshot,
        consumed=True,
        paused=(should_pause and snapshot.maintenance_mode is MaintenanceMode.V2_PAUSED),
        window_id=normalized_window_id,
        window_kind=normalized_kind,
        resumed=resumed,
    )


@transaction.atomic
def _transition_virtual_player_policy_rollout(
    *,
    expected_revision: int,
    expected_target_version: int,
    expected_enabled: bool,
    expected_rollout_percent: int,
    target_version: int,
    enabled: bool,
    rollout_percent: int,
    apply: bool,
) -> PolicyRolloutTransitionResult:
    expected = normalize_policy_rollout(
        target_version=expected_target_version,
        enabled=expected_enabled,
        rollout_percent=expected_rollout_percent,
    )
    proposed = normalize_policy_rollout(
        target_version=target_version,
        enabled=enabled,
        rollout_percent=rollout_percent,
    )
    state = BotRuntimeRoutingState.objects.select_for_update().filter(key=BotRuntimeRoutingState.GLOBAL_KEY).first()
    if state is None:
        raise RuntimeRoutingUnavailable("routing state must be initialized before policy rollout")
    current = _policy_rollout_snapshot(state)
    if state.revision != int(expected_revision):
        raise RuntimeRoutingConflict(f"routing revision changed: expected {expected_revision}, found {state.revision}")
    if current.target_version != expected.target_version:
        raise RuntimeRoutingConflict(
            "policy rollout target changed: " f"expected {expected.target_version}, found {current.target_version}"
        )
    if current.enabled is not expected.enabled:
        raise RuntimeRoutingConflict(
            "policy rollout enabled state changed: " f"expected {expected.enabled}, found {current.enabled}"
        )
    if current.rollout_percent != expected.rollout_percent:
        raise RuntimeRoutingConflict(
            "policy rollout percent changed: " f"expected {expected.rollout_percent}, found {current.rollout_percent}"
        )
    if (
        current.target_version == proposed.target_version
        and current.enabled is proposed.enabled
        and current.rollout_percent == proposed.rollout_percent
    ):
        return PolicyRolloutTransitionResult(snapshot=current, changed=False)

    current_versions = {current.target_version} if current.enabled else set()
    proposed_versions = {proposed.target_version} if proposed.enabled else set()
    if current_versions != proposed_versions:
        from gameplay.services.virtual_player_core.policy_registry import (
            PolicyRegistryError,
            update_routing_policy_references,
        )

        try:
            update_routing_policy_references(
                added_versions=proposed_versions - current_versions,
                removed_versions=current_versions - proposed_versions,
                apply=apply,
            )
        except PolicyRegistryError as exc:
            raise RuntimeRoutingGateBlocked(str(exc)) from exc

    if apply:
        state.policy_rollout_target_version = proposed.target_version
        state.policy_rollout_enabled = proposed.enabled
        state.policy_rollout_percent = proposed.rollout_percent
        state.revision += 1
        state.save(
            update_fields=[
                "policy_rollout_target_version",
                "policy_rollout_enabled",
                "policy_rollout_percent",
                "revision",
                "updated_at",
            ]
        )
        snapshot = _policy_rollout_snapshot(state)
    else:
        snapshot = PolicyRolloutSnapshot(
            target_version=proposed.target_version,
            enabled=proposed.enabled,
            rollout_percent=proposed.rollout_percent,
            revision=int(current.revision or 0) + 1,
            persisted=True,
        )
    return PolicyRolloutTransitionResult(snapshot=snapshot, changed=True)


def transition_virtual_player_policy_rollout(
    *,
    expected_revision: int,
    expected_target_version: int,
    expected_enabled: bool,
    expected_rollout_percent: int,
    target_version: int,
    enabled: bool,
    rollout_percent: int,
) -> PolicyRolloutSnapshot:
    return _transition_virtual_player_policy_rollout(
        expected_revision=expected_revision,
        expected_target_version=expected_target_version,
        expected_enabled=expected_enabled,
        expected_rollout_percent=expected_rollout_percent,
        target_version=target_version,
        enabled=enabled,
        rollout_percent=rollout_percent,
        apply=True,
    ).snapshot


def transition_virtual_player_policy_rollout_operation(
    *,
    expected_revision: int,
    expected_target_version: int,
    expected_enabled: bool,
    expected_rollout_percent: int,
    target_version: int,
    enabled: bool,
    rollout_percent: int,
    apply: bool = False,
) -> PolicyRolloutOperationSummary:
    result = _transition_virtual_player_policy_rollout(
        expected_revision=expected_revision,
        expected_target_version=expected_target_version,
        expected_enabled=expected_enabled,
        expected_rollout_percent=expected_rollout_percent,
        target_version=target_version,
        enabled=enabled,
        rollout_percent=rollout_percent,
        apply=apply,
    )
    return PolicyRolloutOperationSummary(
        scanned=1,
        locked=0,
        changed=int(result.changed),
        skipped=int(not result.changed),
        failed=0,
        reasons=() if result.changed else ("policy_rollout_unchanged",),
        snapshot=result.snapshot,
    )


def reload_runtime_configs() -> dict[str, int]:
    from gameplay.services.arena.coop_core import refresh_arena_coop_constants
    from gameplay.services.arena.coop_rules import clear_arena_coop_rules_cache, load_arena_coop_rules
    from gameplay.services.arena.core import refresh_arena_constants
    from gameplay.services.arena.rewards import clear_arena_reward_cache, load_arena_reward_catalog
    from gameplay.services.arena.rules import clear_arena_rules_cache, load_arena_rules
    from gameplay.services.buildings.forge import (
        clear_forge_blueprint_cache,
        clear_forge_decompose_cache,
        clear_forge_equipment_cache,
        load_forge_blueprint_config,
        load_forge_decompose_config,
        load_forge_equipment_config,
    )
    from gameplay.services.buildings.ranch import clear_ranch_production_cache, load_ranch_production_config
    from gameplay.services.buildings.smithy import clear_smithy_production_cache, load_smithy_production_config
    from gameplay.services.buildings.stable import clear_stable_production_cache, load_stable_production_config
    from gameplay.services.jail_persuasion.profiles import (
        clear_jail_persuasion_profiles_cache,
        load_jail_persuasion_profiles,
    )
    from gameplay.services.virtual_player_core.config import (
        clear_virtual_player_config_cache,
        load_virtual_player_config,
    )
    from guests.growth_rules import clear_guest_growth_rules_cache, load_guest_growth_rules
    from guests.utils.recruitment_utils import refresh_recruitment_rarity_constants
    from guilds.constants import clear_guild_rules_cache, load_guild_rules, refresh_guild_constants
    from guilds.services.warehouse_config import get_warehouse_production, reload_warehouse_production
    from trade.services.auction_config import load_auction_config, reload_auction_config
    from trade.services.market_service import clear_trade_market_rules_cache, load_trade_market_rules
    from trade.services.shop_config import load_shop_config, reload_shop_config

    reload_shop_config()
    shop_items = load_shop_config()

    reload_auction_config()
    auction_config = load_auction_config()

    reload_warehouse_production()
    warehouse_cfg = get_warehouse_production()

    clear_forge_equipment_cache()
    forge_equipment_cfg = load_forge_equipment_config()

    clear_forge_blueprint_cache()
    blueprint_cfg = load_forge_blueprint_config()

    clear_forge_decompose_cache()
    decompose_cfg = load_forge_decompose_config()

    clear_stable_production_cache()
    stable_cfg = load_stable_production_config()

    clear_ranch_production_cache()
    ranch_cfg = load_ranch_production_config()

    clear_smithy_production_cache()
    smithy_cfg = load_smithy_production_config()

    clear_guest_growth_rules_cache()
    guest_growth_rules = load_guest_growth_rules()
    _recruitment_total_weight, recruitment_weights, _recruitment_distribution = refresh_recruitment_rarity_constants()

    clear_arena_reward_cache()
    arena_rewards = load_arena_reward_catalog()

    clear_arena_rules_cache()
    arena_rules = load_arena_rules()
    refresh_arena_constants()

    clear_arena_coop_rules_cache()
    arena_coop_rules = load_arena_coop_rules()
    refresh_arena_coop_constants()

    clear_trade_market_rules_cache()
    trade_market_rules = load_trade_market_rules()

    clear_guild_rules_cache()
    guild_rules = load_guild_rules()
    refresh_guild_constants()

    clear_virtual_player_config_cache()
    virtual_players = load_virtual_player_config()

    clear_jail_persuasion_profiles_cache()
    jail_persuasion = load_jail_persuasion_profiles()

    return {
        "shop_items": len(shop_items),
        "auction_items": len(getattr(auction_config, "items", [])),
        "warehouse_techs": len(warehouse_cfg),
        "forge_equipment": len(forge_equipment_cfg),
        "forge_blueprints": len(blueprint_cfg.get("recipes", []) or []),
        "forge_decompose_rarities": len(decompose_cfg.get("supported_rarities", []) or []),
        "stable_entries": len(stable_cfg),
        "ranch_entries": len(ranch_cfg),
        "smithy_entries": len(smithy_cfg),
        "guest_growth_rarities": len((guest_growth_rules.get("rarity_attribute_growth_range") or {})),
        "recruitment_rarity_weights": len(recruitment_weights),
        "arena_rewards": len(arena_rewards),
        "arena_rank_rules": len((arena_rules.get("rewards") or {}).get("rank_bonus_coins", {})),
        "arena_coop_rank_rules": len((arena_coop_rules.get("rewards") or {}).get("rank_rewards", {})),
        "trade_listing_durations": len((trade_market_rules.get("listing_fees") or {})),
        "guild_tech_rules": len((guild_rules.get("technology") or {}).get("upgrade_costs", {})),
        "virtual_players": len((virtual_players.get("prestige_bands") or {})),
        "jail_persuasion_methods": len((jail_persuasion.get("methods") or {})),
    }


def format_runtime_config_summary(summary: dict[str, Any]) -> str:
    ordered_keys = [
        "shop_items",
        "auction_items",
        "warehouse_techs",
        "forge_equipment",
        "forge_blueprints",
        "forge_decompose_rarities",
        "stable_entries",
        "ranch_entries",
        "smithy_entries",
        "guest_growth_rarities",
        "recruitment_rarity_weights",
        "arena_rewards",
        "arena_rank_rules",
        "arena_coop_rank_rules",
        "trade_listing_durations",
        "guild_tech_rules",
        "virtual_players",
        "jail_persuasion_methods",
    ]
    parts = [f"{key}={summary[key]}" for key in ordered_keys if key in summary]
    return ", ".join(parts)
