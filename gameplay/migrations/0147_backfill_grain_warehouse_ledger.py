from django.db import migrations
from django.utils import timezone


def backfill_grain_warehouse_ledger(apps, schema_editor):
    Manor = apps.get_model("gameplay", "Manor")
    InventoryItem = apps.get_model("gameplay", "InventoryItem")
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")

    grain_template = ItemTemplate.objects.filter(key="grain").first()
    if grain_template is None:
        # ItemTemplate is loaded by bootstrap_game_data after migrations. The
        # provisioning repair command will cover that deployment order.
        return

    # Manor.grain is the historical compatibility value, so every manor is
    # checked and stale/missing warehouse rows are aligned with it.
    creates: list[InventoryItem] = []
    deletes: list[int] = []

    def flush_batches() -> None:
        if creates:
            InventoryItem.objects.bulk_create(creates, ignore_conflicts=True, batch_size=500)
            creates.clear()
        if deletes:
            InventoryItem.objects.filter(pk__in=deletes).delete()
            deletes.clear()

    for manor_id, raw_quantity in Manor.objects.values_list("id", "grain").iterator(chunk_size=500):
        quantity = max(0, int(raw_quantity or 0))
        ledger_row = (
            InventoryItem.objects.filter(
                manor_id=manor_id,
                template_id=grain_template.pk,
                storage_location="warehouse",
            )
            .only("pk", "quantity")
            .first()
        )
        if ledger_row is None:
            if quantity > 0:
                creates.append(
                    InventoryItem(
                        manor_id=manor_id,
                        template_id=grain_template.pk,
                        storage_location="warehouse",
                        quantity=quantity,
                        created_at=timezone.now(),
                        updated_at=timezone.now(),
                    )
                )
        elif quantity <= 0:
            deletes.append(ledger_row.pk)
        elif int(ledger_row.quantity or 0) != quantity:
            InventoryItem.objects.filter(pk=ledger_row.pk).update(
                quantity=quantity,
                updated_at=timezone.now(),
            )
        if len(creates) >= 500 or len(deletes) >= 500:
            flush_batches()
    flush_batches()


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0146_virtual_player_health_and_recovery"),
    ]

    operations = [
        migrations.RunPython(backfill_grain_warehouse_ledger, migrations.RunPython.noop),
    ]
