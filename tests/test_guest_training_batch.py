import threading
from collections import Counter
from datetime import timedelta

import pytest
from django.db import connection, connections, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.config import GUEST
from guests.models import Guest, GuestArchetype, GuestRarity, GuestStatus, GuestTemplate
from guests.services.training import ensure_auto_training, ensure_auto_training_batch
from guests.utils.training_calculator import get_training_duration


@pytest.fixture
def training_guest(manor_factory):
    manor, _user = manor_factory(username="batch_training")
    template = GuestTemplate.objects.create(
        key="batch_training",
        name="批量训练门客",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GRAY,
    )
    return Guest.objects.create(manor=manor, template=template, level=1)


@pytest.mark.django_db
def test_auto_training_batch_locks_current_state_and_dispatches_each_guest_once(monkeypatch, training_guest):
    first = training_guest
    second = Guest.objects.create(manor=first.manor, template=first.template, level=3)
    now = timezone.now()
    dispatched = []
    monkeypatch.setattr(
        "guests.services.training._try_enqueue_complete_guest_training",
        lambda guest, **kwargs: dispatched.append((guest.pk, kwargs)),
    )

    with TestCase.captureOnCommitCallbacks(execute=True):
        with CaptureQueriesContext(connection) as captured:
            assert ensure_auto_training_batch([second, first, first], now=now) == 2
        assert len(captured) <= 4
        assert dispatched == []
        # Stale callers must observe the persisted timer instead of replacing it.
        first.training_complete_at = None
        second.training_complete_at = None
        assert ensure_auto_training_batch([first, second], now=now + timedelta(hours=1)) == 0

    for guest in (first, second):
        duration = get_training_duration(guest)
        expected_eta = now + timedelta(seconds=duration)
        assert guest.training_complete_at == expected_eta
        guest.refresh_from_db()
        assert guest.training_target_level == guest.level + 1
        assert guest.training_complete_at == expected_eta
        assert (guest.pk, {"countdown": duration, "source": "ensure_auto_training"}) in dispatched
    assert len(dispatched) == 2


@pytest.mark.django_db
@pytest.mark.parametrize("blocked_state", ["max_level", "deployed", "active_timer", "paused_timer"])
def test_auto_training_batch_preserves_ineligible_guest_state(monkeypatch, training_guest, blocked_state):
    guest = training_guest
    idle_guest = Guest.objects.create(manor=guest.manor, template=guest.template, level=1)
    now = timezone.now()
    changes = {
        "max_level": {"level": int(GUEST.MAX_LEVEL)},
        "deployed": {"status": GuestStatus.DEPLOYED},
        "active_timer": {"training_target_level": 2, "training_complete_at": now + timedelta(hours=1)},
        "paused_timer": {"training_target_level": 2, "training_remaining_seconds": 0},
    }[blocked_state]
    Guest.objects.filter(pk=guest.pk).update(**changes)
    dispatched = []
    monkeypatch.setattr(
        "guests.services.training._try_enqueue_complete_guest_training",
        lambda scheduled_guest, **kwargs: dispatched.append(scheduled_guest.pk),
    )
    with TestCase.captureOnCommitCallbacks(execute=True):
        assert ensure_auto_training_batch([guest, idle_guest], now=now) == 1
    guest.refresh_from_db()
    assert all(getattr(guest, field) == value for field, value in changes.items())
    assert dispatched == [idle_guest.pk]


@pytest.mark.django_db
def test_auto_training_batch_rolls_back_timers_and_callbacks(monkeypatch, training_guest):
    guest = training_guest
    dispatched = []
    monkeypatch.setattr(
        "guests.services.training._try_enqueue_complete_guest_training",
        lambda *args, **kwargs: dispatched.append(args),
    )
    with TestCase.captureOnCommitCallbacks(execute=True):
        with pytest.raises(RuntimeError, match="bootstrap failed"):
            with transaction.atomic():
                assert ensure_auto_training_batch([guest]) == 1
                raise RuntimeError("bootstrap failed")
    guest.refresh_from_db()
    assert guest.training_target_level == 0
    assert guest.training_complete_at is None
    assert dispatched == []


@pytest.mark.django_db
def test_auto_training_batch_rejects_missing_guest_before_scheduling(training_guest):
    guest = training_guest
    missing_guest = Guest(pk=guest.pk + 1)
    with pytest.raises(Guest.DoesNotExist):
        ensure_auto_training_batch([guest, missing_guest])
    guest.refresh_from_db()
    assert guest.training_complete_at is None


def test_auto_training_batch_accepts_empty_batch_and_rejects_unsaved_guest():
    assert ensure_auto_training_batch([]) == 0
    with pytest.raises(ValueError, match="persisted guests"):
        ensure_auto_training_batch([Guest()])


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_auto_training_overlapping_batches_and_single_caller_schedule_each_guest_once(monkeypatch, training_guest):
    if connection.vendor == "sqlite":
        pytest.skip("SQLite does not provide row-level select_for_update semantics")
    first = training_guest
    second = Guest.objects.create(manor=first.manor, template=first.template, level=3)
    now = timezone.now()
    barrier = threading.Barrier(3)
    results = []
    dispatched = []
    errors = []
    monkeypatch.setattr(
        "guests.services.training._try_enqueue_complete_guest_training",
        lambda guest, **kwargs: dispatched.append(guest.pk),
    )

    def worker(guest_ids, *, single=False):
        connections.close_all()
        try:
            guests = [Guest.objects.get(pk=guest_id) for guest_id in guest_ids]
            barrier.wait(timeout=5)
            if single:
                results.append(int(ensure_auto_training(guests[0], now=now)))
            else:
                results.append(ensure_auto_training_batch(guests, now=now))
        except Exception as exc:  # pragma: no cover - asserted in the parent thread
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [
        threading.Thread(target=worker, args=([first.pk, second.pk],)),
        threading.Thread(target=worker, args=([second.pk, first.pk],)),
        threading.Thread(target=worker, args=([first.pk],), kwargs={"single": True}),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 3
    assert sum(results) == 2
    assert Counter(dispatched) == Counter([first.pk, second.pk])
    for guest in (first, second):
        guest.refresh_from_db()
        assert guest.training_target_level == guest.level + 1
        assert guest.training_complete_at == now + timedelta(seconds=get_training_duration(guest))
