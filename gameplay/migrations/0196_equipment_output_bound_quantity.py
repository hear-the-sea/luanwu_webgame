from django.db import migrations, models


def preserve_existing_bound_outputs(apps, schema_editor):
    EquipmentProduction = apps.get_model("gameplay", "EquipmentProduction")
    EquipmentProduction.objects.using(schema_editor.connection.alias).filter(output_is_bound=True).update(
        output_bound_quantity=models.F("quantity")
    )


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0195_item_binding")]

    operations = [
        migrations.AddField(
            model_name="equipmentproduction",
            name="output_bound_quantity",
            field=models.PositiveIntegerField(default=0, verbose_name="产物绑定数量"),
        ),
        migrations.RunPython(preserve_existing_bound_outputs, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="equipmentproduction",
            constraint=models.CheckConstraint(
                condition=models.Q(output_bound_quantity__lte=models.F("quantity")),
                name="equipment_bound_lte_total",
            ),
        ),
    ]
