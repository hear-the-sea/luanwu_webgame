"""Opt-in exclusive stage timing and SQL evidence for V2 diagnostics."""

from __future__ import annotations

import re
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter
from typing import Iterator

STAGE_DUE_BACKLOG_SELECTION = "due_backlog_selection"
STAGE_PLANNING_SNAPSHOT_PRELOAD = "planning_snapshot_preload"
STAGE_PROFILE_PLAN_REVALIDATION = "profile_plan_revalidation"
STAGE_ACTION_DOMAIN_WRITES = "action_domain_writes"
STAGE_CYCLE_ATTEMPT_RECEIPT = "cycle_attempt_receipt"
STAGE_BATCH_ORCHESTRATION = "batch_orchestration"
STAGE_SAFETY_TASK_WRAPUP = "safety_task_wrapup"
STAGE_SAFETY_ATTEMPT_START = "safety_attempt_start"
STAGE_SAFETY_ATTEMPT_FINISH = "safety_attempt_finish"
STAGE_RECOVERY_STATE = "recovery_state"

# Gate E requires these stages for every benchmark cell.  Keep the contract
# here so the runtime instrumentation, integration test, and evidence recorder
# cannot drift apart when a diagnostic stage is added.
GATE_E_REQUIRED_STAGE_NAMES = (
    STAGE_BATCH_ORCHESTRATION,
    STAGE_DUE_BACKLOG_SELECTION,
    STAGE_PLANNING_SNAPSHOT_PRELOAD,
    STAGE_PROFILE_PLAN_REVALIDATION,
    STAGE_ACTION_DOMAIN_WRITES,
    STAGE_CYCLE_ATTEMPT_RECEIPT,
    STAGE_SAFETY_TASK_WRAPUP,
    STAGE_SAFETY_ATTEMPT_START,
    STAGE_SAFETY_ATTEMPT_FINISH,
)
GATE_E_OPTIONAL_STAGE_NAMES = (STAGE_RECOVERY_STATE,)
GATE_E_ALLOWED_STAGE_NAMES = GATE_E_REQUIRED_STAGE_NAMES + GATE_E_OPTIONAL_STAGE_NAMES

_WRITE_PREFIXES = ("INSERT ", "UPDATE ", "DELETE ", "REPLACE ")
_SQL_STRING_RE = re.compile(r"'(?:''|[^'])*'")
_SQL_NUMBER_RE = re.compile(r"\b\d+\b")
_CURRENT_COLLECTOR: ContextVar[MaintenanceStageMetrics | None] = ContextVar(
    "virtual_player_maintenance_stage_metrics",
    default=None,
)


@dataclass(frozen=True, slots=True)
class MaintenanceStageObservation:
    """One stage sample; ``duration_ms`` excludes nested child stages."""

    stage: str
    duration_ms: float
    query_count: int
    write_query_count: int
    query_fingerprints: tuple[tuple[str, int], ...]
    inclusive_duration_ms: float = 0.0


@dataclass(slots=True)
class _ActiveObservation:
    stage: str
    started_at: float
    query_count: int = 0
    write_query_count: int = 0
    query_fingerprints: Counter[str] = field(default_factory=Counter)
    child_duration_ms: float = 0.0


@dataclass(slots=True)
class MaintenanceStageMetrics:
    """Collect stage observations only while an explicit diagnostic scope is active."""

    observations: dict[str, list[MaintenanceStageObservation]] = field(default_factory=dict)
    _active: list[_ActiveObservation] = field(default_factory=list)

    @contextmanager
    def stage(self, stage: str) -> Iterator[None]:
        active = _ActiveObservation(stage=stage, started_at=perf_counter())
        self._active.append(active)
        try:
            yield
        finally:
            if not self._active or self._active[-1] is not active:
                raise RuntimeError("maintenance stage metrics stack is unbalanced")
            self._active.pop()
            inclusive_duration_ms = max(0.0, (perf_counter() - active.started_at) * 1_000)
            exclusive_duration_ms = max(0.0, inclusive_duration_ms - active.child_duration_ms)
            observation = MaintenanceStageObservation(
                stage=active.stage,
                duration_ms=exclusive_duration_ms,
                query_count=active.query_count,
                write_query_count=active.write_query_count,
                query_fingerprints=tuple(active.query_fingerprints.most_common(10)),
                inclusive_duration_ms=inclusive_duration_ms,
            )
            self.observations.setdefault(active.stage, []).append(observation)
            if self._active:
                self._active[-1].child_duration_ms += inclusive_duration_ms

    def record_query(self, sql: str) -> None:
        """Attach one SQL execution to the innermost active stage.

        The database execute wrapper lives in the diagnostic test, keeping
        Django test instrumentation out of the production service path.
        """

        if not self._active:
            return
        active = self._active[-1]
        normalized = " ".join(str(sql).split())
        fingerprint = _SQL_NUMBER_RE.sub("?", _SQL_STRING_RE.sub("'?'", normalized))[:240]
        active.query_count += 1
        if normalized.lstrip().upper().startswith(_WRITE_PREFIXES):
            active.write_query_count += 1
        active.query_fingerprints[fingerprint] += 1


@contextmanager
def capture_maintenance_stage_metrics() -> Iterator[MaintenanceStageMetrics]:
    metrics = MaintenanceStageMetrics()
    token = _CURRENT_COLLECTOR.set(metrics)
    try:
        yield metrics
    finally:
        _CURRENT_COLLECTOR.reset(token)


@contextmanager
def record_maintenance_stage(stage: str) -> Iterator[None]:
    metrics = _CURRENT_COLLECTOR.get()
    if metrics is None:
        yield
        return
    with metrics.stage(stage):
        yield


def current_maintenance_stage_metrics() -> MaintenanceStageMetrics | None:
    """Return the active collector for test-only database execute wrappers."""

    return _CURRENT_COLLECTOR.get()


__all__ = [
    "MaintenanceStageMetrics",
    "MaintenanceStageObservation",
    "GATE_E_ALLOWED_STAGE_NAMES",
    "GATE_E_OPTIONAL_STAGE_NAMES",
    "GATE_E_REQUIRED_STAGE_NAMES",
    "STAGE_ACTION_DOMAIN_WRITES",
    "STAGE_BATCH_ORCHESTRATION",
    "STAGE_CYCLE_ATTEMPT_RECEIPT",
    "STAGE_DUE_BACKLOG_SELECTION",
    "STAGE_PLANNING_SNAPSHOT_PRELOAD",
    "STAGE_PROFILE_PLAN_REVALIDATION",
    "STAGE_RECOVERY_STATE",
    "STAGE_SAFETY_ATTEMPT_FINISH",
    "STAGE_SAFETY_ATTEMPT_START",
    "STAGE_SAFETY_TASK_WRAPUP",
    "capture_maintenance_stage_metrics",
    "current_maintenance_stage_metrics",
    "record_maintenance_stage",
]
