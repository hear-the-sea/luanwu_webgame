from __future__ import annotations

import threading
from pathlib import Path

import pytest
from django.db import close_old_connections, connection

from gameplay.models import BotRuntimeRoutingState
from gameplay.services import runtime_configs
from gameplay.services.virtual_player_core import gate_d2_acceptance_workflow
from tests.test_virtual_player_gate_d2_acceptance_workflow import UNIT, _candidate_report, _write_candidate_report
from tests.test_virtual_player_gate_d2_routing import (
    MIDDLE_UNIT,
    _configure_trusted_d2_files,
    _persisted_route,
    _route,
    _routing_state,
)

pytestmark = pytest.mark.integration


@pytest.mark.django_db(transaction=True)
def test_gate_d2_route_activation_uses_the_routing_revision_cas_under_mysql(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settings,
) -> None:
    if connection.vendor != "mysql":
        pytest.skip("Gate D2 routing serialization requires MySQL row locks")

    config = _configure_trusted_d2_files(
        monkeypatch=monkeypatch,
        settings=settings,
        project_root=tmp_path,
    )
    for unit in (UNIT, MIDDLE_UNIT):
        _write_candidate_report(
            tmp_path,
            _candidate_report(config, project_root=tmp_path, unit=unit),
            unit=unit,
        )
    _routing_state()

    first_is_in_evidence_preflight = threading.Event()
    allow_first = threading.Event()
    second_started = threading.Event()
    second_finished = threading.Event()
    results: list[str] = []
    errors: list[BaseException] = []
    guard = threading.Lock()
    original_evaluate = gate_d2_acceptance_workflow.evaluate_gate_d2_acceptance
    evaluation_count = 0

    def _block_first_evaluation(unit, **kwargs):
        nonlocal evaluation_count
        with guard:
            evaluation_count += 1
            ordinal = evaluation_count
        if ordinal == 1:
            first_is_in_evidence_preflight.set()
            if not allow_first.wait(timeout=10):
                raise TimeoutError("first Gate D2 transition was not released")
        return original_evaluate(unit, **kwargs)

    monkeypatch.setattr(
        gate_d2_acceptance_workflow,
        "evaluate_gate_d2_acceptance",
        _block_first_evaluation,
    )

    def _worker(*, name: str, unit, finished: threading.Event | None = None) -> None:
        close_old_connections()
        try:
            if name == "second":
                second_started.set()
            runtime_configs.transition_virtual_player_routing(
                expected_revision=0,
                expected_bootstrap_mode="v2_active",
                expected_maintenance_mode="legacy_before_gate",
                bootstrap_mode="v2_active",
                maintenance_mode="legacy_before_gate",
                calibration_routes=[_route(unit)],
            )
            with guard:
                results.append(name)
        except BaseException as exc:  # pragma: no cover - asserted below
            with guard:
                errors.append(exc)
        finally:
            if finished is not None:
                finished.set()
            close_old_connections()

    first = threading.Thread(
        target=_worker,
        kwargs={"name": "first", "unit": UNIT},
        daemon=True,
    )
    second = threading.Thread(
        target=_worker,
        kwargs={
            "name": "second",
            "unit": MIDDLE_UNIT,
            "finished": second_finished,
        },
        daemon=True,
    )
    first.start()
    assert first_is_in_evidence_preflight.wait(timeout=10)
    second.start()
    assert second_started.wait(timeout=10)
    assert second_finished.wait(timeout=10)
    assert results == ["second"]

    allow_first.set()
    first.join(timeout=30)
    second.join(timeout=30)

    assert not first.is_alive()
    assert not second.is_alive()
    assert results == ["second"]
    assert len(errors) == 1
    assert isinstance(errors[0], runtime_configs.RuntimeRoutingConflict)
    assert evaluation_count == 2
    routing = BotRuntimeRoutingState.objects.get()
    assert routing.revision == 1
    assert routing.calibration_routes == [_persisted_route(config, MIDDLE_UNIT)]
