from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0147_backfill_grain_warehouse_ledger"),
    ]

    operations = [
        migrations.AddField(
            model_name="botruntimeroutingstate",
            name="safety_clean_window_kind",
            field=models.CharField(
                blank=True,
                default="",
                max_length=16,
                verbose_name="连续安全窗口类型",
            ),
        ),
    ]
