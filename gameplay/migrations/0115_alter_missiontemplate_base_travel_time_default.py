from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gameplay", "0114_manor_action_points"),
    ]

    operations = [
        migrations.AlterField(
            model_name="missiontemplate",
            name="base_travel_time",
            field=models.PositiveIntegerField(default=1800, help_text="往返基础耗时（秒）"),
        ),
    ]
