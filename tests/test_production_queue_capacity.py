from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.exceptions import ForgeOperationError, ProductionStartError
from gameplay.constants import BUILDING_MAX_LEVELS, BuildingKeys
from gameplay.models import EquipmentProduction, HorseProduction, InventoryItem, ItemTemplate, Manor, SmeltingProduction
from gameplay.services.buildings import forge as forge_service
from gameplay.services.buildings import smithy as smithy_service
from gameplay.services.buildings import stable as stable_service
from gameplay.services.buildings.production_queue import get_production_queue_capacity
from gameplay.services.manor.core import ensure_manor


def _set_building_level(manor: Manor, building_key: str, level: int) -> None:
    building = manor.buildings.get(building_type__key=building_key)
    building.level = level
    building.save(update_fields=["level"])
    manor.invalidate_building_cache()


def _create_manor(django_user_model, username: str) -> Manor:
    user = django_user_model.objects.create_user(username=username, password="pass123")
    manor = ensure_manor(user)
    manor.grain = 500_000
    manor.silver = 500_000
    manor.save(update_fields=["grain", "silver"])
    return manor


@pytest.mark.parametrize(
    ("building_key", "level", "expected_capacity"),
    [
        (BuildingKeys.SMITHY, 9, 1),
        (BuildingKeys.SMITHY, BUILDING_MAX_LEVELS[BuildingKeys.SMITHY], 2),
        (BuildingKeys.FORGE, 9, 1),
        (BuildingKeys.FORGE, BUILDING_MAX_LEVELS[BuildingKeys.FORGE], 2),
        (BuildingKeys.STABLE, 9, 1),
        (BuildingKeys.STABLE, BUILDING_MAX_LEVELS[BuildingKeys.STABLE], 2),
        (BuildingKeys.RANCH, BUILDING_MAX_LEVELS[BuildingKeys.RANCH], 1),
    ],
)
def test_production_queue_capacity_unlocks_only_for_requested_buildings(
    building_key: str,
    level: int,
    expected_capacity: int,
):
    manor = SimpleNamespace(get_building_level=lambda key: level if key == building_key else 1)

    assert get_production_queue_capacity(manor, building_key) == expected_capacity


@pytest.mark.django_db
def test_max_level_smithy_allows_two_parallel_productions(django_user_model, monkeypatch):
    manor = _create_manor(django_user_model, "smithy_queue_capacity")
    _set_building_level(manor, BuildingKeys.SMITHY, 10)
    monkeypatch.setattr(
        smithy_service,
        "METAL_CONFIG",
        {
            "queue_medicine": {
                "cost_type": "silver",
                "cost_amount": 50,
                "base_duration": 60,
                "category": "medicine",
                "required_smithy": 1,
            }
        },
    )
    monkeypatch.setattr("gameplay.services.technology.get_player_technology_level", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(smithy_service, "_schedule_smelting_completion", lambda *_args: None)

    smithy_service.start_smelting_production(manor, "queue_medicine", 1)
    smithy_service.start_smelting_production(manor, "queue_medicine", 1)

    with pytest.raises(ProductionStartError, match="队列已满"):
        smithy_service.start_smelting_production(manor, "queue_medicine", 1)

    assert SmeltingProduction.objects.filter(manor=manor, status=SmeltingProduction.Status.PRODUCING).count() == 2


@pytest.mark.django_db
def test_max_level_stable_allows_two_parallel_productions(django_user_model, monkeypatch):
    manor = _create_manor(django_user_model, "stable_queue_capacity")
    _set_building_level(manor, BuildingKeys.STABLE, 10)
    monkeypatch.setattr(
        stable_service,
        "HORSE_CONFIG",
        {
            "queue_horse": {
                "grain_cost": 10,
                "base_duration": 60,
                "required_horsemanship": 1,
            }
        },
    )
    monkeypatch.setattr("gameplay.services.technology.get_player_technology_level", lambda *_args, **_kwargs: 5)
    monkeypatch.setattr(stable_service, "_schedule_production_completion", lambda *_args: None)

    stable_service.start_horse_production(manor, "queue_horse", 1)
    stable_service.start_horse_production(manor, "queue_horse", 1)

    with pytest.raises(ProductionStartError, match="队列已满"):
        stable_service.start_horse_production(manor, "queue_horse", 1)

    assert HorseProduction.objects.filter(manor=manor, status=HorseProduction.Status.PRODUCING).count() == 2


@pytest.mark.django_db
def test_max_level_forge_allows_two_parallel_productions(django_user_model, monkeypatch):
    manor = _create_manor(django_user_model, "forge_queue_capacity")
    _set_building_level(manor, BuildingKeys.FORGE, 10)
    equipment = ItemTemplate.objects.create(
        key="queue_equipment",
        name="队列测试装备",
        effect_type="equip_helmet",
        rarity="green",
    )
    material = ItemTemplate.objects.create(
        key="queue_material",
        name="队列测试材料",
        effect_type=ItemTemplate.EffectType.RESOURCE,
        rarity="gray",
    )
    InventoryItem.objects.create(manor=manor, template=material, quantity=3)
    monkeypatch.setattr(
        forge_service,
        "EQUIPMENT_CONFIG",
        {
            equipment.key: {
                "category": "helmet",
                "materials": {material.key: 1},
                "base_duration": 60,
                "required_forging": 1,
            }
        },
    )
    monkeypatch.setattr("gameplay.services.technology.get_player_technology_level", lambda *_args, **_kwargs: 5)
    monkeypatch.setattr("gameplay.services.buildings.forge_runtime.schedule_forging_completion", lambda *_args: None)

    forge_service.start_equipment_forging(manor, equipment.key, 1)
    forge_service.start_equipment_forging(manor, equipment.key, 1)

    with pytest.raises(ForgeOperationError, match="队列已满"):
        forge_service.start_equipment_forging(manor, equipment.key, 1)

    assert EquipmentProduction.objects.filter(manor=manor, status=EquipmentProduction.Status.FORGING).count() == 2
