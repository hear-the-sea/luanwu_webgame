from django.db import migrations

PRODUCTION_MAX_LEVEL_TECH_KEYS = ("equipment_forge", "experience_refine", "resource_supply")


def add_guard_armory_technology(apps, schema_editor):
    Guild = apps.get_model("guilds", "Guild")
    GuildTechnology = apps.get_model("guilds", "GuildTechnology")

    existing_guard_guild_ids = set(
        GuildTechnology.objects.filter(tech_key="guard_armory").values_list("guild_id", flat=True)
    )
    rows = [
        GuildTechnology(
            guild_id=guild_id,
            tech_key="guard_armory",
            category="production",
            level=0,
            max_level=10,
        )
        for guild_id in Guild.objects.values_list("id", flat=True)
        if guild_id not in existing_guard_guild_ids
    ]
    if rows:
        GuildTechnology.objects.bulk_create(rows)

    GuildTechnology.objects.filter(tech_key__in=PRODUCTION_MAX_LEVEL_TECH_KEYS, max_level__lt=10).update(max_level=10)


def remove_guard_armory_technology(apps, schema_editor):
    GuildTechnology = apps.get_model("guilds", "GuildTechnology")
    GuildTechnology.objects.filter(tech_key="guard_armory").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("guilds", "0018_alter_guildtechnology_tech_key"),
    ]

    operations = [
        migrations.RunPython(add_guard_armory_technology, remove_guard_armory_technology),
    ]
