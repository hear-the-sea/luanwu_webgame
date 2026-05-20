from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("battle", "0005_add_player_troop"),
    ]

    operations = [
        migrations.AddField(
            model_name="battlereport",
            name="attacker_city_defenses",
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name="battlereport",
            name="defender_city_defenses",
            field=models.JSONField(default=list),
        ),
    ]
