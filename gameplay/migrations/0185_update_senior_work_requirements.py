from __future__ import annotations

from django.db import migrations

SENIOR_WORK_REQUIREMENTS = {
    "biaoju": (45, 210, 0, 120, 0),
    "jingwumeng": (50, 230, 0, 0, 120),
    "shenfengyi": (45, 0, 0, 130, 180),
    "guozijian": (50, 0, 220, 0, 100),
}

PREVIOUS_SENIOR_WORK_REQUIREMENTS = {
    "biaoju": (18, 115, 0, 75, 0),
    "jingwumeng": (23, 140, 0, 0, 80),
    "shenfengyi": (28, 0, 0, 90, 100),
    "guozijian": (33, 0, 200, 0, 95),
}


def _apply_requirements(apps, requirements_by_key):
    WorkTemplate = apps.get_model("gameplay", "WorkTemplate")
    for key, requirements in requirements_by_key.items():
        WorkTemplate.objects.filter(key=key).update(
            required_level=requirements[0],
            required_force=requirements[1],
            required_intellect=requirements[2],
            required_defense=requirements[3],
            required_agility=requirements[4],
        )


def update_senior_work_requirements(apps, schema_editor):
    _apply_requirements(apps, SENIOR_WORK_REQUIREMENTS)


def restore_senior_work_requirements(apps, schema_editor):
    _apply_requirements(apps, PREVIOUS_SENIOR_WORK_REQUIREMENTS)


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0184_missiontemplate_display_order"),
    ]

    operations = [
        migrations.RunPython(update_senior_work_requirements, restore_senior_work_requirements),
    ]
