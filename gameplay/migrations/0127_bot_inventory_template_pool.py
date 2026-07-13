from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0126_virtual_arena_backfill"),
    ]

    operations = [
        migrations.AddField(
            model_name="botprofile",
            name="inventory_template_keys",
            field=models.JSONField(blank=True, default=list, verbose_name="库存模板池"),
        ),
    ]
