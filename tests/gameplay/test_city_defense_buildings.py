from __future__ import annotations

import logging
from pathlib import Path

from core.config import BUILDING_KEYS
from core.utils.yaml_loader import load_yaml_data
from gameplay.constants import BUILDING_MAX_LEVELS
from gameplay.models import BuildingCategory


def test_city_defense_building_category_is_after_special():
    assert BuildingCategory.CITY_DEFENSE == "city_defense"

    payload = load_yaml_data(
        Path("data/building_templates.yaml"),
        logger=logging.getLogger(__name__),
        context="test building templates",
        default={},
    )
    categories = [entry["key"] for entry in payload["categories"]]

    assert categories[categories.index("special") + 1] == "city_defense"


def test_wall_and_arrow_tower_templates_use_late_heavy_upgrade_curve():
    payload = load_yaml_data(
        Path("data/building_templates.yaml"),
        logger=logging.getLogger(__name__),
        context="test building templates",
        default={},
    )
    buildings = {entry["key"]: entry for entry in payload["buildings"]}

    wall = buildings[BUILDING_KEYS.WALL]
    arrow_tower = buildings[BUILDING_KEYS.ARROW_TOWER]

    assert wall["category"] == "city_defense"
    assert arrow_tower["category"] == "city_defense"
    assert wall["base_cost"] == {"silver": 18000}
    assert arrow_tower["base_cost"] == {"silver": 18000}
    assert wall["cost_growth"] == 1.85
    assert arrow_tower["cost_growth"] == 1.85
    assert wall["base_upgrade_time"] == 900
    assert arrow_tower["base_upgrade_time"] == 1200
    assert wall["time_growth"] == 1.85
    assert arrow_tower["time_growth"] == 1.85
    assert BUILDING_MAX_LEVELS[BUILDING_KEYS.WALL] == 10
    assert BUILDING_MAX_LEVELS[BUILDING_KEYS.ARROW_TOWER] == 10
