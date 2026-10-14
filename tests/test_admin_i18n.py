import re

import pytest
from django.apps import apps
from django.contrib import admin
from django.contrib.admin.utils import get_fields_from_path, label_for_field
from django.test import RequestFactory

from gameplay.admin.bots import BotRuntimeRoutingStateAdmin, BotSafetyDecisionAuditAdmin
from gameplay.models import BotRuntimeRoutingState, BotSafetyDecisionAudit

TARGET_APP_LABELS = {"accounts", "admin", "battle", "gameplay", "guests", "guilds", "trade"}
ASCII_RE = re.compile(r"[A-Za-z]")


def test_admin_app_verbose_names_are_chinese():
    issues = []
    for app_label in sorted(TARGET_APP_LABELS):
        app_config = apps.get_app_config(app_label)
        if ASCII_RE.search(str(app_config.verbose_name)):
            issues.append(f"{app_label}: {app_config.verbose_name}")
    assert not issues, "后台应用分组仍含英文:\n" + "\n".join(issues)


def test_admin_list_display_labels_are_chinese():
    issues = []
    for model, model_admin in admin.site._registry.items():
        if model._meta.app_label not in TARGET_APP_LABELS:
            continue
        for field_name in getattr(model_admin, "list_display", ()):
            label = label_for_field(field_name, model, model_admin)
            if isinstance(label, str) and ASCII_RE.search(label):
                issues.append(f"{model._meta.label}.{field_name} -> {label}")
    assert not issues, "后台列表列名仍含英文:\n" + "\n".join(sorted(issues))


def _registered_admin_models():
    for model, model_admin in admin.site._registry.items():
        if model._meta.app_label in TARGET_APP_LABELS:
            yield model, model_admin


def test_admin_detail_and_inline_field_labels_are_chinese():
    issues = []
    for model, model_admin in _registered_admin_models():
        for detail_model in (model, *(inline.model for inline in model_admin.inlines)):
            for field in (*detail_model._meta.fields, *detail_model._meta.many_to_many):
                if ASCII_RE.search(str(field.verbose_name)):
                    issues.append(f"{detail_model._meta.label}.{field.name} -> {field.verbose_name}")
    assert not issues, "后台详情或内联字段仍含英文:\n" + "\n".join(sorted(issues))


def test_admin_model_and_inline_names_are_chinese():
    issues = []
    for model, model_admin in admin.site._registry.items():
        for detail_model in (model, *(inline.model for inline in model_admin.inlines)):
            for name in (detail_model._meta.verbose_name, detail_model._meta.verbose_name_plural):
                if ASCII_RE.search(str(name)):
                    issues.append(f"{detail_model._meta.label} -> {name}")
    assert not issues, "后台模型名称仍含英文:\n" + "\n".join(sorted(issues))
    assert apps.get_model("admin", "LogEntry")._meta.verbose_name == "后台操作日志"


def test_admin_forms_and_readonly_labels_are_chinese():
    request = RequestFactory().get("/admin/")
    request.user = apps.get_model("accounts", "User")(is_superuser=True, is_staff=True, is_active=True)
    issues = []
    for model, model_admin in _registered_admin_models():
        form = model_admin.get_form(request)
        for field_name, field in form.base_fields.items():
            if ASCII_RE.search(str(field.label)):
                issues.append(f"{model._meta.label}.{field_name} -> {field.label}")
        for field_name in model_admin.get_readonly_fields(request):
            label = label_for_field(field_name, model, model_admin)
            if ASCII_RE.search(str(label)):
                issues.append(f"{model._meta.label}.{field_name} -> {label}")
    assert not issues, "后台表单或只读标签仍含英文:\n" + "\n".join(sorted(issues))


def test_admin_filter_titles_are_chinese():
    issues = []
    for model, model_admin in _registered_admin_models():
        for list_filter in model_admin.list_filter:
            filter_field = list_filter[0] if isinstance(list_filter, tuple) else list_filter
            if isinstance(filter_field, str):
                title = get_fields_from_path(model, filter_field)[-1].verbose_name
            else:
                title = filter_field.title
            if ASCII_RE.search(str(title)):
                issues.append(f"{model._meta.label}: {title}")
    assert not issues, "后台筛选标题仍含英文:\n" + "\n".join(sorted(issues))


@pytest.mark.parametrize(
    "field_name,method_name,value",
    [
        ("policy_mode", "policy_mode_label", "legacy"),
        ("policy_mode", "policy_mode_label", "shadow"),
        ("policy_mode", "policy_mode_label", "scoped"),
        ("operation", "operation_label", "monitor"),
        ("operation", "operation_label", "recovery"),
        ("resulting_mode", "resulting_mode_label", "legacy_before_gate"),
        ("resulting_mode", "resulting_mode_label", "v2_cutover"),
        ("resulting_mode", "resulting_mode_label", "v2_active"),
        ("resulting_mode", "resulting_mode_label", "v2_paused"),
    ],
)
def test_safety_audit_state_values_are_chinese(field_name, method_name, value):
    model_admin = BotSafetyDecisionAuditAdmin(BotSafetyDecisionAudit, admin.site)
    obj = BotSafetyDecisionAudit(**{field_name: value})
    assert not ASCII_RE.search(getattr(model_admin, method_name)(obj))


@pytest.mark.parametrize("mode", list(BotRuntimeRoutingState.MaintenanceMode.values))
def test_runtime_routing_mode_values_are_chinese(mode):
    model_admin = BotRuntimeRoutingStateAdmin(BotRuntimeRoutingState, admin.site)
    obj = BotRuntimeRoutingState(maintenance_mode=mode)
    assert not ASCII_RE.search(model_admin.maintenance_mode_label(obj))


@pytest.mark.parametrize("mode", ["", "v2_cutover", "v2_active"])
def test_runtime_routing_previous_mode_values_are_chinese(mode):
    model_admin = BotRuntimeRoutingStateAdmin(BotRuntimeRoutingState, admin.site)
    obj = BotRuntimeRoutingState(paused_from_maintenance_mode=mode)
    assert not ASCII_RE.search(model_admin.paused_from_mode_label(obj))


@pytest.mark.django_db
def test_safety_filters_and_readonly_detail_display_chinese(client, django_user_model):
    from django.urls import reverse

    user = django_user_model.objects.create_superuser(username="safety_translation_admin", password="testpass")
    client.force_login(user)
    audit = BotSafetyDecisionAudit.objects.create(
        window_id="test_window",
        policy_version="test_version",
        policy_mode="scoped",
        operation="recovery",
        previous_mode="v2_paused",
        resulting_mode="v2_active",
        previous_revision=1,
        resulting_revision=2,
    )
    response = client.get(reverse("admin:gameplay_botsafetydecisionaudit_changelist"))
    assert response.status_code == 200
    changelist = response.context["cl"]
    for spec in changelist.filter_specs[:2]:
        assert all(not ASCII_RE.search(str(choice["display"])) for choice in spec.choices(changelist))
    response = client.get(
        reverse("admin:gameplay_botsafetydecisionaudit_changelist"), {"policy_mode": "scoped", "operation": "recovery"}
    )
    assert list(response.context["cl"].queryset) == [audit]
    response = client.get(reverse("admin:gameplay_botsafetydecisionaudit_change", args=[audit.pk]))
    assert response.status_code == 200
    html = response.content.decode()
    for label in ("按作用域评估", "恢复运行", "第二版已暂停", "第二版已启用"):
        assert f">{label}<" in html
    for code in ("scoped", "recovery", "v2_paused", "v2_active"):
        assert f">{code}<" not in html
