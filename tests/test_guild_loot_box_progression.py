"""帮会宝箱的品质档位、实际开箱数量与绑定继承。"""

from fractions import Fraction
from pathlib import Path

import pytest
import yaml

from gameplay.management.commands.load_item_templates import _build_item_defaults
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.inventory import use as inventory_use
from gameplay.services.manor.core import ensure_manor

DATA = Path(__file__).resolve().parents[1] / "data"
ITEMS = {item["key"]: item for item in yaml.safe_load((DATA / "item_templates.yaml").read_text())["items"]}


def _draws(key, choices_field):
    payload = ITEMS[key]["effect_payload"]
    assert payload["skill_book_chance" if choices_field == "skill_book_choices" else "gear_chance"] == 1
    draws = [payload[choices_field]]
    for group in payload.get("random_item_groups", []):
        assert group["chance"] == group["min_quantity"] == group["max_quantity"] == 1
        draws.append(group["choices"])
    return draws


def test_high_skill_boxes_guarantee_increasing_numbers_of_purple_books():
    for key, count in [
        ("guild_skill_book_box_purple", 1),
        ("guild_skill_book_box_refined", 2),
        ("guild_skill_book_box_master", 3),
    ]:
        draws = _draws(key, "skill_book_choices")
        assert len(draws) == count
        assert all(ITEMS[entry["item_key"]]["rarity"] == "purple" for draw in draws for entry in draw)


def test_equipment_boxes_have_exact_orange_odds_and_exclude_exclusive_rewards():
    excluded = {
        item["effect_payload"]["result_item_key"]
        for item in ITEMS.values()
        if item.get("effect_payload", {}).get("collection_cost")
    } | {"equip_chiyoujia", "equip_chiyouzhanxue", "equip_longdanqiang", "equip_longhuangguan"}
    for key, count, orange_chance in [
        ("guild_gear_box_purple", 1, Fraction(0)),
        ("guild_gear_box_refined_purple", 1, Fraction(1, 5)),
        ("guild_gear_box_master", 2, Fraction(3, 10)),
    ]:
        draws = _draws(key, "gear_choices")
        assert len(draws) == count
        for draw in draws:
            total = sum(entry["weight"] for entry in draw)
            orange = sum(entry["weight"] for entry in draw if ITEMS[entry["item_key"]]["rarity"] == "orange")
            assert Fraction(orange, total) == orange_chance
            assert not {entry["item_key"] for entry in draw} & excluded
            assert all(ITEMS[entry["item_key"]]["rarity"] in {"purple", "orange"} for entry in draw)
    assert 1 - (1 - Fraction(3, 10)) ** 2 == Fraction(51, 100)


@pytest.mark.django_db
@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize(
    "key,expected_rarities",
    [
        ("guild_skill_book_box_green", ["green"]),
        ("guild_skill_book_box_blue", ["blue"]),
        ("guild_skill_book_box_purple", ["purple"]),
        ("guild_skill_book_box_refined", ["purple", "purple"]),
        ("guild_skill_book_box_master", ["purple", "purple", "purple"]),
        ("guild_gear_box_refined_purple", ["purple"]),
        ("guild_gear_box_refined_purple", ["orange"]),
        ("guild_gear_box_master", ["purple", "purple"]),
        ("guild_gear_box_master", ["orange", "orange"]),
        ("guild_gear_box_master", ["orange", "purple"]),
    ],
)
def test_actual_opening_grants_every_draw_and_preserves_binding(
    django_user_model, monkeypatch, key, expected_rarities, bound
):
    field = "skill_book_choices" if key.startswith("guild_skill") else "gear_choices"
    draws = _draws(key, field)
    reward_keys = {entry["item_key"] for draw in draws for entry in draw}
    for item_key in reward_keys | {key}:
        ItemTemplate.objects.update_or_create(key=item_key, defaults=_build_item_defaults(ITEMS[item_key]))
    user = django_user_model.objects.create_user(username="guild_box_opening")
    manor = ensure_manor(user)
    chest = InventoryItem.objects.create(
        manor=manor, template=ItemTemplate.objects.get(key=key), quantity=1, bound_quantity=int(bound)
    )
    desired = iter(expected_rarities)

    def choose(choices, _weights, _rng):
        rarity = next(desired)
        return next(item_key for item_key in choices if ITEMS[item_key]["rarity"] == rarity)

    monkeypatch.setattr(inventory_use, "weighted_random_choice", choose)
    monkeypatch.setattr(inventory_use.inventory_random, "random", lambda: 0.0)
    monkeypatch.setattr("gameplay.services.equipment_affixes.AFFIX_CHANCE", 0)
    result = inventory_use.use_inventory_item(chest)
    assert len(result["rewards"]) == len(expected_rarities)
    assert sum(
        InventoryItem.objects.filter(manor=manor, template__key__in=reward_keys).values_list("quantity", flat=True)
    ) == len(expected_rarities)
    granted = list(InventoryItem.objects.filter(manor=manor, template__key__in=reward_keys).select_related("template"))
    actual = sorted(row.template.rarity for row in granted for _ in range(row.quantity))
    assert actual == sorted(expected_rarities)
    assert all(row.bound_quantity == (row.quantity if bound else 0) for row in granted)
    assert not InventoryItem.objects.filter(pk=chest.pk).exists()
