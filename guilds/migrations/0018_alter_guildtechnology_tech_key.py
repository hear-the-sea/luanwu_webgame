from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("guilds", "0017_normalize_guild_technology_rows"),
    ]

    operations = [
        migrations.AlterField(
            model_name="guildtechnology",
            name="tech_key",
            field=models.CharField(
                help_text="如: equipment_forge, troop_tactics",
                max_length=50,
                verbose_name="科技标识",
            ),
        ),
    ]
