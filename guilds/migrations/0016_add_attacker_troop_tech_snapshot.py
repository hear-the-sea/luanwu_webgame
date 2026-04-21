from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("guilds", "0015_merge_20260405_0000"),
    ]

    operations = [
        migrations.AddField(
            model_name="guildmissionrun",
            name="attacker_troop_tech_snapshot",
            field=models.JSONField(blank=True, default=dict, verbose_name="攻击方护院科技快照"),
        ),
        migrations.AddField(
            model_name="guildraidrun",
            name="attacker_troop_tech_snapshot",
            field=models.JSONField(blank=True, default=dict, verbose_name="攻击方护院科技快照"),
        ),
    ]
