import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction

from core.exceptions import GameError, InsufficientStockError
from gameplay.models import InventoryItem, ItemTemplate, Manor
from gameplay.services.inventory.core import (
    add_item_to_inventory,
    add_item_to_inventory_locked,
    add_items_to_inventory_locked,
    consume_inventory_item_for_manor_locked,
    consume_inventory_item_locked,
    set_warehouse_grain_quantity_locked,
)
from gameplay.services.inventory.guest_items import use_xisuidan
from gameplay.services.manor.core import ensure_manor
from gameplay.services.manor.treasury import move_item_to_treasury, move_item_to_warehouse
from gameplay.services.raid.combat.loot import (
    _apply_loot,
    _build_loot_item_queryset,
    _build_weighted_loot_candidates,
    _grant_loot_items,
)
from guests.models import Guest, GuestTemplate


@pytest.fixture
def manor(django_user_model):
    return ensure_manor(django_user_model.objects.create_user(username="item_binding", password="pass"))


@pytest.mark.django_db
def test_same_key_mixes_binding_quantities_and_consumes_bound_first(manor):
    template = ItemTemplate.objects.create(key="binding_stock", name="库存道具", tradeable=True)
    item = add_item_to_inventory(manor, template.key, 10, is_bound=True)
    assert (item.quantity, item.bound_quantity, item.unbound_quantity) == (10, 10, 0)
    item = add_item_to_inventory(manor, template.key, 3, is_bound=False)
    assert InventoryItem.objects.filter(manor=manor, template=template).count() == 1
    assert (item.quantity, item.bound_quantity, item.unbound_quantity) == (13, 10, 3)
    with transaction.atomic():
        locked = InventoryItem.objects.select_for_update().get(pk=item.pk)
        locked.template = template
        assert consume_inventory_item_locked(locked, 6) == 6
        assert (locked.quantity, locked.bound_quantity, locked.unbound_quantity) == (7, 4, 3)
        assert consume_inventory_item_locked(locked, 5) == 4
    item.refresh_from_db()
    assert (item.quantity, item.bound_quantity, item.unbound_quantity) == (2, 0, 2)


@pytest.mark.django_db
def test_unbound_only_consumption_rejects_binding_and_preserves_it(manor):
    template = ItemTemplate.objects.create(key="binding_unbound_only", name="不可转移")
    item = add_item_to_inventory(manor, template.key, 7, is_bound=True)
    add_item_to_inventory(manor, template.key, 2, is_bound=False)
    with transaction.atomic():
        with pytest.raises(InsufficientStockError):
            consume_inventory_item_for_manor_locked(manor, template.key, 3, allow_bound=False)
        assert consume_inventory_item_for_manor_locked(manor, template.key, 2, allow_bound=False) == 0
    item.refresh_from_db()
    assert (item.quantity, item.bound_quantity) == (7, 7)


@pytest.mark.django_db
def test_template_default_and_explicit_grant_overrides(manor):
    template = ItemTemplate.objects.create(key="binding_default", name="默认绑定", default_bound=True)
    add_item_to_inventory(manor, template.key, 3)
    add_item_to_inventory(manor, template.key, 2, is_bound=False)
    with transaction.atomic():
        rows = add_items_to_inventory_locked(manor, {template.key: 4}, bound_quantities={template.key: 1})
    item = rows[template.key]
    assert (item.quantity, item.bound_quantity, item.unbound_quantity) == (9, 4, 5)


@pytest.mark.django_db
@pytest.mark.parametrize("bound", [-1, 3, True, 0.5])
def test_invalid_bound_grant_is_rejected_before_any_inventory_write(manor, bound):
    ItemTemplate.objects.create(key="binding_validation", name="验证道具")
    with transaction.atomic(), pytest.raises(AssertionError):
        add_items_to_inventory_locked(manor, {"binding_validation": 2}, bound_quantities={"binding_validation": bound})
    assert not InventoryItem.objects.filter(manor=manor, template__key="binding_validation").exists()


@pytest.mark.django_db
def test_bound_quantity_database_invariant_and_legacy_defaults(manor):
    template = ItemTemplate.objects.create(key="binding_legacy", name="旧道具")
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=3)
    assert item.bound_quantity == 0
    with transaction.atomic(), pytest.raises(IntegrityError):
        InventoryItem.objects.filter(pk=item.pk).update(bound_quantity=4)
    item.refresh_from_db()
    assert (item.quantity, item.bound_quantity) == (3, 0)


@pytest.mark.django_db
def test_strict_binding_blocks_storage_transfer_without_changing_stock(manor):
    template = ItemTemplate.objects.create(key="binding_treasury", name="绑定道具", default_bound=True)
    item = add_item_to_inventory(manor, template.key, 5)
    add_item_to_inventory(manor, template.key, 2, is_bound=False)
    with pytest.raises(InsufficientStockError):
        move_item_to_treasury(manor, item.pk, 3)
    move_item_to_treasury(manor, item.pk, 2)
    item.refresh_from_db()
    assert (item.quantity, item.bound_quantity) == (5, 5)
    treasury = InventoryItem.objects.get(manor=manor, template=template, storage_location="treasury")
    assert (treasury.quantity, treasury.bound_quantity) == (2, 0)
    move_item_to_warehouse(manor, treasury.pk, 2)
    item.refresh_from_db()
    assert (item.quantity, item.bound_quantity) == (7, 5)
    with transaction.atomic(), pytest.raises(GameError, match="绑定物品不可存入藏宝阁"):
        add_item_to_inventory_locked(manor, template.key, 1, storage_location="treasury", is_bound=True)


@pytest.mark.django_db
def test_grain_binding_shares_the_single_ledger(manor):
    grain, _ = ItemTemplate.objects.get_or_create(key="grain", defaults={"name": "粮食"})
    with transaction.atomic():
        set_warehouse_grain_quantity_locked(manor, 0, grain_template=grain)
        item = add_item_to_inventory_locked(manor, "grain", 8, is_bound=True)
        add_item_to_inventory_locked(manor, "grain", 5, is_bound=False)
        assert consume_inventory_item_for_manor_locked(manor, "grain", 6) == 6
    item.refresh_from_db()
    manor.refresh_from_db()
    assert (item.quantity, item.bound_quantity, item.unbound_quantity, manor.grain) == (7, 2, 5, 7)


@pytest.mark.django_db
def test_raid_can_only_take_unbound_items_and_grain(manor):
    template = ItemTemplate.objects.create(key="binding_raid_item", name="掠夺测试", tradeable=True, default_bound=True)
    item = add_item_to_inventory(manor, template.key, 7, is_bound=True)
    add_item_to_inventory(manor, template.key, 3, is_bound=False)
    grain, _ = ItemTemplate.objects.get_or_create(key="grain", defaults={"name": "粮食", "tradeable": True})
    with transaction.atomic():
        set_warehouse_grain_quantity_locked(manor, 0, grain_template=grain)
        add_item_to_inventory_locked(manor, "grain", 8, is_bound=True)
        add_item_to_inventory_locked(manor, "grain", 4, is_bound=False)
        locked = Manor.objects.select_for_update().get(pk=manor.pk)
        resources, items = _apply_loot(locked, {"grain": 2}, {template.key: 10, "grain": 20}, locked_manor=locked)
    assert resources == {"grain": 2}
    assert items == {template.key: 3, "grain": 2}
    item.refresh_from_db()
    remaining_grain = InventoryItem.objects.get(manor=manor, template=grain)
    assert (item.quantity, item.bound_quantity) == (7, 7)
    assert (remaining_grain.quantity, remaining_grain.bound_quantity) == (8, 8)
    assert _build_weighted_loot_candidates(_build_loot_item_queryset(manor)) == []
    _grant_loot_items(manor, {template.key: 2})
    item.refresh_from_db()
    assert (item.quantity, item.bound_quantity, item.unbound_quantity) == (9, 7, 2)


@pytest.mark.django_db
def test_bound_xisuidan_is_usable_and_keeps_unbound_stock(manor):
    template = ItemTemplate.objects.create(
        key="xisuidan", name="洗髓丹", effect_type="tool", effect_payload={"action": "reroll_growth"}
    )
    item = add_item_to_inventory(manor, template.key, 10, is_bound=True)
    add_item_to_inventory(manor, template.key, 3, is_bound=False)
    guest_template = GuestTemplate.objects.create(
        key="binding_xisui_guest", name="洗髓门客", rarity="blue", archetype="military", growth_range=[5, 9]
    )
    guest = Guest.objects.create(
        manor=manor,
        template=guest_template,
        level=2,
        force=100,
        intellect=100,
        defense_stat=100,
        agility=100,
        initial_force=100,
        initial_intellect=100,
        initial_defense=100,
        initial_agility=100,
    )
    use_xisuidan(manor, item, guest.pk)
    item.refresh_from_db()
    guest.refresh_from_db()
    assert (item.quantity, item.bound_quantity, item.unbound_quantity) == (12, 9, 3)
    assert guest.xisuidan_used == 1


@pytest.mark.django_db
def test_yaml_import_accepts_template_binding_default(tmp_path):
    path = tmp_path / "bound_items.json"
    path.write_text(json.dumps({"items": [{"key": "binding_import", "name": "导入绑定", "default_bound": True}]}))
    call_command("load_item_templates", file=str(path), stdout=StringIO())
    assert ItemTemplate.objects.get(key="binding_import").default_bound is True
