from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("battle", "0007_battlereport_replay_versions"),
        ("gameplay", "0134_arena_virtual_reserve"),
    ]

    operations = [
        migrations.AddField(
            model_name="arenacoopevent",
            name="base_seed",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="arenacoopevent",
            name="rng_version",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="arenacoopevent",
            name="battle_engine_version",
            field=models.CharField(default="legacy", max_length=16),
        ),
        migrations.AddField(
            model_name="arenamatch",
            name="base_seed",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="arenamatch",
            name="rng_version",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="arenamatch",
            name="battle_engine_version",
            field=models.CharField(default="legacy", max_length=16),
        ),
        migrations.AddField(
            model_name="raidrun",
            name="base_seed",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="raidrun",
            name="rng_version",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="raidrun",
            name="battle_engine_version",
            field=models.CharField(default="legacy", max_length=16),
        ),
        migrations.AddField(
            model_name="raidrun",
            name="resources_released",
            field=models.BooleanField(default=False, verbose_name="失败资源已释放"),
        ),
        migrations.AlterField(
            model_name="raidrun",
            name="failure_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("missing_attacker_lineup", "缺少出征门客与快照"),
                    ("invalid_guest_snapshot", "门客战斗快照无效"),
                    ("invalid_troop_loadout", "护院编队快照无效"),
                ],
                default="",
                max_length=64,
                verbose_name="失败原因",
            ),
        ),
    ]
