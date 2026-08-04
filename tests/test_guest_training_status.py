from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from django.test import TestCase
from django.utils import timezone

from battle.services import lock_guests_for_battle
from guests.models import Guest, GuestArchetype, GuestRarity, GuestStatus, GuestTemplate
from guests.services.health import INJURY_RECOVERY_THRESHOLD, heal_guest
from guests.services.status import persist_guest_status_transition
from guests.services.training import finalize_guest_training


def _create_guest(manor, *, level: int = 1) -> Guest:
    suffix = uuid4().hex
    template = GuestTemplate.objects.create(
        key=f"training_status_{suffix}",
        name=f"训练状态门客-{suffix}",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GRAY,
    )
    return Guest.objects.create(manor=manor, template=template, level=level)


@pytest.mark.django_db
def test_battle_status_transition_pauses_and_resumes_training(monkeypatch, manor_factory):
    manor, _user = manor_factory(username="training_status_battle")
    guest = _create_guest(manor)
    started_at = timezone.now()
    guest.training_target_level = guest.level + 1
    guest.training_complete_at = started_at + timedelta(seconds=120)
    guest.save(update_fields=["training_target_level", "training_complete_at"])

    dispatched: list[dict] = []
    monkeypatch.setattr(
        "guests.services.training._try_enqueue_complete_guest_training",
        lambda _guest, **kwargs: dispatched.append(kwargs),
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        with lock_guests_for_battle([guest], manor=manor):
            guest.refresh_from_db()
            assert guest.status == GuestStatus.DEPLOYED
            assert guest.training_complete_at is None
            assert 119 <= guest.training_remaining_seconds <= 120

    guest.refresh_from_db()
    assert guest.status == GuestStatus.IDLE
    assert guest.training_complete_at is not None
    assert guest.training_complete_at > timezone.now()
    assert guest.training_remaining_seconds is None
    assert dispatched and dispatched[0]["source"] == "battle_release"


@pytest.mark.django_db
def test_overdue_non_idle_training_is_paused_instead_of_repeatedly_skipped(manor_factory):
    manor, _user = manor_factory(username="training_status_overdue")
    guest = _create_guest(manor)
    now = timezone.now()
    guest.status = GuestStatus.ARENA
    guest.training_target_level = guest.level + 1
    guest.training_complete_at = now - timedelta(seconds=10)
    guest.save(update_fields=["status", "training_target_level", "training_complete_at"])

    assert finalize_guest_training(guest, now=now) is False

    guest.refresh_from_db()
    assert guest.level == 1
    assert guest.training_complete_at is None
    assert guest.training_remaining_seconds == 0
    assert guest.status == GuestStatus.ARENA


@pytest.mark.django_db
def test_healing_injured_guest_resumes_paused_training(monkeypatch, manor_factory):
    manor, _user = manor_factory(username="training_status_injury")
    guest = _create_guest(manor)
    guest.status = GuestStatus.INJURED
    guest.current_hp = 1
    guest.training_target_level = guest.level + 1
    guest.training_remaining_seconds = 75
    guest.save(update_fields=["status", "current_hp", "training_target_level", "training_remaining_seconds"])

    dispatched: list[dict] = []
    monkeypatch.setattr(
        "guests.services.training._try_enqueue_complete_guest_training",
        lambda _guest, **kwargs: dispatched.append(kwargs),
    )

    heal_amount = int(guest.max_hp * INJURY_RECOVERY_THRESHOLD) + 1
    with TestCase.captureOnCommitCallbacks(execute=True):
        heal_guest(guest, heal_amount)

    guest.refresh_from_db()
    assert guest.status == GuestStatus.IDLE
    assert guest.training_remaining_seconds is None
    assert guest.training_complete_at is not None
    assert dispatched and dispatched[0]["source"] == "injury_heal"


@pytest.mark.django_db
def test_status_transition_does_not_change_training_without_a_timer(monkeypatch, manor_factory):
    manor, _user = manor_factory(username="training_status_no_timer")
    guest = _create_guest(manor)
    dispatched: list[dict] = []
    monkeypatch.setattr(
        "guests.services.training._try_enqueue_complete_guest_training",
        lambda _guest, **kwargs: dispatched.append(kwargs),
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        persist_guest_status_transition(guest, GuestStatus.WORKING, source="test_work_start")

    guest.refresh_from_db()
    assert guest.status == GuestStatus.WORKING
    assert guest.training_complete_at is None
    assert guest.training_remaining_seconds is None
    assert dispatched == []
