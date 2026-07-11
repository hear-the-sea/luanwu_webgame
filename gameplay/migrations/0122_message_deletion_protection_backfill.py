from __future__ import annotations

from django.db import migrations, transaction

BACKFILL_BATCH_SIZE = 1000


def _has_unclaimed_asset_attachments(attachments, is_claimed) -> bool:
    if is_claimed or not isinstance(attachments, dict):
        return False
    resources = attachments.get("resources")
    items = attachments.get("items")
    return bool((isinstance(resources, dict) and resources) or (isinstance(items, dict) and items))


def backfill_message_deletion_protection(apps, schema_editor) -> None:
    Message = apps.get_model("gameplay", "Message")
    database_alias = schema_editor.connection.alias
    last_message_id = 0

    while True:
        with transaction.atomic(using=database_alias):
            rows = list(
                Message.objects.using(database_alias)
                .select_for_update()
                .filter(id__gt=last_message_id)
                .order_by("id")
                .values_list("id", "attachments", "is_claimed")[:BACKFILL_BATCH_SIZE]
            )
            if not rows:
                break

            protected_ids: list[int] = []
            unprotected_ids: list[int] = []
            for message_id, attachments, is_claimed in rows:
                target_ids = (
                    protected_ids if _has_unclaimed_asset_attachments(attachments, is_claimed) else unprotected_ids
                )
                target_ids.append(message_id)

            if protected_ids:
                Message.objects.using(database_alias).filter(
                    id__in=protected_ids,
                    is_deletion_protected=False,
                ).update(is_deletion_protected=True)
            if unprotected_ids:
                Message.objects.using(database_alias).filter(
                    id__in=unprotected_ids,
                    is_deletion_protected=True,
                ).update(is_deletion_protected=False)

            last_message_id = rows[-1][0]


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("gameplay", "0121_message_deletion_protection"),
    ]

    operations = [
        migrations.RunPython(backfill_message_deletion_protection, migrations.RunPython.noop),
    ]
