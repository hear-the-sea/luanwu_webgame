"""Pure audit contracts for isolated virtual-player simulations.

The simulator owns time advancement and database reads; this module only
normalizes event rows and validates the accounting/cardinality invariants.  It
must remain side-effect free so report generation cannot accidentally settle
resources or mutate a player.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime


class SimulationAuditError(ValueError):
    """Raised when a simulation report cannot prove its own invariants."""


RESOURCE_BUCKET_BY_REASON = {
    "produce": "natural_production",
    "upgrade_cost": "upgrade_cost",
    "tech_upgrade": "technology_cost",
    "recruit_cost": "recruitment_cost",
    "training_cost": "training_cost",
    "salary_cost": "salary",
    "item_use": "item_use",
}


@dataclass(frozen=True, slots=True)
class SimulationWindow:
    simulation_id: str
    started_at: datetime
    ended_at: datetime

    def __post_init__(self) -> None:
        if not str(self.simulation_id).strip():
            raise SimulationAuditError("simulation_id must be non-empty")
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise SimulationAuditError("started_at must be timezone-aware")
        if self.ended_at.tzinfo is None or self.ended_at.utcoffset() is None:
            raise SimulationAuditError("ended_at must be timezone-aware")
        if self.ended_at < self.started_at:
            raise SimulationAuditError("ended_at must not precede started_at")


@dataclass(frozen=True, slots=True)
class ResourceLedgerAudit:
    initial: Mapping[str, int]
    final: Mapping[str, int]
    event_delta: Mapping[str, int]
    by_bucket: Mapping[str, Mapping[str, int]]

    def __post_init__(self) -> None:
        resources = set(self.initial) | set(self.final) | set(self.event_delta)
        for resource in resources:
            expected = int(self.initial.get(resource, 0)) + int(self.event_delta.get(resource, 0))
            actual = int(self.final.get(resource, 0))
            if expected != actual:
                raise SimulationAuditError(
                    f"resource ledger mismatch for {resource}: expected {expected}, actual {actual}"
                )


@dataclass(frozen=True, slots=True)
class PlayerCardinalityAudit:
    expected_count: int
    player_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if isinstance(self.expected_count, bool) or not isinstance(self.expected_count, int) or self.expected_count < 0:
            raise SimulationAuditError("expected_count must be a non-negative integer")
        if len(self.player_ids) != self.expected_count:
            raise SimulationAuditError(
                f"player cardinality mismatch: expected {self.expected_count}, got {len(self.player_ids)}"
            )
        if len(set(self.player_ids)) != len(self.player_ids):
            raise SimulationAuditError("player ids must be unique")


def validate_player_cardinality(expected_count: int, player_ids: Sequence[int]) -> PlayerCardinalityAudit:
    """Validate the player count before JSON/Excel aggregation."""

    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count < 0:
        raise SimulationAuditError("expected_count must be a non-negative integer")
    normalized_ids = tuple(int(player_id) for player_id in player_ids)
    return PlayerCardinalityAudit(expected_count=expected_count, player_ids=normalized_ids)


def _row_value(row: object, field: str, default: object = None) -> object:
    if isinstance(row, Mapping):
        return row.get(field, default)
    return getattr(row, field, default)


def build_resource_ledger_audit(
    *,
    initial: Mapping[str, int],
    final: Mapping[str, int],
    events: Iterable[object],
    salary_payments: Iterable[object] = (),
) -> ResourceLedgerAudit:
    """Build and validate a ledger from only this simulation's event rows.

    Callers must apply their per-manor baseline cut before passing events.  No
    timestamp is used here because ``ResourceEvent.created_at`` is wall-clock
    metadata and does not represent a simulator's virtual clock.
    """

    event_delta: defaultdict[str, int] = defaultdict(int)
    by_bucket: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    for event in events:
        resource = str(_row_value(event, "resource_type", "")).strip()
        reason = str(_row_value(event, "reason", "")).strip()
        delta = _row_value(event, "delta", 0)
        if not resource:
            raise SimulationAuditError("resource event is missing resource_type")
        if isinstance(delta, bool) or not isinstance(delta, int):
            raise SimulationAuditError("resource event delta must be an integer")
        bucket = RESOURCE_BUCKET_BY_REASON.get(reason, "other")
        event_delta[resource] += int(delta)
        by_bucket[bucket][resource] += int(delta)
    # New salary settlements are represented by the manor-level
    # ``salary_cost`` ResourceEvent above. The payment rows are guest-owned
    # compatibility receipts and can be deleted together with a replaced
    # guest, so callers should only provide them for legacy rows/events.
    for payment in salary_payments:
        amount = _row_value(payment, "amount", 0)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise SimulationAuditError("salary payment amount must be a non-negative integer")
        event_delta["silver"] -= int(amount)
        by_bucket["salary"]["silver"] -= int(amount)
    normalized_buckets = {bucket: dict(sorted(values.items())) for bucket, values in sorted(by_bucket.items())}
    return ResourceLedgerAudit(
        initial={str(key): int(value) for key, value in initial.items()},
        final={str(key): int(value) for key, value in final.items()},
        event_delta=dict(sorted(event_delta.items())),
        by_bucket=normalized_buckets,
    )


def max_primary_key(rows: Iterable[object]) -> int:
    """Return a baseline primary-key watermark without touching the database."""

    maximum = 0
    for row in rows:
        value = _row_value(row, "id", 0)
        if isinstance(value, bool) or not isinstance(value, int):
            raise SimulationAuditError("audit row id must be an integer")
        maximum = max(maximum, int(value))
    return maximum


__all__ = [
    "PlayerCardinalityAudit",
    "ResourceLedgerAudit",
    "RESOURCE_BUCKET_BY_REASON",
    "SimulationAuditError",
    "SimulationWindow",
    "build_resource_ledger_audit",
    "max_primary_key",
    "validate_player_cardinality",
]
