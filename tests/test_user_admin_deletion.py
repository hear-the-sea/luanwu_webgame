import pytest
from django.contrib import admin
from django.contrib.admin import helpers
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory
from django.urls import reverse

from core.admin_cascade import account_message_cascade_scope
from gameplay.models import Manor, Message
from gameplay.models.items import _message_deletion_is_prevalidated
from guests.models import Guest, GuestTemplate, WorldUniqueGuest

pytestmark = pytest.mark.django_db


def protected_message(user, suffix=""):
    return Message.objects.create(
        manor=user.manor,
        kind=Message.Kind.REWARD,
        title="账号删除测试补偿" + suffix,
        attachments={"items": {"chunqiu_coin": 20}},
    )


@pytest.mark.parametrize(
    "attachments",
    [{"items": {"chunqiu_coin": 20}}, {"bound_items": {"chunqiu_coin": 20}}, {"resources": {"grain": 20}}],
)
def test_superuser_can_confirm_account_deletion_with_unclaimed_mail(client, django_user_model, attachments):
    operator = django_user_model.objects.create_superuser(username="account_delete_admin", password="testpass")
    target = django_user_model.objects.create_user(username="account_delete_target")
    other = django_user_model.objects.create_user(username="account_delete_other")
    message = protected_message(target)
    message.attachments = attachments
    message.save()
    other_message = protected_message(other)
    target_id, manor_id = target.pk, target.manor.pk
    url = reverse("admin:accounts_user_delete", args=[target_id])
    client.force_login(operator)
    response = client.get(url)
    assert response.status_code == 200
    assert not response.context["perms_lacking"]
    assert not response.context["protected"]
    assert django_user_model.objects.filter(pk=target_id).exists()
    response = client.post(url, {"post": "yes"})
    assert response.status_code == 302
    assert not django_user_model.objects.filter(pk=target_id).exists()
    assert not Manor.objects.filter(pk=manor_id).exists()
    assert not Message.objects.filter(pk=message.pk).exists()
    assert Message.objects.filter(pk=other_message.pk).exists()


def test_bulk_account_deletion_only_removes_selected_accounts_mail(client, django_user_model):
    operator = django_user_model.objects.create_superuser(username="bulk_account_delete_admin", password="testpass")
    users = [django_user_model.objects.create_user(username=f"bulk_account_delete_{i}") for i in range(3)]
    mail = [protected_message(user) for user in users]
    client.force_login(operator)
    url = reverse("admin:accounts_user_changelist")
    payload = {
        "action": "delete_selected",
        helpers.ACTION_CHECKBOX_NAME: [str(u.pk) for u in users[:2]],
        "index": "0",
        "select_across": "0",
    }
    response = client.post(url, payload)
    assert response.status_code == 200
    assert not response.context["perms_lacking"]
    response = client.post(url, {**payload, "post": "yes"})
    assert response.status_code == 302
    assert not Message.objects.filter(pk__in=[m.pk for m in mail[:2]]).exists()
    assert Message.objects.filter(pk=mail[2].pk).exists()
    assert django_user_model.objects.filter(pk=users[2].pk).exists()


def test_account_cascade_does_not_bypass_missing_message_permission(client, django_user_model):
    operator = django_user_model.objects.create_user(username="limited_account_delete_admin", is_staff=True)
    operator.user_permissions.add(*Permission.objects.filter(codename__in=["delete_user", "view_user", "delete_manor"]))
    target = django_user_model.objects.create_user(username="limited_account_delete_target")
    message = protected_message(target)
    client.force_login(operator)
    url = reverse("admin:accounts_user_delete", args=[target.pk])
    response = client.get(url)
    assert "消息" in response.context["perms_lacking"]
    assert client.post(url, {"post": "yes"}).status_code == 403
    assert Message.objects.filter(pk=message.pk).exists()
    request = RequestFactory().post(url)
    request.user = operator
    with pytest.raises(PermissionDenied):
        admin.site._registry[type(target)].delete_model(request, target)


def test_account_preview_scope_does_not_enable_standalone_mail_deletion(django_user_model):
    operator = django_user_model.objects.create_superuser(username="scope_account_delete_admin", password="testpass")
    target = django_user_model.objects.create_user(username="scope_account_delete_target")
    message = protected_message(target)
    request = RequestFactory().get("/admin/")
    request.user = operator
    admin.site._registry[type(target)].get_deleted_objects([target], request)
    assert not admin.site._registry[Message].has_delete_permission(request, message)
    with pytest.raises(ProtectedError):
        message.delete()
    assert Message.objects.filter(pk=message.pk).exists()


def test_account_permission_scope_is_limited_to_target_manors(django_user_model):
    operator = django_user_model.objects.create_superuser(username="bounded_account_delete_admin", password="testpass")
    target = django_user_model.objects.create_user(username="bounded_account_delete_target")
    other = django_user_model.objects.create_user(username="bounded_account_delete_other")
    target_message = protected_message(target)
    other_message = protected_message(other)
    request = RequestFactory().get("/admin/")
    request.user = operator
    message_admin = admin.site._registry[Message]
    with account_message_cascade_scope(request, [target.manor.pk]):
        assert message_admin.has_delete_permission(request, target_message)
        assert not message_admin.has_delete_permission(request, other_message)
        with pytest.raises(PermissionDenied):
            message_admin.delete_model(request, target_message)
    assert not message_admin.has_delete_permission(request, target_message)
    assert Message.objects.filter(pk=target_message.pk).exists()


def test_account_delete_failure_rolls_back_mail_and_resets_deletion_scope(django_user_model, monkeypatch):
    operator = django_user_model.objects.create_superuser(username="rollback_account_delete_admin", password="testpass")
    target = django_user_model.objects.create_user(username="rollback_account_delete_target")
    message = protected_message(target)
    request = RequestFactory().post("/admin/")
    request.user = operator

    def fail_after_mail_delete(*args, **kwargs):
        Message.objects.filter(pk=message.pk).delete()
        raise RuntimeError("模拟账号删除失败")

    monkeypatch.setattr(target, "delete", fail_after_mail_delete)
    with pytest.raises(RuntimeError, match="账号删除失败"):
        admin.site._registry[type(target)].delete_model(request, target)
    assert Message.objects.filter(pk=message.pk).exists()
    assert django_user_model.objects.filter(pk=target.pk).exists()
    assert not _message_deletion_is_prevalidated("default", message.pk)
    assert not admin.site._registry[Message].has_delete_permission(request, message)


def test_account_delete_rejects_other_database_instance(django_user_model):
    operator = django_user_model.objects.create_superuser(username="db_account_delete_admin", password="testpass")
    target = django_user_model.objects.create_user(username="db_account_delete_target")
    message = protected_message(target)
    target._state.db = "archive"
    request = RequestFactory().post("/admin/")
    request.user = operator
    with pytest.raises(PermissionDenied):
        admin.site._registry[type(target)].delete_model(request, target)
    assert Message.objects.filter(pk=message.pk).exists()


def test_bulk_account_deletion_keeps_accounts_created_after_selection(django_user_model, monkeypatch):
    operator = django_user_model.objects.create_superuser(username="selection_account_admin", password="testpass")
    target = django_user_model.objects.create_user(username="selection_target_first")
    protected_message(target)
    request = RequestFactory().post("/admin/")
    request.user = operator
    user_admin = admin.site._registry[type(target)]
    original = user_admin.get_deleted_objects
    late_accounts = []

    def create_after_preview(objects, request):
        result = original(objects, request)
        late = django_user_model.objects.create_user(username="selection_target_later")
        protected_message(late)
        late_accounts.append(late)
        return result

    monkeypatch.setattr(user_admin, "get_deleted_objects", create_after_preview)
    user_admin.delete_queryset(request, django_user_model.objects.filter(username__startswith="selection_target_"))
    assert not django_user_model.objects.filter(pk=target.pk).exists()
    assert django_user_model.objects.filter(pk=late_accounts[0].pk).exists()
    assert Message.objects.filter(manor=late_accounts[0].manor).exists()


def test_account_cascade_keeps_real_protected_relations(client, django_user_model):
    operator = django_user_model.objects.create_superuser(username="protected_account_admin", password="testpass")
    target = django_user_model.objects.create_user(username="protected_account_target")
    message = protected_message(target)
    template = GuestTemplate.objects.create(key="account_delete_unique", name="删除保护门客", is_world_unique=True)
    guest = Guest.objects.create(manor=target.manor, template=template)
    state = WorldUniqueGuest.objects.create(
        template=template, owner_manor=target.manor, owner_guest=guest, status=WorldUniqueGuest.Status.SERVING
    )
    client.force_login(operator)
    url = reverse("admin:accounts_user_delete", args=[target.pk])
    response = client.get(url)
    assert response.context["protected"]
    response = client.post(url, {"post": "yes"})
    assert response.status_code == 200
    assert django_user_model.objects.filter(pk=target.pk).exists()
    assert Message.objects.filter(pk=message.pk).exists()
    assert WorldUniqueGuest.objects.filter(pk=state.pk).exists()
