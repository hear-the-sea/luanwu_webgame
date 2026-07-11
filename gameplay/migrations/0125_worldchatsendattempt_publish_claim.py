from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0124_world_chat_send_attempt"),
    ]

    operations = [
        migrations.AddField(
            model_name="worldchatsendattempt",
            name="publish_claim_token",
            field=models.UUIDField(
                blank=True,
                editable=False,
                null=True,
                verbose_name="发布 claim token",
            ),
        ),
        migrations.AddField(
            model_name="worldchatsendattempt",
            name="publish_claimed_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="发布 claim 时间",
            ),
        ),
    ]
