from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0158_arena_growth_budget_and_admission_high_water"),
    ]

    operations = [
        migrations.AddField(
            model_name="arenavirtualdemand",
            name="admission_pause_reason",
            field=models.CharField(blank=True, default="", max_length=64, verbose_name="准入止损原因"),
        ),
        migrations.AddField(
            model_name="arenavirtualdemand",
            name="admission_paused_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="准入止损时间"),
        ),
        migrations.AddConstraint(
            model_name="arenavirtualdemand",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(admission_pause_reason="", admission_paused_at__isnull=True)
                    | (~models.Q(admission_pause_reason="") & models.Q(admission_paused_at__isnull=False))
                ),
                name="arena_vd_admission_pause_fields_together",
            ),
        ),
    ]
