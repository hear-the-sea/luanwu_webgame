from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0178_bot_profile_guest_count_target"),
    ]

    operations = [
        migrations.AddField(
            model_name="botprofile",
            name="recruitment_schedule_snapshot",
            field=models.JSONField(default=dict, blank=True, verbose_name="每日招募配额快照"),
        ),
    ]
