from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0135_battle_replay_and_failure_state"),
    ]

    operations = [
        migrations.AlterField(
            model_name="botprofile",
            name="state",
            field=models.CharField(
                choices=[
                    ("active", "正常成长"),
                    ("slowing", "成长放缓"),
                    ("abandoned", "弃坑"),
                    ("stale", "停滞"),
                    ("retired", "休眠"),
                ],
                default="active",
                max_length=16,
                verbose_name="状态",
            ),
        ),
    ]
