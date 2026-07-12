from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0125_worldchatsendattempt_publish_claim"),
    ]

    operations = [
        migrations.AddField(
            model_name="arenacoopentry",
            name="source",
            field=models.CharField(
                choices=[("player", "玩家"), ("virtual", "虚拟")],
                db_index=True,
                default="player",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="arenacoopevent",
            name="virtual_fill_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="arenacoopevent",
            name="virtual_fill_completed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="arenaentry",
            name="source",
            field=models.CharField(
                choices=[("player", "玩家"), ("virtual", "虚拟")],
                db_index=True,
                default="player",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="arenatournament",
            name="virtual_fill_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="arenatournament",
            name="virtual_fill_completed",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="arenacoopentryguest",
            name="guest",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="arena_coop_entry_links",
                to="guests.guest",
            ),
        ),
        migrations.AlterField(
            model_name="arenaentryguest",
            name="guest",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="arena_entry_links",
                to="guests.guest",
            ),
        ),
        migrations.AddIndex(
            model_name="arenacoopevent",
            index=models.Index(fields=["status", "virtual_fill_at"], name="arena_coop_status_fill_idx"),
        ),
        migrations.AddIndex(
            model_name="arenatournament",
            index=models.Index(fields=["status", "virtual_fill_at"], name="arena_tour_status_fill_idx"),
        ),
    ]
