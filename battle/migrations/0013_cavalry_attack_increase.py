from django.db import migrations


def increase_cavalry_attack(apps, schema_editor):
    troop = apps.get_model("battle", "TroopTemplate")
    troop.objects.using(schema_editor.connection.alias).filter(key="bloodthirsty_cavalry").update(base_attack=18)


class Migration(migrations.Migration):
    dependencies = [
        ("battle", "0012_cavalry_mobility_stats"),
    ]

    operations = [
        migrations.RunPython(increase_cavalry_attack, migrations.RunPython.noop),
    ]
