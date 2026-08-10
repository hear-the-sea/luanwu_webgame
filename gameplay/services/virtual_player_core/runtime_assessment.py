from __future__ import annotations

from dataclasses import dataclass

from gameplay.services.runtime_configs import RuntimeRoutingError, RuntimeRoutingSnapshot, read_virtual_player_routing

from .config import BootstrapMode, MaintenanceMode


@dataclass(frozen=True, slots=True)
class VirtualPlayerRuntimeAssessment:
    """Capability snapshot shared by population and Arena reserve writers."""

    routing_available: bool
    bootstrap_mode: BootstrapMode | None
    maintenance_mode: MaintenanceMode | None
    reason: str = ""

    @property
    def ready_handoff_allowed(self) -> bool:
        """READY handoff needs a readable routing/configuration snapshot."""

        return self.routing_available

    @property
    def reserve_engine_version(self) -> int | None:
        """Engine version eligible for new reserve leases."""

        if not self.routing_available or self.bootstrap_mode is not BootstrapMode.V2_ACTIVE:
            return None
        return 2

    @property
    def growth_engine_version(self) -> int | None:
        """Engine version the active maintenance route can mutate."""

        if not self.routing_available:
            return None
        if self.maintenance_mode is MaintenanceMode.V2_ACTIVE:
            return 2
        return None

    @property
    def growth_allowed(self) -> bool:
        return self.growth_engine_version is not None

    @property
    def training_admission_allowed(self) -> bool:
        return self.reserve_engine_version is not None and self.reserve_engine_version == self.growth_engine_version

    @property
    def v2_population_activation_allowed(self) -> bool:
        return (
            self.routing_available
            and self.bootstrap_mode is BootstrapMode.V2_ACTIVE
            and self.maintenance_mode is MaintenanceMode.V2_ACTIVE
        )

    @property
    def legacy_population_activation_allowed(self) -> bool:
        """Legacy population writes are retired after the single-policy cutover."""

        return False

    @property
    def population_mutation_allowed(self) -> bool:
        """Whether a population writer may create or reactivate a profile."""

        return self.v2_population_activation_allowed


def assess_virtual_player_runtime(
    snapshot: RuntimeRoutingSnapshot | None = None,
) -> VirtualPlayerRuntimeAssessment:
    """Translate persisted routing into explicit write capabilities.

    Routing failures are fail-closed for every population/growth write. A
    missing pre-gate routing row may still be represented by the legacy
    snapshot for historical read compatibility, but it grants no write
    capability after the policy-2 cutover.
    """

    if snapshot is None:
        try:
            snapshot = read_virtual_player_routing()
        except RuntimeRoutingError:
            return VirtualPlayerRuntimeAssessment(
                routing_available=False,
                bootstrap_mode=None,
                maintenance_mode=None,
                reason="routing_unavailable",
            )
    retired_routes = tuple(getattr(snapshot, "calibration_routes", ()) or ())
    if retired_routes:
        return VirtualPlayerRuntimeAssessment(
            routing_available=False,
            bootstrap_mode=snapshot.bootstrap_mode,
            maintenance_mode=snapshot.maintenance_mode,
            reason="static_calibration_routes_retired",
        )
    reason = ""
    if snapshot.bootstrap_mode is BootstrapMode.V2_PAUSED:
        reason = "bootstrap_v2_paused"
    elif snapshot.bootstrap_mode is BootstrapMode.LEGACY_BEFORE_GATE:
        reason = "legacy_runtime_retired"
    elif snapshot.maintenance_mode in {
        MaintenanceMode.V2_CUTOVER,
        MaintenanceMode.V2_PAUSED,
    }:
        reason = f"maintenance_{snapshot.maintenance_mode.value}"
    return VirtualPlayerRuntimeAssessment(
        routing_available=True,
        bootstrap_mode=snapshot.bootstrap_mode,
        maintenance_mode=snapshot.maintenance_mode,
        reason=reason,
    )


__all__ = ["VirtualPlayerRuntimeAssessment", "assess_virtual_player_runtime"]
