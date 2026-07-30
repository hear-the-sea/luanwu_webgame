"""Tests for guest defection logic in guests/tasks.py."""

from __future__ import annotations

import hashlib
from datetime import date

import pytest

from core.exceptions import MessageError
from gameplay.models import Manor
from gameplay.services.manor.core import ensure_manor
from guests import tasks as guest_tasks
from guests.models import Guest, GuestDefection, GuestTemplate


def test_should_defect_deterministic_for_same_input():
    """Test that _should_defect returns consistent results for same inputs."""
    guest_id = 12345
    test_date = date(2026, 2, 8)

    result1 = guest_tasks._should_defect(guest_id, test_date, probability=0.5, hasher=hashlib.sha256)
    result2 = guest_tasks._should_defect(guest_id, test_date, probability=0.5, hasher=hashlib.sha256)

    assert result1 == result2


def test_should_defect_different_for_different_dates():
    """Test that different dates can produce different results."""
    guest_id = 12345

    results = set()
    for day in range(1, 100):
        test_date = date(2026, 1, 1)
        result = guest_tasks._should_defect(guest_id + day * 1000, test_date, probability=0.5, hasher=hashlib.sha256)
        results.add(result)

    assert len(results) == 2


def test_should_defect_probability_zero_always_false():
    """Test that probability=0 always returns False."""
    for guest_id in range(1, 100):
        result = guest_tasks._should_defect(guest_id, date(2026, 2, 8), probability=0.0, hasher=hashlib.sha256)
        assert result is False


def test_should_defect_probability_one_always_true():
    """Test that probability=1 always returns True."""
    for guest_id in range(1, 100):
        result = guest_tasks._should_defect(guest_id, date(2026, 2, 8), probability=1.0, hasher=hashlib.sha256)
        assert result is True


def test_should_defect_uses_hasher_correctly():
    """Test that different hashers produce different results."""
    guest_id = 99999
    test_date = date(2026, 2, 8)

    result_sha256 = guest_tasks._should_defect(guest_id, test_date, probability=0.5, hasher=hashlib.sha256)
    result_sha512 = guest_tasks._should_defect(guest_id, test_date, probability=0.5, hasher=hashlib.sha512)

    assert isinstance(result_sha256, bool)
    assert isinstance(result_sha512, bool)


def _create_guest_for_defection(django_user_model, *, username: str) -> Guest:
    user = django_user_model.objects.create_user(username=username, password="pass12345")
    manor = ensure_manor(user)
    template = GuestTemplate.objects.create(
        key=f"{username}_guest_tpl",
        name="叛逃测试门客",
        archetype="civil",
        rarity="green",
    )
    return Guest.objects.create(
        manor=manor,
        template=template,
        level=12,
        loyalty=8,
        custom_name="韩信",
    )


@pytest.mark.django_db
def test_process_defection_batch_records_once_and_deletes_guest(django_user_model):
    guest = _create_guest_for_defection(django_user_model, username="defection_once")
    calls: list[dict] = []

    count = guest_tasks._process_defection_batch(
        [guest.id, guest.id],
        create_message=lambda **kwargs: calls.append(kwargs),
    )

    assert count == 1
    assert Guest.objects.filter(id=guest.id).exists() is False

    defections = list(GuestDefection.objects.filter(guest_id=guest.id))
    assert len(defections) == 1
    assert defections[0].guest_name == "韩信"
    assert len(calls) == 1
    assert "韩信" in calls[0]["body"]


@pytest.mark.django_db
def test_process_defection_batch_locks_manor_before_guest(
    django_user_model,
    monkeypatch,
):
    guest = _create_guest_for_defection(
        django_user_model,
        username="defection_lock_order",
    )
    lock_order: list[str] = []
    manor_manager = Manor.objects
    guest_manager = Guest.objects
    original_manor_lock = manor_manager.select_for_update
    original_guest_lock = guest_manager.select_for_update

    def _lock_manor(*args, **kwargs):
        lock_order.append("manor")
        return original_manor_lock(*args, **kwargs)

    def _lock_guest(*args, **kwargs):
        lock_order.append("guest")
        return original_guest_lock(*args, **kwargs)

    monkeypatch.setattr(manor_manager, "select_for_update", _lock_manor)
    monkeypatch.setattr(guest_manager, "select_for_update", _lock_guest)

    count = guest_tasks._process_defection_batch(
        [guest.id],
        create_message=lambda **_kwargs: None,
    )

    assert count == 1
    assert lock_order[:2] == ["manor", "guest"]


@pytest.mark.django_db
def test_process_defection_batch_keeps_deletion_when_message_fails(django_user_model):
    guest = _create_guest_for_defection(django_user_model, username="defection_message_fail")

    count = guest_tasks._process_defection_batch(
        [guest.id],
        create_message=lambda **kwargs: (_ for _ in ()).throw(MessageError("message down")),
    )

    assert count == 1
    assert Guest.objects.filter(id=guest.id).exists() is False

    defection = GuestDefection.objects.get(guest_id=guest.id)
    assert defection.guest_name == "韩信"


@pytest.mark.django_db
def test_process_defection_batch_programming_error_bubbles_after_delete(django_user_model):
    guest = _create_guest_for_defection(django_user_model, username="defection_message_bug")

    with pytest.raises(RuntimeError, match="message bug"):
        guest_tasks._process_defection_batch(
            [guest.id],
            create_message=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("message bug")),
        )

    assert Guest.objects.filter(id=guest.id).exists() is False
    assert GuestDefection.objects.filter(guest_id=guest.id).count() == 1
