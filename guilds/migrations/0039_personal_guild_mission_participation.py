import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0016_missiontemplate_missionrun"),
        ("guilds", "0038_guild_technology_route_reset_log"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="GuildPersonalMissionParticipation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "contribution_reward",
                    models.PositiveIntegerField(default=0, verbose_name="贡献奖励快照"),
                ),
                (
                    "started_at",
                    models.DateTimeField(
                        db_index=True,
                        default=django.utils.timezone.now,
                        verbose_name="参与时间",
                    ),
                ),
                (
                    "guild",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="personal_mission_participations",
                        to="guilds.guild",
                        verbose_name="发起时帮会",
                    ),
                ),
                (
                    "member",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="personal_mission_participations",
                        to="guilds.guildmember",
                        verbose_name="发起时成员",
                    ),
                ),
                (
                    "mission_run",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="guild_personal_participation",
                        to="gameplay.missionrun",
                        verbose_name="个人任务出征",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="guild_personal_mission_participations",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="参与玩家",
                    ),
                ),
            ],
            options={
                "verbose_name": "个人帮会任务参与记录",
                "verbose_name_plural": "个人帮会任务参与记录",
                "db_table": "guild_personal_mission_participations",
                "ordering": ["-started_at", "-id"],
                "indexes": [
                    models.Index(
                        fields=["user", "started_at"],
                        name="gpm_user_started_idx",
                    ),
                ],
            },
        ),
        migrations.AlterField(
            model_name="guildresourcelog",
            name="action",
            field=models.CharField(
                choices=[
                    ("donation", "成员捐赠"),
                    ("tech_upgrade", "科技升级"),
                    ("tech_reset", "科技路线重置"),
                    ("guild_upgrade", "帮会升级"),
                    ("production", "科技产出"),
                    ("troop_recruit", "铁骑训练"),
                    ("mission_reward", "个人帮会任务"),
                ],
                max_length=20,
                verbose_name="操作类型",
            ),
        ),
    ]
