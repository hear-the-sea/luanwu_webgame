from copy import deepcopy
from importlib import import_module
from pathlib import Path

import pytest
import yaml

rules = import_module("gameplay.migrations.0200_reduce_chest_card_rewards")
ITEMS = {
    row["key"]: row
    for row in yaml.safe_load((Path(__file__).resolve().parents[1] / "data/item_templates.yaml").read_text())["items"]
}
BASELINES = {
    "work_chest_small": (0.5, 0.5),
    "work_chest_medium": (2 / 3, 1),
    "work_chest_large": (1, 2),
    "mid_autumn_mooncake": (0.05, 0.05),
    "mission_chest_kasukabe_daisakusen": (0.08, 0.08),
    "mission_chest_v3_kasukabe_daisakusen": (0.08, 0.08),
}


@pytest.mark.parametrize("key", BASELINES)
def test_each_chest_card_probability_and_expected_quantity_fall_by_at_least_half(key):
    payload = ITEMS[key]["effect_payload"]
    groups = [g for g in payload["random_item_groups"] if g["choices"] == [{"item_key": "mission_card", "weight": 1}]]
    assert len(groups) == 1
    group = groups[0]
    assert group["min_quantity"] == group["max_quantity"] == 1
    chance, expected = BASELINES[key]
    assert group["chance"] == pytest.approx(chance / 4)
    assert group["chance"] <= chance / 2
    assert group["chance"] <= expected / 2
    assert rules.rebalance_payload(key, payload) == payload


def test_card_rebalance_preserves_other_rewards_and_is_idempotent():
    original = {
        "resources": {"grain": 500},
        "item_rewards": [
            {"item_key": "medicine", "min_quantity": 3, "max_quantity": 8},
            {"item_key": "mission_card", "min_quantity": 1, "max_quantity": 3},
        ],
        "random_item_groups": [{"chance": 0.2, "choices": [{"item_key": "book", "weight": 1}]}],
        "gear_chance": 0.2,
    }
    before = deepcopy(original)
    result = rules.rebalance_payload("work_chest_large", original)
    assert original == before
    assert result["resources"] == original["resources"]
    assert result["gear_chance"] == original["gear_chance"]
    assert result["item_rewards"] == original["item_rewards"][:1]
    assert result["random_item_groups"][1:] == original["random_item_groups"]
    assert rules.rebalance_payload("work_chest_large", result) == result


@pytest.mark.django_db
@pytest.mark.parametrize("roll,expected", [(0.1249, 1), (0.125, 0), (0.9, 0)])
def test_loot_box_actual_card_probability_boundary_and_binding(django_user_model, monkeypatch, roll, expected):
    from gameplay.models import InventoryItem, ItemTemplate
    from gameplay.services.inventory.random_source import inventory_random
    from gameplay.services.inventory.use import use_inventory_item
    from gameplay.services.manor.core import ensure_manor

    manor = ensure_manor(django_user_model.objects.create_user(username="chest_card_boundary"))
    card, _ = ItemTemplate.objects.update_or_create(
        key="mission_card", defaults={"name": "任务卡", "effect_type": "tool"}
    )
    payload = {"random_item_groups": [ITEMS["work_chest_small"]["effect_payload"]["random_item_groups"][0]]}
    box = ItemTemplate.objects.create(
        key="card_boundary_chest",
        name="任务卡概率箱",
        effect_type="loot_box",
        is_usable=True,
        effect_payload=payload,
    )
    entry = InventoryItem.objects.create(manor=manor, template=box, quantity=1, bound_quantity=1)
    monkeypatch.setattr(inventory_random, "random", lambda: roll)
    result = use_inventory_item(entry)
    cards = InventoryItem.objects.filter(manor=manor, template=card).first()
    assert (cards.quantity if cards else 0) == expected
    assert (cards.bound_quantity if cards else 0) == expected
    assert ("任务卡" in result["_message"]) == bool(expected)


@pytest.mark.django_db
def test_targeted_sync_keeps_unrelated_rewards_and_does_not_apply_skill_migration():
    from django.core.management import call_command

    from gameplay.models import ItemTemplate

    payload = {
        "item_rewards": [{"item_key": "mission_card", "min_quantity": 0, "max_quantity": 2}],
        "silver_min": 12345,
    }
    chest, _ = ItemTemplate.objects.update_or_create(
        key="work_chest_medium", defaults={"name": "中箱", "effect_type": "loot_box", "effect_payload": payload}
    )
    call_command("sync_chest_card_rewards")
    chest.refresh_from_db()
    assert chest.effect_payload == payload
    call_command("sync_chest_card_rewards", apply=True)
    chest.refresh_from_db()
    assert chest.effect_payload == rules.rebalance_payload(chest.key, payload)
    call_command("sync_chest_card_rewards", apply=True)
    chest.refresh_from_db()
    assert chest.effect_payload["silver_min"] == 12345
