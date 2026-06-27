from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.utils import timezone

from core.exceptions import InsufficientSilverError, ItemNotConfiguredError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from trade.models import ShopPurchaseLog, ShopSellLog, ShopStock
from trade.services.shop_config import ShopItemConfig
from trade.services.shop_service import (
    _get_category,
    _normalize_effect_type,
    buy_item,
    get_sellable_inventory,
    get_shop_items_for_display,
    sell_item,
)


def _set_manor_silver(manor, amount: int) -> None:
    manor.silver = amount
    manor.resource_updated_at = timezone.now()
    manor.save(update_fields=["silver", "resource_updated_at"])


@pytest.mark.django_db
def test_normalize_effect_type_and_category():
    assert _normalize_effect_type("manor_rename") == ItemTemplate.EffectType.TOOL
    assert _normalize_effect_type(ItemTemplate.EffectType.LOOT_BOX) == ItemTemplate.EffectType.TOOL
    assert _normalize_effect_type(ItemTemplate.EffectType.TOOL) == ItemTemplate.EffectType.TOOL
    assert _get_category(ItemTemplate.EffectType.LOOT_BOX) == "道具"
    assert _get_category("equip_weapon") == "武器"
    assert _get_category("unknown") == "其他"


@pytest.mark.django_db
def test_get_shop_items_for_display_uses_current_stock(monkeypatch):
    template = ItemTemplate.objects.create(
        key="shop_display_item",
        name="展示物品",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
        tradeable=False,
        price=12,
    )
    ShopStock.objects.create(item_key=template.key, current_stock=3)

    monkeypatch.setattr(
        "trade.services.shop_service.get_shop_config",
        lambda: [ShopItemConfig(item_key=template.key, price=None, buy_price=None, stock=10, daily_refresh=False)],
    )

    items = get_shop_items_for_display()
    assert len(items) == 1
    assert items[0].key == template.key
    assert items[0].stock == 3
    assert items[0].available is True
    assert items[0].price == 18


@pytest.mark.django_db
def test_get_shop_items_for_display_defaults_buy_price_to_150_percent_sell_price(monkeypatch):
    template = ItemTemplate.objects.create(
        key="shop_display_default_markup_item",
        name="默认加价物品",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
        tradeable=False,
        price=975,
    )

    monkeypatch.setattr(
        "trade.services.shop_service.get_shop_config",
        lambda: [ShopItemConfig(item_key=template.key, price=None, buy_price=None, stock=-1, daily_refresh=False)],
    )

    items = get_shop_items_for_display()
    assert len(items) == 1
    assert items[0].price == 1463


@pytest.mark.django_db
def test_get_shop_items_for_display_prefers_buy_price(monkeypatch):
    template = ItemTemplate.objects.create(
        key="shop_display_buy_price_item",
        name="展示买入价物品",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
        tradeable=False,
        price=12,
    )

    monkeypatch.setattr(
        "trade.services.shop_service.get_shop_config",
        lambda: [ShopItemConfig(item_key=template.key, price=7, buy_price=30, stock=-1, daily_refresh=False)],
    )

    items = get_shop_items_for_display()
    assert len(items) == 1
    assert items[0].price == 30
    assert items[0].stock == -1
    assert items[0].stock_display == "无限"


@pytest.mark.django_db
def test_get_shop_items_for_display_tolerates_invalid_price_and_stock(monkeypatch):
    template = ItemTemplate.objects.create(
        key="shop_display_invalid_item",
        name="展示异常物品",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
        tradeable=False,
        price=12,
    )

    monkeypatch.setattr(
        "trade.services.shop_service.get_shop_config",
        lambda: [
            SimpleNamespace(
                item_key=template.key,
                price="bad",
                stock="bad",
                daily_refresh=False,
                is_unlimited=False,
            )
        ],
    )

    items = get_shop_items_for_display()
    assert len(items) == 1
    assert items[0].key == template.key
    assert items[0].price == 0
    assert items[0].stock == 0
    assert items[0].available is False


@pytest.mark.django_db
def test_buy_item_decrements_stock_and_increments_inventory(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="shop_buy", password="pass12345")
    manor = ensure_manor(user)
    _set_manor_silver(manor, 1000)

    template = ItemTemplate.objects.create(
        key="shop_buy_item",
        name="购买物品",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
        tradeable=False,
        price=10,
    )

    config = ShopItemConfig(item_key=template.key, price=5, buy_price=None, stock=4, daily_refresh=False)
    monkeypatch.setattr("trade.services.shop_service.get_shop_item_config", lambda *_args, **_kwargs: config)

    result = buy_item(manor, template.key, 2)
    assert result == {"item_name": template.name, "quantity": 2, "total_cost": 16}

    manor.refresh_from_db()
    assert manor.silver == 984

    stock = ShopStock.objects.get(item_key=template.key)
    assert stock.current_stock == 2

    inv = InventoryItem.objects.get(
        manor=manor,
        template=template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert inv.quantity == 2

    log = ShopPurchaseLog.objects.get(manor=manor, item_key=template.key)
    assert log.quantity == 2
    assert log.unit_price == 8
    assert log.total_cost == 16


@pytest.mark.django_db
def test_buy_item_prefers_buy_price_without_changing_sell_price(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="shop_buy_price", password="pass12345")
    manor = ensure_manor(user)
    _set_manor_silver(manor, 1000)

    template = ItemTemplate.objects.create(
        key="shop_buy_price_item",
        name="买入价物品",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
        tradeable=False,
        price=10,
    )

    config = ShopItemConfig(item_key=template.key, price=5, buy_price=30, stock=-1, daily_refresh=False)
    monkeypatch.setattr("trade.services.shop_service.get_shop_item_config", lambda *_args, **_kwargs: config)

    result = buy_item(manor, template.key, 2)
    assert result == {"item_name": template.name, "quantity": 2, "total_cost": 60}

    log = ShopPurchaseLog.objects.get(manor=manor, item_key=template.key)
    assert log.unit_price == 30
    assert log.total_cost == 60

    InventoryItem.objects.filter(
        manor=manor,
        template=template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).update(quantity=1)

    sell_result = sell_item(manor, template.key, 1)
    assert sell_result == {"item_name": template.name, "quantity": 1, "total_income": 10}


@pytest.mark.django_db
def test_buy_item_rejects_negative_price_config(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="shop_buy_bad_price", password="pass12345")
    manor = ensure_manor(user)
    _set_manor_silver(manor, 1000)

    template = ItemTemplate.objects.create(
        key="shop_buy_bad_price_item",
        name="异常价格物品",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
        tradeable=False,
        price=10,
    )

    config = ShopItemConfig(item_key=template.key, price=-5, buy_price=None, stock=4, daily_refresh=False)
    monkeypatch.setattr("trade.services.shop_service.get_shop_item_config", lambda *_args, **_kwargs: config)

    with pytest.raises(ItemNotConfiguredError, match="价格配置异常"):
        buy_item(manor, template.key, 2)

    manor.refresh_from_db()
    assert manor.silver == 1000
    assert not ShopPurchaseLog.objects.filter(manor=manor, item_key=template.key).exists()


@pytest.mark.django_db
def test_buy_item_translates_insufficient_silver_to_domain_error(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="shop_buy_no_silver", password="pass12345")
    manor = ensure_manor(user)
    _set_manor_silver(manor, 1)

    template = ItemTemplate.objects.create(
        key="shop_buy_no_silver_item",
        name="买不起的物品",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
        tradeable=False,
        price=10,
    )

    config = ShopItemConfig(item_key=template.key, price=5, buy_price=None, stock=4, daily_refresh=False)
    monkeypatch.setattr("trade.services.shop_service.get_shop_item_config", lambda *_args, **_kwargs: config)

    with pytest.raises(InsufficientSilverError, match="银两不足"):
        buy_item(manor, template.key, 1)


@pytest.mark.django_db
def test_sell_item_grants_silver_and_clears_zero_inventory(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="shop_sell", password="pass12345")
    manor = ensure_manor(user)
    _set_manor_silver(manor, 0)

    template = ItemTemplate.objects.create(
        key="shop_sell_item",
        name="出售物品",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
        tradeable=False,
        price=7,
    )

    config = ShopItemConfig(item_key=template.key, price=None, buy_price=None, stock=-1, daily_refresh=False)
    monkeypatch.setattr("trade.services.shop_service.get_shop_item_config", lambda *_args, **_kwargs: config)

    InventoryItem.objects.create(
        manor=manor,
        template=template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=1,
    )

    result = sell_item(manor, template.key, 1)
    assert result == {"item_name": template.name, "quantity": 1, "total_income": 7}

    manor.refresh_from_db()
    assert manor.silver == 7

    assert not InventoryItem.objects.filter(
        manor=manor,
        template=template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).exists()

    log = ShopSellLog.objects.get(manor=manor, item_key=template.key)
    assert log.quantity == 1
    assert log.unit_price == 7
    assert log.total_income == 7


@pytest.mark.django_db
def test_sell_item_disables_production_sync_on_resource_grant(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="shop_sell_sync_flag", password="pass12345")
    manor = ensure_manor(user)

    template = ItemTemplate.objects.create(
        key="shop_sell_sync_flag_item",
        name="出售标记物品",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
        tradeable=False,
        price=7,
    )

    InventoryItem.objects.create(
        manor=manor,
        template=template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=1,
    )

    observed: dict[str, object] = {}

    def _fake_grant_resources_locked(locked_manor, rewards, note, reason, *, sync_production=True):
        observed["sync_production"] = sync_production
        observed["rewards"] = rewards
        locked_manor.silver += rewards["silver"]
        locked_manor.save(update_fields=["silver"])
        return rewards, {}

    monkeypatch.setattr("trade.services.shop_service.grant_resources_locked", _fake_grant_resources_locked)

    result = sell_item(manor, template.key, 1)

    assert result == {"item_name": template.name, "quantity": 1, "total_income": 7}
    assert observed == {"sync_production": False, "rewards": {"silver": 7}}


@pytest.mark.django_db
def test_get_sellable_inventory_loads_shop_config_once(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="shop_sellable_perf", password="pass12345")
    manor = ensure_manor(user)

    override_template = ItemTemplate.objects.create(
        key="shop_sellable_override",
        name="覆盖价物品",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=False,
        tradeable=False,
        price=3,
    )
    default_template = ItemTemplate.objects.create(
        key="shop_sellable_default",
        name="默认价物品",
        effect_type=ItemTemplate.EffectType.MEDICINE,
        is_usable=False,
        tradeable=False,
        price=9,
    )

    InventoryItem.objects.create(
        manor=manor,
        template=override_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=2,
    )
    InventoryItem.objects.create(
        manor=manor,
        template=default_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        quantity=2,
    )

    calls = {"count": 0}

    def _fake_shop_config():
        calls["count"] += 1
        return [ShopItemConfig(item_key=override_template.key, price=15, buy_price=None, stock=-1, daily_refresh=False)]

    monkeypatch.setattr("trade.services.shop_service.get_shop_config", _fake_shop_config)

    sellable_items = get_sellable_inventory(manor)
    prices = {row.inventory_item.template.key: row.sell_price for row in sellable_items}

    assert calls["count"] == 1
    assert prices[override_template.key] == 15
    assert prices[default_template.key] == 9
