from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from gameplay.models import ArenaFillServiceEpisode, ArenaFillServiceOutcome, ResourceEvent, ResourceType
from gameplay.services.manor.core import ensure_manor


@pytest.mark.django_db
def test_cleanup_data_report_human_output_contains_stale_count(django_user_model):
    user = django_user_model.objects.create_user(
        username="cleanup_report_user",
        password="pass123",
        email="cleanup_report_user@test.local",
    )
    manor = ensure_manor(user)

    old_event = ResourceEvent.objects.create(
        manor=manor,
        resource_type=ResourceType.SILVER,
        delta=1,
        reason=ResourceEvent.Reason.ADMIN_ADJUST,
        note="old",
    )
    ResourceEvent.objects.filter(pk=old_event.pk).update(created_at=timezone.now() - timedelta(days=31))

    ResourceEvent.objects.create(
        manor=manor,
        resource_type=ResourceType.SILVER,
        delta=2,
        reason=ResourceEvent.Reason.ADMIN_ADJUST,
        note="new",
    )

    out = StringIO()
    call_command("cleanup_data_report", model="gameplay.ResourceEvent", stdout=out, verbosity=0)
    text = out.getvalue()

    assert "gameplay.ResourceEvent" in text
    assert "总量=2" in text
    assert "可清理=1" in text


@pytest.mark.django_db
def test_cleanup_data_report_json_output(django_user_model):
    user = django_user_model.objects.create_user(
        username="cleanup_report_json_user",
        password="pass123",
        email="cleanup_report_json_user@test.local",
    )
    manor = ensure_manor(user)

    old_event = ResourceEvent.objects.create(
        manor=manor,
        resource_type=ResourceType.GRAIN,
        delta=3,
        reason=ResourceEvent.Reason.ADMIN_ADJUST,
        note="old-json",
    )
    ResourceEvent.objects.filter(pk=old_event.pk).update(created_at=timezone.now() - timedelta(days=31))

    out = StringIO()
    call_command(
        "cleanup_data_report",
        model="gameplay.ResourceEvent",
        json=True,
        stdout=out,
        verbosity=0,
    )
    payload = json.loads(out.getvalue())

    assert payload["totals"]["models"] == 1
    assert payload["totals"]["total_records"] >= 1
    assert payload["totals"]["stale_records"] >= 1
    assert payload["rows"][0]["model"] == "gameplay.ResourceEvent"
    assert payload["rows"][0]["status"] == "ok"


@pytest.mark.django_db
def test_cleanup_data_report_includes_fill_service_retention_rows():
    old_at = timezone.now() - timedelta(days=91)
    episode = ArenaFillServiceEpisode.objects.create(
        mode="coop",
        event_id=7101,
        virtual_fill_at=old_at,
        checkpoint_at=old_at,
        first_observed_at=old_at,
        last_observed_at=old_at,
        closed_at=old_at,
        terminal_reason="fulfilled",
        slo_result="human_satisfied",
        last_snapshot={},
        pending_outcomes={},
        publication_pending=False,
    )
    outcome = ArenaFillServiceOutcome.objects.create(
        episode=episode,
        phase="slo",
        result="human_satisfied",
        logical_at=old_at,
        observed_at=old_at,
        payload={"result": "human_satisfied"},
    )
    ArenaFillServiceOutcome.objects.filter(pk=outcome.pk).update(published_at=old_at)

    out = StringIO()
    call_command("cleanup_data_report", model="gameplay.ArenaFillServiceEpisode", stdout=out, verbosity=0)
    text = out.getvalue()

    assert "gameplay.ArenaFillServiceEpisode" in text
    assert "可清理=1" in text
