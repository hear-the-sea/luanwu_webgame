import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gameplay", "0115_alter_missiontemplate_base_travel_time_default"),
    ]

    operations = [
        migrations.CreateModel(
            name="BotProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "archetype",
                    models.CharField(
                        "类型",
                        choices=[
                            ("balanced", "均衡型"),
                            ("rich", "肥羊型"),
                            ("dojo", "武馆型"),
                            ("guard", "护院型"),
                            ("abandoned", "弃坑型"),
                        ],
                        default="balanced",
                        max_length=16,
                    ),
                ),
                (
                    "state",
                    models.CharField(
                        "状态",
                        choices=[
                            ("active", "正常成长"),
                            ("slowing", "成长放缓"),
                            ("abandoned", "弃坑"),
                            ("stale", "停滞"),
                            ("retired", "退场"),
                        ],
                        default="active",
                        max_length=16,
                    ),
                ),
                ("prestige_band", models.CharField("声望段", db_index=True, max_length=32)),
                ("growth_seed", models.PositiveIntegerField("成长种子")),
                ("growth_stage", models.PositiveSmallIntegerField("成长阶段", default=1)),
                ("next_growth_at", models.DateTimeField("下次成长时间", db_index=True)),
                ("abandon_at", models.DateTimeField("弃坑时间", db_index=True)),
                ("retire_at", models.DateTimeField("退场时间", db_index=True)),
                ("loot_budget_daily", models.PositiveIntegerField("每日资源预算", default=0)),
                ("maintenance_stopped_at", models.DateTimeField("维护停止时间", blank=True, null=True)),
                ("last_planned_at", models.DateTimeField("最近规划时间", blank=True, null=True)),
                ("created_at", models.DateTimeField("创建时间", auto_now_add=True)),
                ("updated_at", models.DateTimeField("更新时间", auto_now=True)),
                (
                    "manor",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="bot_profile",
                        to="gameplay.manor",
                        verbose_name="庄园",
                    ),
                ),
            ],
            options={
                "verbose_name": "虚拟玩家档案",
                "verbose_name_plural": "虚拟玩家档案",
            },
        ),
        migrations.AddIndex(
            model_name="botprofile",
            index=models.Index(fields=["state", "next_growth_at"], name="bot_state_next_growth_idx"),
        ),
        migrations.AddIndex(
            model_name="botprofile",
            index=models.Index(fields=["prestige_band", "state"], name="bot_band_state_idx"),
        ),
    ]
