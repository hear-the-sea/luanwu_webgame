from __future__ import annotations

import pytest
from django.contrib.admin import helpers
from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.admin.sites import AdminSite
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages import SUCCESS, get_messages
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db.models.signals import pre_delete
from django.test import RequestFactory
from django.urls import reverse

from gameplay.admin.messages import MessageAdmin
from gameplay.models import Message
from gameplay.services.manor.core import ensure_manor
from gameplay.services.utils.cache import CacheKeys


def _admin_request(user):
    request = RequestFactory().post("/admin/gameplay/message/")
    request.user = user
    return request


@pytest.mark.django_db
def test_message_admin_disables_single_delete_for_unclaimed_attachment(django_user_model):
    user = django_user_model.objects.create_superuser(username="message_admin_single", password="pass123")
    manor = ensure_manor(user)
    protected = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="protected admin message",
        attachments={"items": {"admin_item": 1}},
    )
    admin_obj = MessageAdmin(Message, AdminSite())

    assert admin_obj.has_delete_permission(_admin_request(user), protected) is False


@pytest.mark.django_db
def test_message_admin_single_delete_rejects_forged_bulk_action_marker(django_user_model, client):
    user = django_user_model.objects.create_superuser(username="message_admin_forged_bulk", password="pass123")
    manor = ensure_manor(user)
    protected = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="forged bulk action protected message",
        attachments={"items": {"admin_item": 1}},
    )
    client.force_login(user)

    response = client.post(
        reverse("admin:gameplay_message_delete", args=[protected.pk]),
        {"post": "yes", "action": "delete_selected"},
    )

    assert response.status_code == 403
    assert Message.objects.filter(pk=protected.pk).exists() is True
    assert not any(message.level == SUCCESS for message in get_messages(response.wsgi_request))
    message_content_type = ContentType.objects.get_for_model(Message)
    assert not LogEntry.objects.filter(
        user=user,
        content_type=message_content_type,
        object_id=str(protected.pk),
        action_flag=DELETION,
    ).exists()


@pytest.mark.django_db
def test_message_admin_delete_model_rejects_protected_message_without_custom_feedback(django_user_model, monkeypatch):
    user = django_user_model.objects.create_superuser(username="message_admin_delete_model", password="pass123")
    manor = ensure_manor(user)
    protected = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="protected admin message",
        attachments={"resources": {"silver": 1}},
    )
    cache_key = CacheKeys.unread_count(manor.pk)
    cache.set(cache_key, 999, timeout=60)
    feedback = []
    admin_obj = MessageAdmin(Message, AdminSite())
    monkeypatch.setattr(admin_obj, "message_user", lambda _request, message, **_kwargs: feedback.append(str(message)))

    try:
        with pytest.raises(PermissionDenied):
            admin_obj.delete_model(_admin_request(user), protected)

        assert Message.objects.filter(pk=protected.pk).exists() is True
        assert cache.get(cache_key) == 999
        assert feedback == []
    finally:
        cache.delete(cache_key)


@pytest.mark.django_db
@pytest.mark.parametrize("delete_entrypoint", ["model", "queryset", "action"])
def test_message_admin_rejects_non_default_database_deletion(django_user_model, delete_entrypoint):
    user = django_user_model.objects.create_superuser(
        username=f"message_admin_non_default_{delete_entrypoint}",
        password="pass123",
    )
    manor = ensure_manor(user)
    message = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="non-default admin message")
    admin_obj = MessageAdmin(Message, AdminSite())
    request = _admin_request(user)

    with pytest.raises(PermissionDenied):
        if delete_entrypoint == "model":
            message._state.db = "archive"
            admin_obj.delete_model(request, message)
        elif delete_entrypoint == "queryset":
            admin_obj.delete_queryset(request, Message.objects.using("archive").filter(pk=message.pk))
        else:
            admin_obj.delete_selected(request, Message.objects.using("archive").filter(pk=message.pk))

    assert Message.objects.filter(pk=message.pk).exists() is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("attachments", "is_claimed"),
    [
        ({}, False),
        ({"resources": {"silver": 1}}, True),
    ],
)
def test_message_admin_delete_model_allows_plain_and_claimed_messages(
    django_user_model, monkeypatch, attachments, is_claimed
):
    user = django_user_model.objects.create_superuser(username="message_admin_allowed_delete", password="pass123")
    manor = ensure_manor(user)
    message = Message.objects.create(
        manor=manor,
        kind=Message.Kind.SYSTEM,
        title="allowed admin message",
        attachments=attachments,
        is_claimed=is_claimed,
    )
    cache_key = CacheKeys.unread_count(manor.pk)
    cache.set(cache_key, 999, timeout=60)
    feedback = []
    admin_obj = MessageAdmin(Message, AdminSite())
    request = _admin_request(user)
    monkeypatch.setattr(admin_obj, "message_user", lambda _request, text, **_kwargs: feedback.append(str(text)))

    try:
        assert admin_obj.has_delete_permission(request, message) is True

        result = admin_obj.delete_model(request, message)

        assert result.deleted_count == 1
        assert result.protected_count == 0
        assert Message.objects.filter(pk=message.pk).exists() is False
        assert cache.get(cache_key) is None
        assert feedback == []
    finally:
        cache.delete(cache_key)


@pytest.mark.django_db
def test_message_admin_delete_queryset_uses_protected_delete_service(django_user_model, monkeypatch):
    user = django_user_model.objects.create_superuser(username="message_admin_delete_queryset", password="pass123")
    manor = ensure_manor(user)
    protected = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="protected admin message",
        attachments={"items": {"admin_item": 1}},
    )
    plain = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="plain admin message")
    cache_key = CacheKeys.unread_count(manor.pk)
    cache.set(cache_key, 999, timeout=60)
    feedback = []
    admin_obj = MessageAdmin(Message, AdminSite())
    request = _admin_request(user)
    monkeypatch.setattr(admin_obj, "message_user", lambda _request, message, **_kwargs: feedback.append(str(message)))

    try:
        result = admin_obj.delete_queryset(request, Message.objects.filter(pk__in=[protected.pk, plain.pk]))

        assert result.deleted_count == 1
        assert result.protected_count == 1
        assert Message.objects.filter(pk=protected.pk).exists() is True
        assert Message.objects.filter(pk=plain.pk).exists() is False
        assert cache.get(cache_key) is None
        assert feedback == ["已删除 1 条消息，1 条未领取附件消息已保留"]
        assert "delete_selected" in admin_obj.get_actions(request)
        message_content_type = ContentType.objects.get_for_model(Message)
        assert list(
            LogEntry.objects.filter(content_type=message_content_type, action_flag=DELETION).values_list(
                "object_id", flat=True
            )
        ) == [str(plain.pk)]
    finally:
        cache.delete(cache_key)


@pytest.mark.django_db
def test_message_admin_single_delete_emits_one_native_success_message_and_log_entry(django_user_model, client):
    user = django_user_model.objects.create_superuser(username="message_admin_native_single", password="pass123")
    manor = ensure_manor(user)
    message = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="native single deletion")
    client.force_login(user)

    response = client.post(reverse("admin:gameplay_message_delete", args=[message.pk]), {"post": "yes"})

    assert response.status_code == 302
    assert Message.objects.filter(pk=message.pk).exists() is False
    feedback = [str(entry) for entry in get_messages(response.wsgi_request)]
    assert len(feedback) == 1
    assert "native single deletion" in feedback[0]
    assert "已删除 1 条消息" not in feedback[0]
    message_content_type = ContentType.objects.get_for_model(Message)
    deletion_logs = LogEntry.objects.filter(
        user=user,
        content_type=message_content_type,
        object_id=str(message.pk),
        action_flag=DELETION,
    )
    assert deletion_logs.count() == 1


@pytest.mark.django_db
def test_message_admin_single_delete_rolls_back_native_log_when_row_becomes_protected(
    django_user_model,
    client,
    monkeypatch,
):
    user = django_user_model.objects.create_superuser(username="message_admin_single_race", password="pass123")
    manor = ensure_manor(user)
    message = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="single deletion race")
    original_log_deletions = MessageAdmin.log_deletions

    def _log_then_protect(admin_obj, request, queryset):
        result = original_log_deletions(admin_obj, request, queryset)
        Message.objects.filter(pk=message.pk).update(
            attachments={"resources": {"silver": 1}},
            is_claimed=False,
            is_deletion_protected=True,
        )
        return result

    monkeypatch.setattr(MessageAdmin, "log_deletions", _log_then_protect)
    client.force_login(user)

    response = client.post(reverse("admin:gameplay_message_delete", args=[message.pk]), {"post": "yes"})

    assert response.status_code == 403
    assert Message.objects.filter(pk=message.pk).exists() is True
    assert not any(entry.level == SUCCESS for entry in get_messages(response.wsgi_request))
    message_content_type = ContentType.objects.get_for_model(Message)
    assert not LogEntry.objects.filter(
        user=user,
        content_type=message_content_type,
        object_id=str(message.pk),
        action_flag=DELETION,
    ).exists()


@pytest.mark.django_db
def test_message_admin_delete_selected_action_confirms_and_reports_exact_counts(django_user_model, client):
    user = django_user_model.objects.create_superuser(username="message_admin_action", password="pass123")
    manor = ensure_manor(user)
    protected = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="protected admin action message",
        attachments={"items": {"admin_item": 1}},
    )
    claimed = Message.objects.create(
        manor=manor,
        kind=Message.Kind.REWARD,
        title="claimed admin action message",
        attachments={"items": {"admin_item": 1}},
        is_claimed=True,
    )
    plain = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="plain admin action message")
    selected_ids = [protected.pk, claimed.pk, plain.pk]
    cache_key = CacheKeys.unread_count(manor.pk)
    cache.set(cache_key, 999, timeout=60)
    client.force_login(user)
    changelist_url = reverse("admin:gameplay_message_changelist")
    action_data = {
        "action": "delete_selected",
        helpers.ACTION_CHECKBOX_NAME: selected_ids,
        "index": 0,
    }

    try:
        confirmation = client.post(changelist_url, action_data)

        assert confirmation.status_code == 200
        confirmation_body = confirmation.content.decode("utf-8")
        assert 'name="post" value="yes"' in confirmation_body
        assert Message.objects.filter(pk__in=selected_ids).count() == 3

        response = client.post(
            changelist_url,
            {**action_data, "post": "yes"},
        )

        assert response.status_code == 302
        assert Message.objects.filter(pk=protected.pk).exists() is True
        assert Message.objects.filter(pk__in=[claimed.pk, plain.pk]).exists() is False
        assert cache.get(cache_key) is None
        feedback = [str(message) for message in get_messages(response.wsgi_request)]
        assert feedback == ["已删除 2 条消息，1 条未领取附件消息已保留"]
        message_content_type = ContentType.objects.get_for_model(Message)
        logged_ids = set(
            LogEntry.objects.filter(
                user=user,
                content_type=message_content_type,
                action_flag=DELETION,
            ).values_list("object_id", flat=True)
        )
        assert logged_ids == {str(claimed.pk), str(plain.pk)}
        assert str(protected.pk) not in logged_ids
    finally:
        cache.delete(cache_key)


@pytest.mark.django_db
def test_message_admin_batch_deletion_rolls_back_logs_when_delete_fails(django_user_model, monkeypatch):
    user = django_user_model.objects.create_superuser(username="message_admin_log_rollback", password="pass123")
    manor = ensure_manor(user)
    message = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="rollback deletion log")
    admin_obj = MessageAdmin(Message, AdminSite())
    request = _admin_request(user)
    monkeypatch.setattr(admin_obj, "message_user", lambda *_args, **_kwargs: None)
    audit_attempts = []
    original_log_deletions = admin_obj.log_deletions

    def _track_log_deletions(admin_request, queryset):
        audit_attempts.extend(queryset.values_list("pk", flat=True))
        return original_log_deletions(admin_request, queryset)

    monkeypatch.setattr(admin_obj, "log_deletions", _track_log_deletions)
    dispatch_uid = "test_message_admin_batch_deletion_rolls_back_logs"

    def _abort_delete(sender, instance, **kwargs):
        if instance.pk == message.pk:
            raise RuntimeError("forced message deletion failure")

    pre_delete.connect(_abort_delete, sender=Message, dispatch_uid=dispatch_uid, weak=False)
    try:
        with pytest.raises(RuntimeError, match="forced message deletion failure"):
            admin_obj.delete_queryset(request, Message.objects.filter(pk=message.pk))
    finally:
        pre_delete.disconnect(sender=Message, dispatch_uid=dispatch_uid)

    assert Message.objects.filter(pk=message.pk).exists() is True
    assert audit_attempts == [message.pk]
    message_content_type = ContentType.objects.get_for_model(Message)
    assert not LogEntry.objects.filter(
        user=user,
        content_type=message_content_type,
        object_id=str(message.pk),
        action_flag=DELETION,
    ).exists()


@pytest.mark.django_db
def test_message_admin_log_callback_failure_rolls_back_log_and_preserves_message(django_user_model, monkeypatch):
    user = django_user_model.objects.create_superuser(username="message_admin_callback_rollback", password="pass123")
    manor = ensure_manor(user)
    message = Message.objects.create(manor=manor, kind=Message.Kind.SYSTEM, title="callback rollback message")
    admin_obj = MessageAdmin(Message, AdminSite())
    request = _admin_request(user)
    original_log_deletions = admin_obj.log_deletions

    def _log_then_fail(admin_request, queryset):
        original_log_deletions(admin_request, queryset)
        raise RuntimeError("forced log_deletions callback failure")

    monkeypatch.setattr(admin_obj, "log_deletions", _log_then_fail)

    with pytest.raises(RuntimeError, match="forced log_deletions callback failure"):
        admin_obj.delete_queryset(request, Message.objects.filter(pk=message.pk))

    assert Message.objects.filter(pk=message.pk).exists() is True
    message_content_type = ContentType.objects.get_for_model(Message)
    assert not LogEntry.objects.filter(
        user=user,
        content_type=message_content_type,
        object_id=str(message.pk),
        action_flag=DELETION,
    ).exists()
