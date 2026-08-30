from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("battle", "0007_battlereport_replay_versions"),
    ]

    operations = [
        migrations.AddField(
            model_name="battlereport",
            name="attacker_equipment_bonuses",
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name="battlereport",
            name="defender_equipment_bonuses",
            field=models.JSONField(default=list),
        ),
    ]
