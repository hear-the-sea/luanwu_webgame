from __future__ import annotations

import threading
import uuid

import pytest
from django.db import close_old_connections, connection, transaction

from gameplay.models import InventoryItem, ItemTemplate, Manor, Message, ResourceEvent, ResourceType
from gameplay.services.manor.core import ensure_manor
from gameplay.services.utils.messages import claim_message_attachments, delete_messages

pytestmark = [pytest.mark.integration]


def _create_race_message(django_user_model, *, suffix: str):
    user = django_user_model.objects.create_user(username=f"message_claim_delete_{suffix}", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 0
    manor.silver_capacity = 100
    manor.save(update_fields=["silver", "silver_capacity"])
    item_template = ItemTemplate.objects.create(key=f"message_race_item_{suffix}", name="消息竞争测试道具")
    message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title=f"领取与删除竞争-{suffix}",
        attachments={
            "resources": {"silver": 7},
            "items": {item_template.key: 2},
        },
    )
    return manor, item_template, message


def _assert_assets_granted_once(manor: Manor, item_template: ItemTemplate, *, message_title: str) -> None:
    manor.refresh_from_db()
    assert manor.silver == 7
    inventory = InventoryItem.objects.get(
        manor=manor,
        template=item_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert inventory.quantity == 2
    resource_events = ResourceEvent.objects.filter(
        manor=manor,
        resource_type=ResourceType.SILVER,
        reason=ResourceEvent.Reason.ADMIN_ADJUST,
        note=f"邮件附件：{message_title}",
    )
    assert list(resource_events.values_list("delta", flat=True)) == [7]


@pytest.mark.django_db(transaction=True)
def test_delete_lock_first_preserves_unclaimed_message_then_claim_succeeds(django_user_model):
    if connection.vendor != "mysql":
        pytest.skip("message claim/delete concurrency requires MySQL select_for_update semantics")

    suffix = f"delete-first-{uuid.uuid4().hex[:8]}"
    manor, item_template, message = _create_race_message(django_user_model, suffix=suffix)
    delete_checked = threading.Event()
    claim_attempting = threading.Event()
    outcomes_guard = threading.Lock()
    claim_results = []
    delete_results = []
    errors: list[BaseException] = []

    def _delete_worker() -> None:
        close_old_connections()
        try:
            worker_manor = Manor.objects.get(pk=manor.pk)
            with transaction.atomic():
                Message.objects.select_for_update().get(pk=message.pk)
                result = delete_messages(worker_manor, [message.pk])
                with outcomes_guard:
                    delete_results.append(result)
                delete_checked.set()
                if not claim_attempting.wait(timeout=10):
                    raise AssertionError("claim worker did not attempt while delete lock was held")
        except BaseException as exc:  # pragma: no cover - asserted below
            with outcomes_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    def _claim_worker() -> None:
        close_old_connections()
        try:
            if not delete_checked.wait(timeout=10):
                raise AssertionError("delete worker did not acquire and verify the row lock")
            claim_attempting.set()
            result = claim_message_attachments(Message(pk=message.pk))
            with outcomes_guard:
                claim_results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with outcomes_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=_delete_worker), threading.Thread(target=_claim_worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert [(result.deleted_count, result.protected_count) for result in delete_results] == [(0, 1)]
    assert claim_results == [{"silver": 7, f"item_{item_template.key}": 2}]
    persisted_message = Message.objects.get(pk=message.pk)
    assert persisted_message.is_claimed is True
    assert persisted_message.is_deletion_protected is False
    _assert_assets_granted_once(manor, item_template, message_title=message.title)


@pytest.mark.django_db(transaction=True)
def test_claim_lock_first_commits_before_delete_removes_claimed_message(django_user_model):
    if connection.vendor != "mysql":
        pytest.skip("message claim/delete concurrency requires MySQL select_for_update semantics")

    suffix = f"claim-first-{uuid.uuid4().hex[:8]}"
    manor, item_template, message = _create_race_message(django_user_model, suffix=suffix)
    claim_locked = threading.Event()
    delete_attempting = threading.Event()
    outcomes_guard = threading.Lock()
    claim_results = []
    delete_results = []
    errors: list[BaseException] = []

    def _claim_worker() -> None:
        close_old_connections()
        try:
            with transaction.atomic():
                Message.objects.select_for_update().get(pk=message.pk)
                claim_locked.set()
                if not delete_attempting.wait(timeout=10):
                    raise AssertionError("delete worker did not attempt while claim lock was held")
                result = claim_message_attachments(Message(pk=message.pk))
                with outcomes_guard:
                    claim_results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with outcomes_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    def _delete_worker() -> None:
        close_old_connections()
        try:
            if not claim_locked.wait(timeout=10):
                raise AssertionError("claim worker did not acquire the row lock")
            worker_manor = Manor.objects.get(pk=manor.pk)
            delete_attempting.set()
            result = delete_messages(worker_manor, [message.pk])
            with outcomes_guard:
                delete_results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with outcomes_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=_claim_worker), threading.Thread(target=_delete_worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert claim_results == [{"silver": 7, f"item_{item_template.key}": 2}]
    assert [(result.deleted_count, result.protected_count) for result in delete_results] == [(1, 0)]
    assert Message.objects.filter(pk=message.pk).exists() is False
    _assert_assets_granted_once(manor, item_template, message_title=message.title)
