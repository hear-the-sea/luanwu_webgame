from types import SimpleNamespace

import pytest
from django.contrib import admin
from django.contrib.admin import helpers
from django.contrib.auth.models import Permission
from django.test import RequestFactory
from django.urls import reverse

from gameplay.models import BotProfile, GlobalMailCampaign, Message
from gameplay.services.manor.core import ensure_manor
from guests.models import GuestArchetype, GuestRarity, GuestTemplate, RecruitmentCandidate

ACTION_CASES = [
    (GlobalMailCampaign, "activate_selected_campaigns"),
    (GlobalMailCampaign, "deactivate_selected_campaigns"),
    (GlobalMailCampaign, "backfill_selected_campaigns"),
    (Message, "mark_as_read"),
    (Message, "mark_as_unread"),
    (BotProfile, "mark_selected_stale"),
    (GuestTemplate, "mark_recruitable"),
    (GuestTemplate, "mark_unrecruitable"),
    (RecruitmentCandidate, "mark_revealed"),
    (RecruitmentCandidate, "mark_hidden"),
]


@pytest.mark.parametrize(("model", "action"), ACTION_CASES, ids=[action for _, action in ACTION_CASES])
@pytest.mark.parametrize("permission", ["view", "change"])
def test_admin_mutation_actions_require_change_permission(model, action, permission):
    options = model._meta
    granted_permission = f"{options.app_label}.{permission}_{options.model_name}"
    request = RequestFactory().get("/admin/")
    request.user = SimpleNamespace(
        is_active=True,
        is_staff=True,
        is_superuser=False,
        has_perm=lambda permission_name: permission_name == granted_permission,
    )
    model_admin = admin.site._registry[model]

    assert model_admin.has_view_permission(request) is True
    assert (action in model_admin.get_actions(request)) is (permission == "change")


@pytest.mark.django_db
@pytest.mark.parametrize("permission", ["view", "change"])
@pytest.mark.parametrize(
    ("model", "action", "field", "initial_value"),
    [
        (GlobalMailCampaign, "activate_selected_campaigns", "is_active", False),
        (GlobalMailCampaign, "deactivate_selected_campaigns", "is_active", True),
        (Message, "mark_as_read", "is_read", False),
        (Message, "mark_as_unread", "is_read", True),
        (GuestTemplate, "mark_recruitable", "recruitable", False),
        (GuestTemplate, "mark_unrecruitable", "recruitable", True),
    ],
    ids=[
        "activate_campaign",
        "deactivate_campaign",
        "read_message",
        "unread_message",
        "recruitable_template",
        "unrecruitable_template",
    ],
)
def test_admin_action_post_respects_change_permission(
    django_user_model, client, model, action, field, initial_value, permission
):
    options = model._meta
    operator = django_user_model.objects.create_user(username="action_operator", is_staff=True)
    operator.user_permissions.add(
        Permission.objects.get(
            content_type__app_label=options.app_label,
            content_type__model=options.model_name,
            codename=f"{permission}_{options.model_name}",
        )
    )
    if model is GlobalMailCampaign:
        obj = model.objects.create(key="action_permission_campaign", title="权限测试活动", is_active=initial_value)
    elif model is Message:
        obj = model.objects.create(
            manor=ensure_manor(operator),
            kind=Message.Kind.SYSTEM,
            title="权限测试消息",
            is_read=initial_value,
        )
    else:
        obj = model.objects.create(
            key="action_permission_guest",
            name="权限测试门客",
            archetype=GuestArchetype.choices[0][0],
            rarity=GuestRarity.choices[0][0],
            recruitable=initial_value,
        )
    client.force_login(operator)

    response = client.post(
        reverse(f"admin:{options.app_label}_{options.model_name}_changelist"),
        {"action": action, helpers.ACTION_CHECKBOX_NAME: str(obj.pk), "index": "0", "select_across": "0"},
    )

    assert response.status_code == (302 if permission == "change" else 200)
    obj.refresh_from_db()
    assert getattr(obj, field) is (not initial_value if permission == "change" else initial_value)
