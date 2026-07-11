from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0120_manor_occupied_region_unique"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="is_deletion_protected",
            field=models.BooleanField(default=False, editable=False, verbose_name="禁止删除"),
        ),
    ]
