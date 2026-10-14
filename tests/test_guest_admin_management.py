import json
import re
from datetime import timedelta

import pytest
from django.contrib import admin
from django.forms.models import model_to_dict
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from guests.admin import GuestSkillInline, RecruitmentDueFilter
from guests.models import Guest, GuestRecruitment, RecruitmentExtraAttempt, Skill, SkillBook


@pytest.mark.parametrize("model", [Skill, SkillBook, GuestRecruitment, RecruitmentExtraAttempt])
def test_guest_management_models_have_chinese_detail_fields(model):
    assert model in admin.site._registry
    for field in model._meta.fields:
        assert not re.search(r"[A-Za-z]", str(field.verbose_name)), field.name


@pytest.mark.django_db
@pytest.mark.parametrize("model", [GuestRecruitment, RecruitmentExtraAttempt])
def test_guest_audit_records_cannot_be_created_or_deleted(client, django_user_model, model):
    user = django_user_model.objects.create_superuser(username="guest_record_admin", password="pass123")
    client.force_login(user)
    request = RequestFactory().get("/admin/")
    request.user = user
    model_admin = admin.site._registry[model]
    assert model_admin.has_view_permission(request)
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request)
    assert not model_admin.has_delete_permission(request)
    assert not model_admin.get_actions(request)
    assert client.get(reverse(f"admin:guests_{model._meta.model_name}_add")).status_code == 403
    assert client.get(reverse(f"admin:guests_{model._meta.model_name}_changelist")).status_code == 200


@pytest.mark.django_db
def test_recruitment_due_filter_only_includes_pending_expired_records(
    django_user_model,
):
    now = timezone.now()
    records = []
    for index, (status, deadline) in enumerate(
        [
            (GuestRecruitment.Status.PENDING, now - timedelta(minutes=5)),
            (GuestRecruitment.Status.PENDING, now + timedelta(minutes=5)),
            (GuestRecruitment.Status.COMPLETED, now - timedelta(minutes=5)),
        ]
    ):
        user = django_user_model.objects.create_user(username=f"recruitment_queue_{index}")
        manor = user.manor
        records.append(GuestRecruitment.objects.create(manor=manor, complete_at=deadline, status=status))
    request = RequestFactory().get("/admin/guests/guestrecruitment/")
    model_admin = admin.site._registry[GuestRecruitment]
    overdue = RecruitmentDueFilter(request, {"recruitment_due": ["overdue"]}, GuestRecruitment, model_admin)
    waiting = RecruitmentDueFilter(request, {"recruitment_due": ["waiting"]}, GuestRecruitment, model_admin)
    assert list(overdue.queryset(request, GuestRecruitment.objects.all())) == [records[0]]
    assert list(waiting.queryset(request, GuestRecruitment.objects.all())) == [records[1]]


def test_guest_skills_are_visible_as_immutable_inline():
    guest_admin = admin.site._registry[Guest]
    assert GuestSkillInline in guest_admin.inlines
    inline = GuestSkillInline(Guest, admin.site)
    request = RequestFactory().get("/admin/")
    assert not inline.has_add_permission(request)
    assert not inline.has_change_permission(request)
    assert not inline.has_delete_permission(request)
    assert set(inline.fields) == set(inline.readonly_fields)
    assert "base_probability" in admin.site._registry[Skill].exclude


def _skill_admin_payload(skill):
    payload = model_to_dict(skill, exclude=("id", "base_probability"))
    for field in ("damage_formula", "passive_config"):
        payload[field] = json.dumps(payload[field])
    return payload


@pytest.mark.django_db
@pytest.mark.parametrize(
    "field,value",
    [
        ("damage_formula", [1]),
        ("damage_formula", {"ally": [1]}),
        ("damage_formula", {"enemy": {"defense": "错误数值"}}),
        ("damage_formula", {"base": "Infinity"}),
        ("damage_formula", {"base": float("nan")}),
        ("passive_config", [1]),
        ("passive_config", {"triggers": [1]}),
        (
            "passive_config",
            {"triggers": [{"timing": "action_before", "chance": 2, "effects": []}]},
        ),
        (
            "passive_config",
            {"triggers": [{"timing": "action_before", "effects": [{"type": []}]}]},
        ),
        (
            "passive_config",
            {
                "triggers": [
                    {
                        "timing": "round_start",
                        "effects": [{"type": "heal_ratio", "value": "错误数值"}],
                    }
                ]
            },
        ),
        (
            "passive_config",
            {
                "triggers": [
                    {
                        "timing": "round_start",
                        "conditions": {"hp_ratio_lt": []},
                        "effects": [],
                    }
                ]
            },
        ),
        ("status_probability", -0.1),
        ("status_probability", 1.1),
    ],
)
def test_skill_admin_rejects_invalid_runtime_config_without_writing(client, django_user_model, field, value):
    user = django_user_model.objects.create_superuser(username="skill_editor", password="pass123")
    client.force_login(user)
    skill = Skill.objects.create(key="admin_skill", name="测试技能")
    before = model_to_dict(skill)
    payload = _skill_admin_payload(skill)
    payload["name"] = "不应保存的名称"
    payload[field] = json.dumps(value) if field in {"damage_formula", "passive_config"} else value
    response = client.post(reverse("admin:guests_skill_change", args=[skill.pk]), payload)
    assert response.status_code == 200
    assert field in response.context["adminform"].form.errors
    skill.refresh_from_db()
    assert model_to_dict(skill) == before


@pytest.mark.django_db
def test_skill_admin_saves_valid_runtime_config(client, django_user_model):
    user = django_user_model.objects.create_superuser(username="valid_skill_editor", password="pass123")
    client.force_login(user)
    skill = Skill.objects.create(key="valid_admin_skill", name="测试技能")
    payload = _skill_admin_payload(skill)
    formula = {"base": 100, "ally": {"force": 1.5}, "enemy": {"defense": 0.2}}
    config = {
        "triggers": [
            {
                "timing": "round_start",
                "chance": 0.5,
                "effects": [{"type": "heal_ratio", "value": 0.2}],
            }
        ]
    }
    payload.update(
        name="有效技能",
        damage_formula=json.dumps(formula),
        passive_config=json.dumps(config),
    )
    response = client.post(reverse("admin:guests_skill_change", args=[skill.pk]), payload)
    assert response.status_code == 302
    skill.refresh_from_db()
    assert skill.name == "有效技能"
    assert skill.damage_formula == formula
    assert skill.passive_config == config


def test_skill_admin_accepts_existing_catalog_configs():
    from pathlib import Path

    import yaml
    from django.conf import settings

    from guests.admin_forms import SkillAdminForm

    for filename in ("guest_skills.yaml", "arena_coop_special_skills.yaml"):
        catalog = yaml.safe_load((Path(settings.BASE_DIR) / "data" / filename).read_text())
        for entry in catalog["skills"]:
            form = SkillAdminForm()
            form.cleaned_data = {
                "damage_formula": entry.get("damage_formula", {}),
                "passive_config": entry.get("passive_config", {}),
                "status_probability": entry.get("status_probability", 0),
            }
            assert form.clean_damage_formula() == form.cleaned_data["damage_formula"], entry["key"]
            assert form.clean_passive_config() == form.cleaned_data["passive_config"], entry["key"]
            form.clean_status_probability()


@pytest.mark.parametrize("count", [1.5, -1, {"guest_template": 1.5}, {"guest_template": -1}])
def test_skill_admin_rejects_fractional_or_negative_guest_count_conditions(count):
    from django import forms

    from guests.admin_forms import SkillAdminForm

    form = SkillAdminForm()
    form.cleaned_data = {
        "passive_config": {
            "triggers": [
                {
                    "timing": "round_start",
                    "conditions": {"ally_alive_template_count_gte": count},
                    "effects": [{"type": "heal_ratio", "value": 0.2}],
                }
            ]
        }
    }
    with pytest.raises(forms.ValidationError, match="非负整数"):
        form.clean_passive_config()
