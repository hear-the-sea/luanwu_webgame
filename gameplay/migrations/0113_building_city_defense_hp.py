from __future__ import annotations

import django.utils.timezone
from django.db import migrations, models


def backfill_city_defense_hp(apps, schema_editor):
    Building = apps.get_model("gameplay", "Building")

    max_hp_by_key = {
        "wall": 30000,
        "arrow_tower": 15000,
    }
    now = django.utils.timezone.now()
    for key, max_hp in max_hp_by_key.items():
        for building in Building.objects.filter(building_type__key=key).select_related("building_type"):
            level = max(0, min(10, int(building.level or 0)))
            building.current_hp = max(1, int(max_hp * (level / 10)))
            building.hp_updated_at = now
            building.save(update_fields=["current_hp", "hp_updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("gameplay", "0112_alter_buildingtype_category"),
    ]

    operations = [
        migrations.AddField(
            model_name="building",
            name="current_hp",
            field=models.PositiveIntegerField(default=0, verbose_name="当前耐久"),
        ),
        migrations.AddField(
            model_name="building",
            name="hp_updated_at",
            field=models.DateTimeField(default=django.utils.timezone.now, verbose_name="耐久恢复时间"),
        ),
        migrations.RunPython(backfill_city_defense_hp, migrations.RunPython.noop),
    ]
