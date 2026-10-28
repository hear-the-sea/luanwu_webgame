"""景阳冈任务简介追加夜间出行提醒。"""

from django.db import migrations

WARNING = "（注意别晚上出门哦）"


def append_night_warning(apps, schema_editor):
    MissionTemplate = apps.get_model("gameplay", "MissionTemplate")
    database = schema_editor.connection.alias
    mission = MissionTemplate.objects.using(database).filter(key="jingyanggang").first()
    if mission is not None and WARNING not in mission.description:
        mission.description = mission.description.rstrip() + WARNING + "\n"
        mission.save(using=database, update_fields=["description"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0259_season_occupation_recovery")]
    operations = [migrations.RunPython(append_night_warning, migrations.RunPython.noop)]
