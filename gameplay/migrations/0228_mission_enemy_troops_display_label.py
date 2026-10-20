from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0227_zhanlong_current_combat_profiles")]

    operations = [
        migrations.AlterField(
            model_name="missiontemplate",
            name="enemy_troops_display",
            field=models.JSONField(blank=True, default=dict, verbose_name="敌军数量显示"),
        ),
    ]
