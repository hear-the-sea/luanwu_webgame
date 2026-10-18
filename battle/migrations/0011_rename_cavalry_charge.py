from django.db import migrations
from django.db.models import Value
from django.db.models.functions import Replace


def rename_cavalry_charge(apps, schema_editor):
    troop = apps.get_model("battle", "TroopTemplate")
    troop.objects.using(schema_editor.connection.alias).filter(key="bloodthirsty_cavalry").update(
        description=Replace("description", Value("浴血冲阵"), Value("铁骑冲阵")),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("battle", "0010_cavalry_preemptive_attack"),
    ]

    operations = [
        migrations.RunPython(rename_cavalry_charge, migrations.RunPython.noop),
    ]
