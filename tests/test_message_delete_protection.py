from __future__ import annotations

import pytest
from django.db import transaction
from django.db.models.deletion import Collector, ProtectedError

from gameplay.models import Message
from gameplay.services.manor.core import ensure_manor


def _protected_message(manor, title: str = "protected message") -> Message:
    return Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title=title,
        attachments={"resources": {"silver": 1}},
    )


@pytest.mark.django_db
def test_direct_message_delete_rejects_unclaimed_attachment(django_user_model):
    user = django_user_model.objects.create_user(username="direct_message_delete", password="pass123")
    manor = ensure_manor(user)
    protected = _protected_message(manor)

    with pytest.raises(ProtectedError, match="未领取附件消息"):
        with transaction.atomic():
            protected.delete()

    assert Message.objects.filter(pk=protected.pk).exists() is True


@pytest.mark.django_db
def test_stale_message_instance_delete_reloads_locked_database_state(django_user_model):
    user = django_user_model.objects.create_user(username="stale_message_delete", password="pass123")
    manor = ensure_manor(user)
    stale_message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.SYSTEM,
        title="stale plain message",
    )
    Message.objects.filter(pk=stale_message.pk).update(
        attachments={"resources": {"silver": 1}},
        is_claimed=False,
        is_deletion_protected=True,
    )

    with pytest.raises(ProtectedError, match="未领取附件消息"):
        stale_message.delete()

    persisted = Message.objects.get(pk=stale_message.pk)
    assert persisted.has_protected_attachments is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("delete_path", ["queryset", "cascade"])
def test_collector_delete_reloads_state_changed_after_collection(
    django_user_model,
    monkeypatch,
    delete_path,
):
    user = django_user_model.objects.create_user(
        username=f"collector_stale_message_{delete_path}",
        password="pass123",
    )
    manor = ensure_manor(user)
    message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.SYSTEM,
        title=f"collector stale message {delete_path}",
    )
    original_delete = Collector.delete
    protection_injected = False

    def delete_after_protection(collector):
        nonlocal protection_injected
        collected_message_ids = {instance.pk for instance in collector.data.get(Message, ())}
        if message.pk in collected_message_ids:
            Message.objects.filter(pk=message.pk).update(
                attachments={"resources": {"silver": 1}},
                is_claimed=False,
                is_deletion_protected=True,
            )
            protection_injected = True
        return original_delete(collector)

    monkeypatch.setattr(Collector, "delete", delete_after_protection)

    with pytest.raises(ProtectedError, match="未领取附件消息"):
        if delete_path == "queryset":
            Message.objects.filter(pk=message.pk).delete()
        else:
            manor.delete()

    assert protection_injected is True
    persisted = Message.objects.get(pk=message.pk)
    assert persisted.has_protected_attachments is True
    assert type(manor).objects.filter(pk=manor.pk).exists() is True


@pytest.mark.django_db
def test_manor_delete_rejects_cascade_with_unclaimed_attachment(django_user_model):
    user = django_user_model.objects.create_user(username="manor_message_delete", password="pass123")
    manor = ensure_manor(user)
    protected = _protected_message(manor)

    with pytest.raises(ProtectedError, match="未领取附件消息"):
        with transaction.atomic():
            manor.delete()

    assert type(manor).objects.filter(pk=manor.pk).exists() is True
    assert Message.objects.filter(pk=protected.pk).exists() is True


@pytest.mark.django_db
def test_user_delete_rejects_collector_with_unclaimed_attachment(django_user_model):
    user = django_user_model.objects.create_user(username="user_message_delete", password="pass123")
    manor = ensure_manor(user)
    protected = _protected_message(manor)

    with pytest.raises(ProtectedError, match="未领取附件消息"):
        with transaction.atomic():
            user.delete()

    assert django_user_model.objects.filter(pk=user.pk).exists() is True
    assert Message.objects.filter(pk=protected.pk).exists() is True


@pytest.mark.django_db
def test_manor_delete_allows_plain_and_claimed_attachment_messages(django_user_model):
    user = django_user_model.objects.create_user(username="allowed_message_cascade", password="pass123")
    manor = ensure_manor(user)
    plain = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="plain message")
    claimed = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="claimed message",
        attachments={"items": {"claimed_item": 1}},
        is_claimed=True,
    )

    manor.delete()

    assert type(manor).objects.filter(pk=manor.pk).exists() is False
    assert Message.objects.filter(pk__in=[plain.pk, claimed.pk]).exists() is False
