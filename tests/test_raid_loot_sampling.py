import random

import pytest
from django.contrib.auth import get_user_model

from battle.random_context import RNG_STREAM_LOOT, BattleRandomContext
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from gameplay.services.pvp_runtime.loot import (
    calculate_item_loot_draw_count,
    draw_weighted_item_loot,
    draw_weighted_item_loot_with_grain_fill,
)
from gameplay.services.raid.combat.loot import _calculate_loot


def test_draw_weighted_item_loot_uses_inventory_quantity_as_weight(monkeypatch):
    candidates = [
        {"item_key": "scarce", "remaining_quantity": 2, "storage_space": 1},
        {"item_key": "plentiful", "remaining_quantity": 8, "storage_space": 1},
    ]
    rng = random.Random(1)
    monkeypatch.setattr(rng, "randrange", lambda _stop: 2)

    loot_items = draw_weighted_item_loot(candidates, draw_count=1, capacity=1, rng=rng)

    assert loot_items == {"plentiful": 1}


def test_draw_weighted_item_loot_charges_one_thousandth_capacity_per_grain():
    candidates = [{"item_key": "grain", "remaining_quantity": 10_000, "storage_space": 1}]

    loot_items = draw_weighted_item_loot(candidates, draw_count=2_000, capacity=1, rng=random.Random(2))

    assert loot_items == {"grain": 1_000}


def test_draw_weighted_item_loot_batches_large_grain_stock():
    candidates = [{"item_key": "grain", "remaining_quantity": 100_000_000, "storage_space": 1}]

    loot_items = draw_weighted_item_loot(
        candidates,
        draw_count=20_000_000,
        capacity=30_000,
        rng=random.Random(3),
    )

    assert loot_items == {"grain": 20_000_000}


def test_draw_weighted_item_loot_batches_dominant_grain_with_sparse_other_stock(monkeypatch):
    candidates = [
        {"item_key": "gold_bar", "remaining_quantity": 1, "storage_space": 250},
        {"item_key": "grain", "remaining_quantity": 100_000_000, "storage_space": 1},
    ]
    random_calls = 0

    def choose_last(stop):
        nonlocal random_calls
        random_calls += 1
        if random_calls > 4:
            pytest.fail("主导库存未批量抽取")
        return stop - 1

    rng = random.Random(4)
    monkeypatch.setattr(rng, "randrange", choose_last)

    loot_items = draw_weighted_item_loot(
        candidates,
        draw_count=20_000_000,
        capacity=30_000,
        rng=rng,
    )

    assert loot_items == {"grain": 20_000_000}
    assert random_calls == 1


def test_grain_does_not_inflate_non_grain_quota_and_keeps_its_own_cap():
    loot_items = draw_weighted_item_loot_with_grain_fill(
        [
            {"item_key": "grain", "remaining_quantity": 10_000_000, "storage_space": 1},
            {"item_key": "gold_bar", "remaining_quantity": 5, "storage_space": 1},
            {"item_key": "red_ruby", "remaining_quantity": 5, "storage_space": 1},
        ],
        non_grain_ratio=0.20,
        grain_ratio=0.20,
        capacity=30_000,
        rng=random.Random(7),
    )

    assert sum(quantity for key, quantity in loot_items.items() if key != "grain") == 2
    assert loot_items["grain"] == 2_000_000


def test_grain_fill_is_batched_by_remaining_capacity():
    loot_items = draw_weighted_item_loot_with_grain_fill(
        [
            {"item_key": "grain", "remaining_quantity": 100_000, "storage_space": 1},
            {"item_key": "gold_bar", "remaining_quantity": 5, "storage_space": 1},
        ],
        non_grain_ratio=0.20,
        grain_ratio=1.0,
        capacity=10,
        rng=random.Random(8),
    )

    assert loot_items["gold_bar"] == 1
    assert loot_items["grain"] == 9_000


def test_twenty_percent_draw_can_all_land_on_gold_bars(monkeypatch):
    candidates = [
        {"item_key": "gold_bar", "remaining_quantity": 100, "storage_space": 250},
        {"item_key": "grain", "remaining_quantity": 100, "storage_space": 1},
    ]
    rng = random.Random(5)
    monkeypatch.setattr(rng, "randrange", lambda _stop: 0)

    loot_items = draw_weighted_item_loot(
        candidates,
        draw_count=calculate_item_loot_draw_count(200, 0.20),
        capacity=10_000,
        rng=rng,
    )

    assert loot_items == {"gold_bar": 40}


@pytest.mark.django_db
def test_calculate_loot_draws_without_item_type_limit(monkeypatch):
    """库存池按数量加权抽取，且不限制命中的物品种类数。"""
    rng = random.Random(6)
    monkeypatch.setattr(rng, "randrange", lambda _stop: 0)
    monkeypatch.setattr(rng, "uniform", lambda _a, _b: 0.2)

    User = get_user_model()
    defender_user = User.objects.create_user(username="loot_defender", password="pass123")
    defender = ensure_manor(defender_user)
    defender.grain = 0
    defender.silver = 0
    defender.save(update_fields=["grain", "silver"])

    for idx in range(20):
        template = ItemTemplate.objects.create(
            key=f"loot_item_{idx}",
            name=f"Loot Item {idx}",
            rarity="black",
            tradeable=True,
        )
        InventoryItem.objects.create(
            manor=defender,
            template=template,
            quantity=10,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )

    loot_resources, loot_items = _calculate_loot(defender, rng=rng)
    assert loot_resources == {}
    assert sum(loot_items.values()) == 40
    assert len(loot_items) == 4
    assert all(isinstance(k, str) and k for k in loot_items.keys())
    assert all(v == 10 for v in loot_items.values())


@pytest.mark.django_db
def test_calculate_loot_replays_identically_from_persisted_seed():
    User = get_user_model()
    defender = ensure_manor(User.objects.create_user(username="loot_replay_defender", password="pass123"))
    defender.silver = 200_000
    defender.save(update_fields=["silver"])
    for index, quantity in enumerate([17, 23]):
        template = ItemTemplate.objects.create(
            key=f"loot_replay_item_{index}",
            name=f"重放物品{index}",
            rarity="blue",
            tradeable=True,
            storage_space=index + 1,
        )
        InventoryItem.objects.create(
            manor=defender,
            template=template,
            quantity=quantity,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )

    first_context = BattleRandomContext.create(314159)
    second_context = BattleRandomContext.create(314159)

    first = _calculate_loot(defender, rng=first_context.rng(RNG_STREAM_LOOT))
    replayed = _calculate_loot(defender, rng=second_context.rng(RNG_STREAM_LOOT))

    assert replayed == first
