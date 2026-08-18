from datetime import UTC, datetime, timedelta

import pytest

from gameplay.services.virtual_player_core.simulation_audit import (
    SimulationAuditError,
    SimulationWindow,
    build_resource_ledger_audit,
    max_primary_key,
    validate_player_cardinality,
)


def test_resource_audit_uses_event_watermark_rows_and_reason_buckets() -> None:
    audit = build_resource_ledger_audit(
        initial={"silver": 100, "grain": 50},
        final={"silver": 120, "grain": 40},
        events=(
            {"id": 11, "resource_type": "silver", "delta": 60, "reason": "produce"},
            {"id": 12, "resource_type": "silver", "delta": -35, "reason": "tech_upgrade"},
            {"id": 13, "resource_type": "grain", "delta": -10, "reason": "training_cost"},
        ),
        salary_payments=({"id": 21, "amount": 5},),
    )

    assert audit.event_delta == {"grain": -10, "silver": 20}
    assert audit.by_bucket == {
        "natural_production": {"silver": 60},
        "salary": {"silver": -5},
        "technology_cost": {"silver": -35},
        "training_cost": {"grain": -10},
    }


def test_resource_audit_treats_manor_salary_events_as_durable_debits() -> None:
    audit = build_resource_ledger_audit(
        initial={"silver": 100},
        final={"silver": 75},
        events=({"id": 11, "resource_type": "silver", "delta": -25, "reason": "salary_cost"},),
    )

    assert audit.event_delta == {"silver": -25}
    assert audit.by_bucket == {"salary": {"silver": -25}}


def test_resource_audit_rejects_unbalanced_snapshot() -> None:
    with pytest.raises(SimulationAuditError, match="resource ledger mismatch"):
        build_resource_ledger_audit(
            initial={"silver": 100},
            final={"silver": 101},
            events=(),
        )


def test_player_cardinality_and_window_are_strict() -> None:
    assert validate_player_cardinality(2, [7, 8]).player_ids == (7, 8)
    assert max_primary_key([{"id": 4}, {"id": 9}]) == 9
    SimulationWindow(
        simulation_id="sim-test",
        started_at=datetime(2026, 8, 16, tzinfo=UTC),
        ended_at=datetime(2026, 8, 16, tzinfo=UTC) + timedelta(days=30),
    )

    with pytest.raises(SimulationAuditError, match="cardinality"):
        validate_player_cardinality(2, [7])
    with pytest.raises(SimulationAuditError, match="timezone-aware"):
        SimulationWindow(
            simulation_id="sim-test",
            started_at=datetime(2026, 8, 16),
            ended_at=datetime(2026, 8, 16, tzinfo=UTC),
        )
