from django.db import migrations, models
from django.db.models import Count


def reconcile_existing_inventory_gear(apps, schema_editor):
    GearItem = apps.get_model("guests", "GearItem")
    InventoryItem = apps.get_model("gameplay", "InventoryItem")
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")

    inventory_templates = {
        template.key: template
        for template in ItemTemplate.objects.filter(effect_type__startswith="equip_").only("id", "key")
    }
    free_groups = (
        GearItem.objects.filter(
            guest_id__isnull=True,
            template__key__in=inventory_templates,
        )
        .values("manor_id", "template__key")
        .annotate(free_count=Count("id"))
    )

    for group in free_groups.iterator():
        manor_id = group["manor_id"]
        template_key = group["template__key"]
        item_template = inventory_templates[template_key]
        inventory_item = InventoryItem.objects.filter(
            manor_id=manor_id,
            template_id=item_template.id,
            storage_location="warehouse",
        ).first()
        target_quantity = max(int(group["free_count"]), int(getattr(inventory_item, "quantity", 0)))
        if inventory_item is None:
            InventoryItem.objects.create(
                manor_id=manor_id,
                template_id=item_template.id,
                storage_location="warehouse",
                quantity=target_quantity,
            )
        elif inventory_item.quantity != target_quantity:
            InventoryItem.objects.filter(pk=inventory_item.pk).update(quantity=target_quantity)

        GearItem.objects.filter(
            manor_id=manor_id,
            guest_id__isnull=True,
            template__key=template_key,
        ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("guests", "0064_guestrecruitment_unique_pending_per_manor"),
        ("gameplay", "0125_worldchatsendattempt_publish_claim"),
    ]

    operations = [
        migrations.AddField(
            model_name="gearitem",
            name="inventory_backed",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(reconcile_existing_inventory_gear, migrations.RunPython.noop),
    ]
