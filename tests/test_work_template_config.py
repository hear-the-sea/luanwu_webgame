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
    "wuguan": (14, 100, 0, 0, 0),
    "shanghang": (15, 0, 105, 0, 0),
    "guanfu": (16, 0, 105, 0, 60),
    "qianzhuang": (17, 0, 110, 55, 0),
    "biaoju": (18, 115, 0, 75, 0),
    "jingwumeng": (23, 140, 0, 0, 80),
    "shenfengyi": (28, 0, 0, 90, 100),
    "guozijian": (33, 0, 200, 0, 95),
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
