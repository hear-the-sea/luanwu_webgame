from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse

from core.exceptions import GameError, InsufficientStockError, ItemNotFoundError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services import luanwu_shop
from gameplay.services.manor.core import ensure_manor

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop_world(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="bound_shop_player", password="pass123")
    manor = ensure_manor(user)
    coin, _ = ItemTemplate.objects.update_or_create(
        key="chunqiu_coin", defaults={"name": "春秋币", "effect_type": "resource"}
    )
    reward, _ = ItemTemplate.objects.update_or_create(
        key="bound_shop_reward", defaults={"name": "兑换奖励", "default_bound": True}
    )
    product = luanwu_shop.LuanwuShopProductConfig(
        key="reward", name=None, description=None, price=3, item_key=reward.key, reward_quantity=2
    )
    monkeypatch.setattr(
        luanwu_shop,
        "load_luanwu_shop_config",
        lambda: luanwu_shop.LuanwuShopConfig(currency_item_key=coin.key, products=(product,)),
    )
    return user, manor, coin, reward


@pytest.mark.parametrize("bound_coins,expected_bound", [(0, 0), (1, 2), (3, 2), (4, 4), (9, 6)])
def test_batch_exchange_binds_only_rewards_paid_with_bound_currency(shop_world, bound_coins, expected_bound):
    _, manor, coin, reward = shop_world
    wallet = InventoryItem.objects.create(manor=manor, template=coin, quantity=9, bound_quantity=bound_coins)
    result = luanwu_shop.purchase_luanwu_shop_item(manor, "reward", 3)
    received = InventoryItem.objects.get(manor=manor, template=reward)
    assert not InventoryItem.objects.filter(pk=wallet.pk).exists()
    assert (received.quantity, received.bound_quantity) == (6, expected_bound)
    assert result["bound_currency_consumed"] == bound_coins
    assert result["bound_granted_items"] == ({reward.key: expected_bound} if expected_bound else {})


def test_multiple_exchanges_keep_remaining_unbound_currency_unbound(shop_world):
    _, manor, coin, reward = shop_world
    wallet = InventoryItem.objects.create(manor=manor, template=coin, quantity=12, bound_quantity=4)
    luanwu_shop.purchase_luanwu_shop_item(manor, "reward")
    wallet.refresh_from_db()
    assert (wallet.quantity, wallet.bound_quantity) == (9, 1)
    luanwu_shop.purchase_luanwu_shop_item(manor, "reward", 3)
    received = InventoryItem.objects.get(manor=manor, template=reward)
    assert (received.quantity, received.bound_quantity) == (8, 4)


def test_random_blueprints_record_binding_for_each_paid_share(shop_world, monkeypatch):
    _, manor, coin, reward = shop_world
    second, _ = ItemTemplate.objects.update_or_create(key="bound_shop_other_blueprint", defaults={"name": "另一图纸"})
    product = luanwu_shop.LuanwuShopProductConfig(
        key="blueprint", name="随机图纸", description="随机兑换", price=3, is_random_device_blueprint=True
    )
    monkeypatch.setattr(
        luanwu_shop,
        "load_luanwu_shop_config",
        lambda: luanwu_shop.LuanwuShopConfig(currency_item_key=coin.key, products=(product,)),
    )
    selections = iter([reward.key, second.key, reward.key])
    monkeypatch.setattr(luanwu_shop, "select_random_device_blueprint_key", lambda **kwargs: next(selections))
    InventoryItem.objects.create(manor=manor, template=coin, quantity=9, bound_quantity=4)
    result = luanwu_shop.purchase_luanwu_shop_item(manor, "blueprint", 3)
    received = InventoryItem.objects.get(manor=manor, template=reward)
    other_received = InventoryItem.objects.get(manor=manor, template=second)
    assert (received.quantity, received.bound_quantity) == (2, 1)
    assert (other_received.quantity, other_received.bound_quantity) == (1, 1)
    assert result["bound_granted_items"] == {reward.key: 1, second.key: 1}


def test_missing_reward_rolls_back_total_and_bound_currency(shop_world):
    _, manor, coin, reward = shop_world
    wallet = InventoryItem.objects.create(manor=manor, template=coin, quantity=9, bound_quantity=4)
    reward.delete()
    with pytest.raises(ItemNotFoundError):
        luanwu_shop.purchase_luanwu_shop_item(manor, "reward", 3)
    wallet.refresh_from_db()
    assert (wallet.quantity, wallet.bound_quantity) == (9, 4)


def test_random_pool_error_rolls_back_currency_and_all_rewards(shop_world, monkeypatch):
    _, manor, coin, reward = shop_world
    product = luanwu_shop.LuanwuShopProductConfig(
        key="blueprint", name="随机图纸", description="随机兑换", price=3, is_random_device_blueprint=True
    )
    monkeypatch.setattr(
        luanwu_shop,
        "load_luanwu_shop_config",
        lambda: luanwu_shop.LuanwuShopConfig(currency_item_key=coin.key, products=(product,)),
    )

    def fail_selection(**kwargs):
        raise GameError("随机图纸池异常")

    monkeypatch.setattr(luanwu_shop, "select_random_device_blueprint_key", fail_selection)
    wallet = InventoryItem.objects.create(manor=manor, template=coin, quantity=9, bound_quantity=4)
    with pytest.raises(GameError, match="随机图纸池异常"):
        luanwu_shop.purchase_luanwu_shop_item(manor, "blueprint", 3)
    wallet.refresh_from_db()
    assert (wallet.quantity, wallet.bound_quantity) == (9, 4)
    assert not InventoryItem.objects.filter(manor=manor, template=reward).exists()


def test_insufficient_mixed_currency_does_not_consume_stock(shop_world):
    _, manor, coin, reward = shop_world
    wallet = InventoryItem.objects.create(manor=manor, template=coin, quantity=8, bound_quantity=4)
    with pytest.raises(InsufficientStockError):
        luanwu_shop.purchase_luanwu_shop_item(manor, "reward", 3)
    wallet.refresh_from_db()
    assert (wallet.quantity, wallet.bound_quantity) == (8, 4)
    assert not InventoryItem.objects.filter(manor=manor, template=reward).exists()


def test_shop_context_and_purchase_message_show_binding(shop_world, monkeypatch):
    user, manor, coin, _ = shop_world
    InventoryItem.objects.create(manor=manor, template=coin, quantity=9, bound_quantity=4)
    monkeypatch.setattr(luanwu_shop, "get_device_blueprint_templates", lambda: [])
    context = luanwu_shop.build_luanwu_shop_context(manor)
    assert context["chunqiu_coin_quantity"] == 9
    assert context["chunqiu_coin_bound_quantity"] == 4
    assert context["chunqiu_coin_unbound_quantity"] == 5
    client = Client()
    client.force_login(user)
    page = client.get(reverse("gameplay:luanwu_shop"))
    assert page.status_code == 200
    assert "绑定 4 · 非绑定 5" in page.content.decode()
    response = client.post(
        reverse("gameplay:purchase_luanwu_shop_item"),
        {"product_key": "reward", "quantity": 3, "currency_mode": "allow_bound"},
    )
    assert response.status_code == 302
    text = " ".join(str(message) for message in get_messages(response.wsgi_request))
    assert "其中绑定物品：兑换奖励×4" in text


def test_unbound_only_preserves_bound_coins_and_grants_unbound_rewards(shop_world):
    _, manor, coin, reward = shop_world
    wallet = InventoryItem.objects.create(manor=manor, template=coin, quantity=10, bound_quantity=4)
    result = luanwu_shop.purchase_luanwu_shop_item(manor, "reward", 2, allow_bound=False)
    wallet.refresh_from_db()
    assert (wallet.quantity, wallet.bound_quantity) == (4, 4)
    received = InventoryItem.objects.get(manor=manor, template=reward)
    assert (received.quantity, received.bound_quantity) == (4, 0)
    assert result["bound_currency_consumed"] == 0


@pytest.mark.parametrize("mode", [None, "unbound_only", "invalid"])
def test_view_cannot_silently_consume_bound_coins(shop_world, mode):
    user, manor, coin, reward = shop_world
    wallet = InventoryItem.objects.create(manor=manor, template=coin, quantity=9, bound_quantity=4)
    client = Client()
    client.force_login(user)
    data = {"product_key": "reward", "quantity": 2}
    if mode is not None:
        data["currency_mode"] = mode
    response = client.post(reverse("gameplay:purchase_luanwu_shop_item"), data)
    assert response.status_code == 302
    wallet.refresh_from_db()
    assert (wallet.quantity, wallet.bound_quantity) == (9, 4)
    assert not InventoryItem.objects.filter(manor=manor, template=reward).exists()
    text = " ".join(str(message) for message in get_messages(response.wsgi_request))
    assert ("请选择春秋币使用方式" if mode == "invalid" else "非绑定春秋币不足") in text


def test_view_unbound_only_succeeds_with_mixed_wallet(shop_world):
    user, manor, coin, reward = shop_world
    wallet = InventoryItem.objects.create(manor=manor, template=coin, quantity=9, bound_quantity=4)
    client = Client()
    client.force_login(user)
    response = client.post(
        reverse("gameplay:purchase_luanwu_shop_item"),
        {"product_key": "reward", "quantity": 1, "currency_mode": "unbound_only"},
    )
    assert response.status_code == 302
    wallet.refresh_from_db()
    assert (wallet.quantity, wallet.bound_quantity) == (6, 4)
    received = InventoryItem.objects.get(manor=manor, template=reward)
    assert (received.quantity, received.bound_quantity) == (2, 0)


def test_random_blueprints_unbound_only_preserves_bound_wallet(shop_world, monkeypatch):
    _, manor, coin, reward = shop_world
    product = luanwu_shop.LuanwuShopProductConfig(
        key="blueprint", name="随机图纸", description="随机兑换", price=3, is_random_device_blueprint=True
    )
    monkeypatch.setattr(
        luanwu_shop,
        "load_luanwu_shop_config",
        lambda: luanwu_shop.LuanwuShopConfig(currency_item_key=coin.key, products=(product,)),
    )
    monkeypatch.setattr(luanwu_shop, "select_random_device_blueprint_key", lambda **kwargs: reward.key)
    wallet = InventoryItem.objects.create(manor=manor, template=coin, quantity=10, bound_quantity=4)
    result = luanwu_shop.purchase_luanwu_shop_item(manor, "blueprint", 2, allow_bound=False)
    wallet.refresh_from_db()
    assert (wallet.quantity, wallet.bound_quantity) == (4, 4)
    assert result["bound_granted_items"] == {}
    received = InventoryItem.objects.get(manor=manor, template=reward)
    assert (received.quantity, received.bound_quantity) == (2, 0)
