from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def test_bootstrap_game_data_runs_expected_steps(monkeypatch):
    calls: list[tuple[str, dict]] = []
    reloaded = {"count": 0}

    def _fake_call_command(name, *args, **kwargs):
        calls.append((name, kwargs))

    def _fake_reload(self):
        reloaded["count"] += 1

    monkeypatch.setattr("gameplay.management.commands.bootstrap_game_data.call_command", _fake_call_command)
    monkeypatch.setattr(
        "gameplay.management.commands.bootstrap_game_data.Command._reload_runtime_configs", _fake_reload
    )

    call_command("bootstrap_game_data", verbosity=0, skip_images=True)

    command_names = [name for name, _ in calls]
    assert command_names == [
        "load_building_templates",
        "load_technology_templates",
        "load_item_templates",
        "load_troop_templates",
        "load_guest_templates",
        "load_mission_templates",
        "load_guild_mission_templates",
        "seed_work_templates",
    ]
    kwargs_by_name = {name: kwargs for name, kwargs in calls}
    assert kwargs_by_name["load_item_templates"]["repair_grain_ledger"] is True
    assert kwargs_by_name["load_troop_templates"]["skip_images"] is True
    assert kwargs_by_name["load_guest_templates"]["skip_images"] is True
    assert reloaded["count"] == 1


def test_bootstrap_game_data_skip_config_reload(monkeypatch):
    reloaded = {"count": 0}

    def _fake_call_command(*_args, **_kwargs):
        return None

    def _fake_reload(self):
        reloaded["count"] += 1

    monkeypatch.setattr("gameplay.management.commands.bootstrap_game_data.call_command", _fake_call_command)
    monkeypatch.setattr(
        "gameplay.management.commands.bootstrap_game_data.Command._reload_runtime_configs", _fake_reload
    )

    call_command("bootstrap_game_data", verbosity=0, skip_config_reload=True)

    assert reloaded["count"] == 0


def test_bootstrap_game_data_continue_on_error_keeps_running(monkeypatch):
    calls: list[str] = []

    def _fake_call_command(name, *args, **kwargs):
        calls.append(name)
        if name == "load_item_templates":
            raise CommandError("boom")

    monkeypatch.setattr("gameplay.management.commands.bootstrap_game_data.call_command", _fake_call_command)

    call_command("bootstrap_game_data", verbosity=0, skip_config_reload=True, continue_on_error=True)

    assert calls == [
        "load_building_templates",
        "load_technology_templates",
        "load_item_templates",
        "load_troop_templates",
        "load_guest_templates",
        "load_mission_templates",
        "load_guild_mission_templates",
        "seed_work_templates",
    ]


def test_bootstrap_game_data_fail_fast_by_default(monkeypatch):
    def _fake_call_command(name, *args, **kwargs):
        if name == "load_item_templates":
            raise CommandError("boom")

    monkeypatch.setattr("gameplay.management.commands.bootstrap_game_data.call_command", _fake_call_command)

    with pytest.raises(CommandError):
        call_command("bootstrap_game_data", verbosity=0, skip_config_reload=True)


def test_bootstrap_game_data_programming_error_bubbles_up_even_with_continue_on_error(monkeypatch):
    calls: list[str] = []

    def _fake_call_command(name, *args, **kwargs):
        calls.append(name)
        if name == "load_item_templates":
            raise AssertionError("broken bootstrap step")

    monkeypatch.setattr("gameplay.management.commands.bootstrap_game_data.call_command", _fake_call_command)

    with pytest.raises(AssertionError, match="broken bootstrap step"):
        call_command("bootstrap_game_data", verbosity=0, skip_config_reload=True, continue_on_error=True)

    assert calls == [
        "load_building_templates",
        "load_technology_templates",
        "load_item_templates",
    ]
