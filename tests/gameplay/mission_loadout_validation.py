import pytest

from core.exceptions import TroopLoadoutError
from gameplay.services.missions_impl import loadout as mission_loadout_service
from gameplay.services.missions_impl.launch_command import (
    _resolve_base_travel_time,
    _resolve_max_squad_size,
    prepare_launch_inputs,
)
from gameplay.utils.resource_calculator import calculate_travel_time, normalize_mission_loadout


def test_normalize_mission_loadout_rejects_unknown_troop_keys():
    with pytest.raises(TroopLoadoutError, match="不存在的类型"):
        normalize_mission_loadout(
            {"nonexistent_troop_xxx": 1},
            troop_templates={"archer": {"label": "弓手"}},
        )


def test_normalize_mission_loadout_rejects_invalid_known_quantity():
    with pytest.raises(AssertionError, match="invalid mission troop loadout quantity: archer"):
        normalize_mission_loadout(
            {"archer": "bad"},
            troop_templates={"archer": {"label": "弓手"}},
        )


def test_normalize_mission_loadout_ignores_unknown_troop_keys_with_invalid_quantity_shape():
    loadout = normalize_mission_loadout(
        {"nonexistent_troop_xxx": "not-a-number"},
        troop_templates={"archer": {"label": "弓手"}},
    )

    assert loadout == {"archer": 0}


def test_normalize_mission_loadout_rejects_invalid_payload_shape():
    with pytest.raises(AssertionError, match="invalid mission troop loadout payload"):
        normalize_mission_loadout(
            ["archer"],
            troop_templates={"archer": {"label": "弓手"}},
        )


def test_mission_loadout_service_rejects_missing_troop_templates(monkeypatch):
    monkeypatch.setattr("battle.troops.load_troop_templates", lambda: {})

    with pytest.raises(AssertionError, match="mission troop templates must not be empty"):
        mission_loadout_service.normalize_mission_loadout({"archer": 1})


def test_mission_travel_time_rejects_missing_troop_templates_for_non_empty_loadout(monkeypatch):
    monkeypatch.setattr("battle.troops.load_troop_templates", lambda: {})

    with pytest.raises(AssertionError, match="mission troop templates must not be empty"):
        mission_loadout_service.travel_time_seconds(60, guests=[], troop_loadout={"archer": 1})


def test_calculate_travel_time_uses_average_agility_with_fifty_percent_cap(monkeypatch):
    monkeypatch.setattr(
        "gameplay.utils.resource_calculator.scale_duration",
        lambda seconds, minimum=1: max(minimum, int(seconds)),
    )
    slow_guest = type("_Guest", (), {"agility": 100})()
    fast_guest = type("_Guest", (), {"agility": 9999})()

    moderate = calculate_travel_time(1800, guests=[slow_guest], troop_loadout={}, troop_templates={})
    capped = calculate_travel_time(1800, guests=[fast_guest], troop_loadout={}, troop_templates={})

    assert moderate == 1620
    assert capped == 900


def test_calculate_travel_time_ignores_troop_speed_bonus(monkeypatch):
    monkeypatch.setattr(
        "gameplay.utils.resource_calculator.scale_duration",
        lambda seconds, minimum=1: max(minimum, int(seconds)),
    )

    baseline = calculate_travel_time(1800, guests=[], troop_loadout={}, troop_templates={"archer": {"speed_bonus": 60}})
    with_troops = calculate_travel_time(
        1800,
        guests=[],
        troop_loadout={"archer": 100},
        troop_templates={"archer": {"speed_bonus": 60}},
    )

    assert baseline == 1800
    assert with_troops == baseline


def test_calculate_travel_time_rejects_invalid_loadout_shape():
    with pytest.raises(AssertionError, match="invalid mission troop loadout payload"):
        calculate_travel_time(60, guests=[], troop_loadout=["archer"], troop_templates={"archer": {"speed_bonus": 1}})


def test_calculate_travel_time_rejects_unknown_positive_troop_key():
    with pytest.raises(AssertionError, match="invalid mission troop loadout key"):
        calculate_travel_time(
            60, guests=[], troop_loadout={"unknown": 1}, troop_templates={"archer": {"speed_bonus": 1}}
        )


def test_calculate_travel_time_rejects_invalid_known_troop_quantity():
    with pytest.raises(AssertionError, match="invalid mission troop loadout quantity: archer"):
        calculate_travel_time(
            60, guests=[], troop_loadout={"archer": True}, troop_templates={"archer": {"speed_bonus": 1}}
        )


def test_resolve_max_squad_size_rejects_invalid_bool():
    with pytest.raises(AssertionError, match="invalid mission max_squad_size"):
        _resolve_max_squad_size(type("_Manor", (), {"max_squad_size": True})())


def test_resolve_max_squad_size_rejects_negative_value():
    with pytest.raises(AssertionError, match="invalid mission max_squad_size"):
        _resolve_max_squad_size(type("_Manor", (), {"max_squad_size": -1})())


def test_resolve_base_travel_time_rejects_invalid_bool():
    with pytest.raises(AssertionError, match="invalid mission base_travel_time"):
        _resolve_base_travel_time(type("_Mission", (), {"base_travel_time": True})())


def test_resolve_base_travel_time_rejects_non_positive_value():
    with pytest.raises(AssertionError, match="invalid mission base_travel_time"):
        _resolve_base_travel_time(type("_Mission", (), {"base_travel_time": 0})())


def test_prepare_launch_inputs_rejects_defense_troop_loadout():
    mission = type("_Mission", (), {"is_defense": True, "base_travel_time": 60})()

    with pytest.raises(AssertionError, match="defense mission troop_loadout must be empty"):
        prepare_launch_inputs(
            object(),
            mission,
            [],
            {"archer": 1},
            scale_duration=lambda seconds, minimum=1: max(minimum, seconds),
        )


def test_prepare_launch_inputs_rejects_defense_guest_ids():
    mission = type("_Mission", (), {"is_defense": True, "base_travel_time": 60})()

    with pytest.raises(AssertionError, match="defense mission guest_ids must be empty"):
        prepare_launch_inputs(
            object(), mission, [1], {}, scale_duration=lambda seconds, minimum=1: max(minimum, seconds)
        )
