from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from core.exceptions import GameError, InsufficientStockError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services import luanwu_shop
from gameplay.services.buildings.blueprint_catalog import BlueprintCatalogEntry
from gameplay.services.inventory.core import TREASURY_BLOCKED_ITEM_KEYS
from gameplay.services.manor.core import ensure_manor


def _create_item_template(key: str, name: str, **overrides) -> ItemTemplate:
    defaults = {
        "effect_type": ItemTemplate.EffectType.TOOL,
        "rarity": "blue",
        "tradeable": True,
    }
    defaults.update(overrides)
    template, _created = ItemTemplate.objects.update_or_create(
        key=key,
        defaults={"name": name, **defaults},
    )
    return template


@pytest.mark.django_db
def test_luanwu_shop_page_shows_products_and_warehouse_coin_balance(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="luanwu_shop_page", password="pass123")
    manor = ensure_manor(user)
    coin = _create_item_template("chunqiu_coin", "春秋币", effect_type=ItemTemplate.EffectType.RESOURCE)
    _create_item_template("fangdajing", "放大镜")
    _create_item_template("mission_card", "任务卡")
    _create_item_template("recruitment_card", "招募卡")
    blueprint = _create_item_template("blueprint_shop_device", "机关猫图纸", rarity="purple")
    InventoryItem.objects.create(manor=manor, template=coin, quantity=12)
    monkeypatch.setattr(luanwu_shop, "get_device_blueprint_templates", lambda: [blueprint])

    browser = Client()
    assert browser.login(username="luanwu_shop_page", password="pass123")

    response = browser.get(reverse("gameplay:luanwu_shop"))

    assert response.status_code == 200
    assert response.context["chunqiu_coin_quantity"] == 12
    assert "乱舞商城" in response.content.decode()
    assert "放大镜" in response.content.decode()
    assert "任务卡" in response.content.decode()
    assert "招募卡" in response.content.decode()
    assert "×3" in response.content.decode()
    assert "用于增加每日招募次数" in response.content.decode()
    assert "机关图纸宝箱" in response.content.decode()
    assert "随机池内共" not in response.content.decode()
    assert "查看可能获得的图纸" not in response.content.decode()
    assert "items/large.png" in response.content.decode()
    assert "items/chunqiu_coin.png" in response.content.decode()
    assert "今日可兑换" not in response.content.decode()
    assert "秘市货架" not in response.content.decode()
    assert "查看仓库" not in response.content.decode()
    assert "12" in response.content.decode()


@pytest.mark.django_db
def test_luanwu_shop_fixed_purchase_consumes_warehouse_coins_and_grants_pack(django_user_model):
    user = django_user_model.objects.create_user(username="luanwu_shop_fixed", password="pass123")
    manor = ensure_manor(user)
    coin = _create_item_template("chunqiu_coin", "春秋币", effect_type=ItemTemplate.EffectType.RESOURCE)
    magnifying_glass = _create_item_template("fangdajing", "放大镜")
    InventoryItem.objects.create(manor=manor, template=coin, quantity=3)

    result = luanwu_shop.purchase_luanwu_shop_item(manor, "fangdajing")

    assert result["total_cost"] == 1
    assert result["granted_items"] == {"fangdajing": 10}
    assert InventoryItem.objects.get(manor=manor, template=coin).quantity == 2
    assert InventoryItem.objects.get(manor=manor, template=magnifying_glass).quantity == 10


@pytest.mark.django_db
def test_luanwu_shop_recruitment_card_purchase_grants_three_cards_per_coin(django_user_model):
    user = django_user_model.objects.create_user(username="luanwu_shop_recruitment_card", password="pass123")
    manor = ensure_manor(user)
    coin = _create_item_template("chunqiu_coin", "春秋币", effect_type=ItemTemplate.EffectType.RESOURCE)
    recruitment_card = _create_item_template("recruitment_card", "招募卡")
    InventoryItem.objects.create(manor=manor, template=coin, quantity=2)

    result = luanwu_shop.purchase_luanwu_shop_item(manor, "recruitment_card")

    assert result["total_cost"] == 1
    assert result["granted_items"] == {"recruitment_card": 3}
    assert InventoryItem.objects.get(manor=manor, template=coin).quantity == 1
    assert InventoryItem.objects.get(manor=manor, template=recruitment_card).quantity == 3


@pytest.mark.django_db
def test_luanwu_shop_purchase_view_redirects_with_success_message(django_user_model):
    user = django_user_model.objects.create_user(username="luanwu_shop_view", password="pass123")
    manor = ensure_manor(user)
    coin = _create_item_template("chunqiu_coin", "春秋币", effect_type=ItemTemplate.EffectType.RESOURCE)
    magnifying_glass = _create_item_template("fangdajing", "放大镜")
    InventoryItem.objects.create(manor=manor, template=coin, quantity=1)
    browser = Client()
    assert browser.login(username="luanwu_shop_view", password="pass123")

    response = browser.post(
        reverse("gameplay:purchase_luanwu_shop_item"),
        {"product_key": "fangdajing", "quantity": "1"},
    )

    assert response.status_code == 302
    assert response.url == reverse("gameplay:luanwu_shop")
    assert InventoryItem.objects.get(manor=manor, template=magnifying_glass).quantity == 10
    assert "购买成功" in "".join(message.message for message in response.wsgi_request._messages)


@pytest.mark.django_db
def test_luanwu_shop_random_device_blueprint_selects_only_device_blueprints(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="luanwu_shop_random", password="pass123")
    manor = ensure_manor(user)
    coin = _create_item_template("chunqiu_coin", "春秋币", effect_type=ItemTemplate.EffectType.RESOURCE)
    blueprint_a = _create_item_template("blueprint_shop_device_a", "机关甲图纸", rarity="green")
    blueprint_b = _create_item_template("blueprint_shop_device_b", "机关乙图纸", rarity="purple")
    result_a = _create_item_template("equip_shop_device_a", "机关甲", effect_type="equip_device", rarity="green")
    result_b = _create_item_template("equip_shop_device_b", "机关乙", effect_type="equip_device", rarity="purple")
    weapon_result = _create_item_template(
        "equip_shop_weapon",
        "测试长剑",
        effect_type="equip_weapon",
        rarity="blue",
    )
    InventoryItem.objects.create(manor=manor, template=coin, quantity=10)
    monkeypatch.setattr(
        luanwu_shop,
        "load_blueprint_catalog",
        lambda: {
            blueprint_a.key: BlueprintCatalogEntry(
                key=blueprint_a.key,
                rarity=blueprint_a.rarity,
                result_key=result_a.key,
                result_rarity=result_a.rarity,
            ),
            blueprint_b.key: BlueprintCatalogEntry(
                key=blueprint_b.key,
                rarity=blueprint_b.rarity,
                result_key=result_b.key,
                result_rarity=result_b.rarity,
            ),
            "blueprint_shop_weapon": BlueprintCatalogEntry(
                key="blueprint_shop_weapon",
                rarity="blue",
                result_key=weapon_result.key,
                result_rarity=weapon_result.rarity,
            ),
        },
    )

    class FixedChoice:
        def choice(self, values):
            assert values == [blueprint_a.key, blueprint_b.key]
            return blueprint_b.key

    result = luanwu_shop.purchase_luanwu_shop_item(
        manor,
        luanwu_shop.RANDOM_DEVICE_BLUEPRINT_PRODUCT_KEY,
        rng=FixedChoice(),
    )

    assert result["total_cost"] == 10
    assert result["granted_items"] == {blueprint_b.key: 1}
    assert not InventoryItem.objects.filter(manor=manor, template=coin).exists()
    assert InventoryItem.objects.get(manor=manor, template=blueprint_b).quantity == 1
    assert not InventoryItem.objects.filter(manor=manor, template=blueprint_a).exists()


@pytest.mark.django_db
def test_luanwu_shop_does_not_consume_treasury_coins(django_user_model):
    user = django_user_model.objects.create_user(username="luanwu_shop_treasury", password="pass123")
    manor = ensure_manor(user)
    coin = _create_item_template("chunqiu_coin", "春秋币", effect_type=ItemTemplate.EffectType.RESOURCE)
    magnifying_glass = _create_item_template("fangdajing", "放大镜")
    treasury_coin = InventoryItem.objects.create(
        manor=manor,
        template=coin,
        quantity=5,
        storage_location=InventoryItem.StorageLocation.TREASURY,
    )

    with pytest.raises(InsufficientStockError, match="春秋币"):
        luanwu_shop.purchase_luanwu_shop_item(manor, "fangdajing")

    treasury_coin.refresh_from_db()
    assert treasury_coin.quantity == 5
    assert not InventoryItem.objects.filter(manor=manor, template=magnifying_glass).exists()
    assert "chunqiu_coin" in TREASURY_BLOCKED_ITEM_KEYS


@pytest.mark.django_db
def test_luanwu_shop_rejects_quantity_above_server_limit(django_user_model):
    user = django_user_model.objects.create_user(username="luanwu_shop_quantity_limit", password="pass123")
    manor = ensure_manor(user)
    _create_item_template("chunqiu_coin", "春秋币", effect_type=ItemTemplate.EffectType.RESOURCE)
    _create_item_template("fangdajing", "放大镜")

    with pytest.raises(GameError, match="单次最多兑换"):
        luanwu_shop.purchase_luanwu_shop_item(
            manor,
            "fangdajing",
            luanwu_shop.LUANWU_SHOP_MAX_PURCHASE_QUANTITY + 1,
        )


@pytest.mark.django_db
def test_luanwu_shop_checks_currency_before_random_blueprint_selection(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="luanwu_shop_check_order", password="pass123")
    manor = ensure_manor(user)
    _create_item_template("chunqiu_coin", "春秋币", effect_type=ItemTemplate.EffectType.RESOURCE)
    _create_item_template("device_blueprint_a", "器械图纸甲", effect_type=ItemTemplate.EffectType.TOOL)

    called = False

    def _unexpected_selection(*, rng=None):
        nonlocal called
        called = True
        raise AssertionError("random blueprint selection must not run before affordability check")

    monkeypatch.setattr(luanwu_shop, "select_random_device_blueprint_key", _unexpected_selection)

    with pytest.raises(InsufficientStockError, match="春秋币"):
        luanwu_shop.purchase_luanwu_shop_item(
            manor,
            luanwu_shop.RANDOM_DEVICE_BLUEPRINT_PRODUCT_KEY,
        )

    assert called is False
