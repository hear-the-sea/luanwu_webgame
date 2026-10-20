"""加权奖励每组只抽一次，单项数量覆盖不影响装备和绑定发放。"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.template.loader import render_to_string

from core.exceptions import ItemNotConfiguredError
from core.utils.yaml_validators import validate_item_templates
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory import use as inventory_use
from gameplay.services.manor.core import ensure_manor
from gameplay.views.mission_helpers import build_chest_reward_preview

CHOICES = [
    {"item_key": "choice_test_armor", "weight": 10, "quantity": 1},
    {"item_key": "choice_test_coin", "weight": 20, "quantity": 2},
    {"item_key": "choice_test_weapon", "weight": 70, "quantity": 1},
]


def payload(choices=None):
    return {
        "item_rewards": [{"item_key": "choice_test_card", "min_quantity": 1, "max_quantity": 1}],
        "random_item_groups": [
            {
                "chance": 1,
                "min_quantity": 1,
                "max_quantity": 1,
                "choices": deepcopy(CHOICES if choices is None else choices),
            }
        ],
        "show_reward_probabilities": True,
    }


@pytest.mark.parametrize("value", [0, -1, True, False, 1.2, "2", None, float("inf")])
def test_choice_quantity_must_be_positive_integer_in_runtime_and_yaml(value):
    config = payload([{"item_key": "reward", "weight": 1, "quantity": value}])
    with pytest.raises(ItemNotConfiguredError):
        inventory_use._normalize_random_item_groups(config["random_item_groups"], field_name="random_item_groups")
    assert not validate_item_templates(
        {"items": [{"key": "bad_choice_box", "name": "坏数量箱", "effect_type": "loot_box", "effect_payload": config}]}
    ).is_valid


def test_choice_quantity_is_optional_and_does_not_mutate_existing_config():
    config = [{"chance": 1, "min_quantity": 3, "max_quantity": 7, "choices": [{"item_key": "reward", "weight": 1}]}]
    original = deepcopy(config)
    assert inventory_use._normalize_random_item_groups(config, field_name="random_item_groups") == config
    assert config == original


def test_preview_shows_choice_quantity_and_conditional_weight_probability():
    box = SimpleNamespace(name="蚩尤宝箱", effect_payload=payload())
    templates = {
        "choice_test_card": SimpleNamespace(name="蚩尤卡"),
        "choice_test_armor": SimpleNamespace(name="铠甲"),
        "choice_test_coin": SimpleNamespace(name="春秋币"),
        "choice_test_weapon": SimpleNamespace(name="武器"),
    }
    preview = build_chest_reward_preview(box, templates)
    assert preview["guaranteed"] == ["蚩尤卡 ×1"]
    assert preview["possible"] == ["武器 ×1（70%）", "春秋币 ×2（20%）", "铠甲 ×1（10%）"]
    html = render_to_string("gameplay/includes/mission_chest_preview.html", {"preview": preview})
    assert "春秋币 ×2（20%）" in html
    assert "100%" not in html
    box.effect_payload["show_reward_probabilities"] = False
    assert "%" not in str(build_chest_reward_preview(box, templates))


def test_preview_multiplies_group_chance_by_choice_weight():
    box = SimpleNamespace(name="概率箱", effect_payload=payload())
    box.effect_payload["random_item_groups"][0]["chance"] = 0.5
    preview = build_chest_reward_preview(box, {"choice_test_coin": SimpleNamespace(name="春秋币")})
    assert "春秋币 ×2（10%）" in preview["possible"]


@pytest.fixture
def stock(db, django_user_model):
    manor = ensure_manor(django_user_model.objects.create_user(username="choice_quantities"))
    templates = {}
    for key, name, effect_type in [
        ("choice_test_card", "蚩尤卡", "tool"),
        ("choice_test_armor", "铠甲", "equip_armor"),
        ("choice_test_coin", "春秋币", "tool"),
        ("choice_test_weapon", "武器", "equip_weapon"),
    ]:
        templates[key] = ItemTemplate.objects.create(
            key=key, name=name, effect_type=effect_type, rarity="orange", effect_payload={"force": 60, "defense": 40}
        )
    box = ItemTemplate.objects.create(
        key="choice_quantity_box", name="蚩尤宝箱", effect_type="loot_box", is_usable=True, effect_payload=payload()
    )
    entry = InventoryItem.objects.create(manor=manor, template=box, quantity=3, bound_quantity=2)
    return manor, templates, entry


@pytest.mark.parametrize(
    "roll,key,expected_quantity",
    [
        (0, "choice_test_armor", 1),
        (10, "choice_test_armor", 1),
        (10.001, "choice_test_coin", 2),
        (30, "choice_test_coin", 2),
        (30.001, "choice_test_weapon", 1),
        (100, "choice_test_weapon", 1),
    ],
)
def test_one_weighted_draw_grants_exactly_one_choice_plus_card(stock, monkeypatch, roll, key, expected_quantity):
    manor, templates, entry = stock
    monkeypatch.setattr(inventory_use.inventory_random, "random", lambda: 0.99)
    monkeypatch.setattr(inventory_use.inventory_random, "uniform", lambda *_: roll)
    weighted = Mock(wraps=inventory_use.weighted_random_choice)
    monkeypatch.setattr(inventory_use, "weighted_random_choice", weighted)
    inventory_use.use_inventory_item(entry)
    assert weighted.call_count == 1
    reward = InventoryItem.objects.get(manor=manor, template=templates[key])
    assert (reward.quantity, reward.bound_quantity) == (expected_quantity, expected_quantity)
    assert InventoryItem.objects.get(manor=manor, template=templates["choice_test_card"]).quantity == 1
    assert (
        InventoryItem.objects.filter(manor=manor, template__in=[templates[c["item_key"]] for c in CHOICES]).count() == 1
    )
    entry.refresh_from_db()
    assert (entry.quantity, entry.bound_quantity) == (2, 1)


def test_repeated_openings_draw_once_each_and_preserve_binding(stock, monkeypatch):
    manor, templates, entry = stock
    draws = iter([9, 11, 11])
    monkeypatch.setattr(inventory_use.inventory_random, "random", lambda: 0.99)
    monkeypatch.setattr(inventory_use.inventory_random, "uniform", lambda *_: next(draws))
    weighted = Mock(wraps=inventory_use.weighted_random_choice)
    monkeypatch.setattr(inventory_use, "weighted_random_choice", weighted)
    for _ in range(3):
        inventory_use.use_inventory_item(entry)
    assert weighted.call_count == 3
    for key, quantity, bound in [("choice_test_card", 3, 2), ("choice_test_armor", 1, 1), ("choice_test_coin", 4, 2)]:
        reward = InventoryItem.objects.get(manor=manor, template=templates[key])
        assert (reward.quantity, reward.bound_quantity) == (quantity, bound)
    assert not InventoryItem.objects.filter(pk=entry.pk).exists()


def test_choice_equipment_still_receives_real_affixes_and_inherits_binding(stock, monkeypatch):
    manor, templates, entry = stock
    monkeypatch.setattr(inventory_use.inventory_random, "random", lambda: 0.01)
    monkeypatch.setattr(inventory_use.inventory_random, "uniform", lambda *_: 9)
    inventory_use.use_inventory_item(entry)
    reward = InventoryItem.objects.get(manor=manor, template__effect_payload__equipment_base_key="choice_test_armor")
    assert (reward.quantity, reward.bound_quantity) == (1, 1)
    assert reward.template.effect_payload["equipment_affix"]
    assert reward.template.key != templates["choice_test_armor"].key
    assert not InventoryItem.objects.filter(manor=manor, template=templates["choice_test_coin"]).exists()


def test_missing_choice_quantity_keeps_existing_group_random_quantity(stock, monkeypatch):
    manor, templates, entry = stock
    config = payload([{"item_key": "choice_test_coin", "weight": 1}])
    config["random_item_groups"][0].update(min_quantity=3, max_quantity=7)
    entry.template.effect_payload = config
    entry.template.save(update_fields=["effect_payload"])
    monkeypatch.setattr(inventory_use.inventory_random, "random", lambda: 0.99)
    monkeypatch.setattr(inventory_use.inventory_random, "randint", lambda a, b: b)
    inventory_use.use_inventory_item(entry)
    assert InventoryItem.objects.get(manor=manor, template=templates["choice_test_coin"]).quantity == 7
