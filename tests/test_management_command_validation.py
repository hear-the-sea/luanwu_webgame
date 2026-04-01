from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from battle.management.commands.load_troop_templates import Command, _load_avatar_for_troop
from battle.models import TroopTemplate
from gameplay.models import BuildingType, MissionTemplate
from guilds.models import GuildMissionTemplate


@pytest.mark.django_db
def test_load_building_templates_command_tolerates_invalid_numbers(tmp_path):
    payload_path = tmp_path / "building_templates.yaml"
    payload_path.write_text(
        """
buildings:
  - key: cmd_building_bad_numbers
    name: 脏数据建筑
    resource_type: silver
    base_rate_per_hour: bad
    rate_growth: bad
    base_upgrade_time: bad
    time_growth: bad
    cost_growth: bad
    base_cost: bad
  - not_a_mapping
""",
        encoding="utf-8",
    )

    call_command("load_building_templates", file=str(payload_path), verbosity=0)

    building = BuildingType.objects.get(key="cmd_building_bad_numbers")
    assert building.base_rate_per_hour == 0
    assert building.rate_growth == 0.0
    assert building.base_upgrade_time == 60
    assert building.time_growth == 1.25
    assert building.cost_growth == 1.35
    assert building.base_cost == {}


@pytest.mark.django_db
def test_load_mission_templates_command_tolerates_invalid_numbers(tmp_path):
    payload_path = tmp_path / "mission_templates.yaml"
    payload_path.write_text(
        """
missions:
  - key: cmd_mission_bad_numbers
    name: 脏数据任务
    is_defense: "true"
    guest_only: "false"
    enemy_guests: bad
    enemy_troops: []
    enemy_technology: []
    drop_table: []
    probability_drop_table: []
    base_travel_time: bad
    daily_limit: 0
""",
        encoding="utf-8",
    )

    call_command("load_mission_templates", file=str(payload_path), verbosity=0)

    mission = MissionTemplate.objects.get(key="cmd_mission_bad_numbers")
    assert mission.is_defense is True
    assert mission.guest_only is False
    assert mission.enemy_guests == []
    assert mission.enemy_troops == {}
    assert mission.enemy_technology == {}
    assert mission.drop_table == {}
    assert mission.probability_drop_table == {}
    assert mission.base_travel_time == 1200
    assert mission.daily_limit == 3


@pytest.mark.django_db
def test_load_guild_mission_templates_command_tolerates_invalid_numbers(tmp_path):
    payload_path = tmp_path / "guild_mission_templates.yaml"
    payload_path.write_text(
        """
missions:
  - key: cmd_guild_mission_bad_numbers
    name: 脏数据帮会任务
    enemy_guests: bad
    enemy_troops: []
    enemy_technology: []
    base_duration_seconds: bad
    ruby_reward: -1
    recommended_guest_count: 0
    allow_troops: "true"
    is_active: "false"
    sort_weight: bad
""",
        encoding="utf-8",
    )

    call_command("load_guild_mission_templates", file=str(payload_path), verbosity=0)

    mission = GuildMissionTemplate.objects.get(key="cmd_guild_mission_bad_numbers")
    assert mission.enemy_guests == []
    assert mission.enemy_troops == {}
    assert mission.enemy_technology == {}
    assert mission.base_duration_seconds == 600
    assert mission.ruby_reward == 0
    assert mission.recommended_guest_count == 1
    assert mission.allow_troops is True
    assert mission.is_active is False
    assert mission.sort_weight == 0


@pytest.mark.django_db
def test_load_troop_templates_command_tolerates_invalid_numbers(tmp_path):
    payload_path = tmp_path / "troop_templates.yaml"
    payload_path.write_text(
        """
troops:
  - key: cmd_troop_bad_numbers
    name: 脏数据兵种
    priority: bad
    default_count: -5
  - not_a_mapping
  - key: cmd_troop_missing_name
""",
        encoding="utf-8",
    )

    call_command("load_troop_templates", file=str(payload_path), verbosity=0, skip_images=True)

    troop = TroopTemplate.objects.get(key="cmd_troop_bad_numbers")
    assert troop.priority == 0
    assert troop.default_count == 120
    assert TroopTemplate.objects.filter(key="cmd_troop_missing_name").exists() is False


@pytest.mark.django_db
def test_load_troop_templates_command_fails_when_avatar_dir_missing(tmp_path):
    payload_path = tmp_path / "troop_templates.yaml"
    payload_path.write_text(
        """
troops:
  - key: cmd_troop_avatar_missing_dir
    name: 头像目录缺失兵种
    avatar: sample.png
""",
        encoding="utf-8",
    )

    with override_settings(BASE_DIR=tmp_path):
        with pytest.raises(CommandError, match="Troop avatar directory does not exist"):
            call_command("load_troop_templates", file=str(payload_path), verbosity=0)


@pytest.mark.django_db
def test_load_troop_templates_avatar_programming_error_bubbles_up(tmp_path, monkeypatch):
    troop = TroopTemplate.objects.create(key="cmd_troop_avatar_bug", name="头像契约错误兵种")
    avatar_path = tmp_path / "avatar.png"
    avatar_path.write_bytes(b"not-used")

    command = Command()
    monkeypatch.setattr(
        "battle.management.commands.load_troop_templates.compress_and_resize_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken troop avatar contract")),
    )

    with pytest.raises(AssertionError, match="broken troop avatar contract"):
        _load_avatar_for_troop(
            command,
            troop,
            {"avatar": "avatar.png"},
            tmp_path,
            0,
        )
