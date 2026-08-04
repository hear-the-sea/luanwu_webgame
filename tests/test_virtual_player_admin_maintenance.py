from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from django.contrib import admin
from django.test import Client
from django.urls import reverse

from gameplay.models import (
    BotExternalStrengthReconciliation,
    BotPolicyRelease,
    BotPopulationRecomputeDemand,
    BotProfile,
    BotRuntimeRoutingState,
    BotVirtualPlayerHealth,
)
from gameplay.services.virtual_player_core.contracts import (
    MaintenanceOutcome,
    MaintenanceResult,
    MaintenanceScheduleDisposition,
    MaintenanceTrigger,
)

NOW = datetime(2026, 7, 28, 8, 0, tzinfo=UTC)


def test_virtual_player_v2_admin_models_are_explicit_read_only_exports() -> None:
    from gameplay import admin as gameplay_admin

    expected = {
        BotPolicyRelease: gameplay_admin.BotPolicyReleaseAdmin,
        BotExternalStrengthReconciliation: (gameplay_admin.BotExternalStrengthReconciliationAdmin),
        BotRuntimeRoutingState: gameplay_admin.BotRuntimeRoutingStateAdmin,
        BotPopulationRecomputeDemand: (gameplay_admin.BotPopulationRecomputeDemandAdmin),
        BotVirtualPlayerHealth: gameplay_admin.BotVirtualPlayerHealthAdmin,
    }
    for model, admin_class in expected.items():
        registered = admin.site._registry[model]
        assert isinstance(registered, admin_class)
        assert registered.has_add_permission(None) is False
        assert registered.has_change_permission(None) is False
        assert registered.has_delete_permission(None) is False


def test_virtual_player_v2_admin_list_columns_are_explicit_and_sortable() -> None:
    from gameplay import admin as gameplay_admin

    policy_admin = admin.site._registry[BotPolicyRelease]
    reconciliation_admin = admin.site._registry[BotExternalStrengthReconciliation]
    routing_admin = admin.site._registry[BotRuntimeRoutingState]
    population_admin = admin.site._registry[BotPopulationRecomputeDemand]

    assert isinstance(policy_admin, gameplay_admin.BotPolicyReleaseAdmin)
    assert policy_admin.list_display == (
        "version",
        "checksum",
        "released_at",
        "retire_not_before",
        "retired_at",
    )
    assert policy_admin.ordering == ("-version",)

    assert isinstance(
        reconciliation_admin,
        gameplay_admin.BotExternalStrengthReconciliationAdmin,
    )
    assert reconciliation_admin.list_display == (
        "reconciliation_identifier",
        "profile_identifier",
        "domain_event_kind",
        "domain_event_identifier",
        "status",
        "profile_attempt_count",
        "population_attempt_count",
        "available_at",
        "updated_at",
    )
    assert reconciliation_admin.ordering == (
        "available_at",
        "profile_id",
        "origin_committed_at",
        "id",
    )

    assert isinstance(routing_admin, gameplay_admin.BotRuntimeRoutingStateAdmin)
    assert routing_admin.list_display == (
        "routing_key",
        "revision",
        "bootstrap_mode_label",
        "maintenance_mode_label",
        "last_hourly_safety_window_end_at",
        "last_daily_safety_window_end_at",
        "last_pause_window_identifier",
        "safety_clean_window_streak",
        "safety_clean_window_kind",
        "paused_from_maintenance_mode",
        "pause_reason",
        "updated_at",
    )

    assert isinstance(
        population_admin,
        gameplay_admin.BotPopulationRecomputeDemandAdmin,
    )
    assert population_admin.list_display == (
        "region",
        "prestige_band",
        "requested_revision",
        "completed_revision",
        "claimed_revision",
        "available_at",
        "consecutive_failure_count",
        "updated_at",
    )
    assert population_admin.ordering == (
        "available_at",
        "region",
        "prestige_band",
    )

    expected_displays = (
        (
            reconciliation_admin.reconciliation_identifier,
            "对账编号",
            "id",
        ),
        (
            reconciliation_admin.profile_identifier,
            "虚拟玩家档案编号",
            "profile_id",
        ),
        (
            reconciliation_admin.domain_event_identifier,
            "领域事件编号",
            "domain_event_id",
        ),
        (routing_admin.routing_key, "路由键", "key"),
        (routing_admin.bootstrap_mode_label, "启动模式", "bootstrap_mode"),
        (
            routing_admin.maintenance_mode_label,
            "维护模式",
            "maintenance_mode",
        ),
        (
            routing_admin.last_pause_window_identifier,
            "最近暂停窗口编号",
            "last_pause_window_id",
        ),
    )
    for display, description, ordering in expected_displays:
        assert display.short_description == description
        assert display.admin_order_field == ordering


def test_virtual_player_admin_exposes_v2_versions_and_pause_summary() -> None:
    profile_admin = admin.site._registry[BotProfile]
    assert {
        "engine_version",
        "rng_version",
        "plan_schema_version",
        "policy_version",
    } <= set(profile_admin.list_display)
    assert {
        "engine_version",
        "rng_version",
        "plan_schema_version",
        "policy_version",
    } <= set(profile_admin.list_filter)

    routing_admin = admin.site._registry[BotRuntimeRoutingState]
    assert {"last_pause_window_identifier", "pause_reason"} <= set(routing_admin.list_display)


def _maintenance_result(
    *,
    profile_id: int,
    disposition: MaintenanceScheduleDisposition,
    current_time: datetime = NOW,
) -> MaintenanceResult:
    next_growth_at_after = current_time
    if disposition is MaintenanceScheduleDisposition.ADVANCE_NORMAL_SCHEDULE:
        next_growth_at_after += timedelta(hours=1)
    return MaintenanceResult(
        outcome=MaintenanceOutcome.NO_ACTION,
        trigger=MaintenanceTrigger.ADMIN,
        profile_id=profile_id,
        sequence_before=4,
        sequence_after=5,
        schedule_disposition=disposition,
        next_growth_at_before=current_time,
        next_growth_at_after=next_growth_at_after,
        reason="domain_constraint",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("requires_due", [False, True])
@pytest.mark.parametrize(
    "disposition",
    list(MaintenanceScheduleDisposition),
)
def test_admin_maintenance_endpoint_calls_v2_boundary_with_explicit_semantics(
    client,
    django_user_model,
    monkeypatch,
    requires_due: bool,
    disposition: MaintenanceScheduleDisposition,
) -> None:
    from gameplay.admin import bots

    admin_user = django_user_model.objects.create_superuser(
        username=f"maintenance_admin_{requires_due}_{disposition.value}",
        password="pass123",
    )
    calls = []

    def fake_maintain(profile_id, **kwargs):
        calls.append((profile_id, kwargs))
        return _maintenance_result(
            profile_id=profile_id,
            disposition=kwargs["admin_schedule_disposition"],
        )

    monkeypatch.setattr(bots, "maintain_virtual_player_v2", fake_maintain)
    client.force_login(admin_user)

    response = client.post(
        reverse("admin:gameplay_botprofile_maintenance_v2"),
        {
            "csrfmiddlewaretoken": "known-transport-field",
            "profile_id": "41",
            "requires_due": str(requires_due).lower(),
            "schedule_disposition": disposition.value,
        },
    )

    assert response.status_code == 200
    assert calls == [
        (
            41,
            {
                "trigger": MaintenanceTrigger.ADMIN,
                "admin_requires_due": requires_due,
                "admin_schedule_disposition": disposition,
            },
        )
    ]
    assert response.json() == {
        "profile_id": 41,
        "outcome": "no_action",
        "trigger": "admin",
        "sequence_before": 4,
        "sequence_after": 5,
        "schedule_disposition": disposition.value,
        "next_growth_at_before": "2026-07-28T08:00:00Z",
        "next_growth_at_after": (
            "2026-07-28T09:00:00Z"
            if disposition is MaintenanceScheduleDisposition.ADVANCE_NORMAL_SCHEDULE
            else "2026-07-28T08:00:00Z"
        ),
        "action_kind": "",
        "reason": "domain_constraint",
    }


def test_admin_maintenance_payload_normalizes_aware_times_to_utc() -> None:
    from gameplay.admin.bots import _admin_maintenance_result_payload

    result = _maintenance_result(
        profile_id=41,
        disposition=MaintenanceScheduleDisposition.PRESERVE_NORMAL_SCHEDULE,
        current_time=NOW.astimezone(timezone(timedelta(hours=8))),
    )

    payload = _admin_maintenance_result_payload(result)

    assert payload["next_growth_at_before"] == "2026-07-28T08:00:00Z"
    assert payload["next_growth_at_after"] == "2026-07-28T08:00:00Z"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        (
            {
                "profile_id": "41",
                "schedule_disposition": "preserve_normal_schedule",
            },
            "requires_due must be provided exactly once",
        ),
        (
            {"profile_id": "41", "requires_due": "true"},
            "schedule_disposition must be provided exactly once",
        ),
        (
            {
                "profile_id": "41",
                "requires_due": "1",
                "schedule_disposition": "preserve_normal_schedule",
            },
            "requires_due must be exactly 'true' or 'false'",
        ),
        (
            {
                "profile_id": "41",
                "requires_due": "false",
                "schedule_disposition": "default",
            },
            "schedule_disposition is invalid",
        ),
        (
            {
                "profile_id": "0",
                "requires_due": "false",
                "schedule_disposition": "preserve_normal_schedule",
            },
            "profile_id must be a positive integer",
        ),
        (
            {
                "profile_id": "41",
                "requires_due": "false",
                "schedule_disposition": "preserve_normal_schedule",
                "minimum_guest_count": "999",
            },
            "unknown parameters: minimum_guest_count",
        ),
    ],
)
def test_admin_maintenance_endpoint_rejects_missing_or_invalid_semantics(
    client,
    django_user_model,
    monkeypatch,
    payload,
    detail: str,
) -> None:
    from gameplay.admin import bots

    admin_user = django_user_model.objects.create_superuser(
        username=f"invalid_maintenance_admin_{detail[:12]}",
        password="pass123",
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("maintenance service must not be called")

    monkeypatch.setattr(bots, "maintain_virtual_player_v2", fail_if_called)
    client.force_login(admin_user)

    response = client.post(
        reverse("admin:gameplay_botprofile_maintenance_v2"),
        payload,
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_admin_maintenance_request",
        "detail": detail,
    }


@pytest.mark.django_db
def test_admin_maintenance_endpoint_rejects_duplicate_semantics(
    client,
    django_user_model,
    monkeypatch,
) -> None:
    from gameplay.admin import bots

    admin_user = django_user_model.objects.create_superuser(
        username="duplicate_maintenance_admin",
        password="pass123",
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("maintenance service must not be called")

    monkeypatch.setattr(bots, "maintain_virtual_player_v2", fail_if_called)
    client.force_login(admin_user)

    response = client.post(
        reverse("admin:gameplay_botprofile_maintenance_v2"),
        {
            "profile_id": "41",
            "requires_due": ["true", "false"],
            "schedule_disposition": "preserve_normal_schedule",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "requires_due must be provided exactly once"


@pytest.mark.django_db
def test_admin_maintenance_endpoint_maps_v2_conflict_to_stable_json(
    client,
    django_user_model,
    monkeypatch,
) -> None:
    from gameplay.admin import bots

    admin_user = django_user_model.objects.create_superuser(
        username="conflicting_maintenance_admin",
        password="pass123",
    )

    def raise_conflict(*_args, **_kwargs):
        raise bots.V2MaintenanceError("maintenance_sequence_conflict")

    monkeypatch.setattr(bots, "maintain_virtual_player_v2", raise_conflict)
    client.force_login(admin_user)

    response = client.post(
        reverse("admin:gameplay_botprofile_maintenance_v2"),
        {
            "profile_id": "41",
            "requires_due": "false",
            "schedule_disposition": "preserve_normal_schedule",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": "v2_maintenance_conflict",
        "detail": "maintenance_sequence_conflict",
    }


@pytest.mark.django_db
def test_admin_maintenance_endpoint_requires_post_and_change_permission(
    client,
    django_user_model,
    monkeypatch,
) -> None:
    from gameplay.admin import bots

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("maintenance service must not be called")

    monkeypatch.setattr(bots, "maintain_virtual_player_v2", fail_if_called)
    admin_user = django_user_model.objects.create_superuser(
        username="get_maintenance_admin",
        password="pass123",
    )
    client.force_login(admin_user)
    url = reverse("admin:gameplay_botprofile_maintenance_v2")

    assert client.get(url).status_code == 405

    staff_user = django_user_model.objects.create_user(
        username="unauthorized_maintenance_admin",
        password="pass123",
        is_staff=True,
    )
    client.force_login(staff_user)
    response = client.post(
        url,
        {
            "profile_id": "41",
            "requires_due": "false",
            "schedule_disposition": "preserve_normal_schedule",
        },
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_admin_maintenance_endpoint_is_csrf_protected(
    django_user_model,
    monkeypatch,
) -> None:
    from gameplay.admin import bots

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("maintenance service must not be called")

    monkeypatch.setattr(bots, "maintain_virtual_player_v2", fail_if_called)
    admin_user = django_user_model.objects.create_superuser(
        username="csrf_maintenance_admin",
        password="pass123",
    )
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(admin_user)

    response = csrf_client.post(
        reverse("admin:gameplay_botprofile_maintenance_v2"),
        {
            "profile_id": "41",
            "requires_due": "false",
            "schedule_disposition": "preserve_normal_schedule",
        },
    )

    assert response.status_code == 403
