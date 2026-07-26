import pytest

from core.config import TRADE
from gameplay.models import InventoryItem, ItemTemplate


@pytest.fixture
def tradeable_item_template(db):
    template, _ = ItemTemplate.objects.get_or_create(
        key="test_tradeable_item",
        defaults={
            "name": "测试可交易物品",
            "effect_type": "none",
            "tradeable": True,
            "price": 1000,
        },
    )
    return template


@pytest.fixture
def untradeable_item_template(db):
    template, _ = ItemTemplate.objects.get_or_create(
        key="test_untradeable_item",
        defaults={
            "name": "测试不可交易物品",
            "effect_type": "none",
            "tradeable": False,
            "price": 500,
        },
    )
    return template


@pytest.fixture
def seller_manor(django_user_model, tradeable_item_template):
    user = django_user_model.objects.create_user(username="seller", password="pass12345")
    from gameplay.services.manor.core import ensure_manor

    manor = ensure_manor(user)
    manor.silver = 100000
    manor.silver_capacity = 200000
    manor.prestige = TRADE.MARKET_MIN_PRESTIGE
    manor.save()
    InventoryItem.objects.create(
        manor=manor,
        template=tradeable_item_template,
        quantity=100,
        storage_location="warehouse",
    )
    return manor


@pytest.fixture
def buyer_manor(django_user_model):
    user = django_user_model.objects.create_user(username="buyer", password="pass12345")
    from gameplay.services.manor.core import ensure_manor

    manor = ensure_manor(user)
    manor.silver = 500000
    manor.silver_capacity = 1000000
    manor.prestige = TRADE.MARKET_MIN_PRESTIGE
    manor.save()
    return manor
