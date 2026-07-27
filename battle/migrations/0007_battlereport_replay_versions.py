from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("battle", "0006_battlereport_city_defenses"),
    ]

    operations = [
        migrations.AddField(
            model_name="battlereport",
            name="rng_version",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="battlereport",
            name="battle_engine_version",
            field=models.CharField(default="legacy", max_length=16),
        ),
    ]
