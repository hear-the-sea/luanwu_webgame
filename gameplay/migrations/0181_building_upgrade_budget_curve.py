from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gameplay", "0180_zero_v2_virtual_player_retainers"),
    ]

    operations = [
        migrations.AddField(
            model_name="buildingtype",
            name="upgrade_time_budget",
            field=models.PositiveBigIntegerField(default=0, verbose_name="全等级时长预算(秒)"),
        ),
        migrations.AddField(
            model_name="buildingtype",
            name="time_curve",
            field=models.FloatField(default=1.0, verbose_name="时长逐级增长系数"),
        ),
        migrations.AddField(
            model_name="buildingtype",
            name="upgrade_cost_budget",
            field=models.JSONField(default=dict, verbose_name="全等级成本预算"),
        ),
        migrations.AddField(
            model_name="buildingtype",
            name="cost_curve",
            field=models.FloatField(default=1.0, verbose_name="成本逐级增长系数"),
        ),
    ]
