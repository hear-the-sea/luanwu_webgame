import copy
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from django.apps import apps
from django.db import connection

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.equipment_affixes import build_equipment_affix_payload, equipment_affix_key
from guests.models import GearItem, GearTemplate
from guests.services.equipment_payloads import build_gear_template_defaults
from guests.services.equipment_stats import apply_template_stats_to_guest
from tests.test_equipment_template_sync import _create_guest

migration = importlib.import_module("gameplay.migrations.0214_guild_orange_mounts")
previous = importlib.import_module("gameplay.migrations.0211_equipment_specialization")


def test_orange_mount_catalog_has_distinct_roles_and_matches_migration():
    catalog = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/item_templates.yaml").read_text())
    items = {item["key"]: item for item in catalog["items"]}
    for key, payload in migration.EQUIPMENT_PAYLOADS.items():
        item = items[key]
        assert item["effect_payload"] == payload
        assert item["name"] == migration.ITEM_METADATA[key]["name"]
        assert item["description"] == migration.ITEM_METADATA[key]["description"]
        assert item["rarity"] == "orange"
        assert item["storage_space"] == 250
        assert item["price"] == 65000
        assert payload["required_level"] == 80
        base = SimpleNamespace(key=key, name=item["name"], effect_payload=payload)
        for affix in previous.AFFIXES:
            assert previous._affix_payload(base, affix) == build_equipment_affix_payload(base, affix)
    white = migration.EQUIPMENT_PAYLOADS["equip_jinjingbailongju"]
    lightning = migration.EQUIPMENT_PAYLOADS["equip_shandianqiulongju"]
    assert white["defense"] > lightning["defense"]
    assert white["luck"] > lightning["luck"]
    assert white["troop_capacity"] == 40
    assert lightning.get("troop_capacity", 0) == 0
    assert lightning["agility"] > white["agility"]
    assert lightning["force"] > white["force"]


@pytest.mark.django_db
@pytest.mark.parametrize("key", migration.EQUIPMENT_PAYLOADS)
@pytest.mark.parametrize("affix", [None, "agility"])
@pytest.mark.parametrize("health", [1, 999999])
def test_mount_migration_updates_worn_stats_and_preserves_inventory(django_user_model, key, affix, health):
    manor, guest = _create_guest(django_user_model, f"mount_{key}_{affix}_{health}")
    guest.allocated_force = 17
    guest.force += 17
    naked = {field: getattr(guest, field) for field in previous.STAT_FIELDS.values()}
    base = ItemTemplate.objects.create(
        key=key,
        name=migration.ITEM_METADATA[key]["name"],
        effect_type="equip_mount",
        rarity="purple" if "shandian" in key else "orange",
        effect_payload=copy.deepcopy(previous.EQUIPMENT_PAYLOADS[key]),
    )
    item = base
    if affix:
        item = ItemTemplate.objects.create(
            key=equipment_affix_key(key, affix),
            name=f"敏捷之{base.name}",
            effect_type="equip_mount",
            rarity=base.rarity,
            effect_payload=build_equipment_affix_payload(base, affix),
        )
    inventory = InventoryItem.objects.create(manor=manor, template=item, quantity=3, bound_quantity=1)
    worn = GearTemplate.objects.create(key=item.key, **build_gear_template_defaults(item, slot="mount"))
    gear = GearItem.objects.create(manor=manor, guest=guest, template=worn)
    updates = set()
    apply_template_stats_to_guest(guest, worn, 1, updates)
    guest.current_hp = health
    guest.save()
    orphan_base = SimpleNamespace(
        key=base.key,
        name=base.name,
        rarity=base.rarity,
        effect_payload=base.effect_payload,
    )
    orphan_item = SimpleNamespace(
        key=equipment_affix_key(key, "defense"),
        name=f"防御之{base.name}",
        rarity=base.rarity,
        effect_payload=build_equipment_affix_payload(orphan_base, "defense"),
    )
    orphan = GearTemplate.objects.create(
        key=orphan_item.key,
        **build_gear_template_defaults(orphan_item, slot="mount"),
    )
    counts = (ItemTemplate.objects.count(), InventoryItem.objects.count(), GearTemplate.objects.count())

    migration.upgrade_guild_mounts(apps, SimpleNamespace(connection=connection))

    for row in (guest, base, item, inventory, worn, gear, orphan):
        row.refresh_from_db()
    expected = copy.deepcopy(migration.EQUIPMENT_PAYLOADS[key])
    assert base.effect_payload == expected
    if affix:
        expected = build_equipment_affix_payload(base, affix)
    assert item.effect_payload == expected
    assert base.rarity == item.rarity == worn.rarity == orphan.rarity == "orange"
    assert item.price == 65000 and item.storage_space == 250
    assert worn.required_level == orphan.required_level == 80
    assert inventory.template_id == item.pk
    assert (inventory.quantity, inventory.bound_quantity) == (3, 1)
    assert gear.template_id == worn.pk and gear.guest_id == guest.pk
    assert guest.allocated_force == 17
    for stat, field in previous.STAT_FIELDS.items():
        assert getattr(guest, field) == naked[field] + expected.get(stat, 0)
    assert guest.current_hp == min(health, guest.max_hp)
    assert counts == (ItemTemplate.objects.count(), InventoryItem.objects.count(), GearTemplate.objects.count())
    snapshot = {field: getattr(guest, field) for field in previous.STAT_FIELDS.values()}
    migration.upgrade_guild_mounts(apps, SimpleNamespace(connection=connection))
    guest.refresh_from_db()
    assert snapshot == {field: getattr(guest, field) for field in snapshot}
