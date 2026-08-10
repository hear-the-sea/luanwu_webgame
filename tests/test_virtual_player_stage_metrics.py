from __future__ import annotations

import pytest

from gameplay.services.virtual_player_core.stage_metrics import (
    STAGE_ACTION_DOMAIN_WRITES,
    STAGE_DUE_BACKLOG_SELECTION,
    capture_maintenance_stage_metrics,
    current_maintenance_stage_metrics,
    record_maintenance_stage,
)

pytestmark = pytest.mark.evidence


def test_stage_metrics_are_noop_without_an_explicit_capture_scope() -> None:
    assert current_maintenance_stage_metrics() is None
    with record_maintenance_stage(STAGE_DUE_BACKLOG_SELECTION):
        pass
    assert current_maintenance_stage_metrics() is None


def test_stage_metrics_attach_queries_to_the_innermost_stage() -> None:
    with capture_maintenance_stage_metrics() as metrics:
        with record_maintenance_stage(STAGE_DUE_BACKLOG_SELECTION):
            metrics.record_query("SELECT 1 FROM profile WHERE id = 10")
            with record_maintenance_stage(STAGE_ACTION_DOMAIN_WRITES):
                metrics.record_query("INSERT INTO profile (id) VALUES (10)")
            metrics.record_query("SELECT 2 FROM profile WHERE id = 11")

    selection = metrics.observations[STAGE_DUE_BACKLOG_SELECTION][0]
    writes = metrics.observations[STAGE_ACTION_DOMAIN_WRITES][0]
    assert selection.query_count == 2
    assert selection.write_query_count == 0
    assert writes.query_count == 1
    assert writes.write_query_count == 1
    assert writes.query_fingerprints[0][1] == 1


def test_stage_metrics_finalize_observations_when_the_stage_raises() -> None:
    with pytest.raises(RuntimeError, match="injected"):
        with capture_maintenance_stage_metrics() as metrics:
            with record_maintenance_stage(STAGE_DUE_BACKLOG_SELECTION):
                raise RuntimeError("injected")

    assert len(metrics.observations[STAGE_DUE_BACKLOG_SELECTION]) == 1


def test_stage_metrics_report_exclusive_duration_for_nested_stages(monkeypatch) -> None:
    timestamps = iter((0.0, 1.0, 3.0, 10.0))
    monkeypatch.setattr(
        "gameplay.services.virtual_player_core.stage_metrics.perf_counter",
        lambda: next(timestamps),
    )

    with capture_maintenance_stage_metrics() as metrics:
        with record_maintenance_stage(STAGE_DUE_BACKLOG_SELECTION):
            with record_maintenance_stage(STAGE_ACTION_DOMAIN_WRITES):
                pass

    selection = metrics.observations[STAGE_DUE_BACKLOG_SELECTION][0]
    writes = metrics.observations[STAGE_ACTION_DOMAIN_WRITES][0]
    assert writes.inclusive_duration_ms == 2_000
    assert writes.duration_ms == 2_000
    assert selection.inclusive_duration_ms == 10_000
    assert selection.duration_ms == 8_000
