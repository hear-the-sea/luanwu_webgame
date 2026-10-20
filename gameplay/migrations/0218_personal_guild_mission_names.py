"""个人帮会任务改用直接描述经典情节的名称。"""

from django.db import migrations

MISSION_NAMES = {
    "guild_personal_zhenlong_qipu": "白蛇盗仙草",
    "guild_personal_shaolin_night_shadow": "八仙渡东海",
    "guild_personal_yanziwu_secret_letter": "沉香劈山救母",
}


def rename_personal_guild_missions(apps, schema_editor):
    MissionTemplate = apps.get_model("gameplay", "MissionTemplate")
    database = schema_editor.connection.alias
    for key, name in MISSION_NAMES.items():
        MissionTemplate.objects.using(database).filter(key=key).update(name=name)


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0217_personal_guild_folk_legends")]

    operations = [migrations.RunPython(rename_personal_guild_missions, migrations.RunPython.noop)]
