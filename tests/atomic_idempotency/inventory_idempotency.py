import pytest
from django.db import transaction

from core.exceptions import InsufficientStockError, ItemNotFoundError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory.core import (
    add_item_to_inventory,
    add_item_to_inventory_locked,
    consume_inventory_item,
    consume_inventory_item_locked,
)
from gameplay.services.manor.core import ensure_manor
from trade.models import FrozenGoldBar


@pytest.mark.django_db
def test_consume_inventory_item_is_safe_with_stale_instances(django_user_model):
    user = django_user_model.objects.create_user(username="inv_stale", password="pass12345")
    manor = ensure_manor(user)

    tpl = ItemTemplate.objects.create(
        key="inv_stale_item",
        name="并发测试道具",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=tpl,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    item_a = InventoryItem.objects.select_related("template").get(pk=item.pk)
    item_b = InventoryItem.objects.select_related("template").get(pk=item.pk)

    consume_inventory_item(item_a, 1)
    with pytest.raises(InsufficientStockError):
        consume_inventory_item(item_b, 1)

    assert not InventoryItem.objects.filter(pk=item.pk).exists()


@pytest.mark.django_db
def test_consume_inventory_item_by_key_is_safe_when_row_disappears(django_user_model):
    user = django_user_model.objects.create_user(username="inv_key_stale", password="pass12345")
    manor = ensure_manor(user)

    tpl = ItemTemplate.objects.create(
        key="inv_key_stale_item",
        name="键扣除道具",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
    )
    add_item_to_inventory(manor, tpl.key, 1)

    consume_inventory_item(manor, tpl.key, 1)
    with pytest.raises(InsufficientStockError):
        consume_inventory_item(manor, tpl.key, 1)


@pytest.mark.django_db
def test_consume_inventory_item_direct_instance_rejects_frozen_gold_bars(django_user_model):
    user = django_user_model.objects.create_user(username="inv_direct_frozen_gold", password="pass12345")
    manor = ensure_manor(user)

    tpl = ItemTemplate.objects.create(
        key="gold_bar",
        name="金条",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
    )
    item = InventoryItem.objects.create(
        manor=manor,
        template=tpl,
        quantity=10,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    FrozenGoldBar.objects.create(
        manor=manor,
        amount=10,
        reason=FrozenGoldBar.Reason.AUCTION_BID,
        is_frozen=True,
    )

    with pytest.raises(InsufficientStockError):
        consume_inventory_item(item, 1)

    item.refresh_from_db()
    assert item.quantity == 10


@pytest.mark.django_db
def test_consume_inventory_item_rejects_unsaved_item_instance(django_user_model):
    user = django_user_model.objects.create_user(username="inv_unsaved_item", password="pass12345")
    manor = ensure_manor(user)

    tpl = ItemTemplate.objects.create(
        key="inv_unsaved_item_tpl",
        name="未保存道具",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
    )
    unsaved = InventoryItem(
        manor=manor,
        template=tpl,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with pytest.raises(ItemNotFoundError, match="物品不存在"):
        consume_inventory_item(unsaved, 1)


@pytest.mark.django_db(transaction=True)
def test_consume_inventory_item_locked_rejects_unsaved_item_instance(django_user_model):
    user = django_user_model.objects.create_user(username="inv_unsaved_locked", password="pass12345")
    manor = ensure_manor(user)

    tpl = ItemTemplate.objects.create(
        key="inv_unsaved_locked_tpl",
        name="未保存锁定道具",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
    )
    unsaved = InventoryItem(
        manor=manor,
        template=tpl,
        quantity=1,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with transaction.atomic():
        with pytest.raises(ItemNotFoundError, match="物品不存在"):
            consume_inventory_item_locked(unsaved, 1)


@pytest.mark.django_db(transaction=True)
def test_add_item_to_inventory_locked_requires_positive_quantity(django_user_model):
    user = django_user_model.objects.create_user(username="inv_add_positive_locked", password="pass12345")
    manor = ensure_manor(user)

    tpl = ItemTemplate.objects.create(
        key="inv_add_positive_locked_tpl",
        name="加库存校验道具",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
    )

    with transaction.atomic():
        with pytest.raises(AssertionError, match="requires positive quantity"):
            add_item_to_inventory_locked(manor, tpl.key, 0)


@pytest.mark.django_db
def test_add_item_to_inventory_requires_positive_quantity(django_user_model):
    user = django_user_model.objects.create_user(username="inv_add_positive", password="pass12345")
    manor = ensure_manor(user)

    tpl = ItemTemplate.objects.create(
        key="inv_add_positive_tpl",
        name="加库存包装校验道具",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
    )

    with pytest.raises(AssertionError, match="requires positive quantity"):
        add_item_to_inventory(manor, tpl.key, 0)
