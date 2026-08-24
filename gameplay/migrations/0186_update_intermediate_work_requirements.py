from __future__ import annotations

from django.db import migrations

INTERMEDIATE_WORK_REQUIREMENTS = {
    "wuguan": (20, 120, 0, 0, 0),
    "shanghang": (25, 0, 125, 0, 0),
    "guanfu": (20, 0, 110, 0, 70),
    "qianzhuang": (25, 0, 130, 100, 0),
}

PREVIOUS_INTERMEDIATE_WORK_REQUIREMENTS = {
    "wuguan": (14, 100, 0, 0, 0),
    "shanghang": (15, 0, 105, 0, 0),
    "guanfu": (16, 0, 105, 0, 60),
    "qianzhuang": (17, 0, 110, 55, 0),
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


def update_intermediate_work_requirements(apps, schema_editor):
    _apply_requirements(apps, INTERMEDIATE_WORK_REQUIREMENTS)


def restore_intermediate_work_requirements(apps, schema_editor):
    _apply_requirements(apps, PREVIOUS_INTERMEDIATE_WORK_REQUIREMENTS)


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0185_update_senior_work_requirements"),
    ]

    operations = [
        migrations.RunPython(update_intermediate_work_requirements, restore_intermediate_work_requirements),
    ]
