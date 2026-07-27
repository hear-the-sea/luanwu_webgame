from __future__ import annotations

import contextlib
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from battle.models import TroopTemplate
from core.exceptions import ScoutStartError
from gameplay.constants import PVPConstants
from gameplay.models import PlayerTroop, ScoutRecord
from gameplay.services.raid import scout as scout_service
from tests.raid_scout_refresh.support import build_attacker_defender


def test_start_scout_rechecks_attack_constraints_inside_transaction(monkeypatch):
    attacker = SimpleNamespace(pk=1, id=1)
    defender = SimpleNamespace(pk=2, id=2)
    calls = {"can_attack": 0}

    def _fake_can_attack(*_args, **_kwargs):
        calls["can_attack"] += 1
        if calls["can_attack"] == 1:
            return True, ""
        return False, "对方处于免战牌保护期"

    monkeypatch.setattr(scout_service.scout_start_command.transaction, "atomic", contextlib.nullcontext)
    monkeypatch.setattr(scout_service, "can_attack_target", _fake_can_attack)
    monkeypatch.setattr(scout_service, "check_scout_cooldown", lambda *_args, **_kwargs: (False, None))
    monkeypatch.setattr(scout_service, "get_scout_count", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(scout_service, "_lock_manor_pair", lambda *_args, **_kwargs: (attacker, defender))

    with pytest.raises(ScoutStartError, match="免战牌保护期"):
        scout_service.start_scout(attacker, defender)

    assert calls["can_attack"] == 2


def test_start_scout_precheck_uses_uncached_attack_check(monkeypatch):
    attacker = object()
    defender = object()
    seen = {"use_cached_recent_attacks": None, "check_defeat_protection": None}

    def _fake_can_attack(*_args, **kwargs):
        seen["use_cached_recent_attacks"] = kwargs.get("use_cached_recent_attacks")
        seen["check_defeat_protection"] = kwargs.get("check_defeat_protection")
        return False, "blocked"

    monkeypatch.setattr(scout_service, "can_attack_target", _fake_can_attack)

    with pytest.raises(ScoutStartError, match="blocked"):
        scout_service.start_scout(attacker, defender)

    assert seen["use_cached_recent_attacks"] is False
    assert seen["check_defeat_protection"] is False


def test_lock_manor_pair_raises_scout_start_error_when_target_missing(monkeypatch):
    class _Objects:
        @staticmethod
        def select_for_update():
            return _Objects()

        @staticmethod
        def filter(**_kwargs):
            return _Objects()

        @staticmethod
        def order_by(*_args, **_kwargs):
            return []

    dummy_manor_model = type("_Manor", (), {"objects": _Objects()})
    monkeypatch.setattr(scout_service, "Manor", dummy_manor_model)

    with pytest.raises(ScoutStartError, match="目标庄园不存在"):
        scout_service._lock_manor_pair(1, 2)


def test_calculate_scout_travel_time_uses_halved_distance_and_base_time(monkeypatch):
    attacker = SimpleNamespace(coordinate_x=0, coordinate_y=0)
    defender = SimpleNamespace(coordinate_x=10, coordinate_y=0)
    monkeypatch.setattr(scout_service, "scale_duration", lambda seconds, minimum=1: max(minimum, int(seconds)))

    assert scout_service.calculate_scout_travel_time(attacker, defender) == 40


@pytest.mark.django_db(transaction=True)
def test_request_scout_retreat_recreates_missing_scout_troop_row(django_user_model, monkeypatch):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="scout_retreat_restore_attacker",
        defender_username="scout_retreat_restore_defender",
    )
    scout_template, _ = TroopTemplate.objects.get_or_create(key=PVPConstants.SCOUT_TROOP_KEY, defaults={"name": "探子"})
    PlayerTroop.objects.filter(manor=attacker, troop_template=scout_template).delete()

    record = ScoutRecord.objects.create(
        attacker=attacker,
        defender=defender,
        status=ScoutRecord.Status.SCOUTING,
        scout_cost=2,
        success_rate=0.5,
        travel_time=60,
        complete_at=timezone.now() + timedelta(seconds=60),
    )

    request_time = timezone.now()
    ScoutRecord.objects.filter(pk=record.pk).update(started_at=request_time - timedelta(seconds=90))
    scheduled: list[int] = []
    monkeypatch.setattr(scout_service.timezone, "now", lambda: request_time)
    monkeypatch.setattr(
        scout_service.scout_followups,
        "safe_apply_async",
        lambda *_args, countdown, **_kwargs: scheduled.append(countdown) or True,
    )

    scout_service.request_scout_retreat(record)

    record.refresh_from_db()
    troop = PlayerTroop.objects.get(manor=attacker, troop_template=scout_template)
    assert record.status == ScoutRecord.Status.RETURNING
    assert record.was_retreated is True
    assert record.return_at == request_time + timedelta(seconds=60)
    assert troop.count == 2
    assert scheduled == [60]


@pytest.mark.django_db(transaction=True)
def test_start_scout_dispatch_runs_after_commit(django_user_model, monkeypatch):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="scout_start_attacker",
        defender_username="scout_start_defender",
    )
    scout_template, _ = TroopTemplate.objects.get_or_create(key=PVPConstants.SCOUT_TROOP_KEY, defaults={"name": "探子"})
    troop, _ = PlayerTroop.objects.update_or_create(
        manor=attacker,
        troop_template=scout_template,
        defaults={"count": 2},
    )

    callbacks = []
    monkeypatch.setattr(
        scout_service.scout_followups,
        "schedule_best_effort_after_commit",
        lambda callback, **_kwargs: callbacks.append(callback),
    )
    monkeypatch.setattr(scout_service, "can_attack_target", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(scout_service, "calculate_scout_success_rate", lambda *_args, **_kwargs: 0.5)
    monkeypatch.setattr(scout_service, "calculate_scout_travel_time", lambda *_args, **_kwargs: 45)
    monkeypatch.setattr(
        scout_service.scout_followups.scout_refresh_command,
        "resolve_scout_task",
        lambda task_name: type("_Task", (), {"name": task_name})(),
    )

    dispatched = []
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

    record = scout_service.start_scout(attacker, defender)

    troop.refresh_from_db()
    record.refresh_from_db()
    assert troop.count == 1
    assert record.status == ScoutRecord.Status.SCOUTING
    assert len(callbacks) == 1
    assert dispatched == []

    callbacks[0]()

    assert dispatched == [
        {
            "task_name": "complete_scout_task",
            "args": [record.id],
            "countdown": 45,
        }
    ]


@pytest.mark.django_db(transaction=True)
def test_start_scout_does_not_require_action_points(django_user_model, monkeypatch):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="scout_no_action_points_attacker",
        defender_username="scout_no_action_points_defender",
    )
    attacker.action_points = 0
    attacker.save(update_fields=["action_points"])
    scout_template, _ = TroopTemplate.objects.get_or_create(key=PVPConstants.SCOUT_TROOP_KEY, defaults={"name": "探子"})
    PlayerTroop.objects.update_or_create(
        manor=attacker,
        troop_template=scout_template,
        defaults={"count": 2},
    )

    monkeypatch.setattr(scout_service, "can_attack_target", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(scout_service, "check_scout_cooldown", lambda *_args, **_kwargs: (False, None))
    monkeypatch.setattr(scout_service, "calculate_scout_success_rate", lambda *_args, **_kwargs: 0.5)
    monkeypatch.setattr(scout_service, "calculate_scout_travel_time", lambda *_args, **_kwargs: 45)
    monkeypatch.setattr(scout_service.scout_followups, "schedule_scout_completion", lambda *_args, **_kwargs: None)

    record = scout_service.start_scout(attacker, defender)

    attacker.refresh_from_db()
    troop = PlayerTroop.objects.get(manor=attacker, troop_template=scout_template)
    assert record.status == ScoutRecord.Status.SCOUTING
    assert attacker.action_points == 0
    assert troop.count == 1
