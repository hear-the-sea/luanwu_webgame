import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gameplay", "0113_building_city_defense_hp"),
    ]

    operations = [
        migrations.AddField(
            model_name="manor",
            name="action_points",
            field=models.PositiveSmallIntegerField(default=1000, verbose_name="行动力"),
        ),
        migrations.AddField(
            model_name="manor",
            name="action_points_updated_at",
            field=models.DateTimeField(default=django.utils.timezone.now, verbose_name="行动力更新时间"),
        ),
    ]
