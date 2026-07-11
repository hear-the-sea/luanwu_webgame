from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.db import DataError, IntegrityError, ProgrammingError
from django.utils import timezone
from redis.exceptions import ResponseError

from gameplay.models import WorldChatSendAttempt
from gameplay.services.manor.core import ensure_manor
from gameplay.services.world_chat_delivery import WorldChatInfrastructureError

pytestmark = pytest.mark.django_db


class _RetryRequested(RuntimeError):
    pass


def _capture_retry(monkeypatch, task):
    captured = {}

    def _retry(*, exc=None, **_kwargs):
        captured["exc"] = exc
        raise _RetryRequested("retry requested")

    monkeypatch.setattr(task, "retry", _retry)
    return captured


def _attempt(user_factory, *, status: str, age_seconds: int = 60) -> WorldChatSendAttempt:
    user = user_factory()
    manor = ensure_manor(user)
    attempt = WorldChatSendAttempt.objects.create(
        user=user,
        manor=manor,
        operation_id=uuid.uuid4(),
        text=f"attempt-{uuid.uuid4().hex[:6]}",
        status=status,
        trumpet_consumed=True,
    )
    WorldChatSendAttempt.objects.filter(pk=attempt.pk).update(
        created_at=timezone.now() - timedelta(seconds=age_seconds)
    )
    attempt.refresh_from_db()
    return attempt


def test_world_chat_tasks_have_stable_names_exports_routes_and_beat_schedule():
    from config.settings.celery_conf import CELERY_BEAT_SCHEDULE, CELERY_TASK_ROUTES, CELERY_TIMER_QUEUE
    from gameplay import tasks

    assert tasks.publish_world_chat_attempt_task.name == "gameplay.publish_world_chat_attempt"
    assert tasks.refund_world_chat_attempt_task.name == "gameplay.refund_world_chat_attempt"
    assert tasks.scan_world_chat_attempts_task.name == "gameplay.scan_world_chat_attempts"
    assert CELERY_TASK_ROUTES["gameplay.publish_world_chat_attempt"] == {"queue": CELERY_TIMER_QUEUE}
    assert CELERY_TASK_ROUTES["gameplay.refund_world_chat_attempt"] == {"queue": CELERY_TIMER_QUEUE}
    assert CELERY_TASK_ROUTES["gameplay.scan_world_chat_attempts"] == {"queue": CELERY_TIMER_QUEUE}
    assert CELERY_BEAT_SCHEDULE["scan-world-chat-attempts"]["task"] == "gameplay.scan_world_chat_attempts"
    assert str(CELERY_BEAT_SCHEDULE["scan-world-chat-attempts"]["schedule"]) == "<crontab: * * * * * (m/h/dM/MY/d)>"


@pytest.mark.parametrize("task_name", ["publish_world_chat_attempt_task", "refund_world_chat_attempt_task"])
def test_world_chat_single_attempt_tasks_are_idempotent_wrappers(monkeypatch, task_name):
    from gameplay.tasks import world_chat

    task = getattr(world_chat, task_name)
    service_name = task_name.removesuffix("_task")
    calls = []
    monkeypatch.setattr(world_chat, service_name, lambda attempt_id: calls.append(attempt_id) or len(calls) == 1)

    assert task.run(41) is True
    assert task.run(41) is False
    assert calls == [41, 41]


@pytest.mark.parametrize("task_name", ["publish_world_chat_attempt_task", "refund_world_chat_attempt_task"])
@pytest.mark.parametrize(
    "infrastructure_error",
    [
        pytest.param(WorldChatInfrastructureError("dependency down"), id="wrapped-infrastructure-error"),
        pytest.param(ConnectionError("connection down"), id="connection-error"),
        pytest.param(TimeoutError("timed out"), id="timeout-error"),
    ],
)
def test_world_chat_single_attempt_tasks_retry_infrastructure_errors(
    monkeypatch,
    task_name,
    infrastructure_error,
):
    from gameplay.tasks import world_chat

    task = getattr(world_chat, task_name)
    service_name = task_name.removesuffix("_task")
    monkeypatch.setattr(
        world_chat,
        service_name,
        lambda _attempt_id: (_ for _ in ()).throw(infrastructure_error),
    )
    captured = _capture_retry(monkeypatch, task)

    with pytest.raises(_RetryRequested, match="retry requested"):
        task.run(42)

    assert captured["exc"] is infrastructure_error


@pytest.mark.parametrize("task_name", ["publish_world_chat_attempt_task", "refund_world_chat_attempt_task"])
@pytest.mark.parametrize(
    "programming_error",
    [
        RuntimeError("task bug"),
        ProgrammingError("bad task query"),
        IntegrityError("bad task constraint"),
        DataError("bad task data"),
        ResponseError("bad task redis command"),
    ],
)
def test_world_chat_single_attempt_tasks_do_not_retry_programming_errors(
    monkeypatch,
    task_name,
    programming_error,
):
    from gameplay.tasks import world_chat

    task = getattr(world_chat, task_name)
    service_name = task_name.removesuffix("_task")
    monkeypatch.setattr(
        world_chat,
        service_name,
        lambda _attempt_id: (_ for _ in ()).throw(programming_error),
    )
    monkeypatch.setattr(
        task,
        "retry",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )

    with pytest.raises(type(programming_error)) as exc_info:
        task.run(43)

    assert exc_info.value is programming_error


def test_scan_world_chat_attempts_prioritizes_refunds_and_uses_one_bounded_budget(
    monkeypatch,
    user_factory,
):
    from gameplay.tasks import world_chat

    refund_one = _attempt(user_factory, status=WorldChatSendAttempt.Status.REFUND_PENDING)
    refund_two = _attempt(user_factory, status=WorldChatSendAttempt.Status.REFUND_PENDING)
    old_pending_one = _attempt(user_factory, status=WorldChatSendAttempt.Status.PENDING, age_seconds=60)
    _attempt(user_factory, status=WorldChatSendAttempt.Status.PENDING, age_seconds=60)
    _attempt(user_factory, status=WorldChatSendAttempt.Status.PENDING, age_seconds=5)
    calls = []
    monkeypatch.setattr(
        world_chat,
        "refund_world_chat_attempt",
        lambda attempt_id: calls.append(("refund", attempt_id)) or True,
    )
    monkeypatch.setattr(
        world_chat,
        "publish_world_chat_attempt",
        lambda attempt_id: calls.append(("publish", attempt_id)) or True,
    )

    result = world_chat.scan_world_chat_attempts_task.run(batch_size=3)

    assert result == {"refunds": 2, "publishes": 1}
    assert calls == [
        ("refund", refund_one.pk),
        ("refund", refund_two.pk),
        ("publish", old_pending_one.pk),
    ]


def test_scan_world_chat_attempts_skips_active_claim_and_recovers_expired_claim(
    monkeypatch,
    user_factory,
):
    from gameplay.tasks import world_chat

    now = timezone.now()
    active = _attempt(user_factory, status=WorldChatSendAttempt.Status.PENDING, age_seconds=60)
    expired = _attempt(user_factory, status=WorldChatSendAttempt.Status.PENDING, age_seconds=60)
    unclaimed = _attempt(user_factory, status=WorldChatSendAttempt.Status.PENDING, age_seconds=60)
    WorldChatSendAttempt.objects.filter(pk=active.pk).update(
        publish_claim_token=uuid.uuid4(),
        publish_claimed_at=now - timedelta(minutes=4),
    )
    WorldChatSendAttempt.objects.filter(pk=expired.pk).update(
        publish_claim_token=uuid.uuid4(),
        publish_claimed_at=now - timedelta(minutes=5, seconds=1),
    )
    monkeypatch.setattr(world_chat.timezone, "now", lambda: now)
    published_ids = []
    monkeypatch.setattr(
        world_chat,
        "publish_world_chat_attempt",
        lambda attempt_id: published_ids.append(attempt_id) or True,
    )

    result = world_chat.scan_world_chat_attempts_task.run(batch_size=10)

    assert result == {"refunds": 0, "publishes": 2}
    assert published_ids == [expired.pk, unclaimed.pk]


@pytest.mark.parametrize("batch_size", [0, -1, True, "10"])
def test_scan_world_chat_attempts_requires_positive_integer_batch(monkeypatch, batch_size):
    from gameplay.tasks import world_chat

    monkeypatch.setattr(
        world_chat.scan_world_chat_attempts_task,
        "retry",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )

    with pytest.raises(ValueError, match="positive integer"):
        world_chat.scan_world_chat_attempts_task.run(batch_size=batch_size)


def test_scan_world_chat_attempts_aborts_and_retries_shared_infrastructure_failure(
    monkeypatch,
    user_factory,
):
    from gameplay.services.world_chat_delivery import WorldChatInfrastructureError
    from gameplay.tasks import world_chat

    _attempt(user_factory, status=WorldChatSendAttempt.Status.REFUND_PENDING)
    _attempt(user_factory, status=WorldChatSendAttempt.Status.PENDING, age_seconds=60)
    infrastructure_error = WorldChatInfrastructureError("shared redis down")
    monkeypatch.setattr(
        world_chat,
        "refund_world_chat_attempt",
        lambda _attempt_id: (_ for _ in ()).throw(infrastructure_error),
    )
    publish_calls = []
    monkeypatch.setattr(
        world_chat,
        "publish_world_chat_attempt",
        lambda attempt_id: publish_calls.append(attempt_id),
    )
    captured = _capture_retry(monkeypatch, world_chat.scan_world_chat_attempts_task)

    with pytest.raises(_RetryRequested, match="retry requested"):
        world_chat.scan_world_chat_attempts_task.run(batch_size=100)

    assert captured["exc"] is infrastructure_error
    assert publish_calls == []


def test_scan_world_chat_attempts_does_not_hide_unknown_errors(monkeypatch, user_factory):
    from gameplay.tasks import world_chat

    _attempt(user_factory, status=WorldChatSendAttempt.Status.REFUND_PENDING)
    programming_error = RuntimeError("scanner bug")
    monkeypatch.setattr(
        world_chat,
        "refund_world_chat_attempt",
        lambda _attempt_id: (_ for _ in ()).throw(programming_error),
    )
    monkeypatch.setattr(
        world_chat.scan_world_chat_attempts_task,
        "retry",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )

    with pytest.raises(RuntimeError) as exc_info:
        world_chat.scan_world_chat_attempts_task.run(batch_size=100)

    assert exc_info.value is programming_error
