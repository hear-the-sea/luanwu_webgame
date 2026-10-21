"""将姓氏传说之李的任务名称统一为简体中文。"""

from django.db import migrations


def simplify_mission_name(apps, schema_editor):
    MissionTemplate = apps.get_model("gameplay", "MissionTemplate")
    MissionTemplate.objects.using(schema_editor.connection.alias).filter(key="surname_legend_li").update(
        name="姓氏传说之“李”"
    )


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0237_surname_li_mission")]
    operations = [migrations.RunPython(simplify_mission_name, migrations.RunPython.noop)]
