from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("battle", "0007_battlereport_replay_versions"),
        ("guilds", "0025_add_guild_raid_loot_settlement"),
    ]

    operations = [
        migrations.AlterField(
            model_name="guildraidrun",
            name="status",
            field=models.CharField(
                choices=[
                    ("marching", "行军中"),
                    ("battling", "战斗中"),
                    ("returning", "返程中"),
                    ("completed", "已完成"),
                    ("retreated", "已撤退"),
                    ("failed", "出征失败"),
                ],
                default="marching",
                max_length=16,
                verbose_name="状态",
            ),
        ),
        migrations.AddField(
            model_name="guildraidrun",
            name="failure_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("missing_attacker_lineup", "缺少出征门客快照"),
                    ("invalid_guest_snapshot", "门客战斗快照无效"),
                    ("invalid_troop_loadout", "护院编队快照无效"),
                    ("inactive_attacker_guild", "进攻帮会已失效"),
                ],
                default="",
                max_length=64,
                verbose_name="失败原因",
            ),
        ),
        migrations.AddField(
            model_name="guildraidrun",
            name="resources_released",
            field=models.BooleanField(default=False, verbose_name="失败资源已释放"),
        ),
        migrations.AddField(
            model_name="guildraidrun",
            name="base_seed",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="guildraidrun",
            name="rng_version",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="guildraidrun",
            name="battle_engine_version",
            field=models.CharField(default="legacy", max_length=16),
        ),
    ]
