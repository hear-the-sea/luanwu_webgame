"""战龙在野四关每天各允许使用五张任务卡。"""

from django.db import migrations

MISSION_UPDATES = {
    "zhanlong_yanshan_shizi": {"mission_card_daily_limit": 5},
    "zhanlong_changban_tuwei": {"mission_card_daily_limit": 5},
    "zhanlong_changban_duanqiao": {"mission_card_daily_limit": 5},
    "zhanlong_wuhu_shizhen": {"mission_card_daily_limit": 5},
}


def enable_zhanlong_mission_cards(apps, schema_editor):
    MissionTemplate = apps.get_model("gameplay", "MissionTemplate")
    database = schema_editor.connection.alias
    for key, updates in MISSION_UPDATES.items():
        MissionTemplate.objects.using(database).filter(key=key).update(**updates)


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0230_chiyou_armor_luck")]
    operations = [migrations.RunPython(enable_zhanlong_mission_cards, migrations.RunPython.noop, atomic=True)]
