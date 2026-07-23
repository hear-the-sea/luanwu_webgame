from __future__ import annotations

import threading

import pytest
from django.db import connection

from core.exceptions import JailError
from gameplay.models import InventoryItem, JailInteractionLog, JailPrisoner, Manor
from gameplay.services.jail import recruit_prisoner
from gameplay.services.jail_persuasion.interactions import interact_prisoner, observe_prisoner
from gameplay.services.jail_persuasion.profiles import METHOD_KINDNESS, METHOD_REASON
from guests.models import Guest

pytestmark = [pytest.mark.integration]


class _SuccessfulRng:
    def __init__(self):
        self._first_randint = True

    def randint(self, start: int, end: int) -> int:
        if self._first_randint:
            self._first_randint = False
            assert (start, end) == (1, 100)
            return 1
        if start <= 0 <= end:
            return 0
        return start

    def choice(self, values):
        return values[0]


@pytest.mark.django_db(transaction=True)
def test_concurrent_cross_mode_recruitment_succeeds_only_once(persuasion_world):
    if connection.vendor == "sqlite":
        pytest.skip("SQLite does not provide row-level select_for_update semantics for this concurrency scenario")

    prisoner = persuasion_world.prisoner
    persuasion_world.captor.guests.all().delete()
    prisoner.loyalty = 30
    prisoner.affinity = 100
    prisoner.milestone_stage = 2
    prisoner.save(update_fields=["loyalty", "affinity", "milestone_stage"])

    barrier = threading.Barrier(2)
    results = []
    errors: list[Exception] = []

    def _worker(mode: str) -> None:
        try:
            local_manor = Manor.objects.get(pk=persuasion_world.captor.pk)
            barrier.wait(timeout=5)
            results.append(recruit_prisoner(local_manor, prisoner.id, mode=mode, rng=_SuccessfulRng()))
        except Exception as exc:  # pragma: no cover - validated by assertions below
            errors.append(exc)

    threads = [
        threading.Thread(target=_worker, args=("standard",)),
        threading.Thread(target=_worker, args=("heartfelt",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 1
    assert results[0].recruited is True
    assert results[0].guest is not None
    assert len(errors) == 1
    assert isinstance(errors[0], JailError)

    prisoner.refresh_from_db()
    gold_quantity = InventoryItem.objects.get(
        manor=persuasion_world.captor,
        template=persuasion_world.gold_template,
    ).quantity
    assert prisoner.status == JailPrisoner.Status.RECRUITED
    assert gold_quantity == 9
    assert Guest.objects.filter(manor=persuasion_world.captor).count() == 1
    assert (
        JailInteractionLog.objects.filter(
            prisoner=prisoner,
            attempt_scope="recruitment",
        ).count()
        == 1
    )


@pytest.mark.django_db(transaction=True)
def test_same_speaker_concurrent_requests_succeed_only_once(persuasion_world, monkeypatch):
    if connection.vendor == "sqlite":
        pytest.skip("SQLite does not provide row-level select_for_update semantics for this concurrency scenario")

    observe_prisoner(persuasion_world.captor, persuasion_world.prisoner.id)
    second = JailPrisoner.objects.create(
        captor=persuasion_world.captor,
        original_manor=persuasion_world.original,
        guest_template=persuasion_world.prisoner_template,
        original_guest_name="并发囚徒",
        original_level=10,
        loyalty=70,
        captured_loyalty=70,
    )
    observe_prisoner(persuasion_world.captor, second.id)
    monkeypatch.setattr("gameplay.models.Manor.get_building_level", lambda self, key: 5)
    monkeypatch.setattr("gameplay.services.jail_persuasion.interactions.roll_variations", lambda: (0, 0))

    barrier = threading.Barrier(2)
    results: list[int] = []
    errors: list[Exception] = []

    def _worker(prisoner_id: int) -> None:
        try:
            local_manor = Manor.objects.get(pk=persuasion_world.captor.pk)
            barrier.wait(timeout=5)
            result = interact_prisoner(
                local_manor,
                prisoner_id,
                method=METHOD_REASON,
                speaker_id=persuasion_world.strong_civil.pk,
            )
            results.append(result.log.pk)
        except Exception as exc:  # pragma: no cover - validated by assertions below
            errors.append(exc)

    threads = [
        threading.Thread(target=_worker, args=(persuasion_world.prisoner.id,)),
        threading.Thread(target=_worker, args=(second.id,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], JailError)
    assert "今日已经担任过说客" in str(errors[0])
    assert JailInteractionLog.objects.filter(speaker=persuasion_world.strong_civil).count() == 1


@pytest.mark.django_db(transaction=True)
def test_recruitment_completing_first_makes_concurrent_interaction_fail(persuasion_world, monkeypatch):
    if connection.vendor == "sqlite":
        pytest.skip("SQLite does not provide row-level select_for_update semantics for this concurrency scenario")

    prisoner = persuasion_world.prisoner
    observe_prisoner(persuasion_world.captor, prisoner.id)
    persuasion_world.captor.guests.all().delete()
    prisoner.loyalty = 30
    prisoner.save(update_fields=["loyalty"])
    monkeypatch.setattr("gameplay.services.jail_persuasion.interactions.roll_variations", lambda: (0, 0))

    from gameplay.services import jail as jail_service

    original_recruitment_offer = jail_service.recruitment_offer
    recruit_holds_locks = threading.Event()
    interaction_started = threading.Event()
    release_recruitment = threading.Event()

    def _blocking_recruitment_offer(locked_prisoner, mode):
        offer = original_recruitment_offer(locked_prisoner, mode)
        recruit_holds_locks.set()
        if not release_recruitment.wait(timeout=5):
            raise AssertionError("interaction did not start while recruitment held the prisoner lock")
        return offer

    monkeypatch.setattr(jail_service, "recruitment_offer", _blocking_recruitment_offer)
    recruited: list[int] = []
    interactions: list[int] = []
    errors: list[Exception] = []

    def _recruit_worker() -> None:
        try:
            local_manor = Manor.objects.get(pk=persuasion_world.captor.pk)
            result = recruit_prisoner(local_manor, prisoner.id, rng=_SuccessfulRng())
            assert result.recruited is True
            assert result.guest is not None
            recruited.append(result.guest.pk)
        except Exception as exc:  # pragma: no cover - validated by assertions below
            errors.append(exc)

    def _interaction_worker() -> None:
        try:
            local_manor = Manor.objects.get(pk=persuasion_world.captor.pk)
            interaction_started.set()
            result = interact_prisoner(local_manor, prisoner.id, method=METHOD_KINDNESS)
            interactions.append(result.log.pk)
        except Exception as exc:  # pragma: no cover - validated by assertions below
            errors.append(exc)

    recruit_thread = threading.Thread(target=_recruit_worker)
    recruit_thread.start()
    assert recruit_holds_locks.wait(timeout=5)

    interaction_thread = threading.Thread(target=_interaction_worker)
    interaction_thread.start()
    assert interaction_started.wait(timeout=5)
    release_recruitment.set()

    recruit_thread.join(timeout=10)
    interaction_thread.join(timeout=10)

    assert not recruit_thread.is_alive()
    assert not interaction_thread.is_alive()
    assert len(recruited) == 1
    assert interactions == []
    assert len(errors) == 1
    assert isinstance(errors[0], JailError)
    assert "囚徒不存在或已处理" in str(errors[0])
    prisoner.refresh_from_db()
    assert prisoner.status == JailPrisoner.Status.RECRUITED
    assert (
        JailInteractionLog.objects.filter(
            prisoner=prisoner,
            attempt_scope="recruitment",
            outcome=JailInteractionLog.Outcome.RECRUITED,
        ).count()
        == 1
    )
