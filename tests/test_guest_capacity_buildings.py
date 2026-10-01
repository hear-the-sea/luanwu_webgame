from __future__ import annotations

import importlib
from datetime import timedelta

import pytest
from django.apps import apps
from django.db import connection
from django.utils import timezone

from core.exceptions import BuildingMaxLevelError, MissionSquadSizeExceededError
from gameplay.constants import BUILDING_MAX_LEVELS, BuildingKeys
from gameplay.models import Building, Manor, MissionTemplate
from gameplay.services.manor.core import ensure_manor, finalize_building_upgrade, quote_building_upgrade, start_upgrade
from gameplay.services.missions_impl.launch_command import prepare_offense_launch_inputs
from guests.models import Guest, GuestTemplate


@pytest.mark.django_db
def test_capacity_buildings_start_at_zero_and_repair_without_resetting_existing(manor_factory):
    manor, user = manor_factory(username="capacity_building_initial")
    hall = manor.buildings.get(building_type__key=BuildingKeys.JUXIAN_ZHUANG)
    tower = manor.buildings.get(building_type__key=BuildingKeys.YOUXIA_BAOTA)
    assert (hall.level, tower.level, manor.guest_capacity, manor.max_squad_size) == (0, 0, 5, 5)
    hall.level = 8
    hall.save(update_fields=["level"])
    tower.delete()
    manor.invalidate_building_cache()
    assert manor.max_squad_size == 5
    repaired = ensure_manor(user)
    assert repaired.buildings.get(pk=hall.pk).level == 8
    assert repaired.buildings.get(building_type__key=BuildingKeys.YOUXIA_BAOTA).level == 0
    assert (repaired.guest_capacity, repaired.max_squad_size) == (13, 5)


@pytest.mark.django_db
def test_capacity_progression_first_upgrade_and_level_fifteen_limit(manor_factory):
    manor, _user = manor_factory(username="capacity_building_levels")
    hall = manor.buildings.select_related("building_type").get(building_type__key=BuildingKeys.JUXIAN_ZHUANG)
    tower = manor.buildings.select_related("building_type").get(building_type__key=BuildingKeys.YOUXIA_BAOTA)
    for building in (hall, tower):
        quote = quote_building_upgrade(manor, building)
        assert (quote.current_level, quote.target_level) == (0, 1)
        assert building.next_level_cost() == building.building_type.base_cost
        assert building.next_level_duration() == building.building_type.base_upgrade_time
        assert BUILDING_MAX_LEVELS[building.building_type.key] == 15
    for level in range(16):
        Building.objects.filter(pk__in=[hall.pk, tower.pk]).update(level=level)
        manor.invalidate_building_cache()
        assert manor.guest_capacity == 5 + level
        assert manor.max_squad_size == 5 + level
    for building in (hall, tower):
        building.refresh_from_db()
        with pytest.raises(BuildingMaxLevelError):
            quote_building_upgrade(manor, building)


@pytest.mark.django_db
def test_capacity_description_migration_preserves_existing_upgrade_and_cost(manor_factory):
    manor, _user = manor_factory(username="capacity_description_migration")
    hall = manor.buildings.select_related("building_type").get(building_type__key=BuildingKeys.JUXIAN_ZHUANG)
    Building.objects.filter(pk=hall.pk).update(level=9, is_upgrading=True)
    before = hall.building_type.base_cost.copy()
    migration = importlib.import_module("gameplay.migrations.0194_guest_capacity_building_descriptions")
    migration.update_capacity_descriptions(apps, connection.schema_editor(atomic=False))
    hall.refresh_from_db()
    hall.building_type.refresh_from_db()
    assert (hall.level, hall.is_upgrading, hall.building_type.base_cost) == (9, True, before)
    assert "初始0级" in hall.building_type.description
    assert "20位" in hall.building_type.description


@pytest.mark.django_db
@pytest.mark.parametrize(("level", "limit"), [(0, 5), (15, 20)])
def test_mission_accepts_exact_capacity_and_rejects_one_more(manor_factory, level, limit):
    manor, _user = manor_factory(username=f"capacity_mission_{level}")
    manor.buildings.filter(building_type__key=BuildingKeys.YOUXIA_BAOTA).update(level=level)
    manor.invalidate_building_cache()
    template = GuestTemplate.objects.create(key=f"capacity_mission_{level}", name="容量验证门客")
    guests = [Guest.objects.create(manor=manor, template=template) for _ in range(limit + 1)]
    mission = MissionTemplate(key="capacity_boundary", name="容量边界", guest_only=True, base_travel_time=60)
    selected, loadout, _seconds = prepare_offense_launch_inputs(
        manor, mission, [guest.pk for guest in guests[:limit]], {}
    )
    assert len(selected) == limit
    assert loadout == {}
    with pytest.raises(MissionSquadSizeExceededError):
        prepare_offense_launch_inputs(manor, mission, [guest.pk for guest in guests], {})


@pytest.mark.django_db
@pytest.mark.parametrize("building_key", [BuildingKeys.JUXIAN_ZHUANG, BuildingKeys.YOUXIA_BAOTA])
@pytest.mark.parametrize("level", [0, 14])
def test_capacity_upgrade_changes_limit_only_after_due_completion(manor_factory, monkeypatch, building_key, level):
    manor, _user = manor_factory(username=f"capacity_upgrade_{building_key}_{level}")
    fixed_now = timezone.now()
    Manor.objects.filter(pk=manor.pk).update(
        silver=100_000_000,
        grain=100_000_000,
        silver_capacity=100_000_000,
        grain_capacity=100_000_000,
        resource_updated_at=fixed_now,
    )
    building = manor.buildings.select_related("building_type").get(building_type__key=building_key)
    building.level = level
    building.save(update_fields=["level"])
    manor.refresh_from_db()
    manor.invalidate_building_cache()
    capacity_property = "guest_capacity" if building_key == BuildingKeys.JUXIAN_ZHUANG else "max_squad_size"
    scheduled = []
    monkeypatch.setattr(
        "gameplay.services.manor.core.schedule_building_completion",
        lambda row, delay: scheduled.append((row.pk, delay)),
    )
    start_upgrade(building)
    building.refresh_from_db()
    assert building.level == level
    assert building.is_upgrading
    assert len(scheduled) == 1
    assert getattr(manor, capacity_property) == 5 + level
    assert not finalize_building_upgrade(
        building, now=building.upgrade_complete_at - timedelta(seconds=1), send_notification=False
    )
    assert finalize_building_upgrade(building, now=building.upgrade_complete_at, send_notification=False)
    assert not finalize_building_upgrade(building, now=building.upgrade_complete_at, send_notification=False)
    manor.invalidate_building_cache()
    assert getattr(manor, capacity_property) == 6 + level
