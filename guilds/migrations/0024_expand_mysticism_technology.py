from django.db import migrations


def expand_mysticism_technology(apps, schema_editor):
    GuildTechnology = apps.get_model("guilds", "GuildTechnology")
    GuildTechnology.objects.filter(tech_key="mysticism").update(category="production", max_level=3)


def restore_mysticism_level_cap(apps, schema_editor):
    GuildTechnology = apps.get_model("guilds", "GuildTechnology")
    rows = GuildTechnology.objects.filter(tech_key="mysticism")
    rows.filter(level__gt=1).update(level=1)
    rows.update(category="production", max_level=1)


class Migration(migrations.Migration):
    dependencies = [
        ("guilds", "0023_add_mysticism_technology"),
    ]

    operations = [
        migrations.RunPython(expand_mysticism_technology, restore_mysticism_level_cap),
    ]
