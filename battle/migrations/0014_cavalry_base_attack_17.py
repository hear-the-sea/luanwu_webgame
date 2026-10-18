from django.db import migrations


def set_cavalry_base_attack(apps, schema_editor):
    troop = apps.get_model("battle", "TroopTemplate")
    troop.objects.using(schema_editor.connection.alias).filter(key="bloodthirsty_cavalry").update(base_attack=17)


class Migration(migrations.Migration):
    dependencies = [
        ("battle", "0013_cavalry_attack_increase"),
    ]

    operations = [
        migrations.RunPython(set_cavalry_base_attack, migrations.RunPython.noop),
    ]
