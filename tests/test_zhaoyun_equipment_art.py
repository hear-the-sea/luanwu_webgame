from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.core.files.storage import default_storage
from django.db import connection
from PIL import Image

from gameplay.models import InventoryItem, ItemTemplate

rules = import_module("gameplay.migrations.0226_zhaoyun_equipment_art")


@pytest.mark.django_db
def test_redrawn_art_syncs_base_and_existing_affixes_without_changing_stats_or_inventory(
    settings, tmp_path, manor_factory
):
    settings.MEDIA_ROOT = tmp_path
    manor, _ = manor_factory()
    affected = []
    for key in rules.IMAGE_SOURCES:
        base, _ = ItemTemplate.objects.update_or_create(
            key=key, defaults={"name": key, "image": "items/old.webp", "effect_payload": {"hp": 123}}
        )
        variant, _ = ItemTemplate.objects.update_or_create(
            key="art_test_affix__" + key,
            defaults={
                "name": key + "词缀",
                "image": "items/old.webp",
                "effect_payload": {"equipment_base_key": key, "hp": 456},
            },
        )
        affected.extend((base, variant))
        InventoryItem.objects.create(manor=manor, template=variant, quantity=3, bound_quantity=1)
    other, _ = ItemTemplate.objects.update_or_create(
        key="zhaoyun_art_unrelated", defaults={"name": "保留物品", "image": "items/other.webp"}
    )
    before = {row["key"]: row for row in ItemTemplate.objects.filter(pk__in=[item.pk for item in affected]).values()}
    inventory_before = list(InventoryItem.objects.filter(manor=manor).order_by("pk").values())
    for _ in range(2):
        rules.sync_zhaoyun_equipment_art(apps, SimpleNamespace(connection=connection))
    for item in affected:
        item.refresh_from_db()
        after = ItemTemplate.objects.filter(pk=item.pk).values().get()
        assert {key: value for key, value in after.items() if key != "image"} == {
            key: value for key, value in before[item.key].items() if key != "image"
        }
        with default_storage.open(item.image.name, "rb") as stored:
            with Image.open(stored) as image:
                assert image.format == "WEBP" and max(image.size) <= 200
        base_key = item.effect_payload.get("equipment_base_key", item.key)
        assert len(item.image.name) <= ItemTemplate._meta.get_field("image").max_length
        assert item.image.name == ItemTemplate.objects.get(key=base_key).image.name
        assert "items/zhaoyun/" + base_key + "_" in item.image.name
    other.refresh_from_db()
    assert other.image.name == "items/other.webp"
    assert list(InventoryItem.objects.filter(manor=manor).order_by("pk").values()) == inventory_before
