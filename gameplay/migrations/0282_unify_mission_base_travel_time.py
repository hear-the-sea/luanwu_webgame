"""统一任务模板默认及现有基础往返耗时为 40 分钟（2400 秒）。"""

from __future__ import annotations

from django.db import migrations, models

UNIFIED_MISSION_BASE_TRAVEL_TIME = 2400


def apply_unified_mission_base_travel_time(apps, schema_editor) -> None:
    MissionTemplate = apps.get_model("gameplay", "MissionTemplate")
    database = schema_editor.connection.alias
    MissionTemplate.objects.using(database).exclude(base_travel_time=UNIFIED_MISSION_BASE_TRAVEL_TIME).update(
        base_travel_time=UNIFIED_MISSION_BASE_TRAVEL_TIME
    )


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0281_baimenlou_disguise_card_rate"),
    ]

    operations = [
        migrations.AlterField(
            model_name="missiontemplate",
            name="base_travel_time",
            field=models.PositiveIntegerField(
                default=UNIFIED_MISSION_BASE_TRAVEL_TIME,
                help_text="往返基础耗时（秒）",
            ),
        ),
        migrations.RunPython(apply_unified_mission_base_travel_time, migrations.RunPython.noop, atomic=True),
    ]
