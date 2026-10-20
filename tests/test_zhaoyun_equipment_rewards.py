"""赵云名器、逐鹿剑意归属与张飞头像的增量同步回归。"""

from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory import use as inventory_use
from guests.models import GuestTemplate

rules = import_module("gameplay.migrations.0222_zhaoyun_equipment_rewards")
previous = import_module("gameplay.migrations.0221_chiyou_rewards_and_unique_guests")
FOURTH = "mission_chest_v3_zhanlong_wuhu_shizhen"
ZHUOLU = "mission_chest_v3_zhuolu_zhongyuan"
EQUIPMENT = ("equip_shuduanbaijinpao", "equip_longdanqiang")


def test_fourth_adds_two_orange_items_without_changing_existing_random_rewards():
    current = rules.ITEM_DEFAULTS[FOURTH]["effect_payload"]
    old = previous.ITEM_DEFAULTS[FOURTH]["effect_payload"]
    assert current["random_item_groups"][:-1] == old["random_item_groups"]
    group = current["random_item_groups"][-1]
    assert group["chance"] == 0.1
    assert group["min_quantity"] == group["max_quantity"] == 1
    assert group["choices"] == [{"item_key": key, "weight": 1} for key in EQUIPMENT]
    assert all(row["item_key"] != "xuanyuan_sword_intent" for row in current["item_rewards"])
    assert rules.ITEM_DEFAULTS[ZHUOLU]["effect_payload"]["random_item_groups"] == (
        previous.ITEM_DEFAULTS[ZHUOLU]["effect_payload"]["random_item_groups"]
    )
    for key, effect in zip(EQUIPMENT, ("equip_armor", "equip_weapon")):
        assert rules.ITEM_DEFAULTS[key]["rarity"] == "orange"
        assert rules.ITEM_DEFAULTS[key]["effect_type"] == effect


@pytest.fixture
def synced_rewards(db, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    bridge, _ = GuestTemplate.objects.update_or_create(
        key="task_zhanlong_zhangfei_bridge", defaults={"name": "张飞", "base_attack": 123}
    )
    rules.sync_zhaoyun_equipment_rewards(apps, SimpleNamespace(connection=connection))
    return bridge


@pytest.mark.django_db
def test_repeat_sync_updates_only_bridge_avatar_and_stores_compressed_images(synced_rewards):
    from django.core.files.storage import default_storage
    from PIL import Image

    unrelated, _ = ItemTemplate.objects.update_or_create(
        key="zhaoyun_unrelated_item", defaults={"name": "保留物品", "effect_payload": {"keep": 1}}
    )
    for _ in range(2):
        rules.sync_zhaoyun_equipment_rewards(apps, SimpleNamespace(connection=connection))
    synced_rewards.refresh_from_db()
    unrelated.refresh_from_db()
    assert synced_rewards.base_attack == 123
    assert str(synced_rewards.avatar) == rules.BRIDGE_AVATAR
    assert unrelated.effect_payload == {"keep": 1}
    assert rules.IMAGE_SOURCES[rules.BRIDGE_AVATAR] == "guests/monkey/任务/武047_男_张飞.png"
    for target in rules.IMAGE_SOURCES:
        with default_storage.open(target, "rb") as stored:
            with Image.open(stored) as image:
                assert image.format == "WEBP"
                assert max(image.size) <= (200 if target.startswith("items/") else 400)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "chest_key,expected", [(ZHUOLU, "xuanyuan_sword_intent"), *[(FOURTH, key) for key in EQUIPMENT]]
)
def test_actual_latest_chests_award_sword_intent_and_each_zhaoyun_equipment(
    synced_rewards, manor_factory, monkeypatch, chest_key, expected
):
    from tests.mission_reward_content_helpers import reward_item_keys

    manor, _ = manor_factory(username="zhaoyun_latest_rewards")
    chest = ItemTemplate.objects.get(key=chest_key)
    payload = deepcopy(chest.effect_payload)
    # 仅保留本次新增随机组，隔离既有图纸配套材料结算。
    if chest_key == FOURTH:
        payload["random_item_groups"] = payload["random_item_groups"][-1:]
        payload["blueprint_material_support"] = {}
        payload["blueprint_forging_requirements"] = {}
        chest.effect_payload = payload
        chest.save(update_fields=["effect_payload"])
    for key in reward_item_keys(payload):
        ItemTemplate.objects.get_or_create(key=key, defaults={"name": key})
    stock = InventoryItem.objects.create(manor=manor, template=chest, quantity=1, bound_quantity=1)
    monkeypatch.setattr(inventory_use.inventory_random, "random", lambda: 0.0)
    monkeypatch.setattr(
        inventory_use, "weighted_random_choice", lambda keys, weights, rng: expected if expected in keys else keys[0]
    )
    inventory_use.use_inventory_item(stock, manor)
    inventory = list(InventoryItem.objects.filter(manor=manor).select_related("template"))
    awarded = [
        row for row in inventory if row.template.effect_payload.get("equipment_base_key", row.template.key) == expected
    ]
    assert len(awarded) == 1
    assert awarded[0].quantity == awarded[0].bound_quantity == 1
    if chest_key == FOURTH:
        assert not any(row.template.key == "xuanyuan_sword_intent" for row in inventory)
    else:
        assert InventoryItem.objects.get(manor=manor, template__key="chiyou_card").quantity == 1
