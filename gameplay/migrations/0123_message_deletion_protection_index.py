from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0122_message_deletion_protection_backfill"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="message",
            index=models.Index(
                fields=["is_deletion_protected", "created_at", "id"],
                name="message_protected_cleanup_idx",
            ),
        ),
    ]
