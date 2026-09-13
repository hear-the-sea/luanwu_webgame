from __future__ import annotations

import pytest
from django.test import override_settings

from gameplay.constants import BUILDING_MAX_LEVELS, BuildingKeys, PVPConstants, get_raid_capture_guest_rate


def test_building_max_levels_contains_lianbing_daying_cap():
    assert BUILDING_MAX_LEVELS.get(BuildingKeys.LIANBING_DAYING) == 10


@override_settings(RAID_CAPTURE_GUEST_RATE=1.7)
def test_get_raid_capture_guest_rate_clamps_upper_bound():
    assert get_raid_capture_guest_rate() == 1.0


@override_settings(RAID_CAPTURE_GUEST_RATE=-0.3)
def test_get_raid_capture_guest_rate_clamps_lower_bound():
    assert get_raid_capture_guest_rate() == 0.0


@pytest.mark.parametrize("invalid_value", ["invalid-value", None])
def test_get_raid_capture_guest_rate_falls_back_on_invalid_value(invalid_value):
    with override_settings(RAID_CAPTURE_GUEST_RATE=invalid_value):
        assert get_raid_capture_guest_rate() == PVPConstants.RAID_CAPTURE_GUEST_RATE
