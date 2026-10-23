from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.management import call_command

from gameplay.admin.buildings import WorkTemplateAdmin
from gameplay.models import WorkTemplate

EXPECTED_WORK_REQUIREMENTS = {
    "jiulou": (3, 0, 0, 0, 30),
    "yiguan": (5, 0, 45, 0, 0),
    "yizhan": (7, 0, 0, 0, 42),
    "shuyuan": (9, 0, 60, 0, 0),
    "chaguan": (11, 0, 70, 0, 0),
    "matou": (13, 80, 0, 0, 0),
    "wuguan": (20, 120, 0, 0, 0),
    "shanghang": (25, 0, 125, 0, 0),
    "guanfu": (20, 0, 110, 0, 70),
    "qianzhuang": (25, 0, 130, 100, 0),
    "biaoju": (45, 210, 0, 120, 0),
    "jingwumeng": (50, 230, 0, 0, 120),
    "shenfengyi": (45, 0, 0, 130, 180),
    "guozijian": (50, 0, 220, 0, 100),
}


@pytest.mark.django_db
def test_seed_work_templates_uses_official_attribute_requirements():
    WorkTemplate.objects.all().delete()

    call_command("seed_work_templates", verbosity=0)

    actual = {
        work.key: (
            work.required_level,
            work.required_force,
            work.required_intellect,
            work.required_defense,
            work.required_agility,
        )
        for work in WorkTemplate.objects.all()
    }
    assert actual == EXPECTED_WORK_REQUIREMENTS


@pytest.mark.django_db
def test_seed_work_template_descriptions_explain_relevant_duties():
    WorkTemplate.objects.all().delete()
    call_command("seed_work_templates", verbosity=0)

    descriptions = dict(WorkTemplate.objects.values_list("key", "description"))
    assert "跑堂" in descriptions["jiulou"]
    assert "辨药" in descriptions["yiguan"]
    assert "押库" in descriptions["qianzhuang"]
    assert "急递" in descriptions["shenfengyi"]
    assert "校勘" in descriptions["guozijian"]


@pytest.mark.django_db
def test_reload_work_templates_preserves_existing_assignment_and_custom_templates(manor_with_user):
    from django.utils import timezone

    from gameplay.models import WorkAssignment
    from guests.models import Guest, GuestTemplate

    manor, _ = manor_with_user
    work, _ = WorkTemplate.objects.update_or_create(key="jiulou", defaults={"name": "旧酒楼", "required_level": 99})
    custom = WorkTemplate.objects.create(key="custom_release_work", name="自定义工作", required_level=88)
    guest = Guest.objects.create(
        manor=manor, template=GuestTemplate.objects.create(key="work_reload_guest", name="测试门客")
    )
    assignment = WorkAssignment.objects.create(
        manor=manor, guest=guest, work_template=work, complete_at=timezone.now() + timezone.timedelta(hours=1)
    )

    call_command("seed_work_templates", update_existing=True, verbosity=0)
    call_command("seed_work_templates", update_existing=True, verbosity=0)

    work.refresh_from_db()
    assignment.refresh_from_db()
    custom.refresh_from_db()
    assert work.name == "酒楼" and work.required_level == 3
    assert assignment.work_template_id == work.pk
    assert assignment.status == WorkAssignment.Status.WORKING
    assert custom.name == "自定义工作" and custom.required_level == 88
    assert WorkTemplate.objects.filter(key__in=EXPECTED_WORK_REQUIREMENTS).count() == 14


def test_work_template_admin_lists_all_attribute_requirements():
    work_admin = WorkTemplateAdmin(WorkTemplate, AdminSite())

    assert work_admin.list_display == (
        "name",
        "tier",
        "required_level",
        "required_force",
        "required_intellect",
        "required_defense",
        "required_agility",
        "reward_silver",
        "work_duration",
        "display_order",
    )
