from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0273_direct_mission_progression_tokens"),
    ]

    operations = [
        migrations.AddField(
            model_name="arenavirtualdemand",
            name="power_formula_version",
            field=models.PositiveSmallIntegerField(default=1, verbose_name="阵容战力公式版本"),
        ),
    ]
