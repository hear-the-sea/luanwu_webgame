import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0189_bot_safety_decision_audit")]
    operations = [
        migrations.CreateModel(
            name="ArenaFillServiceEpisode",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("mode", models.CharField(choices=[("tournament", "普通"), ("coop", "共斗")], max_length=16)),
                ("event_id", models.PositiveBigIntegerField()),
                ("virtual_fill_at", models.DateTimeField()),
                ("opening_ordinal", models.PositiveIntegerField(default=1)),
                ("checkpoint_at", models.DateTimeField()),
                ("first_observed_at", models.DateTimeField()),
                ("first_real_entry_at", models.DateTimeField(blank=True, null=True)),
                ("first_demand_at", models.DateTimeField(blank=True, null=True)),
                ("initial_missing_count", models.PositiveIntegerField(default=0)),
                ("first_completed_at", models.DateTimeField(blank=True, null=True)),
                ("completion_kind", models.CharField(blank=True, default="", max_length=32)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("terminal_reason", models.CharField(blank=True, default="", max_length=32)),
                ("slo_result", models.CharField(blank=True, default="", max_length=32)),
                ("slo_observed_at", models.DateTimeField(blank=True, null=True)),
                ("last_observed_at", models.DateTimeField()),
                ("last_snapshot", models.JSONField(default=dict)),
                ("pending_outcomes", models.JSONField(blank=True, default=dict)),
                ("publication_pending", models.BooleanField(db_index=True, default=False)),
                ("sampled_gap_seconds", models.FloatField(default=0)),
                ("unknown_observation_seconds", models.FloatField(default=0)),
                ("version", models.PositiveIntegerField(default=1)),
                (
                    "demand",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="fill_service_episodes",
                        to="gameplay.arenavirtualdemand",
                    ),
                ),
            ],
            options={
                "verbose_name": "竞技场补位服务期次",
                "verbose_name_plural": "竞技场补位服务期次",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("mode", "event_id", "virtual_fill_at", "opening_ordinal"),
                        name="arena_fill_episode_unique",
                    )
                ],
                "indexes": [models.Index(fields=["closed_at", "last_observed_at"], name="arena_fill_pending_idx")],
            },
        ),
        migrations.CreateModel(
            name="ArenaFillServiceOutcome",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("phase", models.CharField(choices=[("slo", "服务期限"), ("completion", "最终补齐")], max_length=16)),
                ("result", models.CharField(max_length=32)),
                ("logical_at", models.DateTimeField(db_index=True)),
                ("observed_at", models.DateTimeField()),
                ("published_at", models.DateTimeField(auto_now_add=True)),
                ("payload", models.JSONField()),
                (
                    "episode",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="outcomes",
                        to="gameplay.arenafillserviceepisode",
                    ),
                ),
            ],
            options={
                "verbose_name": "竞技场补位服务结果",
                "verbose_name_plural": "竞技场补位服务结果",
                "constraints": [models.UniqueConstraint(fields=("episode", "phase"), name="arena_fill_outcome_unique")],
            },
        ),
    ]
