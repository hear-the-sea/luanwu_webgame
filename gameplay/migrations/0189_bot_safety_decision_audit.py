from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0188_alter_missiontemplate_mission_card_daily_limit")]

    operations = [
        migrations.CreateModel(
            name="BotSafetyDecisionAudit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("window_id", models.CharField(max_length=128)),
                ("policy_version", models.CharField(max_length=64)),
                ("policy_mode", models.CharField(max_length=16)),
                ("operation", models.CharField(default="monitor", max_length=16)),
                ("reasons", models.JSONField(default=list)),
                ("effective_pause", models.BooleanField(default=False)),
                ("scoped_pause", models.BooleanField(default=False)),
                ("previous_mode", models.CharField(max_length=32)),
                ("resulting_mode", models.CharField(max_length=32)),
                ("previous_reason", models.TextField(blank=True)),
                ("resulting_reason", models.TextField(blank=True)),
                ("previous_revision", models.PositiveBigIntegerField()),
                ("resulting_revision", models.PositiveBigIntegerField(unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ("-created_at", "-pk"),
                "verbose_name": "虚拟玩家安全决策审计",
                "verbose_name_plural": "虚拟玩家安全决策审计",
                "indexes": [models.Index(fields=["window_id", "policy_version"], name="bot_safety_audit_window")],
            },
        ),
    ]
