from types import SimpleNamespace

import pytest

from core.exceptions import ForgeOperationError
from gameplay.services.buildings.forge_decompose_helpers import (
    build_decomposable_equipment_option,
    roll_decompose_rewards,
)


def test_build_decomposable_equipment_option_applies_category_filter_and_labels():
    item = SimpleNamespace(
        quantity=3,
        template=SimpleNamespace(
            key="equip_custom_jian",
            name="自定义剑",
            rarity="green",
            effect_type="equip_weapon",
            equipment_effect_summary="武力+10",
        ),
    )

    option = build_decomposable_equipment_option(
        item,
        rarity_labels={"green": "绿色"},
        category_labels={"weapon": "武器"},
        infer_equipment_category=lambda key, effect_type: "sword" if key == "equip_custom_jian" else effect_type,
        to_decompose_category=lambda category: "weapon" if category == "sword" else category,
        category_filter="weapon",
    )

    assert option == {
        "key": "equip_custom_jian",
        "name": "自定义剑",
        "effect_summary": "武力+10",
        "rarity": "green",
        "rarity_label": "绿色",
        "quantity": 3,
        "effect_type": "equip_weapon",
        "category": "weapon",
        "category_name": "武器",
    }

    skipped = build_decomposable_equipment_option(
        item,
        rarity_labels={"green": "绿色"},
        category_labels={"helmet": "头盔"},
        infer_equipment_category=lambda *_args, **_kwargs: "sword",
        to_decompose_category=lambda _category: "weapon",
        category_filter="helmet",
    )
    assert skipped is None


def test_roll_decompose_rewards_uses_ranges_and_probability_hooks():
    config = {
        "supported_rarities": ["green"],
        "base_materials": {"green": {"tong": [2, 5], "xi": [1, 3]}},
        "chance_rewards": {"green": {"wood_essence": 0.8, "copper_essence": 0.2}},
    }
    float_rolls = iter([0.1, 0.9, 0.1, 0.1])

    rewards = roll_decompose_rewards(
        "green",
        2,
        config,
        randint_func=lambda a, _b: a,
        random_func=lambda: next(float_rolls),
    )

    assert rewards == {
        "tong": 4,
        "xi": 2,
        "wood_essence": 2,
        "copper_essence": 1,
    }


def test_roll_decompose_rewards_rejects_unsupported_rarity():
    with pytest.raises(ForgeOperationError, match="仅绿色及以上装备可分解"):
        roll_decompose_rewards(
            "black",
            1,
            {
                "supported_rarities": ["green"],
                "base_materials": {"green": {"tong": [1, 1]}},
                "chance_rewards": {"green": {}},
            },
        )
