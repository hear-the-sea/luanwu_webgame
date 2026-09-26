from __future__ import annotations

import pytest
from django.test import override_settings

from gameplay.constants import PVPConstants, get_raid_capture_guest_rate


@override_settings(RAID_ATTACKER_DEFEAT_CAPTURE_RATE=1.7)
def test_attacker_defeat_capture_rate_clamps_upper_bound():
    assert get_raid_capture_guest_rate(losing_side="attacker") == 1.0


@override_settings(RAID_DEFENDER_DEFEAT_CAPTURE_RATE=-0.3)
def test_defender_defeat_capture_rate_clamps_lower_bound():
    assert get_raid_capture_guest_rate(losing_side="defender") == 0.0


@pytest.mark.parametrize(
    ("losing_side", "setting_name", "expected"),
    [
        ("attacker", "RAID_ATTACKER_DEFEAT_CAPTURE_RATE", PVPConstants.RAID_ATTACKER_DEFEAT_CAPTURE_RATE),
        ("defender", "RAID_DEFENDER_DEFEAT_CAPTURE_RATE", PVPConstants.RAID_DEFENDER_DEFEAT_CAPTURE_RATE),
    ],
)
def test_role_specific_capture_rate_falls_back_on_invalid_value(losing_side, setting_name, expected):
    with override_settings(**{setting_name: "invalid-value"}):
        assert get_raid_capture_guest_rate(losing_side=losing_side) == expected


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
