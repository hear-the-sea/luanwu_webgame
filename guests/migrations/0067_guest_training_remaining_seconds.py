import math
from datetime import datetime

from django.db import migrations, models
from django.utils import timezone

NON_IDLE_STATUSES = ["working", "deployed", "arena", "injured"]
BACKUP_TABLE = "_guest_training_backup_0067"


def _create_backup_table(schema_editor) -> None:
    connection = schema_editor.connection
    quote_name = connection.ops.quote_name
    table = quote_name(BACKUP_TABLE)
    with connection.cursor() as cursor:
        cursor.execute(
            f"CREATE TABLE IF NOT EXISTS {table} ("
            "guest_id BIGINT PRIMARY KEY, "
            "training_complete_at VARCHAR(255) NOT NULL)"
        )
        cursor.execute(f"DELETE FROM {table}")


def _drop_backup_table(schema_editor) -> None:
    connection = schema_editor.connection
    quote_name = connection.ops.quote_name
    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {quote_name(BACKUP_TABLE)}")


def pause_non_idle_guest_training(apps, schema_editor):
    Guest = apps.get_model("guests", "Guest")
    now = timezone.now()
    guests = Guest.objects.filter(
        status__in=NON_IDLE_STATUSES,
        training_complete_at__isnull=False,
    ).only("id", "training_complete_at")
    updates = []
    backup_rows = []
    connection = schema_editor.connection
    quote_name = connection.ops.quote_name
    table = quote_name(BACKUP_TABLE)
    _create_backup_table(schema_editor)
    with connection.cursor() as cursor:
        for guest in guests.iterator(chunk_size=500):
            backup_rows.append((guest.pk, guest.training_complete_at.isoformat()))
            remaining = max(0, math.ceil((guest.training_complete_at - now).total_seconds()))
            guest.training_remaining_seconds = remaining
            guest.training_complete_at = None
            updates.append(guest)
            if len(backup_rows) >= 500:
                cursor.executemany(
                    f"INSERT INTO {table} (guest_id, training_complete_at) VALUES (%s, %s)",
                    backup_rows,
                )
                backup_rows = []
            if len(updates) >= 500:
                Guest.objects.bulk_update(updates, ["training_complete_at", "training_remaining_seconds"])
                updates = []
        if backup_rows:
            cursor.executemany(
                f"INSERT INTO {table} (guest_id, training_complete_at) VALUES (%s, %s)",
                backup_rows,
            )
        if updates:
            Guest.objects.bulk_update(updates, ["training_complete_at", "training_remaining_seconds"])


def restore_non_idle_guest_training(apps, schema_editor):
    Guest = apps.get_model("guests", "Guest")
    connection = schema_editor.connection
    quote_name = connection.ops.quote_name
    table = quote_name(BACKUP_TABLE)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT guest_id, training_complete_at FROM {table}")
        while True:
            rows = cursor.fetchmany(500)
            if not rows:
                break
            timestamps_by_guest = {
                int(guest_id): datetime.fromisoformat(raw_timestamp) for guest_id, raw_timestamp in rows
            }
            guests = Guest.objects.filter(pk__in=timestamps_by_guest).only("id", "training_remaining_seconds")
            updates = []
            for guest in guests:
                guest.training_complete_at = timestamps_by_guest[guest.pk]
                guest.training_remaining_seconds = None
                updates.append(guest)
            if updates:
                Guest.objects.bulk_update(updates, ["training_complete_at", "training_remaining_seconds"])
    _drop_backup_table(schema_editor)


class Migration(migrations.Migration):
    dependencies = [
        ("guests", "0066_guest_injury_loyalty_processed_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="guest",
            name="training_remaining_seconds",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.RunPython(pause_non_idle_guest_training, restore_non_idle_guest_training),
    ]
