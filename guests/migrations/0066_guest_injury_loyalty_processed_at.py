from __future__ import annotations

from django.db import migrations, models
from django.utils import timezone


def initialize_existing_injured_guests(apps, schema_editor):
    Guest = apps.get_model("guests", "Guest")
    Guest.objects.filter(status="injured", injury_loyalty_processed_at__isnull=True).update(
        injury_loyalty_processed_at=timezone.now()
    )


class Migration(migrations.Migration):
    dependencies = [
        ("guests", "0065_gearitem_inventory_backed"),
    ]

    operations = [
        migrations.AddField(
            model_name="guest",
            name="injury_loyalty_processed_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="记录战斗重伤期间最近一次忠诚度结算边界",
                null=True,
                verbose_name="重伤忠诚度结算时间",
            ),
        ),
        migrations.RunPython(initialize_existing_injured_guests, migrations.RunPython.noop),
    ]
