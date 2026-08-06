from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import OperationalError
from django.utils import timezone

import gameplay.tasks as tasks
from tests.gameplay_tasks.support import Chain, patch_model


@pytest.mark.django_db
def test_complete_mission_task_not_found(monkeypatch):
    monkeypatch.setattr("gameplay.tasks.missions.MissionRun", SimpleNamespace(objects=Chain(first_result=None)))

    assert tasks.complete_mission_task.run(123) == "not_found"


@pytest.mark.django_db
def test_complete_mission_task_reschedules_when_not_due(monkeypatch):
    now = timezone.now()
    run = SimpleNamespace(return_at=now + timedelta(seconds=10))

    monkeypatch.setattr("gameplay.tasks.missions.MissionRun", SimpleNamespace(objects=Chain(first_result=run)))
    finalize = []
    monkeypatch.setattr("gameplay.tasks.missions.finalize_mission_run", lambda *_args, **_kwargs: finalize.append(True))

    called = {}

    def _apply_async(*, args=None, countdown=None, **_kwargs):
        called["args"] = args
        called["countdown"] = countdown

    monkeypatch.setattr(tasks.complete_mission_task, "apply_async", _apply_async)

    assert tasks.complete_mission_task.run(1) == "rescheduled"
    assert called["args"] == [1]
    assert called["countdown"] > 0
    assert not finalize


@pytest.mark.django_db
def test_complete_mission_task_reschedules_with_ceil_for_fractional_remaining(monkeypatch):
    now = timezone.now()
    run = SimpleNamespace(return_at=now + timedelta(milliseconds=250))

    monkeypatch.setattr("gameplay.tasks.missions.MissionRun", SimpleNamespace(objects=Chain(first_result=run)))
    monkeypatch.setattr("gameplay.tasks.missions.timezone.now", lambda: now)

    called = {}

    def _safe_apply_async_with_dedup(*_args, args=None, countdown=None, **_kwargs):
        called["args"] = args
        called["countdown"] = countdown
        return True

    monkeypatch.setattr("gameplay.tasks.missions.safe_apply_async_with_dedup", _safe_apply_async_with_dedup)

    assert tasks.complete_mission_task.run(101) == "rescheduled"
    assert called["args"] == [101]
    assert called["countdown"] == 1


@pytest.mark.django_db
def test_complete_mission_task_retries_when_reschedule_dispatch_fails(monkeypatch):
    now = timezone.now()
    run = SimpleNamespace(return_at=now + timedelta(seconds=5))

    monkeypatch.setattr("gameplay.tasks.missions.MissionRun", SimpleNamespace(objects=Chain(first_result=run)))
    monkeypatch.setattr("gameplay.tasks.missions.safe_apply_async_with_dedup", lambda *_args, **_kwargs: False)

    retried = {}

    def _retry(*, exc=None, **_kwargs):
        retried["exc"] = exc
        raise RuntimeError("retry requested")

    monkeypatch.setattr(tasks.complete_mission_task, "retry", _retry)

    with pytest.raises(RuntimeError, match="retry requested"):
        tasks.complete_mission_task.run(102)

    assert "mission reschedule dispatch failed" in str(retried["exc"])


@pytest.mark.django_db
def test_complete_mission_task_programming_error_bubbles_without_retry(monkeypatch):
    now = timezone.now()
    run = SimpleNamespace(return_at=now - timedelta(seconds=1))

    monkeypatch.setattr("gameplay.tasks.missions.MissionRun", SimpleNamespace(objects=Chain(first_result=run)))
    monkeypatch.setattr(
        "gameplay.tasks.missions.finalize_mission_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken mission finalize contract")),
    )
    monkeypatch.setattr(
        tasks.complete_mission_task,
        "retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )

    with pytest.raises(AssertionError, match="broken mission finalize contract"):
        tasks.complete_mission_task.run(103)


@pytest.mark.django_db
def test_scan_due_missions_programming_error_bubbles_up(monkeypatch):
    now = timezone.now()
    runs = [SimpleNamespace(id=1, pk=1)]

    class _Status:
        ACTIVE = "active"

    patch_model(monkeypatch, "gameplay.tasks.missions.MissionRun", slice_result=runs, status_cls=_Status)
    monkeypatch.setattr("gameplay.tasks.missions.timezone.now", lambda: now)
    monkeypatch.setattr(
        "gameplay.tasks.missions.finalize_mission_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken mission scan contract")),
    )

    with pytest.raises(AssertionError, match="broken mission scan contract"):
        tasks.scan_due_missions()


@pytest.mark.django_db
def test_scan_due_missions_uses_finalizer_result_without_status_probe(monkeypatch):
    now = timezone.now()
    runs = [SimpleNamespace(id=1, pk=1), SimpleNamespace(id=2, pk=2)]

    class _Status:
        ACTIVE = "active"

    patch_model(monkeypatch, "gameplay.tasks.missions.MissionRun", slice_result=runs, status_cls=_Status)
    monkeypatch.setattr("gameplay.tasks.missions.timezone.now", lambda: now)
    monkeypatch.setattr(
        "gameplay.tasks.missions.finalize_mission_run",
        lambda run, *, now=None: run.id == 1,
    )

    assert tasks.scan_due_missions() == 1


@pytest.mark.django_db
def test_complete_building_upgrade_completes_or_skips(monkeypatch):
    now = timezone.now()
    building = SimpleNamespace(upgrade_complete_at=now - timedelta(seconds=1), id=7)

    class _Building:
        objects = Chain(first_result=building)

    monkeypatch.setattr("gameplay.models.Building", _Building)
    monkeypatch.setattr("gameplay.tasks.buildings.finalize_building_upgrade", lambda *_args, **_kwargs: True)

    assert tasks.complete_building_upgrade.run(7) == "completed"

    monkeypatch.setattr("gameplay.tasks.buildings.finalize_building_upgrade", lambda *_args, **_kwargs: False)
    assert tasks.complete_building_upgrade.run(7) == "skipped"


@pytest.mark.django_db
def test_complete_building_upgrade_reschedules_when_fractional_remaining(monkeypatch):
    now = timezone.now()
    building = SimpleNamespace(upgrade_complete_at=now + timedelta(milliseconds=300), id=8)

    class _Building:
        objects = Chain(first_result=building)

    monkeypatch.setattr("gameplay.models.Building", _Building)
    monkeypatch.setattr("gameplay.tasks.buildings.timezone.now", lambda: now)

    finalized = []
    monkeypatch.setattr(
        "gameplay.tasks.buildings.finalize_building_upgrade",
        lambda *_args, **_kwargs: finalized.append(True),
    )

    called = {}

    def _safe_apply_async_with_dedup(*_args, args=None, countdown=None, **_kwargs):
        called["args"] = args
        called["countdown"] = countdown
        return True

    monkeypatch.setattr("gameplay.tasks.buildings.safe_apply_async_with_dedup", _safe_apply_async_with_dedup)

    assert tasks.complete_building_upgrade.run(8) == "rescheduled"
    assert called["args"] == [8]
    assert called["countdown"] == 1
    assert not finalized


@pytest.mark.django_db
def test_scan_building_upgrades_counts_successes(monkeypatch):
    buildings = [SimpleNamespace(id=1), SimpleNamespace(id=2), SimpleNamespace(id=3)]

    class _Building:
        objects = Chain(slice_result=buildings)

    monkeypatch.setattr("gameplay.models.Building", _Building)

    def _finalize(building, **_kwargs):
        if building.id == 2:
            raise OperationalError("db down")
        return building.id != 3

    monkeypatch.setattr("gameplay.tasks.buildings.finalize_building_upgrade", _finalize)

    assert tasks.scan_building_upgrades() == 1


@pytest.mark.django_db
def test_complete_building_upgrade_programming_error_bubbles_without_retry(monkeypatch):
    now = timezone.now()
    building = SimpleNamespace(upgrade_complete_at=now - timedelta(seconds=1), id=9)

    class _Building:
        objects = Chain(first_result=building)

    monkeypatch.setattr("gameplay.models.Building", _Building)
    monkeypatch.setattr(
        "gameplay.tasks.buildings.complete_building_upgrade.retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )
    monkeypatch.setattr(
        "gameplay.tasks.buildings.finalize_building_upgrade",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken building finalize contract")),
    )

    with pytest.raises(AssertionError, match="broken building finalize contract"):
        tasks.complete_building_upgrade.run(9)


@pytest.mark.django_db
def test_scan_building_upgrades_programming_error_bubbles_up(monkeypatch):
    buildings = [SimpleNamespace(id=1)]

    class _Building:
        objects = Chain(slice_result=buildings)

    monkeypatch.setattr("gameplay.models.Building", _Building)
    monkeypatch.setattr(
        "gameplay.tasks.buildings.finalize_building_upgrade",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken building scan contract")),
    )

    with pytest.raises(AssertionError, match="broken building scan contract"):
        tasks.scan_building_upgrades()


@pytest.mark.django_db
def test_complete_work_assignments_task_returns_count(monkeypatch):
    monkeypatch.setattr("gameplay.services.work.complete_work_assignments", lambda: 3)
    assert tasks.complete_work_assignments_task.run() == "完成 3 个打工任务"


@pytest.mark.django_db
def test_complete_work_assignments_task_programming_error_bubbles_up(monkeypatch):
    monkeypatch.setattr(
        "gameplay.services.work.complete_work_assignments",
        lambda: (_ for _ in ()).throw(AssertionError("broken work completion contract")),
    )

    with pytest.raises(AssertionError, match="broken work completion contract"):
        tasks.complete_work_assignments_task.run()
