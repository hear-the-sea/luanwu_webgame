from django.db import migrations
from django.db.models import Value
from django.db.models.functions import Replace


def rename_guild_assets_copy(apps, schema_editor):
    troop = apps.get_model("battle", "TroopTemplate")
    troop.objects.using(schema_editor.connection.alias).filter(key="bloodthirsty_cavalry").update(
        description=Replace("description", Value("帮会仓库"), Value("帮会资产"))
    )


class Migration(migrations.Migration):
    dependencies = [("guilds", "0035_forging_equipment_forge")]

    operations = [migrations.RunPython(rename_guild_assets_copy, migrations.RunPython.noop)]
