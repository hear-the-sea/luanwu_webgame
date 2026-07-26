from django.db import migrations


def add_mysticism_technology(apps, schema_editor):
    Guild = apps.get_model("guilds", "Guild")
    GuildTechnology = apps.get_model("guilds", "GuildTechnology")

    existing_rows = GuildTechnology.objects.filter(tech_key="mysticism")
    existing_guild_ids = set(existing_rows.values_list("guild_id", flat=True))
    existing_rows.update(category="production", max_level=1)
    GuildTechnology.objects.filter(tech_key="mysticism", level__gt=1).update(level=1)
    rows = [
        GuildTechnology(
            guild_id=guild_id,
            tech_key="mysticism",
            category="production",
            level=0,
            max_level=1,
        )
        for guild_id in Guild.objects.values_list("id", flat=True)
        if guild_id not in existing_guild_ids
    ]
    if rows:
        GuildTechnology.objects.bulk_create(rows)


def remove_mysticism_technology(apps, schema_editor):
    GuildTechnology = apps.get_model("guilds", "GuildTechnology")
    GuildTechnology.objects.filter(tech_key="mysticism").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("guilds", "0022_rename_blueprint_reward_claim_index"),
    ]

    operations = [
        migrations.RunPython(add_mysticism_technology, remove_mysticism_technology),
    ]
