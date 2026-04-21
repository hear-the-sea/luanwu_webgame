from __future__ import annotations

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from battle.management.commands.load_troop_templates import Command, _load_avatar_for_troop
from battle.models import TroopTemplate
from gameplay.models import BuildingType, MissionTemplate
from guests.models import GuestTemplate
from guilds.models import GuildMissionTemplate


def _enemy_guest_key(entry):
    if isinstance(entry, str):
        return entry
    return entry.get("template_key") or entry.get("key") or ""


def _enemy_guest_keys(entries):
    return [_enemy_guest_key(entry) for entry in entries]


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
def test_default_mission_templates_define_junior_mission_tiering():
    payload_path = settings.BASE_DIR / "data" / "mission_templates.yaml"

    call_command("load_mission_templates", file=str(payload_path), verbosity=0)

    expected_enemy_technology = {
        "huashan_lunjian": {
            "difficulty": "junior",
            "enemy_technology": {"level": 2, "guest_level": 39, "guest_bonus": 0.1},
        },
        "jingyanggang": {
            "difficulty": "junior",
            "enemy_technology": {"level": 3, "guest_level": 39, "guest_bonus": 0.02},
        },
        "wulongshan": {
            "difficulty": "junior",
            "enemy_technology": {"level": 4, "guest_level": 43, "guest_bonus": 0.04},
        },
        "fugui_shanzhuang": {
            "difficulty": "junior",
            "enemy_technology": {"level": 3, "guest_level": 40, "guest_bonus": 0.05},
        },
        "biwu_zhaoqin": {
            "difficulty": "junior",
            "enemy_technology": {"level": 4, "guest_level": 45, "guest_bonus": 0.08},
        },
        "taozi_fenban": {
            "difficulty": "junior",
            "enemy_technology": {"level": 3, "guest_level": 41, "guest_bonus": 0.06},
        },
        "wagangzhai_nixi": {
            "difficulty": "intermediate",
            "enemy_technology": {"level": 8, "guest_level": 61, "guest_bonus": 0.1},
        },
        "wagangzhai": {
            "difficulty": "intermediate",
            "enemy_technology": {"level": 5, "guest_level": 53, "guest_bonus": 0.1},
        },
        "shizipo_heidian": {
            "difficulty": "intermediate",
            "enemy_technology": {"level": 6, "guest_level": 57, "guest_bonus": 0.1},
        },
        "shanhaiguan": {
            "difficulty": "intermediate",
            "enemy_technology": {"level": 7, "guest_level": 60, "guest_bonus": 0.18},
        },
        "shiren_daochang": {
            "difficulty": "intermediate",
            "enemy_technology": {"level": 6, "guest_level": 55, "guest_bonus": 0.08},
        },
        "jiguanshou_chuxian": {
            "difficulty": "intermediate",
            "enemy_technology": {"level": 7, "guest_level": 58, "guest_bonus": 0.12},
        },
        "tianpeng_dijiao_1": {
            "difficulty": "intermediate",
            "enemy_technology": {"level": 8, "guest_level": 64, "guest_bonus": 0.12},
        },
        "jiguan_chuniao": {
            "difficulty": "intermediate",
            "enemy_technology": {"level": 7, "guest_level": 59, "guest_bonus": 0.1},
        },
        "jiufeng_feihuan": {
            "difficulty": "intermediate",
            "enemy_technology": {"level": 6, "guest_level": 58, "guest_bonus": 0.12},
        },
        "liangshanbo_zhuyingtai": {
            "difficulty": "intermediate",
            "enemy_technology": {"level": 5, "guest_level": 54, "guest_bonus": 0.16},
        },
        "tianpeng_dijiao_2": {
            "difficulty": "advanced",
            "enemy_technology": {"level": 9, "guest_level": 80, "guest_bonus": 0.2},
        },
        "dongtian_fudi": {
            "difficulty": "advanced",
            "enemy_technology": {"level": 9, "guest_level": 75, "guest_bonus": 0.22},
        },
        "simian_chuge": {
            "difficulty": "advanced",
            "enemy_technology": {"level": 10, "guest_level": 83, "guest_bonus": 0.28},
        },
        "manzu_ruqin": {
            "difficulty": "advanced",
            "enemy_technology": {"level": 10, "guest_level": 86, "guest_bonus": 0.3},
        },
        "zhuiji_manzu": {
            "difficulty": "advanced",
            "enemy_technology": {"level": 10, "guest_level": 81, "guest_bonus": 0.26},
        },
    }

    for mission_key, expected in expected_enemy_technology.items():
        mission = MissionTemplate.objects.get(key=mission_key)
        assert mission.difficulty == expected["difficulty"]
        assert mission.enemy_technology == expected["enemy_technology"]

    jingyanggang = MissionTemplate.objects.get(key="jingyanggang")
    assert jingyanggang.enemy_guests == [{"key": "task_jingyang_tiger", "label": "猛虎"}]

    huashan = MissionTemplate.objects.get(key="huashan_lunjian")
    assert huashan.enemy_guests == [
        {"key": "task_huashan_jianwang", "label": "贱王之王"},
        {"key": "task_huashan_jianjing", "label": "顶级贱精"},
        {"key": "task_huashan_jianjing", "label": "顶级贱精"},
        {"key": "task_huashan_audience_a", "label": "观众甲"},
    ]


@pytest.mark.django_db
def test_default_mission_templates_split_wulongshan_enemy_keys_by_display_name():
    payload_path = settings.BASE_DIR / "data" / "mission_templates.yaml"

    call_command("load_mission_templates", file=str(payload_path), verbosity=0)

    wulongshan = MissionTemplate.objects.get(key="wulongshan")
    assert wulongshan.enemy_guests == [
        {"key": "task_wulong_bandit_chief_zuanshanbao", "label": "钻山豹"},
        {"key": "task_wulong_bandit_chief_zuoshandiao", "label": "座山雕"},
    ]


@pytest.mark.django_db
def test_default_mission_templates_split_shiren_named_enemy_keys_by_display_name():
    payload_path = settings.BASE_DIR / "data" / "mission_templates.yaml"

    call_command("load_mission_templates", file=str(payload_path), verbosity=0)

    shiren = MissionTemplate.objects.get(key="shiren_daochang")
    assert shiren.enemy_guests == [
        "hero_du_sha",
        "hero_li_dazui",
        "hero_hahaer",
        "hero_tu_jiaojiao",
        "hero_yin_jiuyou",
        {"key": "task_shiren_xiaomimi", "label": "萧咪咪"},
        {"key": "task_shiren_xuanyuansanguang", "label": "轩辕三光"},
        {"key": "task_shiren_tiezhan", "label": "铁战"},
        "hero_bai_kaixin",
        {"key": "task_shiren_luoshixiongdi", "label": "罗氏兄弟"},
    ]


@pytest.mark.django_db
def test_default_mission_templates_enemy_guest_keys_resolve_to_guest_templates():
    guest_payload_path = settings.BASE_DIR / "data" / "guest_templates.yaml"
    mission_payload_path = settings.BASE_DIR / "data" / "mission_templates.yaml"

    call_command("load_guest_templates", file=str(guest_payload_path), verbosity=0, skip_images=True)
    call_command("load_mission_templates", file=str(mission_payload_path), verbosity=0)

    guest_keys: set[str] = set()
    for mission in MissionTemplate.objects.all():
        for entry in mission.enemy_guests:
            if isinstance(entry, str):
                guest_keys.add(entry)
            else:
                guest_keys.add(entry["key"])

    existing_keys = set(GuestTemplate.objects.filter(key__in=guest_keys).values_list("key", flat=True))
    assert guest_keys == existing_keys


@pytest.mark.django_db
def test_default_guild_mission_templates_use_guild_specific_enemy_templates():
    guest_payload_path = settings.BASE_DIR / "data" / "guest_templates.yaml"
    mission_payload_path = settings.BASE_DIR / "data" / "guild_mission_templates.yaml"

    call_command("load_guest_templates", file=str(guest_payload_path), verbosity=0, skip_images=True)
    call_command("load_guild_mission_templates", file=str(mission_payload_path), verbosity=0)

    patrol = GuildMissionTemplate.objects.get(key="guild_patrol_alpha")
    escort = GuildMissionTemplate.objects.get(key="guild_supply_escort")
    assault = GuildMissionTemplate.objects.get(key="guild_blackwind_assault")

    patrol_keys = _enemy_guest_keys(patrol.enemy_guests)
    escort_keys = _enemy_guest_keys(escort.enemy_guests)
    assault_keys = _enemy_guest_keys(assault.enemy_guests)

    assert patrol_keys == [
        "guild_wulinzhai_zhai_zhu",
        "guild_wulinzhai_junshi",
        "guild_wulinzhai_night_watch",
        "guild_wulinzhai_blade_ambusher",
        "guild_wulinzhai_blade_ambusher",
        "guild_wulinzhai_blade_ambusher",
        "guild_wulinzhai_torch_archer",
        "guild_wulinzhai_torch_archer",
        "guild_wulinzhai_torch_archer",
        "guild_wulinzhai_path_scout",
        "guild_wulinzhai_minion",
        "guild_wulinzhai_minion",
    ]
    assert patrol.enemy_troops == {
        "qiang_hao": 760,
        "jian_hao": 700,
        "fast_archer": 650,
        "dao_jie": 500,
    }
    assert patrol.enemy_technology == {"level": 7, "guest_level": 88, "guest_bonus": 0.36}
    assert escort_keys == [
        "guild_bloodflag_head_escort",
        "guild_bloodflag_deputy_escort",
        "guild_bloodflag_rearguard_veteran",
        "guild_bloodflag_blade_guard",
        "guild_bloodflag_blade_guard",
        "guild_bloodflag_blade_guard",
        "guild_bloodflag_blade_guard",
        "guild_bloodflag_bow_guard",
        "guild_bloodflag_bow_guard",
        "guild_bloodflag_bow_guard",
        "guild_bloodflag_bow_guard",
        "guild_bloodflag_pathfinder",
        "guild_bloodflag_pathfinder",
        "guild_bloodflag_escort_master",
        "guild_bloodflag_escort_master",
        "guild_bloodflag_escort_master",
    ]
    assert escort.enemy_troops == {
        "qiang_ba": 1180,
        "jian_hao": 980,
        "divine_archer": 1080,
        "quan_wang": 860,
    }
    assert escort.enemy_technology == {"level": 10, "guest_level": 102, "guest_bonus": 0.62}
    assert assault_keys == [
        "guild_blackwind_gate_general",
        "guild_blackwind_gate_overseer",
        "guild_blackwind_iron_guard",
        "guild_blackwind_iron_guard",
        "guild_blackwind_iron_guard",
        "guild_blackwind_bow_captain",
        "guild_blackwind_bow_captain",
        "guild_blackwind_bow_captain",
        "guild_blackwind_assault_blade",
        "guild_blackwind_assault_blade",
        "guild_blackwind_assault_blade",
        "guild_blackwind_assault_blade",
        "guild_blackwind_assault_blade",
        "guild_blackwind_patrol_captain",
        "guild_blackwind_patrol_captain",
        "guild_blackwind_guard_soldier",
        "guild_blackwind_guard_soldier",
        "guild_blackwind_guard_soldier",
        "guild_blackwind_spear_soldier",
        "guild_blackwind_spear_soldier",
        "guild_blackwind_spear_soldier",
        "guild_blackwind_spear_soldier",
    ]
    assert assault.enemy_troops == {
        "qiang_wang": 1700,
        "jian_sheng": 1450,
        "arrow_god": 1620,
        "quan_sheng": 980,
        "dao_sheng": 930,
    }
    assert assault.enemy_technology == {"level": 12, "guest_level": 108, "guest_bonus": 0.96}

    guest_keys = set(patrol_keys + escort_keys + assault_keys)
    existing_keys = set(GuestTemplate.objects.filter(key__in=guest_keys).values_list("key", flat=True))
    assert guest_keys == existing_keys


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
    assert mission.task_type == "troop"
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
