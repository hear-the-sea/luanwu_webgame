from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

import gameplay.tasks as tasks
from gameplay.models import RaidRun
from gameplay.services.manor.core import ensure_manor
from tests.gameplay_tasks.support import Chain


@pytest.mark.django_db
def test_scan_scout_records_counts_both_phases(monkeypatch):
    scouting = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
    returning = [SimpleNamespace(id=3)]

    class _Status:
        SCOUTING = "scouting"
        RETURNING = "returning"

    class _ScoutObjects:
        def __init__(self):
            self._status = None

        def select_related(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            self._status = kwargs.get("status")
            return self

        def order_by(self, *args, **kwargs):
            return self

        def __getitem__(self, item):
            if self._status == _Status.SCOUTING:
                return list(scouting)
            if self._status == _Status.RETURNING:
                return list(returning)
            return []

    dummy_cls = type("_ScoutRecord", (), {"objects": _ScoutObjects(), "Status": _Status})
    monkeypatch.setattr("gameplay.models.ScoutRecord", dummy_cls)

    called = {"scout": 0, "return": 0}

    def _finalize_scout(*_args, **_kwargs):
        called["scout"] += 1

    def _finalize_return(*_args, **_kwargs):
        called["return"] += 1

    monkeypatch.setattr("gameplay.services.raid.finalize_scout", _finalize_scout)
    monkeypatch.setattr("gameplay.services.raid.finalize_scout_return", _finalize_return)

    assert tasks.scan_scout_records() == 3
    assert called["scout"] == 2
    assert called["return"] == 1


@pytest.mark.django_db
def test_complete_scout_task_programming_error_bubbles_without_retry(monkeypatch):
    now = timezone.now()

    class _Status:
        SCOUTING = "scouting"

    record = SimpleNamespace(status=_Status.SCOUTING, complete_at=now - timedelta(seconds=1))
    monkeypatch.setattr(
        "gameplay.models.ScoutRecord",
        SimpleNamespace(objects=Chain(first_result=record), Status=_Status),
    )
    monkeypatch.setattr("gameplay.tasks.pvp.timezone.now", lambda: now)
    monkeypatch.setattr(
        "gameplay.tasks.pvp.complete_scout_task.retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )
    monkeypatch.setattr(
        "gameplay.services.raid.finalize_scout",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken scout finalize contract")),
    )

    with pytest.raises(AssertionError, match="broken scout finalize contract"):
        tasks.complete_scout_task.run(301)


@pytest.mark.django_db
def test_complete_raid_task_programming_error_bubbles_without_retry(monkeypatch):
    now = timezone.now()

    class _Status:
        COMPLETED = "completed"
        RETREATED = "retreated"
        RETURNING = "returning"

    run = SimpleNamespace(status=_Status.RETURNING, return_at=now - timedelta(seconds=1))
    monkeypatch.setattr(
        "gameplay.models.RaidRun",
        SimpleNamespace(objects=Chain(first_result=run), Status=_Status),
    )
    monkeypatch.setattr("gameplay.tasks.pvp.timezone.now", lambda: now)
    monkeypatch.setattr(
        "gameplay.tasks.pvp.complete_raid_task.retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )
    monkeypatch.setattr(
        "gameplay.services.raid.finalize_raid",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken raid finalize contract")),
    )

    with pytest.raises(AssertionError, match="broken raid finalize contract"):
        tasks.complete_raid_task.run(302)


@pytest.mark.django_db
def test_scan_raid_runs_programming_error_bubbles_up(monkeypatch):
    now = timezone.now()
    marching = [SimpleNamespace(id=11)]
    returning = []
    retreated = []

    class _Status:
        MARCHING = "marching"
        RETURNING = "returning"
        RETREATED = "retreated"

    class _RaidObjects:
        def __init__(self):
            self._status = None

        def select_related(self, *args, **kwargs):
            return self

        def prefetch_related(self, *args, **kwargs):
            return self

        def filter(self, *args, **kwargs):
            self._status = kwargs.get("status")
            return self

        def order_by(self, *args, **kwargs):
            return self

        def __getitem__(self, item):
            if self._status == _Status.MARCHING:
                return list(marching)
            if self._status == _Status.RETURNING:
                return list(returning)
            if self._status == _Status.RETREATED:
                return list(retreated)
            return []

    monkeypatch.setattr(
        "gameplay.models.RaidRun",
        type("_RaidRun", (), {"objects": _RaidObjects(), "Status": _Status}),
    )
    monkeypatch.setattr("gameplay.tasks.pvp.timezone.now", lambda: now)
    monkeypatch.setattr(
        "gameplay.services.raid.process_raid_battle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken raid scan contract")),
    )

    with pytest.raises(AssertionError, match="broken raid scan contract"):
        tasks.scan_raid_runs()


@pytest.mark.django_db
def test_scan_raid_runs_recovers_marching_run_without_battle_deadline(monkeypatch, django_user_model):
    attacker_user = django_user_model.objects.create_user(username="raid_null_deadline_attacker", password="pass123")
    defender_user = django_user_model.objects.create_user(username="raid_null_deadline_defender", password="pass123")
    attacker = ensure_manor(attacker_user)
    defender = ensure_manor(defender_user)
    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        travel_time=60,
        battle_at=None,
    )
    processed = []
    monkeypatch.setattr(
        "gameplay.services.raid.process_raid_battle",
        lambda due_run, now=None: processed.append(due_run.pk),
    )

    assert tasks.scan_raid_runs(limit=1) == 1
    assert processed == [run.pk]


@pytest.mark.parametrize("status", [RaidRun.Status.RETURNING, RaidRun.Status.RETREATED])
@pytest.mark.django_db
def test_scan_raid_runs_recovers_finished_travel_without_return_deadline(monkeypatch, django_user_model, status):
    attacker_user = django_user_model.objects.create_user(
        username=f"raid_null_return_attacker_{status}", password="pass123"
    )
    defender_user = django_user_model.objects.create_user(
        username=f"raid_null_return_defender_{status}", password="pass123"
    )
    attacker = ensure_manor(attacker_user)
    defender = ensure_manor(defender_user)
    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=status,
        travel_time=60,
        return_at=None,
    )
    finalized = []
    monkeypatch.setattr(
        "gameplay.services.raid.finalize_raid",
        lambda due_run, now=None: finalized.append(due_run.pk),
    )

    assert tasks.scan_raid_runs(limit=1) == 1
    assert finalized == [run.pk]


@pytest.mark.django_db(transaction=True)
def test_scan_raid_runs_continues_after_invalid_durable_run(monkeypatch, django_user_model):
    attacker_user = django_user_model.objects.create_user(username="raid_scan_invalid_a", password="pass123")
    defender_user = django_user_model.objects.create_user(username="raid_scan_invalid_d", password="pass123")
    attacker = ensure_manor(attacker_user)
    defender = ensure_manor(defender_user)
    now = timezone.now()
    invalid_run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        guest_snapshots=[],
        battle_at=None,
    )
    later_run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.MARCHING,
        guest_snapshots=[{"sentinel": True}],
        battle_at=now - timedelta(seconds=1),
    )

    from gameplay.services import raid as raid_service
    from gameplay.services.raid.combat import battle as combat_battle

    real_process = raid_service.process_raid_battle
    seen: list[int] = []

    def _process(run, now=None):
        seen.append(run.id)
        if run.id == invalid_run.id:
            monkeypatch.setattr(combat_battle, "_get_defender_battle_block_reason", lambda *_args, **_kwargs: None)
            return real_process(run, now=now)
        RaidRun.objects.filter(pk=run.pk).update(
            status=RaidRun.Status.RETURNING,
            return_at=now + timedelta(minutes=1),
        )
        return None

    monkeypatch.setattr(raid_service, "process_raid_battle", _process)

    assert tasks.scan_raid_runs(limit=10) == 2
    invalid_run.refresh_from_db()
    assert invalid_run.status == RaidRun.Status.FAILED
    assert seen == [invalid_run.id, later_run.id]
