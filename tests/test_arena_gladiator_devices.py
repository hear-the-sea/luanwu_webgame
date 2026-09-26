from pathlib import Path

import pytest
import yaml
from PIL import Image

from core.exceptions import ArenaRewardLimitError
from gameplay.models import ArenaExchangeRecord, InventoryItem, ItemTemplate
from gameplay.services.arena.core import exchange_arena_reward
from gameplay.services.manor.core import ensure_manor
from guests.services.equipment_payloads import build_gear_template_preview

BASE_DIR = Path(__file__).resolve().parents[1]

GLADIATOR_DEVICES = (
    ("arena_bronze_gladiator_device", "equip_qingtongzhijiaodoushi", "green", 800, 2),
    ("arena_silver_gladiator_device", "equip_baiyinzhijiaodoushi", "blue", 1200, 1),
    ("arena_gold_gladiator_device", "equip_huangjinzhijiaodoushi", "purple", 2000, 1),
)


def _item_config(item_key: str) -> dict:
    items = yaml.safe_load((BASE_DIR / "data/item_templates.yaml").read_text(encoding="utf-8"))["items"]
    return next(item for item in items if item["key"] == item_key)


@pytest.mark.parametrize("reward_key,item_key,rarity,cost,weekly_limit", GLADIATOR_DEVICES)
def test_arena_gladiator_device_config_and_image(reward_key, item_key, rarity, cost, weekly_limit):
    rewards = yaml.safe_load((BASE_DIR / "data/arena_rewards.yaml").read_text(encoding="utf-8"))["rewards"]
    reward = next(row for row in rewards if row["key"] == reward_key)
    item = _item_config(item_key)

    assert reward["cost_coins"] == cost
    assert reward["weekly_limit"] == weekly_limit
    assert reward["rewards"]["items"] == {item_key: 1}
    assert item["effect_type"] == "equip_device"
    assert item["rarity"] == rarity
    preview = build_gear_template_preview(
        ItemTemplate(
            key=item_key,
            name=item["name"],
            effect_type=item["effect_type"],
            rarity=rarity,
            effect_payload=item["effect_payload"],
        )
    )
    assert preview is not None
    assert preview.slot == "device"

    image_path = BASE_DIR / "data/images/items" / item["image"]
    with Image.open(image_path) as image:
        assert image.width == image.height
        image.verify()


@pytest.mark.django_db
@pytest.mark.parametrize("reward_key,item_key,rarity,cost,weekly_limit", GLADIATOR_DEVICES)
def test_arena_gladiator_device_exchange_enforces_weekly_limit(
    django_user_model, reward_key, item_key, rarity, cost, weekly_limit
):
    user = django_user_model.objects.create_user(username=f"exchange_{rarity}_gladiator")
    manor = ensure_manor(user)
    item = _item_config(item_key)
    ItemTemplate.objects.create(
        key=item_key,
        name=item["name"],
        effect_type=item["effect_type"],
        rarity=rarity,
        effect_payload=item["effect_payload"],
    )
    manor.arena_coins = cost * (weekly_limit + 1)
    manor.save(update_fields=["arena_coins"])

    result = exchange_arena_reward(manor, reward_key, quantity=weekly_limit)

    assert result.total_cost == cost * weekly_limit
    assert result.granted_items == {item_key: weekly_limit}
    assert InventoryItem.objects.get(manor=manor, template__key=item_key).quantity == weekly_limit
    assert ArenaExchangeRecord.objects.get(manor=manor, reward_key=reward_key).quantity == weekly_limit
    with pytest.raises(ArenaRewardLimitError):
        exchange_arena_reward(manor, reward_key, quantity=1)
    manor.refresh_from_db()
    assert manor.arena_coins == cost
