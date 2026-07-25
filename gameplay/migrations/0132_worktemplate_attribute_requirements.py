from __future__ import annotations

from django.db import migrations, models

OFFICIAL_WORK_REQUIREMENTS = {
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


def apply_work_requirements(apps, schema_editor):
    WorkTemplate = apps.get_model("gameplay", "WorkTemplate")
    for key, requirements in OFFICIAL_WORK_REQUIREMENTS.items():
        WorkTemplate.objects.filter(key=key).update(
            required_level=requirements[0],
            required_force=requirements[1],
            required_intellect=requirements[2],
            required_defense=requirements[3],
            required_agility=requirements[4],
        )


def remove_work_requirements(apps, schema_editor):
    WorkTemplate = apps.get_model("gameplay", "WorkTemplate")
    WorkTemplate.objects.filter(key__in=OFFICIAL_WORK_REQUIREMENTS).update(
        required_force=0,
        required_intellect=0,
        required_defense=0,
        required_agility=0,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0131_split_personal_red_ruby_to_chunqiu_coin"),
    ]

    operations = [
        migrations.AddField(
            model_name="worktemplate",
            name="required_defense",
            field=models.PositiveIntegerField(default=0, verbose_name="防御要求"),
        ),
        migrations.AddField(
            model_name="worktemplate",
            name="required_agility",
            field=models.PositiveIntegerField(default=0, verbose_name="敏捷要求"),
        ),
        migrations.RunPython(apply_work_requirements, remove_work_requirements),
    ]
