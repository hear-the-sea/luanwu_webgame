from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import F


def backfill_captured_loyalty(apps, schema_editor):
    JailPrisoner = apps.get_model("gameplay", "JailPrisoner")
    JailPrisoner.objects.filter(captured_loyalty__isnull=True).update(captured_loyalty=F("loyalty"))


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0128_bot_maintenance_started_at"),
        ("guests", "0065_gearitem_inventory_backed"),
    ]

    operations = [
        migrations.AddField(
            model_name="jailprisoner",
            name="captured_loyalty",
            field=models.PositiveSmallIntegerField(null=True, verbose_name="被俘时忠诚"),
        ),
        migrations.RunPython(backfill_captured_loyalty, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="jailprisoner",
            name="captured_loyalty",
            field=models.PositiveSmallIntegerField(verbose_name="被俘时忠诚"),
        ),
        migrations.AddField(
            model_name="jailprisoner",
            name="affinity",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="归心"),
        ),
        migrations.AddField(
            model_name="jailprisoner",
            name="stance_method",
            field=models.CharField(blank=True, default="", max_length=16, verbose_name="招降突破口"),
        ),
        migrations.AddField(
            model_name="jailprisoner",
            name="taboo_method",
            field=models.CharField(blank=True, default="", max_length=16, verbose_name="招降忌讳"),
        ),
        migrations.AddField(
            model_name="jailprisoner",
            name="revealed_level",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="线索揭示等级"),
        ),
        migrations.AddField(
            model_name="jailprisoner",
            name="milestone_stage",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="里程碑阶段"),
        ),
        migrations.AddField(
            model_name="jailprisoner",
            name="interaction_date",
            field=models.DateField(blank=True, db_index=True, null=True, verbose_name="招降次数日期"),
        ),
        migrations.AddField(
            model_name="jailprisoner",
            name="interactions_today",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="今日招降次数"),
        ),
        migrations.AddField(
            model_name="jailprisoner",
            name="last_method",
            field=models.CharField(blank=True, default="", max_length=16, verbose_name="最近招降手段"),
        ),
        migrations.AddField(
            model_name="jailprisoner",
            name="same_method_streak",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="连续同手段次数"),
        ),
        migrations.AddField(
            model_name="jailprisoner",
            name="observed_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="察言时间"),
        ),
        migrations.AddConstraint(
            model_name="jailprisoner",
            constraint=models.CheckConstraint(
                condition=models.Q(("captured_loyalty__gte", 0), ("captured_loyalty__lte", 100)),
                name="jail_captured_loyalty_0_100",
            ),
        ),
        migrations.AddConstraint(
            model_name="jailprisoner",
            constraint=models.CheckConstraint(
                condition=models.Q(("affinity__gte", 0), ("affinity__lte", 100)),
                name="jail_affinity_0_100",
            ),
        ),
        migrations.AddConstraint(
            model_name="jailprisoner",
            constraint=models.CheckConstraint(
                condition=models.Q(("revealed_level__gte", 0), ("revealed_level__lte", 3)),
                name="jail_revealed_level_0_3",
            ),
        ),
        migrations.AddConstraint(
            model_name="jailprisoner",
            constraint=models.CheckConstraint(
                condition=models.Q(("milestone_stage__gte", 0), ("milestone_stage__lte", 2)),
                name="jail_milestone_stage_0_2",
            ),
        ),
        migrations.CreateModel(
            name="JailInteractionLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("method", models.CharField(max_length=24, verbose_name="招降手段")),
                (
                    "speaker_name_snapshot",
                    models.CharField(blank=True, default="", max_length=64, verbose_name="说客姓名快照"),
                ),
                (
                    "speaker_template_key_snapshot",
                    models.CharField(blank=True, default="", max_length=64, verbose_name="说客模板快照"),
                ),
                (
                    "speaker_base_value_snapshot",
                    models.PositiveIntegerField(blank=True, null=True, verbose_name="说客基础值快照"),
                ),
                (
                    "speaker_loyalty_before",
                    models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="说客忠诚变化前"),
                ),
                (
                    "speaker_loyalty_after",
                    models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="说客忠诚变化后"),
                ),
                ("usage_date", models.DateField(db_index=True, verbose_name="使用日期")),
                ("heart_before", models.PositiveSmallIntegerField(verbose_name="心防变化前")),
                ("heart_after", models.PositiveSmallIntegerField(verbose_name="心防变化后")),
                ("affinity_before", models.PositiveSmallIntegerField(verbose_name="归心变化前")),
                ("affinity_after", models.PositiveSmallIntegerField(verbose_name="归心变化后")),
                (
                    "outcome",
                    models.CharField(
                        choices=[
                            ("matched", "契合"),
                            ("neutral", "普通"),
                            ("taboo", "犯忌"),
                            ("failed", "失败"),
                            ("backfire", "反噬"),
                            ("event", "事件"),
                        ],
                        max_length=16,
                        verbose_name="结果",
                    ),
                ),
                ("copy_key", models.CharField(max_length=128, verbose_name="文案键")),
                ("copy_params", models.JSONField(blank=True, default=dict, verbose_name="文案参数")),
                ("resource_cost", models.JSONField(blank=True, default=dict, verbose_name="资源消耗")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="创建时间")),
                (
                    "captor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="jail_interaction_logs",
                        to="gameplay.manor",
                        verbose_name="庄园",
                    ),
                ),
                (
                    "prisoner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="interaction_logs",
                        to="gameplay.jailprisoner",
                        verbose_name="囚徒",
                    ),
                ),
                (
                    "speaker",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="jail_persuasion_logs",
                        to="guests.guest",
                        verbose_name="说客",
                    ),
                ),
            ],
            options={
                "verbose_name": "监牢招降日志",
                "verbose_name_plural": "监牢招降日志",
                "ordering": ["-created_at", "-id"],
                "indexes": [
                    models.Index(fields=["prisoner", "-created_at"], name="jail_log_prisoner_created_idx"),
                    models.Index(fields=["captor", "usage_date"], name="jail_log_captor_date_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("speaker", "usage_date"), name="uniq_jail_speaker_usage_date")
                ],
            },
        ),
    ]
