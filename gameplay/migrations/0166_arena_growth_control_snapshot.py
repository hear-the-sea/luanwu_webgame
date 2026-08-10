from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0165_bot_maintenance_recovery_success"),
    ]

    operations = [
        migrations.AddField(
            model_name="arenavirtualreservemember",
            name="growth_control_snapshot_digest",
            field=models.CharField(
                blank=True,
                default="",
                max_length=64,
                verbose_name="成长控制快照摘要",
            ),
        ),
    ]
