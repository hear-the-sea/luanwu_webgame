from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import DatabaseError
from django.test import TestCase
from django.utils import timezone

from core.exceptions import MessageError
from gameplay.constants import PVPConstants
from gameplay.models import RaidRun
from gameplay.services.raid.combat import travel as combat_travel
from tests.raid_combat_battle.support import build_attacker_defender, build_locked_run


@pytest.mark.django_db
def test_raid_travel_time_ignores_scout_and_fast_troop_speed_bonus(django_user_model, monkeypatch):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="raid_travel_no_cavalry_bonus_attacker",
        defender_username="raid_travel_no_cavalry_bonus_defender",
    )
    attacker.region = "overseas"
    attacker.coordinate_x = 0
    attacker.coordinate_y = 0
    attacker.save(update_fields=["region", "coordinate_x", "coordinate_y"])
    defender.region = "overseas"
    defender.coordinate_x = 10
    defender.coordinate_y = 0
    defender.save(update_fields=["region", "coordinate_x", "coordinate_y"])

    monkeypatch.setattr(combat_travel, "scale_duration", lambda seconds, minimum=1: max(minimum, int(seconds)))

    guest = type("_Guest", (), {"agility": 100})()
    expected = int((PVPConstants.RAID_BASE_TRAVEL_TIME + 10 * PVPConstants.RAID_TRAVEL_TIME_PER_DISTANCE) * 0.9)

    baseline = combat_travel.calculate_raid_travel_time(attacker, defender, [guest], {})
    with_scout = combat_travel.calculate_raid_travel_time(attacker, defender, [guest], {"scout": 1})
    with_fast_troop = combat_travel.calculate_raid_travel_time(
        attacker,
        defender,
        [guest],
        {"fast_horse": 1},
    )

    assert baseline == expected
    assert with_scout == baseline
    assert with_fast_troop == baseline


@pytest.mark.django_db
def test_raid_travel_time_caps_at_eight_hours_and_agility_reduction_at_thirty_percent(django_user_model, monkeypatch):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="raid_travel_cap_attacker",
        defender_username="raid_travel_cap_defender",
    )
    attacker.region = "north"
    attacker.coordinate_x = 1
    attacker.coordinate_y = 1
    attacker.save(update_fields=["region", "coordinate_x", "coordinate_y"])
    defender.region = "south"
    defender.coordinate_x = 999
    defender.coordinate_y = 999
    defender.save(update_fields=["region", "coordinate_x", "coordinate_y"])
    monkeypatch.setattr(combat_travel, "scale_duration", lambda seconds, minimum=1: max(minimum, int(seconds)))

    no_agility = combat_travel.calculate_raid_travel_time(attacker, defender, [], {})
    high_agility_guest = type("_Guest", (), {"agility": 9999})()
    high_agility = combat_travel.calculate_raid_travel_time(attacker, defender, [high_agility_guest], {})

    assert no_agility == 8 * 60 * 60
    assert high_agility == int((8 * 60 * 60) * 0.7)


@pytest.mark.django_db
def test_dismiss_marching_raids_if_protected_reacts_to_defeat_protection(django_user_model, monkeypatch):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="raid_dismiss_attacker",
        defender_username="raid_dismiss_defender",
    )

    now = timezone.now()
    defender.defeat_protection_until = now + timedelta(minutes=30)
    defender.save(update_fields=["defeat_protection_until"])

    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        troop_loadout={},
        travel_time=60,
        battle_at=now + timedelta(seconds=30),
        return_at=now + timedelta(seconds=60),
    )

    sent_messages = []
    scheduled = []
    monkeypatch.setattr(combat_travel, "create_message", lambda **kwargs: sent_messages.append(kwargs))
    monkeypatch.setattr(
        combat_travel, "safe_apply_async", lambda task, **kwargs: scheduled.append((task, kwargs)) or True
    )

    import gameplay.tasks as gameplay_tasks

    monkeypatch.setattr(gameplay_tasks, "complete_raid_task", object(), raising=False)

    with TestCase.captureOnCommitCallbacks(execute=False) as callbacks:
        dismissed = combat_travel._dismiss_marching_raids_if_protected(defender)

    run.refresh_from_db()
    assert dismissed == 1
    assert run.status == RaidRun.Status.RETREATED
    assert run.return_at is not None and run.return_at > now
    assert len(scheduled) == 1
    assert sent_messages == []

    for callback in callbacks:
        callback()

    assert len(sent_messages) == 1
    assert "战败保护期" in sent_messages[0]["body"]


def test_resolve_complete_raid_task_missing_target_module_degrades(monkeypatch):
    def _missing_module(_name):
        exc = ModuleNotFoundError("No module named 'gameplay.tasks'")
        exc.name = "gameplay.tasks"
        raise exc

    monkeypatch.setattr(combat_travel, "import_module", _missing_module)

    assert combat_travel.resolve_complete_raid_task(logger=combat_travel.logger) is None


def test_resolve_complete_raid_task_nested_import_error_bubbles_up(monkeypatch):
    def _nested_import_failure(_name):
        exc = ModuleNotFoundError("No module named 'redis'")
        exc.name = "redis"
        raise exc

    monkeypatch.setattr(combat_travel, "import_module", _nested_import_failure)

    with pytest.raises(ModuleNotFoundError, match="redis"):
        combat_travel.resolve_complete_raid_task(logger=combat_travel.logger)


@pytest.mark.django_db
def test_dismiss_marching_raids_if_protected_db_message_failure_still_persists(django_user_model, monkeypatch):
    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="raid_dismiss_db_attacker",
        defender_username="raid_dismiss_db_defender",
    )

    now = timezone.now()
    defender.defeat_protection_until = now + timedelta(minutes=30)
    defender.save(update_fields=["defeat_protection_until"])

    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        troop_loadout={},
        travel_time=60,
        battle_at=now + timedelta(seconds=30),
        return_at=now + timedelta(seconds=60),
    )
    scheduled = []

    monkeypatch.setattr(
        combat_travel,
        "create_message",
        lambda **_kwargs: (_ for _ in ()).throw(DatabaseError("db write failed")),
    )
    monkeypatch.setattr(
        combat_travel, "safe_apply_async", lambda task, **kwargs: scheduled.append((task, kwargs)) or True
    )

    import gameplay.tasks as gameplay_tasks

    monkeypatch.setattr(gameplay_tasks, "complete_raid_task", object(), raising=False)

    with TestCase.captureOnCommitCallbacks(execute=False) as callbacks:
        dismissed = combat_travel._dismiss_marching_raids_if_protected(defender)

    run.refresh_from_db()
    assert dismissed == 1
    assert run.status == RaidRun.Status.RETREATED
    assert run.return_at is not None and run.return_at > now
    assert len(scheduled) == 1

    for callback in callbacks:
        callback()


def test_retreat_raid_run_due_to_blocked_target_programming_error_bubbles_up(monkeypatch):
    now = timezone.now()
    saved = {"fields": None}
    locked_run = build_locked_run(run_id=21, now=now, save_fields=saved)
    callbacks = []

    monkeypatch.setattr(
        combat_travel,
        "schedule_best_effort_after_commit",
        lambda callback, **_kwargs: callbacks.append(callback),
    )

    monkeypatch.setattr(
        combat_travel,
        "create_message",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("broken blocked-target message contract")),
    )

    return_time = combat_travel._retreat_raid_run_due_to_blocked_target(locked_run, now=now, reason="战败保护")

    assert return_time == 15
    assert locked_run.status == RaidRun.Status.RETREATED
    assert locked_run.return_at == now + timedelta(seconds=15)
    assert saved["fields"] == ["status", "return_at"]
    assert len(callbacks) == 1

    with pytest.raises(AssertionError, match="broken blocked-target message contract"):
        callbacks[0]()


def test_retreat_raid_run_due_to_blocked_target_explicit_message_error_degrades(monkeypatch):
    now = timezone.now()
    saved = {"fields": None}
    locked_run = build_locked_run(run_id=22, now=now, save_fields=saved)
    callbacks = []

    def _capture_after_commit(callback, *, expected_exceptions, **_kwargs):
        def _run():
            try:
                callback()
            except expected_exceptions:
                return None

        callbacks.append(_run)

    monkeypatch.setattr(
        combat_travel,
        "schedule_best_effort_after_commit",
        _capture_after_commit,
    )

    monkeypatch.setattr(
        combat_travel,
        "create_message",
        lambda **_kwargs: (_ for _ in ()).throw(MessageError("message backend down")),
    )

    return_time = combat_travel._retreat_raid_run_due_to_blocked_target(locked_run, now=now, reason="战败保护")

    assert return_time == 15
    assert locked_run.status == RaidRun.Status.RETREATED
    assert locked_run.return_at == now + timedelta(seconds=15)
    assert saved["fields"] == ["status", "return_at"]
    assert len(callbacks) == 1
    callbacks[0]()


def test_retreat_raid_run_due_to_blocked_target_runtime_marker_error_bubbles_up(monkeypatch):
    now = timezone.now()
    saved = {"fields": None}
    locked_run = build_locked_run(run_id=23, now=now, save_fields=saved)
    callbacks = []

    monkeypatch.setattr(
        combat_travel,
        "schedule_best_effort_after_commit",
        lambda callback, **_kwargs: callbacks.append(callback),
    )

    monkeypatch.setattr(
        combat_travel,
        "create_message",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("message backend down")),
    )

    return_time = combat_travel._retreat_raid_run_due_to_blocked_target(locked_run, now=now, reason="战败保护")

    assert return_time == 15
    assert locked_run.status == RaidRun.Status.RETREATED
    assert locked_run.return_at == now + timedelta(seconds=15)
    assert saved["fields"] == ["status", "return_at"]
    assert len(callbacks) == 1

    with pytest.raises(RuntimeError, match="message backend down"):
        callbacks[0]()
