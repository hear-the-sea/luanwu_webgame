from __future__ import annotations

from importlib import import_module

import pytest
from django.contrib.auth import get_user_model


def _migration_module():
    return import_module("gameplay.migrations.0107_four_continents_regions")


def _create_manor(username: str):
    user = get_user_model().objects.create_user(username=username, password="pass123")
    return user.manor


def test_legacy_region_mapping_covers_known_old_values():
    migration = _migration_module()

    assert migration.LEGACY_REGION_TO_NEW_REGION["beijing"] == "north"
    assert migration.LEGACY_REGION_TO_NEW_REGION["shanghai"] == "east"
    assert migration.LEGACY_REGION_TO_NEW_REGION["sichuan"] == "west"
    assert migration.LEGACY_REGION_TO_NEW_REGION["guangdong"] == "south"
    assert migration.LEGACY_REGION_TO_NEW_REGION["overseas"] == "overseas"


@pytest.mark.django_db
def test_remap_manor_regions_regenerates_only_conflicting_manor(monkeypatch):
    migration = _migration_module()

    manor_a = _create_manor("legacy_region_a")
    manor_b = _create_manor("legacy_region_b")

    manor_a.region = "beijing"
    manor_a.coordinate_x = 100
    manor_a.coordinate_y = 200
    manor_a.save(update_fields=["region", "coordinate_x", "coordinate_y"])

    manor_b.region = "tianjin"
    manor_b.coordinate_x = 100
    manor_b.coordinate_y = 200
    manor_b.save(update_fields=["region", "coordinate_x", "coordinate_y"])

    monkeypatch.setattr(
        "gameplay.migrations.0107_four_continents_regions.generate_unique_coordinate",
        lambda region, occupied_locations=None: (333, 444),
    )

    migration.remap_manor_regions(apps=None, schema_editor=None)

    manor_a.refresh_from_db(fields=["region", "coordinate_x", "coordinate_y"])
    manor_b.refresh_from_db(fields=["region", "coordinate_x", "coordinate_y"])

    assert manor_a.region == "north"
    assert (manor_a.coordinate_x, manor_a.coordinate_y) == (100, 200)
    assert manor_b.region == "north"
    assert (manor_b.coordinate_x, manor_b.coordinate_y) == (333, 444)


@pytest.mark.django_db
def test_remap_manor_regions_raises_for_unknown_legacy_region():
    migration = _migration_module()

    manor = _create_manor("legacy_region_unknown")
    manor.region = "mystery_land"
    manor.coordinate_x = 12
    manor.coordinate_y = 34
    manor.save(update_fields=["region", "coordinate_x", "coordinate_y"])

    with pytest.raises(RuntimeError, match="unknown legacy region"):
        migration.remap_manor_regions(apps=None, schema_editor=None)
