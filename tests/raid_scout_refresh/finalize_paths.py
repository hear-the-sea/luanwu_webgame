from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from battle.models import TroopTemplate
from gameplay.constants import PVPConstants
from gameplay.models import PlayerTroop, ScoutCooldown, ScoutRecord
from gameplay.services.raid import scout as scout_service
from tests.raid_scout_refresh.support import build_attacker_defender


@pytest.mark.django_db(transaction=True)
def test_finalize_scout_return_marks_retreated_records_without_failure_message(django_user_model, monkeypatch):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="scout_retreat_attacker",
        defender_username="scout_retreat_defender",
    )
    scout_template, _ = TroopTemplate.objects.get_or_create(key=PVPConstants.SCOUT_TROOP_KEY, defaults={"name": "探子"})
    troop, _ = PlayerTroop.objects.update_or_create(
        manor=attacker,
        troop_template=scout_template,
        defaults={"count": 3},
    )

    record = ScoutRecord.objects.create(
        attacker=attacker,
        defender=defender,
        status=ScoutRecord.Status.SCOUTING,
        scout_cost=1,
        success_rate=0.5,
        travel_time=60,
        complete_at=timezone.now() + timedelta(seconds=60),
    )

    request_time = timezone.now()
    monkeypatch.setattr(scout_service.timezone, "now", lambda: request_time)
    monkeypatch.setattr(scout_service.scout_followups, "safe_apply_async", lambda *_args, **_kwargs: True)

    scout_service.request_scout_retreat(record)

    record.refresh_from_db()
    troop.refresh_from_db()
    assert record.status == ScoutRecord.Status.RETURNING
    assert record.was_retreated is True
    assert record.is_success is None
    assert troop.count == 4

    sent = {"retreat": 0, "fail": 0}
    monkeypatch.setattr(
        scout_service.scout_followups,
        "send_scout_retreat_message",
        lambda *_args, **_kwargs: sent.__setitem__("retreat", sent["retreat"] + 1),
    )
    monkeypatch.setattr(
        scout_service.scout_followups,
        "send_scout_fail_message",
        lambda *_args, **_kwargs: sent.__setitem__("fail", sent["fail"] + 1),
    )
    callbacks = []
    monkeypatch.setattr(
        scout_service.scout_followups,
        "schedule_best_effort_after_commit",
        lambda callback, **_kwargs: callbacks.append(callback),
    )

    complete_time = request_time + timedelta(seconds=5)
    scout_service.finalize_scout_return(record, now=complete_time)

    record.refresh_from_db()
    troop.refresh_from_db()
    assert record.status == ScoutRecord.Status.FAILED
    assert record.was_retreated is True
    assert record.completed_at == complete_time
    assert troop.count == 4
    assert len(callbacks) == 1
    assert sent == {"retreat": 0, "fail": 0}

    callbacks[0]()

    assert sent == {"retreat": 1, "fail": 0}


@pytest.mark.django_db(transaction=True)
def test_finalize_scout_return_recreates_missing_scout_troop_row_for_success(django_user_model, monkeypatch):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="scout_success_restore_attacker",
        defender_username="scout_success_restore_defender",
    )
    scout_template, _ = TroopTemplate.objects.get_or_create(key=PVPConstants.SCOUT_TROOP_KEY, defaults={"name": "探子"})
    PlayerTroop.objects.filter(manor=attacker, troop_template=scout_template).delete()

    record = ScoutRecord.objects.create(
        attacker=attacker,
        defender=defender,
        status=ScoutRecord.Status.RETURNING,
        scout_cost=3,
        success_rate=1.0,
        return_at=timezone.now() + timedelta(seconds=30),
        complete_at=timezone.now() - timedelta(seconds=30),
        travel_time=30,
        is_success=True,
        intel_data={"troop_description": "少量", "guest_count": 1, "avg_guest_level": 1, "asset_level": "普通"},
    )

    callbacks = []
    monkeypatch.setattr(
        scout_service.scout_followups,
        "schedule_best_effort_after_commit",
        lambda callback, **_kwargs: callbacks.append(callback),
    )
    monkeypatch.setattr(scout_service.scout_followups, "send_scout_success_message", lambda *_args, **_kwargs: None)

    complete_time = timezone.now()
    scout_service.finalize_scout_return(record, now=complete_time)

    record.refresh_from_db()
    troop = PlayerTroop.objects.get(manor=attacker, troop_template=scout_template)
    assert record.status == ScoutRecord.Status.SUCCESS
    assert record.completed_at == complete_time
    assert troop.count == 3
    assert len(callbacks) == 1


@pytest.mark.django_db(transaction=True)
def test_finalize_scout_detected_message_runs_after_commit_and_failure_does_not_rollback(
    django_user_model, monkeypatch
):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="scout_detect_attacker",
        defender_username="scout_detect_defender",
    )
    record = ScoutRecord.objects.create(
        attacker=attacker,
        defender=defender,
        status=ScoutRecord.Status.SCOUTING,
        scout_cost=1,
        success_rate=0.0,
        travel_time=45,
        complete_at=timezone.now() + timedelta(seconds=45),
    )

    monkeypatch.setattr(scout_service, "_roll_scout_success", lambda: 0.5)
    dispatched = []
    monkeypatch.setattr(
        scout_service.scout_followups.scout_refresh_command,
        "resolve_scout_task",
        lambda task_name: SimpleNamespace(name=task_name),
    )
    monkeypatch.setattr(
        scout_service.scout_followups,
        "safe_apply_async",
        lambda task, *, args, countdown, **_kwargs: dispatched.append(
            {
                "task_name": getattr(task, "name", str(task)),
                "args": args,
                "countdown": countdown,
            }
        )
        or True,
    )

    sent = {"count": 0}

    def _fail_detected(*_args, **_kwargs):
        sent["count"] += 1
        from core.exceptions import MessageError

        raise MessageError("message backend down")

    monkeypatch.setattr(scout_service.scout_followups, "send_scout_detected_message", _fail_detected)
    callbacks = []
    monkeypatch.setattr(
        scout_service.scout_followups,
        "schedule_best_effort_after_commit",
        lambda callback, **_kwargs: callbacks.append(callback),
    )

    now = timezone.now()
    scout_service.finalize_scout(record, now=now)

    record.refresh_from_db()
    cooldown = ScoutCooldown.objects.get(attacker=attacker, defender=defender)
    assert record.status == ScoutRecord.Status.RETURNING
    assert record.is_success is False
    assert record.return_at == now + timedelta(seconds=record.travel_time)
    assert int((cooldown.cooldown_until - now).total_seconds()) == 10 * 60
    assert sent["count"] == 0
    assert dispatched == []
    assert len(callbacks) == 2

    for callback in callbacks:
        callback()

    record.refresh_from_db()
    assert record.status == ScoutRecord.Status.RETURNING
    assert record.is_success is False
    assert sent["count"] == 1
    assert dispatched == [
        {
            "task_name": "complete_scout_return_task",
            "args": [record.id],
            "countdown": record.travel_time,
        }
    ]
