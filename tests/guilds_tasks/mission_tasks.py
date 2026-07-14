from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from tests.guilds_tasks.support import create_active_guild_run


@pytest.mark.django_db
def test_complete_guild_mission_task_reschedules_when_not_due(monkeypatch, django_user_model):
    from guilds.tasks import complete_guild_mission_task

    now = timezone.now()
    run = create_active_guild_run(
        django_user_model,
        username="guild_task_future",
        key_suffix="future",
        return_at=now + timedelta(seconds=10),
    )

    called: dict[str, object] = {}
    finalized: list[int] = []

    monkeypatch.setattr("guilds.tasks.timezone.now", lambda: now)
    monkeypatch.setattr(
        "guilds.tasks.safe_apply_async_with_dedup",
        lambda *_args, args=None, countdown=None, **_kwargs: called.update({"args": args, "countdown": countdown})
        or True,
    )
    monkeypatch.setattr("guilds.tasks.finalize_guild_mission_run", lambda *_args, **_kwargs: finalized.append(run.id))
    monkeypatch.setattr(
        complete_guild_mission_task,
        "retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )

    assert complete_guild_mission_task.run(run.id) == "rescheduled"
    assert called["args"] == [run.id]
    assert called["countdown"] == 10
    assert not finalized


@pytest.mark.django_db
def test_complete_guild_mission_task_finalizes_due_run(monkeypatch, django_user_model):
    from guilds.tasks import complete_guild_mission_task

    now = timezone.now()
    run = create_active_guild_run(
        django_user_model,
        username="guild_task_due",
        key_suffix="due",
        return_at=now - timedelta(seconds=1),
    )

    finalized: list[tuple[int, object]] = []
    monkeypatch.setattr("guilds.tasks.timezone.now", lambda: now)
    monkeypatch.setattr(
        "guilds.tasks.finalize_guild_mission_run",
        lambda locked_run, now=None: finalized.append((locked_run.id, now)) or True,
    )

    assert complete_guild_mission_task.run(run.id) == "completed"
    assert finalized == [(run.id, now)]


@pytest.mark.django_db
def test_complete_guild_mission_task_retries_when_due_run_remains_active(monkeypatch, django_user_model):
    from guilds.tasks import complete_guild_mission_task

    now = timezone.now()
    run = create_active_guild_run(
        django_user_model,
        username="guild_task_owner_changed",
        key_suffix="owner_changed",
        return_at=now - timedelta(seconds=1),
    )
    retried: dict[str, object] = {}

    monkeypatch.setattr("guilds.tasks.timezone.now", lambda: now)
    monkeypatch.setattr("guilds.tasks.finalize_guild_mission_run", lambda *_args, **_kwargs: False)

    def _retry(*, exc=None, **_kwargs):
        retried["exc"] = exc
        raise RuntimeError("retried")

    monkeypatch.setattr(complete_guild_mission_task, "retry", _retry)

    with pytest.raises(RuntimeError, match="retried"):
        complete_guild_mission_task.run(run.id)

    assert "remains active" in str(retried["exc"])


@pytest.mark.django_db
def test_complete_guild_mission_task_accepts_concurrent_completion(monkeypatch, django_user_model):
    from guilds.models import GuildMissionRun
    from guilds.tasks import complete_guild_mission_task

    now = timezone.now()
    run = create_active_guild_run(
        django_user_model,
        username="guild_task_concurrent_completion",
        key_suffix="concurrent_completion",
        return_at=now - timedelta(seconds=1),
    )

    monkeypatch.setattr("guilds.tasks.timezone.now", lambda: now)

    def _concurrent_finalize(*_args, **_kwargs):
        GuildMissionRun.objects.filter(pk=run.pk).update(
            status=GuildMissionRun.Status.COMPLETED,
            completed_at=now,
        )
        return False

    monkeypatch.setattr("guilds.tasks.finalize_guild_mission_run", _concurrent_finalize)
    monkeypatch.setattr(
        complete_guild_mission_task,
        "retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )

    assert complete_guild_mission_task.run(run.id) == "already_completed"


@pytest.mark.django_db
def test_complete_guild_mission_task_retries_when_reschedule_dispatch_fails(monkeypatch, django_user_model):
    from guilds.tasks import complete_guild_mission_task

    now = timezone.now()
    run = create_active_guild_run(
        django_user_model,
        username="guild_task_retry",
        key_suffix="retry",
        return_at=now + timedelta(seconds=5),
    )

    retried: dict[str, object] = {}

    monkeypatch.setattr("guilds.tasks.timezone.now", lambda: now)
    monkeypatch.setattr("guilds.tasks.safe_apply_async_with_dedup", lambda *_args, **_kwargs: False)

    def _retry(*, exc=None, **_kwargs):
        retried["exc"] = exc
        raise RuntimeError("retried")

    monkeypatch.setattr(complete_guild_mission_task, "retry", _retry)

    with pytest.raises(RuntimeError, match="retried"):
        complete_guild_mission_task.run(run.id)

    assert "guild mission reschedule dispatch failed" in str(retried["exc"])


@pytest.mark.django_db
def test_scan_due_guild_missions_finalizes_overdue_runs(monkeypatch, django_user_model):
    from guilds.models import GuildMissionRun
    from guilds.tasks import scan_due_guild_missions

    now = timezone.now()
    due_run = create_active_guild_run(
        django_user_model,
        username="guild_task_scan_due",
        key_suffix="scan_due",
        return_at=now - timedelta(seconds=5),
    )
    future_run = create_active_guild_run(
        django_user_model,
        username="guild_task_scan_future",
        key_suffix="scan_future",
        return_at=now + timedelta(seconds=30),
    )

    finalized: list[int] = []
    monkeypatch.setattr("guilds.tasks.timezone.now", lambda: now)

    def _fake_finalize(run, *, now=None):
        GuildMissionRun.objects.filter(pk=run.pk).update(status=GuildMissionRun.Status.COMPLETED, completed_at=now)
        finalized.append(run.id)
        return True

    monkeypatch.setattr("guilds.tasks.finalize_guild_mission_run", _fake_finalize)

    assert scan_due_guild_missions() == 1
    assert finalized == [due_run.id]
    assert GuildMissionRun.objects.get(pk=future_run.pk).status == GuildMissionRun.Status.ACTIVE


@pytest.mark.django_db
def test_scan_due_guild_missions_programming_error_bubbles_up(monkeypatch, django_user_model):
    from guilds.tasks import scan_due_guild_missions

    now = timezone.now()
    create_active_guild_run(
        django_user_model,
        username="guild_task_scan_bug",
        key_suffix="scan_bug",
        return_at=now - timedelta(seconds=5),
    )

    monkeypatch.setattr("guilds.tasks.timezone.now", lambda: now)
    monkeypatch.setattr(
        "guilds.tasks.finalize_guild_mission_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken guild mission scan contract")),
    )

    with pytest.raises(AssertionError, match="broken guild mission scan contract"):
        scan_due_guild_missions()
