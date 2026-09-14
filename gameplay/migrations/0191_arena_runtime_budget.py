from django.db import migrations, models


def seed_budgets(apps, schema_editor):
    budget = apps.get_model("gameplay", "ArenaRuntimeBudget")
    for key in ("materialization", "training", "growth"):
        budget.objects.using(schema_editor.connection.alias).get_or_create(key=key)


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0190_arena_fill_service_episode")]

    operations = [
        migrations.CreateModel(
            name="ArenaRuntimeBudget",
            fields=[
                ("key", models.CharField(max_length=24, primary_key=True, serialize=False)),
                ("entries", models.JSONField(default=list)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(key__in=["materialization", "training", "growth"]),
                        name="arena_runtime_budget_key_valid",
                    )
                ],
            },
        ),
        migrations.RunPython(seed_budgets, migrations.RunPython.noop),
    ]
