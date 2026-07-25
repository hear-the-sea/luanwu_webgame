import django.db.models.deletion
from django.db import migrations, models


def create_population_control(apps, schema_editor):
    del schema_editor
    control_model = apps.get_model("gameplay", "BotPopulationControl")
    control_model.objects.get_or_create(key="global")


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0133_raidrun_failure_reason"),
    ]

    operations = [
        migrations.AddField(
            model_name="botprofile",
            name="arena_participation_count",
            field=models.PositiveIntegerField(default=0, verbose_name="竞技场累计参赛次数"),
        ),
        migrations.AddField(
            model_name="botprofile",
            name="last_arena_participated_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name="最近竞技场参赛时间",
            ),
        ),
        migrations.CreateModel(
            name="BotPopulationControl",
            fields=[
                (
                    "key",
                    models.CharField(
                        default="global",
                        editable=False,
                        max_length=16,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "虚拟玩家人口协调",
                "verbose_name_plural": "虚拟玩家人口协调",
            },
        ),
        migrations.AddConstraint(
            model_name="botpopulationcontrol",
            constraint=models.CheckConstraint(
                condition=models.Q(("key", "global")),
                name="bot_population_control_global_only",
            ),
        ),
        migrations.RunPython(create_population_control, migrations.RunPython.noop),
        migrations.CreateModel(
            name="ArenaVirtualDemand",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "协调中"),
                            ("satisfied", "已完成补位"),
                            ("closed", "已关闭"),
                        ],
                        db_index=True,
                        default="active",
                        max_length=16,
                        verbose_name="协调状态",
                    ),
                ),
                ("version", models.PositiveIntegerField(default=1, verbose_name="需求版本")),
                ("target_guest_count", models.PositiveSmallIntegerField(default=0, verbose_name="目标门客数")),
                ("target_team_power", models.PositiveBigIntegerField(default=0, verbose_name="目标队伍战力")),
                ("missing_entry_count", models.PositiveSmallIntegerField(default=0, verbose_name="缺少席位数")),
                ("reserve_target_count", models.PositiveIntegerField(default=0, verbose_name="后备目标数")),
                ("max_reserve_target_count", models.PositiveIntegerField(default=0, verbose_name="后备目标上限")),
                ("created_profile_count", models.PositiveIntegerField(default=0, verbose_name="已创建虚拟玩家数")),
                (
                    "next_retry_at",
                    models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="下次重试时间"),
                ),
                ("last_checked_at", models.DateTimeField(blank=True, null=True, verbose_name="最近检查时间")),
                (
                    "last_failure_reason",
                    models.CharField(blank=True, default="", max_length=64, verbose_name="最近失败原因"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "coop_event",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="virtual_demand",
                        to="gameplay.arenacoopevent",
                        verbose_name="共斗活动",
                    ),
                ),
                (
                    "tournament",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="virtual_demand",
                        to="gameplay.arenatournament",
                        verbose_name="普通竞技场",
                    ),
                ),
            ],
            options={
                "verbose_name": "竞技场虚拟后备需求",
                "verbose_name_plural": "竞技场虚拟后备需求",
            },
        ),
        migrations.CreateModel(
            name="ArenaVirtualReserveMember",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("ready", "可补位"),
                            ("training", "培养中"),
                            ("exhausted", "培养已达上限"),
                        ],
                        db_index=True,
                        default="training",
                        max_length=16,
                        verbose_name="后备状态",
                    ),
                ),
                ("evaluated_version", models.PositiveIntegerField(default=1, verbose_name="已评估版本")),
                ("current_lineup_power", models.PositiveBigIntegerField(default=0, verbose_name="当前阵容战力")),
                (
                    "accelerated_growth_rounds",
                    models.PositiveSmallIntegerField(default=0, verbose_name="加速成长轮次"),
                ),
                (
                    "next_acceleration_at",
                    models.DateTimeField(blank=True, db_index=True, null=True, verbose_name="下次加速时间"),
                ),
                ("last_checked_at", models.DateTimeField(blank=True, null=True, verbose_name="最近检查时间")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "demand",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reserve_members",
                        to="gameplay.arenavirtualdemand",
                        verbose_name="后备需求",
                    ),
                ),
                (
                    "profile",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="arena_virtual_reserve",
                        to="gameplay.botprofile",
                        verbose_name="虚拟玩家档案",
                    ),
                ),
            ],
            options={
                "verbose_name": "竞技场虚拟后备成员",
                "verbose_name_plural": "竞技场虚拟后备成员",
            },
        ),
        migrations.AddConstraint(
            model_name="arenavirtualdemand",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("coop_event__isnull", True), ("tournament__isnull", False))
                    | models.Q(("coop_event__isnull", False), ("tournament__isnull", True))
                ),
                name="arena_virtual_demand_one_event",
            ),
        ),
        migrations.AddIndex(
            model_name="arenavirtualdemand",
            index=models.Index(fields=["status", "next_retry_at"], name="arena_vd_status_retry_idx"),
        ),
        migrations.AddConstraint(
            model_name="arenavirtualreservemember",
            constraint=models.UniqueConstraint(
                fields=("demand", "profile"),
                name="arena_virtual_member_demand_profile",
            ),
        ),
        migrations.AddIndex(
            model_name="arenavirtualreservemember",
            index=models.Index(fields=["demand", "state"], name="arena_vm_demand_state_idx"),
        ),
        migrations.AddIndex(
            model_name="arenavirtualreservemember",
            index=models.Index(fields=["state", "next_acceleration_at"], name="arena_vm_state_accel_idx"),
        ),
    ]
