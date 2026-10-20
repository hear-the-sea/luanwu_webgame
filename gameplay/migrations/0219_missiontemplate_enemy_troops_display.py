from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0218_personal_guild_mission_names")]

    operations = [
        migrations.AddField(
            model_name="missiontemplate",
            name="enemy_troops_display",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
