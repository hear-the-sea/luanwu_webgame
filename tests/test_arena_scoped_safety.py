from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from gameplay.models import BotSafetyDecisionAudit, BotSafetyMetricEvent, BotSafetyMetricWindow
from gameplay.services import runtime_configs
from gameplay.services.virtual_player_core import arena_pause_recovery as recovery
from gameplay.services.virtual_player_core import safety_monitor
from gameplay.services.virtual_player_core.safety_metrics import ECONOMY_CAP_BREACH_METRIC, SAFETY_HEARTBEAT_METRIC
from gameplay.services.virtual_player_core.safety_policy import (
    ARENA_WARNING_CODES,
    arena_warning_scope,
    classify_reasons,
)
from tests.test_virtual_player_safety_monitor import HOURLY_START, _routing_state, _window

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("code", sorted(ARENA_WARNING_CODES))
def test_exact_warning_whitelist_and_unknown_scope_fail_closed(code):
    reason = f"{code}:coop:middle"
    assert classify_reasons((reason, "economy_cap_breach")) == (("economy_cap_breach",), (reason,))
    for invalid in (code, reason + ":extra", f"{code}:unknown:middle", f"{code}:coop:unknown", " " + reason):
        assert arena_warning_scope(invalid) is None
        assert classify_reasons((invalid,))[0] == (invalid,)
    assert classify_reasons(("arena_shortage_baseline_conflict:coop:middle",))[0]


@pytest.mark.parametrize(
    "invalid_reason",
    [
        "arena_shortage_baseline_missing:coop:unknown",
        "arena_shortage_baseline_missing:unknown:middle",
        "arena_shortage_baseline_missing:coop:middle:extra",
        "arena_shortage_unknown_code:coop:middle",
    ],
)
@pytest.mark.parametrize("policy", ["scoped", "legacy", "shadow"])
def test_malformed_arena_warning_is_never_auto_recoverable(settings, policy, invalid_reason):
    settings.VIRTUAL_PLAYER_ARENA_SAFETY_POLICY = policy

    assert runtime_configs.is_recoverable_safety_pause_reason(invalid_reason) is False


@pytest.mark.parametrize("policy,paused", [("scoped", False), ("shadow", True), ("legacy", True)])
@pytest.mark.parametrize("hard_breach", [False, True])
def test_missing_baseline_policy_actions_and_atomic_audit(settings, policy, paused, hard_breach):
    settings.VIRTUAL_PLAYER_ARENA_SAFETY_POLICY = policy
    _routing_state(hourly_cursor=HOURLY_START)
    window = _window(
        metrics={"economy_cap_breach_count": int(hard_breach)},
        arena=[
            {
                "kind": "coop",
                "prestige_band": "middle",
                "sample_count": 3,
                "current_ratio_max": 0.4,
                "real_entry_count_min": 3,
                "reserve_ready_count_min": 0,
                "reserve_training_count_max": 0,
            }
        ],
    )
    decision = safety_monitor.evaluate_finalized_safety_window(window, now=window.window_end_at)
    effective_pause = paused or hard_breach
    assert decision.should_pause is effective_pause
    assert decision.warning_reasons == ("arena_shortage_baseline_missing:coop:middle",)
    result, _ = safety_monitor._apply_with_cas_retry(decision, max_cas_attempts=1)
    assert result.paused is effective_pause
    audit = BotSafetyDecisionAudit.objects.get()
    assert audit.policy_mode == policy
    assert audit.effective_pause is effective_pause
    assert audit.scoped_pause is hard_breach
    warning = next(reason for reason in audit.reasons if reason["severity"] == "warning")
    assert warning["scope"] == {"kind": "coop", "prestige_band": "middle"}
    safety_monitor._apply_with_cas_retry(decision, max_cas_attempts=1)
    assert BotSafetyDecisionAudit.objects.count() == 1


def test_hard_reason_survives_later_warning_and_recoverable_outage(settings):
    settings.VIRTUAL_PLAYER_ARENA_SAFETY_POLICY = "scoped"
    _routing_state(hourly_cursor=HOURLY_START)
    for index, (should_pause, reason, warnings) in enumerate(
        (
            (True, "economy_cap_breach", ("arena_shortage_baseline_missing:coop:middle",)),
            (False, "", ("arena_shortage_baseline_missing:coop:middle",)),
            (True, "heartbeat_incomplete:safety_monitor", ()),
        )
    ):
        runtime_configs.apply_virtual_player_safety_decision(
            expected_revision=4 + index,
            window_id=f"hard-preservation:{index}",
            window_kind="hourly",
            window_end_at=HOURLY_START + timedelta(hours=index + 1),
            should_pause=should_pause,
            pause_reason=reason,
            warning_reasons=warnings,
        )
    routing = runtime_configs.read_virtual_player_routing()
    assert "economy_cap_breach" in routing.pause_reason
    assert not runtime_configs.is_recoverable_safety_pause_reason(routing.pause_reason)
    assert BotSafetyDecisionAudit.objects.count() == 3


def test_audit_failure_rolls_back_cursor_and_pause(monkeypatch):
    state = _routing_state(hourly_cursor=HOURLY_START)

    def fail_audit(**kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(BotSafetyDecisionAudit.objects, "create", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        runtime_configs.apply_virtual_player_safety_decision(
            expected_revision=4,
            window_id="rollback",
            window_kind="hourly",
            window_end_at=HOURLY_START + timedelta(hours=1),
            should_pause=True,
            pause_reason="economy_cap_breach",
        )
    state.refresh_from_db()
    assert state.revision == 4 and state.maintenance_mode == "v2_active"
    assert state.last_hourly_safety_window_end_at == HOURLY_START


def _recovery_evidence():
    now = timezone.now()
    ends = {kind: safety_monitor._latest_mature_window_end(now, window_kind=kind) for kind in ("hourly", "daily")}
    state = _routing_state(hourly_cursor=ends["hourly"], daily_cursor=ends["daily"])
    state.maintenance_mode = "v2_paused"
    state.paused_from_maintenance_mode = "v2_active"
    state.pause_reason = "arena_shortage_baseline_missing:coop:middle"
    state.save()
    for kind, count in (("hourly", 3), ("daily", 2)):
        step = timedelta(hours=1) if kind == "hourly" else timedelta(days=1)
        for index in range(count):
            _window(kind=kind, start_at=ends[kind] - (index + 1) * step)
    heartbeat_at = now.replace(second=0, microsecond=0)
    BotSafetyMetricEvent.objects.create(
        event_id=f"safety-heartbeat:safety_monitor:{heartbeat_at.strftime('%Y%m%dT%H%MZ')}",
        metric_name=SAFETY_HEARTBEAT_METRIC,
        occurred_at=heartbeat_at,
        dimensions={"stream": "safety_monitor"},
        value=1,
        payload_digest="a" * 64,
    )
    return state, now


def test_recovery_dry_run_select_only_then_cas_resume(settings):
    settings.VIRTUAL_PLAYER_ARENA_SAFETY_POLICY = "scoped"
    state, now = _recovery_evidence()
    with CaptureQueriesContext(connection) as queries:
        assessment = recovery.assess_arena_pause_recovery(now=now)
    assert assessment.allowed, assessment.blockers
    assert all(q["sql"].lstrip().upper().startswith("SELECT") for q in queries)
    assert assessment.preflight["allowed"] is True
    assert set(assessment.window_evidence) == {"hourly", "daily"}
    assert all(assessment.window_evidence[kind]["complete"] for kind in ("hourly", "daily"))
    assert set(assessment.runtime_budget) == {"materialization", "training", "growth"}
    assert assessment.reserve_lease["active_claims"] == 0
    assert not runtime_configs.is_recoverable_safety_pause_reason(state.pause_reason)
    with pytest.raises(runtime_configs.RuntimeRoutingConflict):
        recovery.recover_arena_pause(expected_revision=state.revision + 1, expected_pause_reason=state.pause_reason)
    after = recovery.recover_arena_pause(expected_revision=state.revision, expected_pause_reason=state.pause_reason)
    assert after.maintenance_mode.value == "v2_active"
    assert BotSafetyDecisionAudit.objects.get().operation == "recovery"


def test_recovery_refuses_structurally_invalid_runtime_budget(settings):
    settings.VIRTUAL_PLAYER_ARENA_SAFETY_POLICY = "scoped"
    settings.ARENA_LOW_POPULATION_MODE = True
    _recovery_evidence()
    from gameplay.models import ArenaRuntimeBudget

    ArenaRuntimeBudget.objects.filter(pk="growth").update(entries=[{"demand_id": 1, "at": timezone.now().timestamp()}])
    assessment = recovery.assess_arena_pause_recovery(now=timezone.now())
    assert not assessment.allowed
    assert "arena_runtime_budget_growth_invalid" in assessment.blockers


@pytest.mark.parametrize(
    "fault", ["manual", "mixed", "origin", "missing_window", "corrupt", "heartbeat", "pending_hard", "lagging_monitor"]
)
def test_legacy_pause_recovery_refuses_uncertainty(settings, fault):
    settings.VIRTUAL_PLAYER_ARENA_SAFETY_POLICY = "scoped"
    state, now = _recovery_evidence()
    if fault == "manual":
        state.pause_reason = "manual"
    elif fault == "mixed":
        state.pause_reason += ",economy_cap_breach"
    elif fault == "origin":
        state.paused_from_maintenance_mode = "v2_cutover"
    elif fault == "missing_window":
        BotSafetyMetricWindow.objects.filter(kind="daily").delete()
    elif fault == "corrupt":
        BotSafetyMetricWindow.objects.update(snapshot_digest="a" * 64)
    elif fault == "heartbeat":
        BotSafetyMetricEvent.objects.all().delete()
    elif fault == "pending_hard":
        BotSafetyMetricEvent.objects.create(
            event_id="pending-hard",
            metric_name=ECONOMY_CAP_BREACH_METRIC,
            occurred_at=now,
            dimensions={},
            value=1,
            payload_digest="b" * 64,
        )
    else:
        state.last_hourly_safety_window_end_at -= timedelta(hours=1)
    state.save()
    assert not recovery.assess_arena_pause_recovery(now=now).allowed
    with pytest.raises(runtime_configs.RuntimeRoutingGateBlocked):
        recovery.recover_arena_pause(expected_revision=state.revision, expected_pause_reason=state.pause_reason)
    assert not BotSafetyDecisionAudit.objects.exists()
