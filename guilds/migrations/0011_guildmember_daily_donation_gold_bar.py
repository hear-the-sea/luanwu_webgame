from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("guilds", "0010_expand_guild_battle_lineup_slot_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="guildmember",
            name="daily_donation_gold_bar",
            field=models.PositiveIntegerField(default=0, verbose_name="今日捐赠金条"),
        ),
    ]
