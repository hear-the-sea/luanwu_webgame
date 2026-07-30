from __future__ import annotations

import pytest
from django.utils import timezone

from core.config import BUILDING_KEYS
from gameplay.services.city_defense import city_defense_max_hp
from gameplay.services.manor.core import finalize_building_upgrade


def _wall(manor):
    return manor.buildings.select_related("building_type").get(building_type__key=BUILDING_KEYS.WALL)


@pytest.mark.parametrize(
    ("level", "current_hp", "expected_hp"),
    [
        (1, 3_000, 6_000),
        (1, 2_500, 5_500),
        (1, 1, 3_001),
        (0, 0, 3_000),
    ],
)
@pytest.mark.django_db
def test_city_defense_upgrade_preserves_missing_hp(
    manor_with_user,
    level: int,
    current_hp: int,
    expected_hp: int,
):
    manor, _client = manor_with_user
    wall = _wall(manor)
    completed_at = timezone.now()
    wall.level = level
    wall.current_hp = current_hp
    wall.hp_updated_at = completed_at
    wall.is_upgrading = True
    wall.upgrade_complete_at = completed_at - timezone.timedelta(seconds=1)
    wall.save(update_fields=["level", "current_hp", "hp_updated_at", "is_upgrading", "upgrade_complete_at"])

    assert finalize_building_upgrade(wall, now=completed_at, send_notification=False) is True

    wall.refresh_from_db()
    assert wall.level == level + 1
    assert wall.current_hp == expected_hp
    assert wall.current_hp <= city_defense_max_hp(BUILDING_KEYS.WALL, wall.level)
    assert wall.hp_updated_at == completed_at


@pytest.mark.django_db
def test_city_defense_upgrade_recovers_at_previous_level_before_preserving_missing_hp(manor_with_user):
    manor, _client = manor_with_user
    wall = _wall(manor)
    completed_at = timezone.now()
    wall.level = 1
    wall.current_hp = 2_500
    wall.hp_updated_at = completed_at - timezone.timedelta(hours=1)
    wall.is_upgrading = True
    wall.upgrade_complete_at = completed_at - timezone.timedelta(seconds=1)
    wall.save(update_fields=["level", "current_hp", "hp_updated_at", "is_upgrading", "upgrade_complete_at"])

    assert finalize_building_upgrade(wall, now=completed_at, send_notification=False) is True

    wall.refresh_from_db()
    assert wall.level == 2
    assert wall.current_hp == 5_650
    assert wall.hp_updated_at == completed_at
