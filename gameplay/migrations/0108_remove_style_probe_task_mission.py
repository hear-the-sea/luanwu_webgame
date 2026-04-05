from django.db import migrations


def remove_style_probe_task_mission(apps, schema_editor):
    MissionTemplate = apps.get_model("gameplay", "MissionTemplate")
    MissionTemplate.objects.filter(key="style_probe_task").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0107_four_continents_regions"),
    ]

    operations = [
        migrations.RunPython(remove_style_probe_task_mission, migrations.RunPython.noop),
    ]
