from datetime import timedelta

import pytest
from django.contrib import admin
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from core.admin_site import GameAdminSite
from gameplay.admin.arena_ring import TenureStatusFilter
from gameplay.models import ArenaRing, ArenaRingDaily, ArenaRingMatch, ArenaRingTenure, Manor


@pytest.mark.django_db
def test_operations_home_only_exposes_permitted_models(client, django_user_model):
    user = django_user_model.objects.create_user(username="limited_operator", is_staff=True)
    user.user_permissions.add(Permission.objects.get(content_type__app_label="gameplay", codename="view_manor"))
    client.force_login(user)
    response = client.get(reverse("admin:index"))
    assert response.status_code == 200
    assert isinstance(admin.site._wrapped, GameAdminSite)
    models = [model for group in response.context["operation_groups"] for model in group["models"]]
    assert [model["object_name"] for model in models] == ["Manor"]
    assert "查找管理功能" in response.content.decode()
    assert "擂台每日奖励统计" not in response.content.decode()
    assert "操作日志" not in response.content.decode()


@pytest.mark.django_db
def test_operations_home_keeps_every_available_model_once(client, django_user_model):
    user = django_user_model.objects.create_superuser(username="operations_admin", password="testpass")
    client.force_login(user)
    response = client.get(reverse("admin:index"))
    grouped = [model["admin_url"] for group in response.context["operation_groups"] for model in group["models"]]
    original = [model["admin_url"] for app in response.context["app_list"] for model in app["models"]]
    assert sorted(grouped) == sorted(original)
    assert len(set(grouped)) == len(grouped)
    assert "配置与技术记录" in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("model", [ArenaRing, ArenaRingTenure, ArenaRingMatch, ArenaRingDaily])
def test_ring_admin_is_read_only_even_for_superuser(client, django_user_model, model):
    user = django_user_model.objects.create_superuser(username="ring_reader", password="testpass")
    client.force_login(user)
    request = RequestFactory().get("/admin/")
    request.user = user
    model_admin = admin.site._registry[model]
    assert model_admin.has_view_permission(request)
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request)
    assert not model_admin.has_delete_permission(request)
    assert not model_admin.get_actions(request)
    assert client.get(reverse(f"admin:gameplay_{model._meta.model_name}_changelist")).status_code == 200
    assert client.post(reverse(f"admin:gameplay_{model._meta.model_name}_add"), {}).status_code == 403


@pytest.mark.django_db
def test_ring_expiration_filter_and_detail_page(client, django_user_model):
    user = django_user_model.objects.create_superuser(username="tenure_operator", password="testpass")
    ring = ArenaRing.objects.create(key="test_ring", name="测试擂台")
    now = timezone.now()
    tenure = ArenaRingTenure.objects.create(
        ring=ring,
        manor=user.manor,
        started_at=now - timedelta(hours=2),
        ends_at=now - timedelta(minutes=1),
        settled_at=now,
        lineup_updated_at=now,
    )
    request = RequestFactory().get("/admin/")
    request.user = user
    model_admin = admin.site._registry[ArenaRingTenure]
    overdue = TenureStatusFilter(request, {"tenure_status": ["expired"]}, ArenaRingTenure, model_admin)
    assert list(overdue.queryset(request, ArenaRingTenure.objects.all())) == [tenure]
    client.force_login(user)
    detail_url = reverse("admin:gameplay_arenaringtenure_change", args=[tenure.pk])
    response = client.get(detail_url)
    assert response.status_code == 200
    assert "到期待结算" in response.content.decode()
    assert client.post(detail_url, {"defenses": "999"}).status_code == 403
    tenure.refresh_from_db()
    assert tenure.defenses == 0
    tenure.ended_at = now
    tenure.save(update_fields=["ended_at"])
    assert not overdue.queryset(request, ArenaRingTenure.objects.all()).exists()


@pytest.mark.django_db
def test_operation_log_does_not_leak_to_staff_with_log_permission(client, django_user_model):
    user = django_user_model.objects.create_user(username="log_staff", is_staff=True)
    user.user_permissions.add(Permission.objects.get(content_type__app_label="admin", codename="view_logentry"))
    client.force_login(user)
    assert client.get(reverse("admin:admin_logentry_changelist")).status_code == 403


@pytest.mark.django_db
def test_superuser_can_search_audit_log_but_cannot_modify(client, django_user_model):
    user = django_user_model.objects.create_superuser(username="audit_operator", password="testpass")
    entry = LogEntry.objects.create(
        user=user,
        content_type=ContentType.objects.get_for_model(Manor),
        object_id=str(user.manor.pk),
        object_repr="测试庄园",
        action_flag=CHANGE,
        change_message="调整庄园记录",
    )
    client.force_login(user)
    response = client.get(reverse("admin:admin_logentry_changelist"), {"q": "调整庄园"})
    assert response.status_code == 200
    assert "调整庄园记录" in response.content.decode()
    assert client.post(reverse("admin:admin_logentry_change", args=[entry.pk]), {}).status_code == 403
    assert client.get(reverse("admin:admin_logentry_delete", args=[entry.pk])).status_code == 403


@pytest.mark.django_db
def test_manor_related_records_are_read_only_and_not_duplicate_menus(django_user_model):
    request = RequestFactory().get("/admin/")
    request.user = django_user_model.objects.create_superuser(username="manor_reader", password="testpass")
    inlines = admin.site._registry[Manor].get_inline_instances(request, request.user.manor)
    assert len(inlines) == 3
    for inline in inlines:
        assert inline.model not in admin.site._registry
        assert not inline.has_add_permission(request)
        assert not inline.has_change_permission(request)
        assert not inline.has_delete_permission(request)


@pytest.mark.django_db
def test_manor_view_permission_does_not_grant_access_to_related_records(client, django_user_model):
    user = django_user_model.objects.create_user(username="limited_manor_reader", is_staff=True)
    user.user_permissions.add(Permission.objects.get(content_type__app_label="gameplay", codename="view_manor"))
    request = RequestFactory().get("/admin/")
    request.user = user
    assert admin.site._registry[Manor].get_inline_instances(request, user.manor) == []
    client.force_login(user)
    response = client.get(reverse("admin:gameplay_manor_change", args=[user.manor.pk]))
    assert response.status_code == 200
    assert not response.context["inline_admin_formsets"]


@pytest.mark.django_db
def test_audit_log_detail_translates_structured_change_messages(client, django_user_model):
    import json

    user = django_user_model.objects.create_superuser(username="audit_detail_admin", password="testpass")
    entry = LogEntry.objects.create(
        user=user,
        object_repr="测试对象",
        action_flag=CHANGE,
        change_message=json.dumps([{"changed": {"fields": ["名称"]}}]),
    )
    client.force_login(user)
    response = client.get(reverse("admin:admin_logentry_change", args=[entry.pk]))
    assert response.status_code == 200
    assert "对象编号" in response.content.decode()
    assert "&quot;changed&quot;" not in response.content.decode()
    assert "名称" in response.content.decode()
