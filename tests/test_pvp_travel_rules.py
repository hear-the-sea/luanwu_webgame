from __future__ import annotations

import math
from types import SimpleNamespace

import pytest
from django.test import override_settings

from gameplay.services.pvp_runtime.travel import (
    calculate_agility_factor,
    calculate_pvp_travel_time,
    calculate_size_factor,
    round_game_seconds_up_to_minute,
)


@pytest.mark.parametrize(
    ("average_agility", "expected_factor"),
    [
        (60, 1.20),
        (160, 1.00),
        (310, 0.70),
        (-9999, 1.20),
        (9999, 0.70),
    ],
)
def test_agility_factor_uses_160_baseline_and_two_percent_per_ten_points(average_agility, expected_factor):
    assert calculate_agility_factor(average_agility) == pytest.approx(expected_factor)


def test_size_factor_is_continuous_and_counts_each_troop_equally():
    empty_score, empty_factor = calculate_size_factor(guest_count=1, troop_count=0)
    score, factor = calculate_size_factor(guest_count=2, troop_count=200)

    assert empty_score == 0
    assert empty_factor == 1
    assert score == 2
    assert factor == pytest.approx(1 + 0.5 * 2 / 22)


def test_travel_rounds_game_time_up_to_a_minute_before_applying_time_scale():
    assert round_game_seconds_up_to_minute(60.01) == 120

    with override_settings(GAME_TIME_MULTIPLIER=10):
        estimate = calculate_pvp_travel_time(
            route_seconds=61,
            guests=[SimpleNamespace(agility=160)],
            troop_loadout={},
        )

    assert estimate.game_seconds == 120
    assert estimate.scaled_seconds == 12


def test_travel_has_no_final_time_cap():
    with override_settings(GAME_TIME_MULTIPLIER=1):
        estimate = calculate_pvp_travel_time(
            route_seconds=100_000,
            guests=[SimpleNamespace(agility=60)],
            troop_loadout={"guards": 2_000_000},
        )

    assert estimate.agility_factor == 1.2
    assert estimate.size_factor < 1.5
    assert estimate.game_seconds == math.ceil(100_000 * estimate.agility_factor * estimate.size_factor / 60) * 60
    assert estimate.scaled_seconds > 8 * 60 * 60


def test_guild_travel_uses_eight_hour_base_and_march_technology(monkeypatch):
    from guilds.services import guild_raid_rules

    guest = SimpleNamespace(agility=160)
    monkeypatch.setattr(guild_raid_rules, "get_tech_bonus", lambda _guild, _bonus_type: 0.0)

    with override_settings(GAME_TIME_MULTIPLIER=1):
        baseline = guild_raid_rules.calculate_guild_raid_travel_time(object(), [guest], {})

    monkeypatch.setattr(guild_raid_rules, "get_tech_bonus", lambda _guild, _bonus_type: 0.25)
    with override_settings(GAME_TIME_MULTIPLIER=1):
        max_tech = guild_raid_rules.calculate_guild_raid_travel_time(object(), [guest], {})

    assert baseline == 8 * 60 * 60
    assert max_tech == 6 * 60 * 60


def test_guild_march_factor_never_falls_below_seventy_five_percent(monkeypatch):
    from guilds.services import guild_raid_rules

    monkeypatch.setattr(guild_raid_rules, "get_tech_bonus", lambda _guild, _bonus_type: 0.90)

    assert guild_raid_rules.get_guild_raid_march_factor(object()) == 0.75
