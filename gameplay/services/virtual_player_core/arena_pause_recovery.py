"""Reviewed migration of legacy arena-only pauses; assessment is SELECT-only."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.db import DatabaseError, transaction

from gameplay.models import (
    ArenaRuntimeBudget,
    ArenaVirtualReserveMember,
    BotSafetyDecisionAudit,
    BotSafetyMetricEvent,
    BotSafetyMetricWindow,
)
from gameplay.services import runtime_configs
from gameplay.services.arena.low_population_budget import validate_arena_runtime_budget_entries
from gameplay.services.arena.low_population_policy import low_population_enabled

from .config import MaintenanceMode
from .database_clock import database_utc_now
from .safety_metrics import DUPLICATE_OR_PARTIAL_COMMIT_METRIC, ECONOMY_CAP_BREACH_METRIC, HARD_CONSTRAINT_METRIC
from .safety_monitor import InvalidSafetySnapshotError, _latest_mature_window_end, evaluate_finalized_safety_window
from .safety_policy import POLICY_VERSION, arena_warning_scope, reason_details, safety_policy_mode
from .safety_preflight import check_v2_development_write_preflight


@dataclass(frozen=True)
class ArenaPauseRecoveryAssessment:
    allowed: bool
    revision: int | None
    pause_reason: str
    blockers: tuple[str, ...]
    checked_window_ids: tuple[str, ...]
    preflight: dict[str, object] = field(default_factory=dict)
    window_evidence: dict[str, dict[str, object]] = field(default_factory=dict)
    reserve_lease: dict[str, int] = field(default_factory=dict)
    runtime_budget: dict[str, dict[str, object]] = field(default_factory=dict)


def _iso_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _recovery_runtime_diagnostics(
    *, now: datetime | None = None
) -> tuple[dict[str, int], dict[str, dict[str, object]], tuple[str, ...]]:
    reserve_lease = {
        "ready": ArenaVirtualReserveMember.objects.filter(state=ArenaVirtualReserveMember.State.READY).count(),
        "training": ArenaVirtualReserveMember.objects.filter(state=ArenaVirtualReserveMember.State.TRAINING).count(),
        "exhausted": ArenaVirtualReserveMember.objects.filter(state=ArenaVirtualReserveMember.State.EXHAUSTED).count(),
        "paused_training": ArenaVirtualReserveMember.objects.filter(
            state=ArenaVirtualReserveMember.State.TRAINING,
            lease_paused_at__isnull=False,
        ).count(),
        "active_claims": ArenaVirtualReserveMember.objects.filter(growth_claim_token__isnull=False).count(),
    }
    runtime_budget: dict[str, dict[str, object]] = {}
    budget_blockers: list[str] = []
    for key in ("materialization", "training", "growth"):
        budget = ArenaRuntimeBudget.objects.filter(pk=key).only("entries", "updated_at").first()
        if budget is None:
            runtime_budget[key] = {"status": "missing", "entry_count": None, "updated_at": None}
            budget_blockers.append(f"arena_runtime_budget_{key}_missing")
            continue
        try:
            entries = validate_arena_runtime_budget_entries(key, budget.entries, now=now)
        except (TypeError, ValueError):
            runtime_budget[key] = {
                "status": "invalid",
                "entry_count": None,
                "updated_at": _iso_datetime(budget.updated_at),
            }
            budget_blockers.append(f"arena_runtime_budget_{key}_invalid")
            continue
        runtime_budget[key] = {
            "status": "ok",
            "entry_count": len(entries),
            "updated_at": _iso_datetime(budget.updated_at),
        }
    return reserve_lease, runtime_budget, tuple(budget_blockers)


def assess_arena_pause_recovery(*, now: datetime | None = None) -> ArenaPauseRecoveryAssessment:
    """Require fresh complete consumed evidence, never infer health from idle alone."""
    blockers: list[str] = []
    checked: list[str] = []
    revision = None
    pause_reason = ""
    preflight_details: dict[str, object] = {}
    window_evidence: dict[str, dict[str, object]] = {}
    reserve_lease: dict[str, int] = {}
    runtime_budget: dict[str, dict[str, object]] = {}
    try:
        current_time = now or database_utc_now()
        routing = runtime_configs.read_virtual_player_routing()
        revision, pause_reason = routing.revision, routing.pause_reason
        if safety_policy_mode() != "scoped":
            blockers.append("scoped_policy_required")
        if not routing.persisted or routing.maintenance_mode is not MaintenanceMode.V2_PAUSED:
            blockers.append("persisted_paused_routing_required")
        if routing.paused_from_maintenance_mode != MaintenanceMode.V2_ACTIVE.value:
            blockers.append("active_pause_origin_required")
        reasons = tuple(pause_reason.split(","))
        if not reasons or any(arena_warning_scope(reason) is None for reason in reasons):
            blockers.append("pause_contains_non_arena_warning")
        preflight = check_v2_development_write_preflight(now=current_time)
        preflight_details = {
            "allowed": preflight.allowed,
            "reason": preflight.reason,
            "checked_at": _iso_datetime(preflight.checked_at),
            "monitor_heartbeat_at": _iso_datetime(preflight.monitor_heartbeat_at),
        }
        if not preflight.allowed:
            blockers.append(preflight.reason)
        reserve_lease, runtime_budget, budget_blockers = _recovery_runtime_diagnostics(now=current_time)
        if low_population_enabled():
            blockers.extend(budget_blockers)
        latest_hour = _latest_mature_window_end(current_time, window_kind="hourly")
        # Preserve the evaluator's existing multi-window rules. A missing
        # history is insufficient evidence for this explicit recovery operation.
        for kind, count in (("hourly", 3), ("daily", 2)):
            end = _latest_mature_window_end(current_time, window_kind=kind)
            duration = timedelta(hours=1) if kind == "hourly" else timedelta(days=1)
            windows = list(
                BotSafetyMetricWindow.objects.filter(
                    kind=kind,
                    window_end_at__lte=end,
                    window_end_at__gt=end - count * duration,
                    finalized_at__isnull=False,
                ).order_by("-window_end_at")
            )
            cursor = getattr(routing, f"last_{kind}_safety_window_end_at")
            cursor_caught_up = cursor is not None and cursor >= end
            evidence_complete = len(windows) == count and all(
                w.window_end_at == end - i * duration for i, w in enumerate(windows)
            )
            window_evidence[kind] = {
                "latest_mature_window_end": end.isoformat(),
                "cursor_end": _iso_datetime(cursor),
                "cursor_caught_up": cursor_caught_up,
                "expected_count": count,
                "found_count": len(windows),
                "window_ids": [window.window_id for window in windows],
                "complete": evidence_complete,
            }
            if not cursor_caught_up:
                blockers.append(f"{kind}_monitor_not_caught_up")
            if not evidence_complete:
                blockers.append(f"{kind}_evidence_incomplete")
                continue
            for i, window in enumerate(windows):
                decision = evaluate_finalized_safety_window(window, history=windows[i + 1 :], now=current_time)
                checked.append(window.window_id)
                blockers.extend(decision.pause_reasons)
        if (
            BotSafetyMetricEvent.objects.filter(
                occurred_at__gte=latest_hour,
                metric_name__in=(HARD_CONSTRAINT_METRIC, ECONOMY_CAP_BREACH_METRIC, DUPLICATE_OR_PARTIAL_COMMIT_METRIC),
            )
            .exclude(value=0)
            .exists()
        ):
            blockers.append("pending_hard_safety_evidence")
    except (DatabaseError, runtime_configs.RuntimeRoutingError, InvalidSafetySnapshotError, ValueError, TypeError):
        blockers.append("safety_evidence_unreadable_or_invalid")
    return ArenaPauseRecoveryAssessment(
        allowed=not blockers,
        revision=revision,
        pause_reason=pause_reason,
        blockers=tuple(dict.fromkeys(blockers)),
        checked_window_ids=tuple(checked),
        preflight=preflight_details,
        window_evidence=window_evidence,
        reserve_lease=reserve_lease,
        runtime_budget=runtime_budget,
    )


@transaction.atomic
def recover_arena_pause(
    *, expected_revision: int, expected_pause_reason: str
) -> runtime_configs.RuntimeRoutingSnapshot:
    """Acquire routing first; recheck evidence and commit lease recovery + audit."""
    before = runtime_configs.lock_virtual_player_routing()
    if before.revision != expected_revision or before.pause_reason != expected_pause_reason:
        raise runtime_configs.RuntimeRoutingConflict("routing changed after recovery assessment")
    assessment = assess_arena_pause_recovery()
    if not assessment.allowed:
        raise runtime_configs.RuntimeRoutingGateBlocked(
            "arena pause recovery blocked: " + ",".join(assessment.blockers)
        )
    after = runtime_configs.transition_virtual_player_routing(
        expected_revision=expected_revision,
        bootstrap_mode=before.bootstrap_mode,
        maintenance_mode=MaintenanceMode.V2_ACTIVE,
        expected_maintenance_mode=MaintenanceMode.V2_PAUSED,
        expected_pause_reason=expected_pause_reason,
        resume_paused=True,
    )
    assert before.revision is not None and after.revision is not None
    BotSafetyDecisionAudit.objects.create(
        window_id=before.last_pause_window_id or "legacy-arena-recovery",
        policy_version=POLICY_VERSION,
        policy_mode="scoped",
        operation="recovery",
        reasons=[
            {**reason, "checked_window_ids": list(assessment.checked_window_ids)}
            for reason in reason_details(tuple(before.pause_reason.split(",")))
        ],
        effective_pause=False,
        scoped_pause=False,
        previous_mode=before.maintenance_mode.value,
        resulting_mode=after.maintenance_mode.value,
        previous_reason=before.pause_reason,
        resulting_reason=after.pause_reason,
        previous_revision=before.revision,
        resulting_revision=after.revision,
    )
    return after
