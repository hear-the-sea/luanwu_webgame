from django.db import migrations


def add_equipment_forge(apps, schema_editor):
    guild_model = apps.get_model("guilds", "Guild")
    technology = apps.get_model("guilds", "GuildTechnology")
    database = schema_editor.connection.alias
    for guild in guild_model.objects.using(database).all().iterator():
        tech, _created = technology.objects.using(database).get_or_create(
            guild_id=guild.pk,
            tech_key="forging_equipment_forge",
            defaults={"category": "forging", "level": 0, "max_level": 10},
        )
        # 保留已有等级和当前研究路线，已完成锻造系可在当前路线完成后补研新节点。
        completed = list(guild.completed_technology_routes or [])
        if tech.level < 10 and "forging" in completed:
            guild_model.objects.using(database).filter(pk=guild.pk).update(
                completed_technology_routes=[key for key in completed if key != "forging"]
            )


class Migration(migrations.Migration):
    dependencies = [("guilds", "0034_warehouse_exchange_prices")]

    operations = [migrations.RunPython(add_equipment_forge, migrations.RunPython.noop)]
