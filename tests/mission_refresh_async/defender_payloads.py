from __future__ import annotations

from types import SimpleNamespace

import pytest

import gameplay.services.missions_impl.execution as mission_execution
import gameplay.services.missions_impl.launch_post_actions as mission_launch_post_actions


def _build_defender_setup(mission, *, loadout):
    return mission_launch_post_actions.build_defender_setup_and_drop_table(
        mission,
        loadout=loadout,
        normalize_guest_configs=mission_execution._normalize_guest_configs,
        normalize_mapping=mission_execution._normalize_mapping,
    )


def test_build_defender_setup_and_drop_table_rejects_invalid_troop_mapping():
    mission = SimpleNamespace(
        is_defense=False,
        enemy_guests=[],
        enemy_troops="bad-troops",
        enemy_technology={},
        drop_table={},
    )

    with pytest.raises(AssertionError, match="invalid mission enemy_troops"):
        _build_defender_setup(mission, loadout={})


def test_build_defender_setup_and_drop_table_rejects_invalid_drop_table():
    mission = SimpleNamespace(
        is_defense=False,
        enemy_guests=[],
        enemy_troops={},
        enemy_technology={},
        drop_table="bad-drops",
    )

    with pytest.raises(AssertionError, match="invalid mission drop_table"):
        _build_defender_setup(mission, loadout={})


def test_build_defender_setup_and_drop_table_rejects_invalid_guest_configs():
    mission = SimpleNamespace(
        is_defense=False,
        enemy_guests="bad-guests",
        enemy_troops={},
        enemy_technology={},
        drop_table={},
    )

    with pytest.raises(AssertionError, match="invalid mission enemy_guests"):
        _build_defender_setup(mission, loadout={})


def test_build_defender_setup_and_drop_table_rejects_invalid_guest_config_entry():
    mission = SimpleNamespace(
        is_defense=False,
        enemy_guests=[123],
        enemy_troops={},
        enemy_technology={},
        drop_table={},
    )

    with pytest.raises(AssertionError, match="invalid mission enemy_guests"):
        _build_defender_setup(mission, loadout={})


def test_build_defender_setup_and_drop_table_rejects_blank_guest_config_entry():
    mission = SimpleNamespace(
        is_defense=False,
        enemy_guests=["   "],
        enemy_troops={},
        enemy_technology={},
        drop_table={},
    )

    with pytest.raises(AssertionError, match="invalid mission enemy_guests"):
        _build_defender_setup(mission, loadout={})


def test_build_defender_setup_and_drop_table_rejects_mapping_guest_config_without_key():
    mission = SimpleNamespace(
        is_defense=False,
        enemy_guests=[{"skills": ["slash"]}],
        enemy_troops={},
        enemy_technology={},
        drop_table={},
    )

    with pytest.raises(AssertionError, match="invalid mission enemy_guests"):
        _build_defender_setup(mission, loadout={})


def test_build_defender_setup_and_drop_table_rejects_mapping_guest_config_with_invalid_skills():
    mission = SimpleNamespace(
        is_defense=False,
        enemy_guests=[{"key": "enemy_guest", "skills": "bad-skills"}],
        enemy_troops={},
        enemy_technology={},
        drop_table={},
    )

    with pytest.raises(AssertionError, match="invalid mission enemy_guests"):
        _build_defender_setup(mission, loadout={})


def test_build_defender_setup_and_drop_table_rejects_blank_guest_label():
    mission = SimpleNamespace(
        is_defense=False,
        enemy_guests=[{"key": "enemy_guest", "label": "   "}],
        enemy_troops={},
        enemy_technology={},
        drop_table={},
    )

    with pytest.raises(AssertionError, match="invalid mission enemy_guests"):
        _build_defender_setup(mission, loadout={})


def test_build_defender_setup_and_drop_table_rejects_non_string_guest_label():
    mission = SimpleNamespace(
        is_defense=False,
        enemy_guests=[{"key": "enemy_guest", "label": 123}],
        enemy_troops={},
        enemy_technology={},
        drop_table={},
    )

    with pytest.raises(AssertionError, match="invalid mission enemy_guests"):
        _build_defender_setup(mission, loadout={})


def test_build_defender_setup_and_drop_table_preserves_guest_label():
    mission = SimpleNamespace(
        is_defense=False,
        enemy_guests=[{"key": "enemy_guest", "label": "任务别名"}],
        enemy_troops={},
        enemy_technology={},
        drop_table={},
    )

    defender_setup, _drop_table = _build_defender_setup(mission, loadout={})

    assert defender_setup["guest_keys"] == [{"key": "enemy_guest", "skills": None, "label": "任务别名"}]


def test_build_defender_setup_and_drop_table_rejects_invalid_enemy_technology():
    mission = SimpleNamespace(
        is_defense=False,
        enemy_guests=[],
        enemy_troops={},
        enemy_technology="bad-tech",
        drop_table={},
    )

    with pytest.raises(AssertionError, match="invalid mission enemy_technology"):
        _build_defender_setup(mission, loadout={})


def test_build_defender_setup_and_drop_table_rejects_invalid_mapping_key():
    mission = SimpleNamespace(
        is_defense=False,
        enemy_guests=[],
        enemy_troops={"": 1},
        enemy_technology={},
        drop_table={},
    )

    with pytest.raises(AssertionError, match="invalid mission enemy_troops"):
        _build_defender_setup(mission, loadout={})


def test_build_defender_setup_and_drop_table_rejects_non_string_mapping_key():
    mission = SimpleNamespace(
        is_defense=False,
        enemy_guests=[],
        enemy_troops={1: 1},
        enemy_technology={},
        drop_table={},
    )

    with pytest.raises(AssertionError, match="invalid mission enemy_troops"):
        _build_defender_setup(mission, loadout={})


def test_build_defender_setup_and_drop_table_still_tolerates_missing_optional_payloads():
    mission = SimpleNamespace(
        is_defense=False,
        enemy_guests=None,
        enemy_troops={},
        enemy_technology=None,
        drop_table=None,
    )

    defender_setup, drop_table = _build_defender_setup(mission, loadout={})

    assert defender_setup["guest_keys"] == []
    assert defender_setup["troop_loadout"] == {}
    assert defender_setup["technology"] == {}
    assert drop_table == {}


def test_build_defender_setup_and_drop_table_for_defense_keeps_runtime_loadout():
    mission = SimpleNamespace(is_defense=True)
    loadout = {"archer": 10}

    defender_setup, drop_table = _build_defender_setup(mission, loadout=loadout)

    assert defender_setup == {"troop_loadout": loadout}
    assert drop_table == {}
