import random
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction

from gameplay.models import InventoryItem, ItemTemplate, RaidRun, ResourceEvent
from gameplay.services.manor.core import ensure_manor
from gameplay.services.raid.combat import runs as combat_runs
from gameplay.services.raid.combat.loot import (
    _apply_loot,
    _build_loot_item_queryset,
    _calculate_loot_items,
    _calculate_resource_loot,
    _format_battle_rewards_description,
    _format_loot_description,
    _grant_loot_items,
)

User = get_user_model()


def _guest(level: int):
    return SimpleNamespace(level=level)


@pytest.mark.django_db
def test_calculate_resource_loot_reaches_full_cap_with_full_squad_and_troops():
    defender = SimpleNamespace(silver=10_000_000)

    loot = _calculate_resource_loot(
        defender,
        0.30,
        guests=[_guest(1) for _ in range(18)],
        troop_loadout={"dao_ke": 3600},
        battle_report=SimpleNamespace(losses={}),
    )

    assert loot == {"silver": 2_000_000}


@pytest.mark.django_db
def test_calculate_resource_loot_reduces_cap_when_attacker_troops_die():
    defender = SimpleNamespace(silver=10_000_000)
    battle_report = SimpleNamespace(
        losses={
            "attacker": {
                "casualties": [
                    {"key": "dao_ke", "lost": 1800},
                ]
            }
        }
    )

    loot = _calculate_resource_loot(
        defender,
        0.30,
        guests=[_guest(1) for _ in range(18)],
        troop_loadout={"dao_ke": 3600},
        battle_report=battle_report,
    )

    assert loot == {"silver": 1_350_000}


def test_calculate_resource_loot_keeps_max_cap_without_raid_context():
    defender = SimpleNamespace(silver=10_000_000)

    loot = _calculate_resource_loot(defender, 0.30)

    assert loot == {"silver": 2_000_000}


@pytest.mark.django_db
def test_calculate_loot_items_clamps_quantity_by_item_capacity(monkeypatch):
    user = User.objects.create_user(username="raid_item_capacity", password="pass123")
    defender = ensure_manor(user)
    template = ItemTemplate.objects.create(
        key="raid_heavy_loot_item",
        name="重型战利品",
        tradeable=True,
        rarity="black",
        storage_space=12_000,
    )
    InventoryItem.objects.create(
        manor=defender,
        template=template,
        quantity=100,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    loot_items = _calculate_loot_items(
        _build_loot_item_queryset(defender),
        rng=random.Random(1),
        guests=[_guest(1) for _ in range(18)],
        troop_loadout={"dao_ke": 3600},
        battle_report=SimpleNamespace(losses={}),
    )

    assert loot_items == {"raid_heavy_loot_item": 2}


@pytest.mark.django_db
def test_calculate_loot_items_reduces_item_capacity_after_troop_losses(monkeypatch):
    user = User.objects.create_user(username="raid_item_loss_capacity", password="pass123")
    defender = ensure_manor(user)
    template = ItemTemplate.objects.create(
        key="raid_heavy_loss_item",
        name="战损重型战利品",
        tradeable=True,
        rarity="black",
        storage_space=12_000,
    )
    InventoryItem.objects.create(
        manor=defender,
        template=template,
        quantity=100,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    battle_report = SimpleNamespace(
        losses={
            "attacker": {
                "casualties": [
                    {"key": "dao_ke", "lost": 1800},
                ]
            }
        }
    )
    loot_items = _calculate_loot_items(
        _build_loot_item_queryset(defender),
        rng=random.Random(2),
        guests=[_guest(1) for _ in range(18)],
        troop_loadout={"dao_ke": 3600},
        battle_report=battle_report,
    )

    assert loot_items == {"raid_heavy_loss_item": 1}


@pytest.mark.django_db
def test_calculate_loot_items_uses_fractional_grain_capacity(monkeypatch):
    user = User.objects.create_user(username="raid_grain_capacity", password="pass123")
    defender = ensure_manor(user)
    grain_template = ItemTemplate.objects.create(
        key="grain",
        name="粮食",
        tradeable=True,
        rarity="black",
        storage_space=1,
    )
    InventoryItem.objects.create(
        manor=defender,
        template=grain_template,
        quantity=10_000,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    monkeypatch.setattr("gameplay.services.raid.combat.loot._calculate_item_loot_capacity", lambda **_kwargs: 1)

    loot_items = _calculate_loot_items(
        _build_loot_item_queryset(defender),
        rng=random.Random(3),
    )

    assert loot_items == {"grain": 1_000}


@pytest.mark.django_db
def test_calculate_loot_items_keeps_strict_twenty_percent_limit_for_small_inventory(monkeypatch):
    user = User.objects.create_user(username="raid_small_inventory", password="pass123")
    defender = ensure_manor(user)
    template = ItemTemplate.objects.create(
        key="raid_small_inventory_item",
        name="小库存战利品",
        tradeable=True,
        rarity="black",
    )
    InventoryItem.objects.create(
        manor=defender,
        template=template,
        quantity=4,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    loot_items = _calculate_loot_items(
        _build_loot_item_queryset(defender),
        rng=random.Random(4),
    )

    assert loot_items == {}


@pytest.mark.django_db
def test_calculate_loot_items_shares_total_inventory_limit_across_types(monkeypatch):
    user = User.objects.create_user(username="raid_shared_inventory_limit", password="pass123")
    defender = ensure_manor(user)
    for index in range(3):
        template = ItemTemplate.objects.create(
            key=f"raid_shared_inventory_item_{index}",
            name=f"零散战利品{index}",
            tradeable=True,
            rarity="black",
        )
        InventoryItem.objects.create(
            manor=defender,
            template=template,
            quantity=4,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
    loot_items = _calculate_loot_items(
        _build_loot_item_queryset(defender),
        rng=random.Random(5),
    )

    assert sum(loot_items.values()) == 2


@pytest.mark.django_db
def test_calculate_loot_items_ignores_item_rarity(monkeypatch):
    user = User.objects.create_user(username="raid_inventory_chance_miss", password="pass123")
    defender = ensure_manor(user)
    template = ItemTemplate.objects.create(
        key="raid_inventory_chance_miss_item",
        name="橙色战利品",
        tradeable=True,
        rarity="orange",
    )
    InventoryItem.objects.create(
        manor=defender,
        template=template,
        quantity=5,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    loot_items = _calculate_loot_items(
        _build_loot_item_queryset(defender),
        rng=random.Random(6),
    )

    assert loot_items == {"raid_inventory_chance_miss_item": 1}


@pytest.mark.django_db
def test_apply_loot_clamps_to_available_resources():
    user = User.objects.create_user(username="raid_defender", password="pass123")
    defender = ensure_manor(user)
    defender.grain = 50
    defender.silver = 20
    defender.save(update_fields=["grain", "silver"])

    with transaction.atomic():
        actual_resources, actual_items = _apply_loot(
            defender,
            loot_resources={"grain": 100, "silver": 10},
            loot_items={},
        )

    defender.refresh_from_db()
    assert actual_resources == {"grain": 50, "silver": 10}
    assert actual_items == {}
    assert defender.grain == 0
    assert defender.silver == 10

    deltas = {
        event.resource_type: event.delta
        for event in ResourceEvent.objects.filter(
            manor=defender,
            reason=ResourceEvent.Reason.ADMIN_ADJUST,
            note="踢馆被掠夺",
        )
    }
    assert deltas == {"grain": -50, "silver": -10}


@pytest.mark.django_db
def test_apply_loot_deducts_item_pool_grain_from_resource_ledger():
    user = User.objects.create_user(username="raid_grain_defender", password="pass123")
    defender = ensure_manor(user)
    grain_template = ItemTemplate.objects.create(key="grain", name="粮食", tradeable=True)
    defender.grain = 1_500
    defender.silver = 20
    defender.save(update_fields=["grain", "silver"])
    InventoryItem.objects.create(
        manor=defender,
        template=grain_template,
        quantity=1_500,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    with transaction.atomic():
        actual_resources, actual_items = _apply_loot(
            defender,
            loot_resources={"silver": 10},
            loot_items={"grain": 1_000},
        )

    defender.refresh_from_db()
    grain_item = InventoryItem.objects.get(
        manor=defender,
        template=grain_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert actual_resources == {"silver": 10}
    assert actual_items == {"grain": 1_000}
    assert defender.grain == 500
    assert grain_item.quantity == 500

    deltas = {
        event.resource_type: event.delta
        for event in ResourceEvent.objects.filter(
            manor=defender,
            reason=ResourceEvent.Reason.ADMIN_ADJUST,
            note="踢馆被掠夺",
        )
    }
    assert deltas == {"grain": -1_000, "silver": -10}


@pytest.mark.django_db
def test_finalize_raid_merges_new_and_legacy_grain_into_resource_ledger():
    attacker_user = User.objects.create_user(username="raid_grain_attacker", password="pass123")
    defender_user = User.objects.create_user(username="raid_grain_finalize_defender", password="pass123")
    attacker = ensure_manor(attacker_user)
    defender = ensure_manor(defender_user)
    grain_template = ItemTemplate.objects.create(key="grain", name="粮食", tradeable=True)
    loot_template = ItemTemplate.objects.create(key="raid_regular_loot", name="普通战利品", tradeable=True)
    attacker.grain = 100
    attacker.silver = 100
    attacker.save(update_fields=["grain", "silver"])
    InventoryItem.objects.create(
        manor=attacker,
        template=grain_template,
        quantity=100,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    run = RaidRun.objects.create(
        attacker=attacker,
        defender=defender,
        status=RaidRun.Status.RETURNING,
        is_attacker_victory=True,
        loot_resources={"grain": 20, "silver": 10},
        loot_items={"grain": 30, "raid_regular_loot": 2},
    )

    combat_runs.finalize_raid(run)

    attacker.refresh_from_db()
    run.refresh_from_db()
    grain_item = InventoryItem.objects.get(
        manor=attacker,
        template=grain_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    loot_item = InventoryItem.objects.get(
        manor=attacker,
        template=loot_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    grain_events = ResourceEvent.objects.filter(
        manor=attacker,
        resource_type="grain",
        reason=ResourceEvent.Reason.BATTLE_REWARD,
        note="踢馆掠夺",
    )
    assert run.status == RaidRun.Status.COMPLETED
    assert attacker.grain == 150
    assert attacker.silver == 110
    assert grain_item.quantity == 150
    assert loot_item.quantity == 2
    assert list(grain_events.values_list("delta", flat=True)) == [50]


@pytest.mark.django_db
def test_grant_loot_items_normalizes_quantities():
    user = User.objects.create_user(username="raid_loot_grant", password="pass123")
    manor = ensure_manor(user)
    template = ItemTemplate.objects.create(key="raid_loot_item", name="Raid Loot", tradeable=True)

    _grant_loot_items(
        manor,
        {
            "raid_loot_item": "2",
            "raid_loot_item_bad": -3,
            "": 5,
        },
    )

    item = InventoryItem.objects.get(
        manor=manor,
        template=template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert item.quantity == 2


def test_formatters_tolerate_invalid_mapping_shapes():
    assert _format_loot_description(["bad"], ["shape"]) == "无"
    assert _format_battle_rewards_description(["bad"]) == ""
    assert "经验果 ×3" in _format_battle_rewards_description({"exp_fruit": "3", "equipment": ["bad"]})
