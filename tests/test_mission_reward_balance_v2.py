from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.exceptions import InsufficientStockError, ItemNotConfiguredError, ItemNotFoundError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory import use as inventory_use
from gameplay.services.manor.core import ensure_manor
from tests.mission_reward_content_helpers import assert_fixed_reward

DATA = Path(__file__).resolve().parents[1] / "data"
ITEMS = {x["key"]: x for x in yaml.safe_load((DATA / "item_templates.yaml").read_text())["items"]}
MISSIONS = yaml.safe_load((DATA / "mission_templates.yaml").read_text())["missions"]
BOXES = [ITEMS[key] for mission in MISSIONS for key in mission["drop_table"] if key != "silver"]
HISTORICAL_BOXES = [ITEMS["mission_chest_" + mission["key"]] for mission in MISSIONS]
TOKENS = [x for x in ITEMS.values() if x.get("effect_payload", {}).get("collection_cost")]


def test_v2_has_daily_fengshen_progression_and_bounded_collection_goals():
    assert len(BOXES) == 32 and len(TOKENS) == 6
    assert len(HISTORICAL_BOXES) == 32
    assert all(x["effect_payload"]["reward_balance_version"] == 3 for x in BOXES)
    assert all(x["effect_payload"]["reward_balance_version"] == 2 for x in HISTORICAL_BOXES)
    for prefix in ("mission_chest_", "mission_chest_v3_"):
        assert_fixed_reward(ITEMS[prefix + "biyou_candeng"]["effect_payload"], "wanyin_flag_fragment", 1)
        assert_fixed_reward(ITEMS[prefix + "shier_xianyin"]["effect_payload"], "yuxu_broken_seal", 1)
    for token in TOKENS:
        p = token["effect_payload"]
        assert p["required_items"] == {token["key"]: p["collection_cost"] - 1}
        assert 30 <= p["collection_cost"] <= 40
        assert token["tradeable"] is False
        assert ITEMS[p["result_item_key"]]["rarity"] == "orange"
        assert ITEMS[p["result_item_key"]]["effect_type"] != "equip_device"
        for boxes in (BOXES, HISTORICAL_BOXES):
            assert (
                sum(
                    any(
                        r["item_key"] == token["key"] and r["min_quantity"] == r["max_quantity"] == 1
                        for r in b["effect_payload"]["item_rewards"]
                    )
                    for b in boxes
                )
                == 1
            )


def test_material_support_matches_actual_device_recipe_without_rounding_inflation():
    recipes = {x["blueprint_key"]: x for x in yaml.safe_load((DATA / "forge_blueprints.yaml").read_text())["recipes"]}
    for box in BOXES + HISTORICAL_BOXES:
        payload = box["effect_payload"]
        keys = {r["item_key"] for r in payload["item_rewards"]} | {
            c["item_key"] for g in payload["random_item_groups"] for c in g["choices"]
        }
        expected = {
            key
            for key in keys & recipes.keys()
            if ITEMS[recipes[key]["result_item_key"]]["effect_type"] == "equip_device"
        }
        support = payload["blueprint_material_support"]
        assert set(support) == expected
        assert payload["blueprint_forging_requirements"] == {key: recipes[key]["required_forging"] for key in expected}
        for key, entries in support.items():
            amounts = {x["item_key"]: x["min_quantity"] for x in entries}
            assert amounts == {k: q // 2 for k, q in recipes[key]["costs"].items() if q // 2}
            assert all(x["min_quantity"] == x["max_quantity"] for x in entries)


@pytest.mark.parametrize("token", TOKENS, ids=lambda t: t["key"])
@pytest.mark.django_db
def test_collection_consumes_exact_threshold_and_can_repeat(token, django_user_model):
    manor = ensure_manor(django_user_model.objects.create_user(username=token["key"]))
    payload = token["effect_payload"]
    threshold = payload["collection_cost"]
    target = ITEMS[payload["result_item_key"]]
    result_template = ItemTemplate.objects.create(
        key=target["key"], name=target["name"], effect_type=target["effect_type"]
    )
    template = ItemTemplate.objects.create(
        key=token["key"], name=token["name"], effect_type="tool", is_usable=True, effect_payload=payload
    )
    item = InventoryItem.objects.create(manor=manor, template=template, quantity=threshold - 1)
    with pytest.raises(InsufficientStockError):
        inventory_use.use_inventory_item(item, manor)
    item.refresh_from_db()
    assert item.quantity == threshold - 1
    item.quantity = 2 * threshold
    item.save(update_fields=["quantity"])
    inventory_use.use_inventory_item(item, manor)
    item.refresh_from_db()
    assert item.quantity == threshold
    assert InventoryItem.objects.get(manor=manor, template=result_template).quantity == 1
    inventory_use.use_inventory_item(item, manor)
    assert not InventoryItem.objects.filter(pk=item.pk).exists()
    assert InventoryItem.objects.get(manor=manor, template=result_template).quantity == 2


@pytest.mark.parametrize("roll,expected", [(0.8, 0), (0.1, 3)])
@pytest.mark.django_db
def test_materials_only_follow_actual_blueprint_hit(roll, expected, django_user_model, monkeypatch):
    manor = ensure_manor(django_user_model.objects.create_user(username="material_support_" + str(expected)))
    bp = ItemTemplate.objects.create(key="support_blueprint", name="器械图纸", effect_type="tool")
    mat = ItemTemplate.objects.create(key="support_material", name="锻造材料")
    box = ItemTemplate.objects.create(
        key="support_box",
        name="支持箱",
        effect_type="loot_box",
        is_usable=True,
        effect_payload={
            "random_item_groups": [{"chance": 0.4, "choices": [{"item_key": bp.key, "weight": 1}]}],
            "blueprint_material_support": {bp.key: [{"item_key": mat.key, "min_quantity": 3, "max_quantity": 3}]},
        },
    )
    item = InventoryItem.objects.create(manor=manor, template=box, quantity=1)
    monkeypatch.setattr(inventory_use.inventory_random, "random", lambda: roll)
    inventory_use.use_inventory_item(item, manor)
    assert sum(InventoryItem.objects.filter(manor=manor, template=mat).values_list("quantity", flat=True)) == expected


@pytest.mark.django_db
def test_missing_support_material_preserves_box_and_blueprint(django_user_model):
    manor = ensure_manor(django_user_model.objects.create_user(username="missing_support"))
    bp = ItemTemplate.objects.create(key="missing_support_bp", name="图纸")
    box = ItemTemplate.objects.create(
        key="missing_support_box",
        name="箱子",
        effect_type="loot_box",
        is_usable=True,
        effect_payload={
            "item_rewards": [{"item_key": bp.key, "min_quantity": 1}],
            "blueprint_material_support": {bp.key: [{"item_key": "missing_support_mat", "min_quantity": 1}]},
        },
    )
    item = InventoryItem.objects.create(manor=manor, template=box, quantity=1)
    with pytest.raises(ItemNotConfiguredError):
        inventory_use.use_inventory_item(item, manor)
    item.refresh_from_db()
    assert item.quantity == 1
    assert not InventoryItem.objects.filter(manor=manor, template=bp).exists()


@pytest.mark.parametrize("fail_during_grant", [False, True])
@pytest.mark.django_db
def test_material_support_scales_and_rolls_back_atomically(fail_during_grant, django_user_model, monkeypatch):
    manor = ensure_manor(django_user_model.objects.create_user(username="support_atomic_" + str(fail_during_grant)))
    bp = ItemTemplate.objects.create(key="atomic_support_bp", name="图纸")
    material = ItemTemplate.objects.create(key="atomic_support_mat", name="材料")
    box = ItemTemplate.objects.create(
        key="atomic_support_box",
        name="箱子",
        effect_type="loot_box",
        is_usable=True,
        effect_payload={
            "item_rewards": [{"item_key": bp.key, "min_quantity": 2, "max_quantity": 2}],
            "blueprint_material_support": {bp.key: [{"item_key": material.key, "min_quantity": 3}]},
        },
    )
    item = InventoryItem.objects.create(manor=manor, template=box, quantity=1)
    if fail_during_grant:
        grant = inventory_use.add_items_to_inventory_locked

        def partially_grant_then_fail(*args, **kwargs):
            grant(*args, **kwargs)
            raise ItemNotFoundError("simulated material failure")

        monkeypatch.setattr(inventory_use, "add_items_to_inventory_locked", partially_grant_then_fail)
        with pytest.raises(ItemNotConfiguredError):
            inventory_use.use_inventory_item(item, manor)
        item.refresh_from_db()
        assert item.quantity == 1
        assert not InventoryItem.objects.filter(manor=manor, template__in=[bp, material]).exists()
    else:
        inventory_use.use_inventory_item(item, manor)
        assert InventoryItem.objects.get(manor=manor, template=bp).quantity == 2
        assert InventoryItem.objects.get(manor=manor, template=material).quantity == 6
        assert not InventoryItem.objects.filter(pk=item.pk).exists()
