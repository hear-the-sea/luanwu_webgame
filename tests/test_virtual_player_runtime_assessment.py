from __future__ import annotations

from types import SimpleNamespace

from gameplay.services.runtime_configs import RuntimeRoutingUnavailable
from gameplay.services.virtual_player_core import runtime_assessment
from gameplay.services.virtual_player_core.config import BootstrapMode, MaintenanceMode


def _routing(
    bootstrap_mode: BootstrapMode,
    maintenance_mode: MaintenanceMode,
):
    return SimpleNamespace(
        bootstrap_mode=bootstrap_mode,
        maintenance_mode=maintenance_mode,
    )


def test_runtime_assessment_separates_ready_handoff_from_growth_and_population_writes() -> None:
    paused = runtime_assessment.assess_virtual_player_runtime(
        _routing(BootstrapMode.V2_ACTIVE, MaintenanceMode.V2_PAUSED)
    )

    assert paused.routing_available is True
    assert paused.ready_handoff_allowed is True
    assert paused.reserve_engine_version == 2
    assert paused.growth_engine_version is None
    assert paused.growth_allowed is False
    assert paused.training_admission_allowed is False
    assert paused.population_mutation_allowed is False
    assert paused.v2_population_activation_allowed is False

    active = runtime_assessment.assess_virtual_player_runtime(
        _routing(BootstrapMode.V2_ACTIVE, MaintenanceMode.V2_ACTIVE)
    )

    assert active.growth_allowed is True
    assert active.reserve_engine_version == 2
    assert active.growth_engine_version == 2
    assert active.training_admission_allowed is True
    assert active.population_mutation_allowed is True
    assert active.v2_population_activation_allowed is True


def test_runtime_assessment_blocks_training_during_bootstrap_maintenance_engine_mismatch() -> None:
    assessment = runtime_assessment.assess_virtual_player_runtime(
        _routing(BootstrapMode.V2_ACTIVE, MaintenanceMode.LEGACY_BEFORE_GATE)
    )

    assert assessment.ready_handoff_allowed is True
    assert assessment.reserve_engine_version == 2
    assert assessment.growth_engine_version is None
    assert assessment.growth_allowed is False
    assert assessment.training_admission_allowed is False


def test_runtime_assessment_fails_closed_when_routing_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_assessment,
        "read_virtual_player_routing",
        lambda: (_ for _ in ()).throw(RuntimeRoutingUnavailable("unavailable")),
    )

    assessment = runtime_assessment.assess_virtual_player_runtime()

    assert assessment.routing_available is False
    assert assessment.ready_handoff_allowed is False
    assert assessment.reserve_engine_version is None
    assert assessment.growth_engine_version is None
    assert assessment.growth_allowed is False
    assert assessment.population_mutation_allowed is False
    assert assessment.reason == "routing_unavailable"
