from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import DatabaseError
from django.utils import timezone

from core.exceptions import MessageError
from gameplay.models import GlobalMailCampaign, GlobalMailDelivery, Message
from gameplay.services.manor.core import ensure_manor
from gameplay.tasks.global_mail import (
    FAILED_GLOBAL_MAIL_MANOR_IDS_TTL,
    backfill_global_mail_campaign_task,
    clear_failed_manor_ids,
    enqueue_global_mail_backfill,
    get_failed_manor_ids,
    persist_failed_manor_ids,
)


@pytest.mark.django_db
def test_backfill_global_mail_campaign_task_returns_not_found_for_missing_campaign():
    result = backfill_global_mail_campaign_task.run(999999)

    assert result["status"] == "not_found"
    assert result["campaign_id"] == 999999
    assert result["scanned"] == 0
    assert result["delivered"] == 0
    assert result["failed"] == 0


@pytest.mark.django_db
def test_backfill_global_mail_campaign_task_skips_inactive_campaign(django_user_model):
    user = django_user_model.objects.create_user(username="global_mail_task_inactive", password="pass123")
    ensure_manor(user)

    now = timezone.now()
    campaign = GlobalMailCampaign.objects.create(
        key="global_mail_task_future",
        kind=Message.Kind.REWARD,
        title="未来补发活动",
        body="尚未开始",
        attachments={"resources": {"silver": 100}},
        is_active=True,
        start_at=now + timedelta(hours=1),
        end_at=now + timedelta(days=1),
    )

    result = backfill_global_mail_campaign_task.run(campaign.id)

    assert result["status"] == "inactive"
    assert result["campaign_id"] == campaign.id
    assert result["scanned"] == 0
    assert result["delivered"] == 0
    assert result["failed"] == 0
    assert GlobalMailDelivery.objects.filter(campaign=campaign).count() == 0


@pytest.mark.django_db
def test_backfill_global_mail_campaign_task_is_idempotent(django_user_model):
    user_a = django_user_model.objects.create_user(username="global_mail_task_user_a", password="pass123")
    user_b = django_user_model.objects.create_user(username="global_mail_task_user_b", password="pass123")
    manor_a = ensure_manor(user_a)
    manor_b = ensure_manor(user_b)

    campaign = GlobalMailCampaign.objects.create(
        key="global_mail_task_active",
        kind=Message.Kind.REWARD,
        title="任务补发奖励",
        body="异步补发测试",
        attachments={"resources": {"grain": 88}, "items": {"peace_shield_small": 1}},
        is_active=True,
    )

    first_result = backfill_global_mail_campaign_task.run(campaign.id, batch_size=1)
    second_result = backfill_global_mail_campaign_task.run(campaign.id, batch_size=1)

    assert first_result["status"] == "ok"
    assert first_result["delivered"] == 2
    assert first_result["failed"] == 0
    assert first_result["scanned"] >= 2

    assert second_result["status"] == "ok"
    assert second_result["delivered"] == 0
    assert second_result["failed"] == 0
    assert second_result["scanned"] >= 2

    assert GlobalMailDelivery.objects.filter(campaign=campaign).count() == 2
    assert GlobalMailDelivery.objects.filter(campaign=campaign, manor=manor_a).exists()
    assert GlobalMailDelivery.objects.filter(campaign=campaign, manor=manor_b).exists()
    assert Message.objects.filter(title="任务补发奖励").count() == 2


@pytest.mark.django_db
def test_backfill_global_mail_campaign_task_records_failed_manor_ids(monkeypatch, django_user_model):
    user_a = django_user_model.objects.create_user(username="global_mail_task_fail_user_a", password="pass123")
    user_b = django_user_model.objects.create_user(username="global_mail_task_fail_user_b", password="pass123")
    manor_a = ensure_manor(user_a)
    manor_b = ensure_manor(user_b)

    campaign = GlobalMailCampaign.objects.create(
        key="global_mail_task_partial_failure",
        kind=Message.Kind.REWARD,
        title="任务补发失败记录",
        body="失败列表测试",
        attachments={"resources": {"wood": 10}},
        is_active=True,
    )

    original_deliver = backfill_global_mail_campaign_task.run.__globals__["deliver_campaign_to_manor"]

    def _deliver_with_failure(current_campaign, manor, now):
        if manor.id == manor_b.id:
            raise MessageError("message backend down")
        return original_deliver(current_campaign, manor, now=now)

    monkeypatch.setattr("gameplay.tasks.global_mail.deliver_campaign_to_manor", _deliver_with_failure)

    result = backfill_global_mail_campaign_task.run(campaign.id, batch_size=1)

    assert result["status"] == "partial_failure"
    assert result["delivered"] == 1
    assert result["failed"] == 1
    assert result["failed_manor_ids"] == [manor_b.id]
    assert str(manor_b.id) in result["summary"]
    assert GlobalMailDelivery.objects.filter(campaign=campaign, manor=manor_a).exists()
    assert not GlobalMailDelivery.objects.filter(campaign=campaign, manor=manor_b).exists()


@pytest.mark.django_db
def test_backfill_global_mail_campaign_task_database_error_is_partial_failure(monkeypatch, django_user_model):
    user_a = django_user_model.objects.create_user(username="global_mail_task_db_user_a", password="pass123")
    user_b = django_user_model.objects.create_user(username="global_mail_task_db_user_b", password="pass123")
    ensure_manor(user_a)
    manor_b = ensure_manor(user_b)

    campaign = GlobalMailCampaign.objects.create(
        key="global_mail_task_db_failure",
        kind=Message.Kind.REWARD,
        title="任务补发数据库失败记录",
        body="数据库失败列表测试",
        attachments={"resources": {"wood": 10}},
        is_active=True,
    )

    original_deliver = backfill_global_mail_campaign_task.run.__globals__["deliver_campaign_to_manor"]

    def _deliver_with_failure(current_campaign, manor, now):
        if manor.id == manor_b.id:
            raise DatabaseError("delivery table unavailable")
        return original_deliver(current_campaign, manor, now=now)

    monkeypatch.setattr("gameplay.tasks.global_mail.deliver_campaign_to_manor", _deliver_with_failure)

    result = backfill_global_mail_campaign_task.run(campaign.id, batch_size=1)

    assert result["status"] == "partial_failure"
    assert result["failed"] == 1
    assert result["failed_manor_ids"] == [manor_b.id]


@pytest.mark.django_db
def test_backfill_global_mail_campaign_task_programming_error_bubbles_up(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="global_mail_task_programming_user", password="pass123")
    ensure_manor(user)

    campaign = GlobalMailCampaign.objects.create(
        key="global_mail_task_programming_failure",
        kind=Message.Kind.REWARD,
        title="任务补发编程错误",
        body="编程错误测试",
        attachments={"resources": {"wood": 10}},
        is_active=True,
    )

    monkeypatch.setattr(
        "gameplay.tasks.global_mail.deliver_campaign_to_manor",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken global mail delivery contract")),
    )

    with pytest.raises(AssertionError, match="broken global mail delivery contract"):
        backfill_global_mail_campaign_task.run(campaign.id, batch_size=1)


def test_global_mail_failed_manor_ids_cache_programming_errors_bubble_up(monkeypatch):
    monkeypatch.setattr(
        "gameplay.tasks.global_mail.cache.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken global mail failed-id cache get")),
    )

    with pytest.raises(AssertionError, match="broken global mail failed-id cache get"):
        persist_failed_manor_ids(7, [1, 2])

    with pytest.raises(AssertionError, match="broken global mail failed-id cache get"):
        get_failed_manor_ids(7)


def test_persist_failed_manor_ids_merges_existing_ids_atomically(monkeypatch):
    calls = []

    def _fake_merge(key, ids, *, ttl):
        calls.append((key, ids, ttl))
        return [1, 2, 3]

    monkeypatch.setattr("gameplay.tasks.global_mail.merge_int_id_set", _fake_merge)

    persist_failed_manor_ids(7, [2, 3])

    assert calls == [("gameplay:global_mail:failed_manor_ids:7", [2, 3], FAILED_GLOBAL_MAIL_MANOR_IDS_TTL)]


def test_global_mail_failed_manor_ids_cache_delete_programming_error_bubbles_up(monkeypatch):
    monkeypatch.setattr(
        "gameplay.tasks.global_mail.cache.delete",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken global mail failed-id cache delete")),
    )

    with pytest.raises(AssertionError, match="broken global mail failed-id cache delete"):
        clear_failed_manor_ids(7)


def test_enqueue_global_mail_backfill_submits_expected_args(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_apply_async(*_args, **kwargs):
        captured["args"] = kwargs.get("args")
        return None

    monkeypatch.setattr(backfill_global_mail_campaign_task, "apply_async", _fake_apply_async)

    result = enqueue_global_mail_backfill(7, batch_size=123)

    assert result is True
    assert captured["args"] == [7, 123]
