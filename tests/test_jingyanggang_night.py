"""景阳冈夜间属性、昼夜边界及简介同步。"""

import json
from datetime import datetime
from datetime import timezone as dt_timezone
from importlib import import_module
from pathlib import Path
from random import Random
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
import yaml
from django.apps import apps
from django.db import connection

from battle.combatants_pkg.guest_builder import serialize_guest_for_report
from battle.services import _build_defender_guest_and_loadout, _extract_defender_tech_profile
from gameplay.models import MissionTemplate
from gameplay.services.missions_impl.execution_adapters import normalize_guest_configs, normalize_mapping
from gameplay.services.missions_impl.launch_post_actions import build_defender_setup_and_drop_table
from gameplay.services.missions_impl.launch_resilience import prepare_launch_report_best_effort
from guests.models import GuestTemplate

rules = import_module("gameplay.migrations.0260_jingyanggang_night_warning")
MISSIONS = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/mission_templates.yaml").read_text())["missions"]
MISSION = next(row for row in MISSIONS if row["key"] == "jingyanggang")


def _setup(hour, minute=0, *, mission_key="jingyanggang"):
    mission = SimpleNamespace(**{**MISSION, "key": mission_key, "is_defense": False, "enemy_troops": {}})
    now = datetime(2026, 10, 25, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai"))
    setup, _ = build_defender_setup_and_drop_table(
        mission,
        {},
        normalize_guest_configs=normalize_guest_configs,
        normalize_mapping=normalize_mapping,
        now=now.astimezone(dt_timezone.utc),
    )
    return setup


def _defenders(setup):
    _, level, bonuses, skills = _extract_defender_tech_profile(setup)
    defenders, _ = _build_defender_guest_and_loadout(
        defender_guests=None,
        defender_setup=setup,
        defender_limit=2,
        fill_default_troops=False,
        rng=Random(13),
        now=datetime(2026, 10, 26, 12, tzinfo=dt_timezone.utc),
        defender_guest_level=level,
        defender_guest_bonuses=bonuses,
        defender_guest_skills=skills,
    )
    return defenders


@pytest.fixture
def tiger_template(db, monkeypatch):
    template = GuestTemplate.objects.create(
        key="task_jingyang_tiger",
        name="猛虎",
        archetype="military",
        rarity="green",
        base_attack=101,
        base_intellect=20,
        base_defense=80,
        base_agility=70,
        base_hp=1201,
    )
    monkeypatch.setattr("battle.combatants_pkg.ai_generator.get_all_guest_templates", lambda: {template.key: template})
    return template


@pytest.mark.parametrize(
    ("hour", "minute", "hp_multiplier", "attack_multiplier"),
    [
        (5, 59, 2, 1.5),
        (6, 0, 1, 1),
        (12, 0, 1, 1),
        (17, 59, 1, 1),
        (18, 0, 1, 1),
        (21, 59, 1, 1),
        (22, 0, 2, 1.5),
        (23, 59, 2, 1.5),
        (0, 0, 2, 1.5),
    ],
)
def test_tiger_final_stats_follow_shanghai_departure_time(
    tiger_template, hour, minute, hp_multiplier, attack_multiplier
):
    day = _defenders(_setup(12))[0]
    # 模拟异步序列化及延迟到次日生成战报，仍沿用出征时的加成。
    actual = _defenders(json.loads(json.dumps(_setup(hour, minute))))[0]
    assert actual.max_hp == day.max_hp * hp_multiplier
    assert actual.hp == actual.initial_hp == actual.max_hp
    assert actual.attack == pytest.approx(day.attack * attack_multiplier)
    assert actual.defense == day.defense
    assert actual.agility == day.agility
    report = serialize_guest_for_report(actual)
    assert report["hp"] == report["initial_hp"] == actual.max_hp
    assert report["attack"] == actual.attack


def test_other_missions_have_no_night_multiplier():
    assert _setup(23, mission_key="other_mission") == _setup(12, mission_key="other_mission")


def test_launch_preparation_uses_departure_time_after_daybreak(monkeypatch):
    monkeypatch.setattr(
        "gameplay.services.missions_impl.launch_post_actions.timezone.now",
        lambda: datetime(2026, 10, 26, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    dispatch = Mock(return_value=None)
    prepare_launch_report_best_effort(
        SimpleNamespace(id=1),
        SimpleNamespace(**{**MISSION, "id": 1, "is_defense": False, "enemy_troops": {}}),
        SimpleNamespace(id=1, started_at=datetime(2026, 10, 25, 22, tzinfo=ZoneInfo("Asia/Shanghai"))),
        [],
        {},
        0,
        13,
        None,
        logger=Mock(),
        build_defender_setup_and_drop_table=build_defender_setup_and_drop_table,
        normalize_guest_configs=normalize_guest_configs,
        normalize_mapping=normalize_mapping,
        dispatch_or_sync_launch_report=dispatch,
        attach_run_report_if_empty=Mock(),
        build_guest_snapshot_proxies=Mock(),
        build_guest_battle_snapshots=Mock(),
        generate_sync_battle_report=Mock(),
        safe_apply_async=Mock(),
        settings_obj=SimpleNamespace(DEBUG=False),
        environ={},
        mission_run_model=Mock(),
    )
    assert dispatch.call_args.args[5] == _setup(22)


def test_night_multiplier_only_changes_tiger(tiger_template, monkeypatch):
    other = GuestTemplate.objects.create(key="other_enemy", name="其他敌人", rarity="green", base_hp=1201)
    monkeypatch.setattr(
        "battle.combatants_pkg.ai_generator.get_all_guest_templates",
        lambda: {tiger_template.key: tiger_template, other.key: other},
    )
    day_setup, night_setup = _setup(12), _setup(23)
    for setup in (day_setup, night_setup):
        setup["guest_keys"].append({"key": other.key})
    day, night = _defenders(day_setup), _defenders(night_setup)
    assert night[0].max_hp == day[0].max_hp * 2
    assert serialize_guest_for_report(night[1]) == serialize_guest_for_report(day[1])


@pytest.mark.django_db
def test_warning_migration_is_idempotent_and_preserves_combat_config():
    mission = MissionTemplate.objects.create(
        key="jingyanggang",
        name=MISSION["name"],
        description=MISSION["description"].replace(rules.WARNING, ""),
        enemy_technology=MISSION["enemy_technology"],
        enemy_guests=MISSION["enemy_guests"],
    )
    for _ in range(2):
        rules.append_night_warning(apps, SimpleNamespace(connection=connection))
    mission.refresh_from_db()
    assert mission.description == MISSION["description"]
    assert mission.description.count(rules.WARNING) == 1
    assert mission.enemy_technology == MISSION["enemy_technology"]
    assert mission.enemy_guests == MISSION["enemy_guests"]
