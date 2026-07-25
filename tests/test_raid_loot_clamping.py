from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction

from gameplay.models import InventoryItem, ItemTemplate, ResourceEvent
from gameplay.services.manor.core import ensure_manor
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
    defender = SimpleNamespace(grain=10_000_000, silver=10_000_000)

    loot = _calculate_resource_loot(
        defender,
        0.30,
        guests=[_guest(1) for _ in range(18)],
        troop_loadout={"dao_ke": 3600},
        battle_report=SimpleNamespace(losses={}),
    )

    assert loot == {"grain": 2_000_000, "silver": 2_000_000}


@pytest.mark.django_db
def test_calculate_resource_loot_reduces_cap_when_attacker_troops_die():
    defender = SimpleNamespace(grain=10_000_000, silver=10_000_000)
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

    assert loot == {"grain": 1_350_000, "silver": 1_350_000}


def test_calculate_resource_loot_keeps_max_cap_without_raid_context():
    defender = SimpleNamespace(grain=10_000_000, silver=10_000_000)

    loot = _calculate_resource_loot(defender, 0.30)

    assert loot == {"grain": 2_000_000, "silver": 2_000_000}


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
    monkeypatch.setattr("gameplay.services.raid.combat.loot.random.shuffle", lambda _rows: None)
    monkeypatch.setattr("gameplay.services.raid.combat.loot.random.random", lambda: 0.0)
    monkeypatch.setattr("gameplay.services.raid.combat.loot.random.randint", lambda _a, b: b)

    loot_items = _calculate_loot_items(
        _build_loot_item_queryset(defender),
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
    monkeypatch.setattr("gameplay.services.raid.combat.loot.random.shuffle", lambda _rows: None)
    monkeypatch.setattr("gameplay.services.raid.combat.loot.random.random", lambda: 0.0)
    monkeypatch.setattr("gameplay.services.raid.combat.loot.random.randint", lambda _a, b: b)

    loot_items = _calculate_loot_items(
        _build_loot_item_queryset(defender),
        guests=[_guest(1) for _ in range(18)],
        troop_loadout={"dao_ke": 3600},
        battle_report=battle_report,
    )

    assert loot_items == {"raid_heavy_loss_item": 1}


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
