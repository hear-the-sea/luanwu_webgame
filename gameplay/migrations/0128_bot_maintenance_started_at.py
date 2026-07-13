from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0127_bot_inventory_template_pool"),
    ]

    operations = [
        migrations.AddField(
            model_name="botprofile",
            name="maintenance_started_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="维护开始时间"),
        ),
    ]
