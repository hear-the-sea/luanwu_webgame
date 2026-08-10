from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0166_arena_growth_control_snapshot"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="virtualplayergrowthcontrolsnapshot",
            name="bot_growth_control_date_region_band",
        ),
        migrations.AddConstraint(
            model_name="virtualplayergrowthcontrolsnapshot",
            constraint=models.UniqueConstraint(
                fields=("control_date", "region", "prestige_band", "snapshot_digest"),
                name="bot_growth_control_snapshot_uniq",
            ),
        ),
    ]
