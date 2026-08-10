from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0164_virtual_player_cycles_recovery_and_control"),
    ]

    operations = [
        migrations.AddField(
            model_name="botmaintenancerecovery",
            name="last_success_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="最近成功时间"),
        ),
    ]
