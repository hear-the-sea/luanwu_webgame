from django.db import migrations


def normalize_guild_technology_rows(apps, schema_editor):
    GuildTechnology = apps.get_model("guilds", "GuildTechnology")

    GuildTechnology.objects.filter(tech_key="troop_tactics").exclude(max_level=10).update(max_level=10)
    GuildTechnology.objects.filter(tech_key="military_study").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("guilds", "0016_add_attacker_troop_tech_snapshot"),
    ]

    operations = [
        migrations.RunPython(normalize_guild_technology_rows, migrations.RunPython.noop),
    ]
