import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0209_target_manor_items"),
    ]

    operations = [
        migrations.CreateModel(
            name="ManorResourceProductionBuff",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("resource_type", models.CharField(max_length=16)),
                ("bonus_percent", models.PositiveSmallIntegerField(default=25)),
                ("starts_at", models.DateTimeField()),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "manor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="resource_production_buffs",
                        to="gameplay.manor",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="manorresourceproductionbuff",
            constraint=models.UniqueConstraint(
                fields=("manor", "resource_type"),
                name="uniq_manor_resource_production_buff",
            ),
        ),
        migrations.AddIndex(
            model_name="manorresourceproductionbuff",
            index=models.Index(fields=["manor", "expires_at"], name="manor_resource_buff_exp_idx"),
        ),
    ]
