from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from django.db import connection, connections
from django.test import TestCase
from django.utils import timezone

from core.config import GUEST
from gameplay.models import BotProfile
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestArchetype, GuestRarity, GuestStatus, GuestTemplate
from guests.services.training import ensure_auto_training
from guests.tasks import scan_guest_training


def _create_guest(django_user_model, *, suffix: str) -> Guest:
    user = django_user_model.objects.create_user(username=f"training_reconcile_{suffix}", password="pass123")
    manor = ensure_manor(user)
    template = GuestTemplate.objects.create(
        key=f"training_reconcile_{suffix}",
        name=f"训练恢复门客-{suffix}",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GRAY,
    )
    return Guest.objects.create(manor=manor, template=template, level=1)


def _create_bot_profile(guest: Guest) -> BotProfile:
    now = timezone.now()
    return BotProfile.objects.create(
        manor=guest.manor,
        prestige_band="newbie",
        growth_seed=1,
        next_growth_at=now + timedelta(hours=1),
        abandon_at=now + timedelta(days=30),
        retire_at=now + timedelta(days=60),
    )


@pytest.mark.django_db
def test_ensure_auto_training_schedules_once_for_repeated_calls(monkeypatch, django_user_model):
    guest = _create_guest(django_user_model, suffix="idempotent")
    dispatched = []
    monkeypatch.setattr(
        "guests.services.training._try_enqueue_complete_guest_training",
        lambda scheduled_guest, **kwargs: dispatched.append((scheduled_guest.pk, kwargs)),
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        assert ensure_auto_training(guest) is True
        assert ensure_auto_training(guest) is False

    guest.refresh_from_db()
    assert guest.training_target_level == 2
    assert guest.training_complete_at is not None
    assert len(dispatched) == 1


@pytest.mark.django_db
def test_scan_guest_training_schedules_idle_real_player_orphan(django_user_model):
    guest = _create_guest(django_user_model, suffix="orphan")

    assert scan_guest_training(limit=10) == 1

    guest.refresh_from_db()
    assert guest.training_target_level == 2
    assert guest.training_complete_at is not None


@pytest.mark.django_db
def test_scan_guest_training_excludes_virtual_player_before_applying_limit(django_user_model):
    virtual_guest = _create_guest(django_user_model, suffix="virtual")
    _create_bot_profile(virtual_guest)
    real_guest = _create_guest(django_user_model, suffix="real_after_virtual")

    assert scan_guest_training(limit=1) == 1

    virtual_guest.refresh_from_db()
    real_guest.refresh_from_db()
    assert virtual_guest.training_complete_at is None
    assert real_guest.training_complete_at is not None


@pytest.mark.django_db
def test_scan_guest_training_excludes_non_idle_guest_before_applying_limit(django_user_model):
    deployed_guest = _create_guest(django_user_model, suffix="deployed")
    deployed_guest.status = GuestStatus.DEPLOYED
    deployed_guest.save(update_fields=["status"])
    idle_guest = _create_guest(django_user_model, suffix="idle_after_deployed")

    assert scan_guest_training(limit=1) == 1

    deployed_guest.refresh_from_db()
    idle_guest.refresh_from_db()
    assert deployed_guest.training_complete_at is None
    assert idle_guest.training_complete_at is not None


@pytest.mark.django_db
def test_scan_guest_training_excludes_max_level_guest_before_applying_limit(django_user_model):
    max_level_guest = _create_guest(django_user_model, suffix="max_level")
    max_level_guest.level = int(GUEST.MAX_LEVEL)
    max_level_guest.save(update_fields=["level"])
    trainable_guest = _create_guest(django_user_model, suffix="trainable_after_max")

    assert scan_guest_training(limit=1) == 1

    max_level_guest.refresh_from_db()
    trainable_guest.refresh_from_db()
    assert max_level_guest.training_complete_at is None
    assert trainable_guest.training_complete_at is not None


@pytest.mark.django_db
def test_scan_guest_training_prioritizes_due_training_within_limit(monkeypatch, django_user_model):
    due_guest = _create_guest(django_user_model, suffix="due")
    due_guest.training_target_level = 2
    due_guest.training_complete_at = timezone.now() - timedelta(seconds=1)
    due_guest.save(update_fields=["training_target_level", "training_complete_at"])
    orphan_guest = _create_guest(django_user_model, suffix="orphan_after_due")
    finalized = []
    monkeypatch.setattr(
        "guests.services.training.finalize_guest_training",
        lambda guest, now=None: finalized.append(guest.pk) or True,
    )

    assert scan_guest_training(limit=1) == 1

    orphan_guest.refresh_from_db()
    assert finalized == [due_guest.pk]
    assert orphan_guest.training_complete_at is None


@pytest.mark.django_db
def test_scan_guest_training_does_not_reschedule_existing_timer(django_user_model):
    guest = _create_guest(django_user_model, suffix="existing_timer")
    assert ensure_auto_training(guest) is True
    scheduled_at = guest.training_complete_at

    assert scan_guest_training(limit=10) == 0

    guest.refresh_from_db()
    assert guest.training_complete_at == scheduled_at


@pytest.mark.django_db
def test_scan_guest_training_is_idempotent_after_reconciliation(django_user_model):
    guest = _create_guest(django_user_model, suffix="repeat_scan")

    assert scan_guest_training(limit=10) == 1
    guest.refresh_from_db()
    scheduled_at = guest.training_complete_at
    assert scan_guest_training(limit=10) == 0

    guest.refresh_from_db()
    assert guest.training_complete_at == scheduled_at


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_ensure_auto_training_concurrent_calls_schedule_once(monkeypatch, django_user_model):
    if connection.vendor == "sqlite":
        pytest.skip("SQLite does not provide row-level select_for_update semantics")

    guest = _create_guest(django_user_model, suffix="concurrent")
    barrier = threading.Barrier(2)
    results = []
    dispatched = []
    errors = []
    monkeypatch.setattr(
        "guests.services.training._try_enqueue_complete_guest_training",
        lambda scheduled_guest, **kwargs: dispatched.append((scheduled_guest.pk, kwargs)),
    )

    def _worker() -> None:
        connections.close_all()
        try:
            local_guest = Guest.objects.get(pk=guest.pk)
            barrier.wait(timeout=5)
            results.append(ensure_auto_training(local_guest))
        except Exception as exc:  # pragma: no cover - asserted in the parent thread
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert sorted(results) == [False, True]
    assert len(dispatched) == 1
