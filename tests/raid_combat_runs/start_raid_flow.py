from __future__ import annotations

import contextlib
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import DatabaseError, transaction
from django.test import TestCase
from django.utils import timezone

from core.exceptions import MessageError, RaidStartError
from gameplay.services.raid import utils as raid_utils
from gameplay.services.raid.combat import runs as combat_runs


def test_start_raid_rechecks_attack_constraints_inside_transaction(monkeypatch):
    attacker = SimpleNamespace(pk=1, id=1, defeat_protection_until=None)
    defender = SimpleNamespace(pk=2, id=2)

    monkeypatch.setattr(combat_runs.transaction, "atomic", contextlib.nullcontext)
    monkeypatch.setattr(
        combat_runs,
        "_validate_and_normalize_raid_inputs",
        lambda *_args, **_kwargs: ([101], {"inf": 1}),
    )
    monkeypatch.setattr(combat_runs, "_lock_manor_pair", lambda *_args, **_kwargs: (attacker, defender))
    monkeypatch.setattr(
        combat_runs,
        "_recheck_can_attack_target",
        lambda *_args, **_kwargs: (False, "对方处于免战牌保护期"),
    )

    def _unexpected_active_count(*_args, **_kwargs):
        raise AssertionError("should not read active raid count when lock-time attack check fails")

    called = {"load_guests": 0}

    def _unexpected_load_guests(*_args, **_kwargs):
        called["load_guests"] += 1
        return []

    monkeypatch.setattr(combat_runs, "get_active_raid_count", _unexpected_active_count)
    monkeypatch.setattr(combat_runs, "_load_and_validate_attacker_guests", _unexpected_load_guests)

    with pytest.raises(RaidStartError, match="免战牌保护期"):
        combat_runs.start_raid(attacker, defender, [101], {"inf": 1})

    assert called["load_guests"] == 0


@pytest.mark.django_db(transaction=True)
def test_start_raid_invalidates_recent_attack_cache_on_commit(monkeypatch):
    attacker = SimpleNamespace(pk=1, id=1, defeat_protection_until=None)
    defender = SimpleNamespace(pk=2, id=2)
    invalidated = {"defender_id": None}

    monkeypatch.setattr(
        combat_runs,
        "_validate_and_normalize_raid_inputs",
        lambda *_args, **_kwargs: ([101], {"inf": 1}),
    )
    monkeypatch.setattr(combat_runs, "_lock_manor_pair", lambda *_args, **_kwargs: (attacker, defender))
    monkeypatch.setattr(combat_runs, "_recheck_can_attack_target", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(combat_runs, "get_active_raid_count", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(combat_runs, "_load_and_validate_attacker_guests", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(combat_runs, "_normalize_and_validate_raid_loadout", lambda *_args, **_kwargs: {"inf": 1})
    monkeypatch.setattr(combat_runs, "_deduct_troops", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_runs, "calculate_raid_travel_time", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(
        combat_runs,
        "_create_raid_run_record",
        lambda *_args, **_kwargs: SimpleNamespace(id=99, attacker=attacker, defender=defender),
    )
    monkeypatch.setattr(combat_runs, "_send_raid_incoming_message", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_runs, "_dispatch_raid_battle_task", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "gameplay.services.raid.utils.invalidate_recent_attacks_cache",
        lambda defender_id: invalidated.__setitem__("defender_id", defender_id),
    )

    with transaction.atomic():
        with TestCase.captureOnCommitCallbacks(execute=False) as callbacks:
            combat_runs.start_raid(attacker, defender, [101], {"inf": 1})

        assert invalidated["defender_id"] is None
        assert len(callbacks) == 1

        callbacks[0]()

    assert invalidated["defender_id"] == defender.id


@pytest.mark.django_db(transaction=True)
def test_start_raid_clears_attacker_defeat_protection_on_success(monkeypatch):
    now = timezone.now()
    saved = {"fields": None}
    attacker = SimpleNamespace(
        pk=1,
        id=1,
        defeat_protection_until=now + timedelta(minutes=15),
        save=lambda *, update_fields: saved.__setitem__("fields", update_fields),
    )
    defender = SimpleNamespace(pk=2, id=2)

    monkeypatch.setattr(
        combat_runs,
        "_validate_and_normalize_raid_inputs",
        lambda *_args, **_kwargs: ([101], {"inf": 1}),
    )
    monkeypatch.setattr(combat_runs, "_lock_manor_pair", lambda *_args, **_kwargs: (attacker, defender))
    monkeypatch.setattr(combat_runs, "_recheck_can_attack_target", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(combat_runs, "get_active_raid_count", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(combat_runs, "_load_and_validate_attacker_guests", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(combat_runs, "_normalize_and_validate_raid_loadout", lambda *_args, **_kwargs: {"inf": 1})
    monkeypatch.setattr(combat_runs, "_deduct_troops", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_runs, "calculate_raid_travel_time", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(
        combat_runs,
        "_create_raid_run_record",
        lambda *_args, **_kwargs: SimpleNamespace(id=99, attacker=attacker, defender=defender),
    )
    monkeypatch.setattr(combat_runs, "_send_raid_incoming_message", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_runs, "_dispatch_raid_battle_task", lambda *_args, **_kwargs: None)

    with transaction.atomic():
        with TestCase.captureOnCommitCallbacks(execute=True) as callbacks:
            combat_runs.start_raid(attacker, defender, [101], {"inf": 1})

        assert len(callbacks) == 1

    assert attacker.defeat_protection_until is None
    assert saved["fields"] == ["defeat_protection_until"]


def test_start_raid_succeeds_when_incoming_message_fails(monkeypatch):
    attacker = SimpleNamespace(pk=1, id=1, defeat_protection_until=None)
    defender = SimpleNamespace(pk=2, id=2)
    created_run = SimpleNamespace(id=99, attacker=attacker, defender=defender)
    dispatched = {"run_id": None, "travel_time": None}

    monkeypatch.setattr(combat_runs.transaction, "atomic", contextlib.nullcontext)
    monkeypatch.setattr(
        combat_runs,
        "_validate_and_normalize_raid_inputs",
        lambda *_args, **_kwargs: ([101], {"inf": 1}),
    )
    monkeypatch.setattr(combat_runs, "_lock_manor_pair", lambda *_args, **_kwargs: (attacker, defender))
    monkeypatch.setattr(combat_runs, "_recheck_can_attack_target", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(combat_runs, "get_active_raid_count", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(combat_runs, "_load_and_validate_attacker_guests", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(combat_runs, "_normalize_and_validate_raid_loadout", lambda *_args, **_kwargs: {"inf": 1})
    monkeypatch.setattr(combat_runs, "_deduct_troops", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_runs, "calculate_raid_travel_time", lambda *_args, **_kwargs: 45)
    monkeypatch.setattr(combat_runs, "_create_raid_run_record", lambda *_args, **_kwargs: created_run)
    monkeypatch.setattr(combat_runs, "_invalidate_recent_attacks_cache_on_commit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        combat_runs,
        "_send_raid_incoming_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(MessageError("message backend down")),
    )
    monkeypatch.setattr(
        combat_runs,
        "_dispatch_raid_battle_task",
        lambda run, travel_time: dispatched.update({"run_id": run.id, "travel_time": travel_time}),
    )

    result = combat_runs.start_raid(attacker, defender, [101], {"inf": 1})

    assert result is created_run
    assert dispatched == {"run_id": 99, "travel_time": 45}


def test_start_raid_succeeds_when_incoming_message_database_failure(monkeypatch):
    attacker = SimpleNamespace(pk=1, id=1, defeat_protection_until=None)
    defender = SimpleNamespace(pk=2, id=2)
    created_run = SimpleNamespace(id=101, attacker=attacker, defender=defender)
    dispatched = {"run_id": None, "travel_time": None}

    monkeypatch.setattr(combat_runs.transaction, "atomic", contextlib.nullcontext)
    monkeypatch.setattr(
        combat_runs,
        "_validate_and_normalize_raid_inputs",
        lambda *_args, **_kwargs: ([101], {"inf": 1}),
    )
    monkeypatch.setattr(combat_runs, "_lock_manor_pair", lambda *_args, **_kwargs: (attacker, defender))
    monkeypatch.setattr(combat_runs, "_recheck_can_attack_target", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(combat_runs, "get_active_raid_count", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(combat_runs, "_load_and_validate_attacker_guests", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(combat_runs, "_normalize_and_validate_raid_loadout", lambda *_args, **_kwargs: {"inf": 1})
    monkeypatch.setattr(combat_runs, "_deduct_troops", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_runs, "calculate_raid_travel_time", lambda *_args, **_kwargs: 45)
    monkeypatch.setattr(combat_runs, "_create_raid_run_record", lambda *_args, **_kwargs: created_run)
    monkeypatch.setattr(combat_runs, "_invalidate_recent_attacks_cache_on_commit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        combat_runs,
        "_send_raid_incoming_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("message table unavailable")),
    )
    monkeypatch.setattr(
        combat_runs,
        "_dispatch_raid_battle_task",
        lambda run, travel_time: dispatched.update({"run_id": run.id, "travel_time": travel_time}),
    )

    result = combat_runs.start_raid(attacker, defender, [101], {"inf": 1})

    assert result is created_run
    assert dispatched == {"run_id": 101, "travel_time": 45}


def test_start_raid_incoming_message_runtime_marker_error_bubbles_up(monkeypatch):
    attacker = SimpleNamespace(pk=1, id=1, defeat_protection_until=None)
    defender = SimpleNamespace(pk=2, id=2)
    created_run = SimpleNamespace(id=100, attacker=attacker, defender=defender)
    dispatched = {"run_id": None, "travel_time": None}

    monkeypatch.setattr(combat_runs.transaction, "atomic", contextlib.nullcontext)
    monkeypatch.setattr(
        combat_runs,
        "_validate_and_normalize_raid_inputs",
        lambda *_args, **_kwargs: ([101], {"inf": 1}),
    )
    monkeypatch.setattr(combat_runs, "_lock_manor_pair", lambda *_args, **_kwargs: (attacker, defender))
    monkeypatch.setattr(combat_runs, "_recheck_can_attack_target", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(combat_runs, "get_active_raid_count", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(combat_runs, "_load_and_validate_attacker_guests", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(combat_runs, "_normalize_and_validate_raid_loadout", lambda *_args, **_kwargs: {"inf": 1})
    monkeypatch.setattr(combat_runs, "_deduct_troops", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_runs, "calculate_raid_travel_time", lambda *_args, **_kwargs: 45)
    monkeypatch.setattr(combat_runs, "_create_raid_run_record", lambda *_args, **_kwargs: created_run)
    monkeypatch.setattr(combat_runs, "_invalidate_recent_attacks_cache_on_commit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        combat_runs,
        "_send_raid_incoming_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("message backend down")),
    )
    monkeypatch.setattr(
        combat_runs,
        "_dispatch_raid_battle_task",
        lambda run, travel_time: dispatched.update({"run_id": run.id, "travel_time": travel_time}),
    )

    with pytest.raises(RuntimeError, match="message backend down"):
        combat_runs.start_raid(attacker, defender, [101], {"inf": 1})

    assert dispatched == {"run_id": None, "travel_time": None}


def test_start_raid_incoming_message_programming_error_bubbles_up(monkeypatch):
    attacker = SimpleNamespace(pk=1, id=1, defeat_protection_until=None)
    defender = SimpleNamespace(pk=2, id=2)
    created_run = SimpleNamespace(id=99, attacker=attacker, defender=defender)
    dispatched = {"run_id": None, "travel_time": None}

    monkeypatch.setattr(combat_runs.transaction, "atomic", contextlib.nullcontext)
    monkeypatch.setattr(
        combat_runs,
        "_validate_and_normalize_raid_inputs",
        lambda *_args, **_kwargs: ([101], {"inf": 1}),
    )
    monkeypatch.setattr(combat_runs, "_lock_manor_pair", lambda *_args, **_kwargs: (attacker, defender))
    monkeypatch.setattr(combat_runs, "_recheck_can_attack_target", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(combat_runs, "get_active_raid_count", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(combat_runs, "_load_and_validate_attacker_guests", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(combat_runs, "_normalize_and_validate_raid_loadout", lambda *_args, **_kwargs: {"inf": 1})
    monkeypatch.setattr(combat_runs, "_deduct_troops", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_runs, "calculate_raid_travel_time", lambda *_args, **_kwargs: 45)
    monkeypatch.setattr(combat_runs, "_create_raid_run_record", lambda *_args, **_kwargs: created_run)
    monkeypatch.setattr(combat_runs, "_invalidate_recent_attacks_cache_on_commit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        combat_runs,
        "_send_raid_incoming_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken incoming message contract")),
    )
    monkeypatch.setattr(
        combat_runs,
        "_dispatch_raid_battle_task",
        lambda run, travel_time: dispatched.update({"run_id": run.id, "travel_time": travel_time}),
    )

    with pytest.raises(AssertionError, match="broken incoming message contract"):
        combat_runs.start_raid(attacker, defender, [101], {"inf": 1})


def test_start_raid_dispatch_runtime_marker_error_bubbles_up(monkeypatch):
    attacker = SimpleNamespace(pk=1, id=1, defeat_protection_until=None)
    defender = SimpleNamespace(pk=2, id=2)
    created_run = SimpleNamespace(id=102, attacker=attacker, defender=defender)

    monkeypatch.setattr(combat_runs.transaction, "atomic", contextlib.nullcontext)
    monkeypatch.setattr(
        combat_runs,
        "_validate_and_normalize_raid_inputs",
        lambda *_args, **_kwargs: ([101], {"inf": 1}),
    )
    monkeypatch.setattr(combat_runs, "_lock_manor_pair", lambda *_args, **_kwargs: (attacker, defender))
    monkeypatch.setattr(combat_runs, "_recheck_can_attack_target", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(combat_runs, "get_active_raid_count", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(combat_runs, "_load_and_validate_attacker_guests", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(combat_runs, "_normalize_and_validate_raid_loadout", lambda *_args, **_kwargs: {"inf": 1})
    monkeypatch.setattr(combat_runs, "_deduct_troops", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_runs, "calculate_raid_travel_time", lambda *_args, **_kwargs: 45)
    monkeypatch.setattr(combat_runs, "_create_raid_run_record", lambda *_args, **_kwargs: created_run)
    monkeypatch.setattr(combat_runs, "_invalidate_recent_attacks_cache_on_commit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_runs, "_send_raid_incoming_message", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        combat_runs,
        "_dispatch_raid_battle_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("dispatch backend down")),
    )

    with pytest.raises(RuntimeError, match="dispatch backend down"):
        combat_runs.start_raid(attacker, defender, [101], {"inf": 1})


def test_start_raid_dispatch_programming_error_bubbles_up(monkeypatch):
    attacker = SimpleNamespace(pk=1, id=1, defeat_protection_until=None)
    defender = SimpleNamespace(pk=2, id=2)
    created_run = SimpleNamespace(id=103, attacker=attacker, defender=defender)

    monkeypatch.setattr(combat_runs.transaction, "atomic", contextlib.nullcontext)
    monkeypatch.setattr(
        combat_runs,
        "_validate_and_normalize_raid_inputs",
        lambda *_args, **_kwargs: ([101], {"inf": 1}),
    )
    monkeypatch.setattr(combat_runs, "_lock_manor_pair", lambda *_args, **_kwargs: (attacker, defender))
    monkeypatch.setattr(combat_runs, "_recheck_can_attack_target", lambda *_args, **_kwargs: (True, ""))
    monkeypatch.setattr(combat_runs, "get_active_raid_count", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(combat_runs, "_load_and_validate_attacker_guests", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(combat_runs, "_normalize_and_validate_raid_loadout", lambda *_args, **_kwargs: {"inf": 1})
    monkeypatch.setattr(combat_runs, "_deduct_troops", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_runs, "calculate_raid_travel_time", lambda *_args, **_kwargs: 45)
    monkeypatch.setattr(combat_runs, "_create_raid_run_record", lambda *_args, **_kwargs: created_run)
    monkeypatch.setattr(combat_runs, "_invalidate_recent_attacks_cache_on_commit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(combat_runs, "_send_raid_incoming_message", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        combat_runs,
        "_dispatch_raid_battle_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken raid dispatch contract")),
    )

    with pytest.raises(AssertionError, match="broken raid dispatch contract"):
        combat_runs.start_raid(attacker, defender, [101], {"inf": 1})


def test_validate_and_normalize_raid_inputs_uses_uncached_attack_check(monkeypatch):
    attacker = SimpleNamespace(id=1)
    defender = SimpleNamespace(id=2)
    seen = {"use_cached_recent_attacks": None}

    def _fake_can_attack(_attacker, _defender, **kwargs):
        seen["use_cached_recent_attacks"] = kwargs.get("use_cached_recent_attacks")
        return True, ""

    monkeypatch.setattr(raid_utils, "can_attack_target", _fake_can_attack)
    monkeypatch.setattr(combat_runs, "get_active_raid_count", lambda *_args, **_kwargs: 0)

    guest_ids, troop_loadout = combat_runs._validate_and_normalize_raid_inputs(
        attacker,
        defender,
        [101],
        {"inf": 1},
    )

    assert guest_ids == [101]
    assert troop_loadout == {"inf": 1}
    assert seen["use_cached_recent_attacks"] is False
