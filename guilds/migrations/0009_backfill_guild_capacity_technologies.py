from django.db import migrations

CAPACITY_TECH_ROWS = (
    ("guild_lineup_capacity", "combat", 20),
    ("guild_dispatch_capacity", "combat", 20),
)


def backfill_guild_capacity_technologies(apps, schema_editor):
    Guild = apps.get_model("guilds", "Guild")
    GuildTechnology = apps.get_model("guilds", "GuildTechnology")

    existing_pairs = set(
        GuildTechnology.objects.filter(tech_key__in=[row[0] for row in CAPACITY_TECH_ROWS]).values_list(
            "guild_id", "tech_key"
        )
    )

    to_create = []
    for guild_id in Guild.objects.values_list("id", flat=True):
        for tech_key, category, max_level in CAPACITY_TECH_ROWS:
            if (guild_id, tech_key) in existing_pairs:
                continue
            to_create.append(
                GuildTechnology(
                    guild_id=guild_id,
                    tech_key=tech_key,
                    category=category,
                    level=0,
                    max_level=max_level,
                )
            )

    if to_create:
        GuildTechnology.objects.bulk_create(to_create)


class Migration(migrations.Migration):
    dependencies = [
        ("guilds", "0008_guild_mission_and_troop_models"),
    ]

    operations = [
        migrations.RunPython(backfill_guild_capacity_technologies, migrations.RunPython.noop),
    ]
