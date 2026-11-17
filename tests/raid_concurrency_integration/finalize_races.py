from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from django.db import close_old_connections, connection
from django.utils import timezone

from gameplay.models import RaidRun
from gameplay.services.raid.combat import runs as combat_runs
from guests.models import Guest, GuestStatus, GuestTemplate
from tests.raid_concurrency_integration.support import build_attacker_defender


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_overlapping_raids_return_concurrently_release_guest_after_both_finish(django_user_model):
    if connection.vendor == "sqlite":
        pytest.skip("SQLite does not provide row-level select_for_update semantics")

    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="raid_overlap_return_attacker",
        defender_username="raid_overlap_return_defender",
    )
    template = GuestTemplate.objects.create(key="raid_overlap_return_guest", name="返程门客")
    guest = Guest.objects.create(manor=attacker, template=template, status=GuestStatus.DEPLOYED)
    now = timezone.now()
    runs = [
        RaidRun.objects.create(
            attacker=attacker,
            defender=defender,
            status=RaidRun.Status.RETURNING,
            return_at=now - timedelta(seconds=1),
        )
        for _ in range(2)
    ]
    for run in runs:
        run.guests.add(guest)
    barrier = threading.Barrier(2)
    errors = []

    def finalize(run_id):
        close_old_connections()
        try:
            run = RaidRun.objects.get(pk=run_id)
            barrier.wait(timeout=10)
            combat_runs.finalize_raid(run, now=timezone.now())
        except Exception as exc:
            errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=finalize, args=(run.pk,)) for run in runs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert RaidRun.objects.filter(pk__in=[run.pk for run in runs], status=RaidRun.Status.COMPLETED).count() == 2
    guest.refresh_from_db()
    assert guest.status == GuestStatus.IDLE


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_finalize_raid_concurrent_requests_only_one_thread_completes(monkeypatch, django_user_model, load_guest_data):
    if connection.vendor == "sqlite":
        pytest.skip("SQLite does not provide row-level select_for_update semantics for this concurrency scenario")

    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="raid_finalize_concurrent_attacker",
        defender_username="raid_finalize_concurrent_defender",
    )

    template = GuestTemplate.objects.first()
    if template is None:
        pytest.skip("No guest template available")

    guest = Guest.objects.create(
        manor=attacker,
        template=template,
        level=20,
        status=GuestStatus.DEPLOYED,
        custom_name="raid_finalize_concurrent_guest",
    )
    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        troop_loadout={},
        status=RaidRun.Status.RETURNING,
        travel_time=60,
        return_at=timezone.now() - timedelta(seconds=1),
    )
    run.guests.add(guest)

    returned: list[int] = []
    returned_lock = threading.Lock()

    def _record_returned(locked_run) -> None:
        with returned_lock:
            returned.append(locked_run.pk)

    monkeypatch.setattr(combat_runs, "_return_surviving_troops", _record_returned)

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def _worker() -> None:
        try:
            local_run = RaidRun.objects.get(pk=run.pk)
            barrier.wait(timeout=5)
            combat_runs.finalize_raid(local_run, now=timezone.now())
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    run.refresh_from_db()
    guest.refresh_from_db()

    assert errors == []
    assert returned == [run.pk]
    assert run.status == RaidRun.Status.COMPLETED
    assert run.completed_at is not None
    assert guest.status == GuestStatus.IDLE


@pytest.mark.integration
@pytest.mark.django_db(transaction=True)
def test_refresh_raid_runs_concurrent_with_finalize_raid_completes_only_once(
    monkeypatch, django_user_model, load_guest_data
):
    if connection.vendor == "sqlite":
        pytest.skip("SQLite does not provide row-level select_for_update semantics for this concurrency scenario")

    attacker, defender = build_attacker_defender(
        django_user_model,
        attacker_username="raid_refresh_finalize_concurrent_attacker",
        defender_username="raid_refresh_finalize_concurrent_defender",
    )

    template = GuestTemplate.objects.first()
    if template is None:
        pytest.skip("No guest template available")

    guest = Guest.objects.create(
        manor=attacker,
        template=template,
        level=20,
        status=GuestStatus.DEPLOYED,
        custom_name="raid_refresh_finalize_guest",
    )
    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        troop_loadout={},
        status=RaidRun.Status.RETURNING,
        travel_time=60,
        return_at=timezone.now() - timedelta(seconds=1),
    )
    run.guests.add(guest)

    returned: list[int] = []
    returned_lock = threading.Lock()

    def _record_returned(locked_run) -> None:
        with returned_lock:
            returned.append(locked_run.pk)

    monkeypatch.setattr(combat_runs, "_return_surviving_troops", _record_returned)

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def _finalize_worker() -> None:
        try:
            local_run = RaidRun.objects.get(pk=run.pk)
            barrier.wait(timeout=5)
            combat_runs.finalize_raid(local_run, now=timezone.now())
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def _refresh_worker() -> None:
        try:
            local_attacker = type(attacker).objects.get(pk=attacker.pk)
            barrier.wait(timeout=5)
            combat_runs.refresh_raid_runs(local_attacker, prefer_async=False)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [
        threading.Thread(target=_finalize_worker),
        threading.Thread(target=_refresh_worker),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    run.refresh_from_db()
    guest.refresh_from_db()

    assert errors == []
    assert returned == [run.pk]
    assert run.status == RaidRun.Status.COMPLETED
    assert run.completed_at is not None
    assert guest.status == GuestStatus.IDLE
