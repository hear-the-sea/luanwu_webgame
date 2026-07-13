import pytest
from django.db import transaction

from core.exceptions import InsufficientSpaceError
from gameplay.models import Building, BuildingType, InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from gameplay.services.manor.treasury import get_treasury_capacity, move_item_to_treasury, move_item_to_warehouse


@pytest.mark.django_db
def test_move_item_to_treasury_rejects_non_positive_quantity(django_user_model):
    user = django_user_model.objects.create_user(username="treasury_move_qty_non_positive", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(key="treasury_move_qty_item", name="契约测试物品")
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=3,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with transaction.atomic(), pytest.raises(AssertionError, match="requires positive quantity"):
        move_item_to_treasury(manor, item.id, 0)


@pytest.mark.django_db
def test_move_item_to_warehouse_rejects_non_positive_quantity(django_user_model):
    user = django_user_model.objects.create_user(username="warehouse_move_qty_non_positive", password="pass123")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(key="warehouse_move_qty_item", name="契约测试物品")
    item = InventoryItem.objects.create(
        manor=manor,
        template=template,
        quantity=3,
        storage_location=InventoryItem.StorageLocation.TREASURY,
    )

    with transaction.atomic(), pytest.raises(AssertionError, match="requires positive quantity"):
        move_item_to_warehouse(manor, item.id, -1)


@pytest.mark.django_db
def test_over_capacity_treasury_rejects_deposits_but_allows_withdrawals(django_user_model):
    user = django_user_model.objects.create_user(username="treasury_over_capacity", password="pass123")
    manor = ensure_manor(user)
    treasury_type = BuildingType.objects.get(key="treasury")
    treasury = Building.objects.get(manor=manor, building_type=treasury_type)
    treasury.level = 1
    treasury.save(update_fields=["level"])
    protected_template = ItemTemplate.objects.create(
        key="treasury_over_capacity_protected",
        name="超额保护物品",
        storage_space=300,
    )
    deposit_template = ItemTemplate.objects.create(
        key="treasury_over_capacity_deposit",
        name="待存物品",
        storage_space=1,
    )
    protected_item = InventoryItem.objects.create(
        manor=manor,
        template=protected_template,
        quantity=2,
        storage_location=InventoryItem.StorageLocation.TREASURY,
    )
    deposit_item = InventoryItem.objects.create(
        manor=manor,
        template=deposit_template,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(InsufficientSpaceError):
        move_item_to_treasury(manor, deposit_item.id, 1)

    protected_item.refresh_from_db()
    deposit_item.refresh_from_db()
    assert protected_item.quantity == 2
    assert deposit_item.quantity == 1

    move_item_to_warehouse(manor, protected_item.id, 1)

    protected_item.refresh_from_db()
    warehouse_copy = InventoryItem.objects.get(
        manor=manor,
        template=protected_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert protected_item.quantity == 1
    assert warehouse_copy.quantity == 1


@pytest.mark.django_db
def test_treasury_capacity_uses_building_max_level(django_user_model):
    user = django_user_model.objects.create_user(username="treasury_level_cap", password="pass123")
    manor = ensure_manor(user)
    treasury = Building.objects.get(manor=manor, building_type__key="treasury")
    treasury.level = 25
    treasury.save(update_fields=["level"])

    assert get_treasury_capacity(manor) == 10_000
