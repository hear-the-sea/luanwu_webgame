import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("battle", "0005_add_player_troop"),
        ("guilds", "0012_promote_grain_gold_bar_to_warehouse_items"),
    ]

    operations = [
        migrations.AddField(
            model_name="guild",
            name="defeat_protection_until",
            field=models.DateTimeField(blank=True, null=True, verbose_name="战败保护截止时间"),
        ),
        migrations.AddField(
            model_name="guild",
            name="newbie_protection_until",
            field=models.DateTimeField(blank=True, null=True, verbose_name="新帮保护截止时间"),
        ),
        migrations.AddField(
            model_name="guild",
            name="pvp_attack_count_reset_at",
            field=models.DateField(default=django.utils.timezone.localdate, verbose_name="PVP 主动进攻重置时间"),
        ),
        migrations.AddField(
            model_name="guild",
            name="pvp_attack_count_today",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="今日主动进攻次数"),
        ),
        migrations.AddField(
            model_name="guild",
            name="pvp_defense_count_reset_at",
            field=models.DateField(default=django.utils.timezone.localdate, verbose_name="PVP 被攻击重置时间"),
        ),
        migrations.AddField(
            model_name="guild",
            name="pvp_defense_count_today",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="今日被攻击次数"),
        ),
        migrations.CreateModel(
            name="GuildRaidRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("selected_guest_count", models.PositiveIntegerField(default=0, verbose_name="出征门客数量")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("marching", "行军中"),
                            ("battling", "战斗中"),
                            ("returning", "返程中"),
                            ("completed", "已完成"),
                            ("retreated", "已撤退"),
                        ],
                        default="marching",
                        max_length=16,
                        verbose_name="状态",
                    ),
                ),
                ("guest_ids", models.JSONField(blank=True, default=list, verbose_name="出征门客ID")),
                ("guest_snapshots", models.JSONField(blank=True, default=list, verbose_name="出征门客快照")),
                ("troop_loadout", models.JSONField(blank=True, default=dict, verbose_name="护院编队")),
                ("travel_time", models.PositiveIntegerField(default=0, verbose_name="单程行军时间(秒)")),
                ("loot_silver", models.PositiveIntegerField(default=0, verbose_name="掠夺银两")),
                ("loot_items", models.JSONField(blank=True, default=dict, verbose_name="掠夺物品")),
                ("battle_rewards", models.JSONField(blank=True, default=dict, verbose_name="战斗奖励")),
                ("blocked_reason", models.CharField(blank=True, default="", max_length=64, verbose_name="阻塞原因")),
                ("is_attacker_victory", models.BooleanField(blank=True, null=True, verbose_name="进攻方是否胜利")),
                ("started_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="出发时间")),
                ("battle_at", models.DateTimeField(blank=True, null=True, verbose_name="开战时间")),
                ("return_at", models.DateTimeField(blank=True, null=True, verbose_name="返程时间")),
                ("completed_at", models.DateTimeField(blank=True, null=True, verbose_name="完成时间")),
                (
                    "attacker_guild",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="raid_runs_sent",
                        to="guilds.guild",
                        verbose_name="进攻帮会",
                    ),
                ),
                (
                    "battle_report",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="guild_raid_runs",
                        to="battle.battlereport",
                        verbose_name="战报",
                    ),
                ),
                (
                    "defender_guild",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="raid_runs_received",
                        to="guilds.guild",
                        verbose_name="防守帮会",
                    ),
                ),
                (
                    "started_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="started_guild_raid_runs",
                        to="guilds.guildmember",
                        verbose_name="发起成员",
                    ),
                ),
            ],
            options={
                "verbose_name": "帮会掠夺出征",
                "verbose_name_plural": "帮会掠夺出征",
                "db_table": "guild_raid_runs",
                "ordering": ["-started_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="guildraidrun",
            index=models.Index(fields=["attacker_guild", "status", "-started_at"], name="grr_attacker_status_idx"),
        ),
        migrations.AddIndex(
            model_name="guildraidrun",
            index=models.Index(fields=["defender_guild", "status", "-started_at"], name="grr_defender_status_idx"),
        ),
        migrations.AddIndex(
            model_name="guildraidrun",
            index=models.Index(fields=["status", "battle_at"], name="grr_status_battle_idx"),
        ),
        migrations.AddIndex(
            model_name="guildraidrun",
            index=models.Index(fields=["status", "return_at"], name="grr_status_return_idx"),
        ),
    ]
