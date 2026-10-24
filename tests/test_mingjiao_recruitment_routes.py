from io import StringIO
from pathlib import Path

import pytest
import yaml
from django.core.management import call_command
from PIL import Image

from core.exceptions import TradeValidationError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.arena.core import exchange_arena_reward
from gameplay.services.inventory.core import add_item_to_inventory
from gameplay.services.manor.core import ensure_manor
from guests.models import GuestTemplate
from trade.services.market_service import create_listing, get_tradeable_inventory
from trade.services.shop_service import buy_item

BASE_DIR = Path(__file__).resolve().parents[1]


def _create_items():
    entries = yaml.safe_load((BASE_DIR / "data/item_templates.yaml").read_text())["items"]
    keys = {"mingjiao_holy_fire_token", "mingjiao_fire_seed", "xiaozhao_recruit_scroll"}
    for entry in entries:
        if entry["key"] in keys:
            ItemTemplate.objects.create(**{key: value for key, value in entry.items() if key != "image"})


@pytest.mark.django_db
def test_fire_seed_exchange_charges_per_item_and_binds_rewards(django_user_model):
    _create_items()
    manor = ensure_manor(django_user_model.objects.create_user(username="mingjiao_exchange"))
    manor.arena_coins = 150
    manor.save(update_fields=["arena_coins"])

    result = exchange_arena_reward(manor, "mingjiao_fire_seed_exchange", quantity=3)

    manor.refresh_from_db()
    item = InventoryItem.objects.get(manor=manor, template__key="mingjiao_fire_seed")
    assert result.total_cost == 60
    assert manor.arena_coins == 90
    assert (item.quantity, item.bound_quantity) == (3, 3)


@pytest.mark.django_db
@pytest.mark.parametrize("item_key", ["mingjiao_holy_fire_token", "mingjiao_fire_seed"])
def test_mingjiao_materials_cannot_be_listed_even_if_inventory_is_unbound(django_user_model, item_key):
    _create_items()
    manor = ensure_manor(django_user_model.objects.create_user(username="mingjiao_market"))
    item = add_item_to_inventory(manor, item_key, 10, is_bound=False)

    assert not get_tradeable_inventory(manor).filter(pk=item.pk).exists()
    with pytest.raises(TradeValidationError, match="不可交易"):
        create_listing(manor, item_key, 1, 1, 86400)
    item.refresh_from_db()
    assert item.quantity == 10


@pytest.mark.django_db
def test_xiaozhao_scroll_can_be_purchased_from_public_shop(django_user_model):
    _create_items()
    manor = ensure_manor(django_user_model.objects.create_user(username="xiaozhao_shop"))
    manor.silver = 100000000
    manor.save(update_fields=["silver"])

    buy_item(manor, "xiaozhao_recruit_scroll", 1)

    assert InventoryItem.objects.get(manor=manor, template__key="xiaozhao_recruit_scroll").quantity == 1


@pytest.mark.django_db
def test_scoped_sync_installs_complete_route_without_touching_other_templates(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    unrelated = ItemTemplate.objects.create(key="unrelated_mingjiao_sync", name="保留的物品", price=123)

    call_command("sync_mingjiao_recruitment", stdout=StringIO())

    assert GuestTemplate.objects.get(key="hero_jinyong_0215").rarity == "purple"
    for key in ("mingjiao_holy_fire_token", "mingjiao_fire_seed", "xiaozhao_recruit_scroll"):
        item = ItemTemplate.objects.get(key=key)
        assert item.image.storage.exists(item.image.name)
        with item.image.open("rb") as file:
            with Image.open(file) as image:
                assert image.width == image.height == 200
        if key != "xiaozhao_recruit_scroll":
            assert item.tradeable is False
            assert item.default_bound is True
    unrelated.refresh_from_db()
    assert unrelated.price == 123
