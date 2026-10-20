"""民间传说任务的配置、迁移兼容与真实页面回归。"""

import random
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from django.apps import apps
from django.db import connection
from django.urls import reverse

from battle.combatants_pkg.ai_generator import build_named_ai_guests
from gameplay.models import MissionTemplate
from guests.management.commands.load_guest_templates import Command
from guests.models import GuestTemplate
from guilds.services.personal_missions import PERSONAL_GUILD_MISSION_KEYS

rules = import_module("gameplay.migrations.0217_personal_guild_folk_legends")
names = import_module("gameplay.migrations.0218_personal_guild_mission_names")
profile_alignment = import_module("gameplay.migrations.0229_personal_guild_guest_profile_alignment")
DATA = Path(__file__).resolve().parents[2] / "data"
MISSIONS = {
    row["key"]: row
    for row in yaml.safe_load((DATA / "mission_templates.yaml").read_text())["missions"]
    if row["key"] in PERSONAL_GUILD_MISSION_KEYS
}


def test_folk_legends_reference_existing_skills_and_match_importer_and_migration():
    skills = {row["key"] for row in yaml.safe_load((DATA / "guest_skills.yaml").read_text())["skills"]}
    heroes = yaml.safe_load((DATA / "guests/folk_legends.yaml").read_text())["heroes"]
    assert set(MISSIONS) == set(PERSONAL_GUILD_MISSION_KEYS) == set(rules.MISSION_DEFAULTS)
    for rarity, entries in heroes.items():
        for entry in entries:
            imported = Command()._build_template_from_stats(entry, rarity, entry["archetype"], entry["custom_stats"])
            expected = {
                **rules.ENEMY_DEFAULTS[entry["key"]],
                **profile_alignment.GUEST_DEFAULT_OVERRIDES.get(entry["key"], {}),
            }
            assert {key: imported[key] for key in expected} == expected
            assert imported["recruitable"] is False
    for key, mission in MISSIONS.items():
        expected = {**rules.MISSION_DEFAULTS[key], "name": names.MISSION_NAMES[key]}
        assert {field: value for field, value in mission.items() if field != "key"} == expected
        assert mission["guest_only"] and mission["mission_card_daily_limit"] == 0
        for enemy in mission["enemy_guests"]:
            assert enemy["key"] in rules.ENEMY_DEFAULTS
            assert set(enemy["skills"]) <= skills


@pytest.mark.django_db
def test_legend_migration_preserves_mission_identity_and_existing_run(django_user_model):
    from gameplay.models import Manor, MissionRun
    from guilds.models import Guild, GuildMember, GuildPersonalMissionParticipation

    user = django_user_model.objects.create_user(username="legend_history")
    manor = Manor.objects.get(user=user)
    guild = Guild.objects.create(name="传说帮会", founder=user)
    member = GuildMember.objects.create(guild=guild, user=user)
    key = next(iter(rules.MISSION_DEFAULTS))
    mission, _ = MissionTemplate.objects.update_or_create(key=key, defaults={"name": "旧任务"})
    run = MissionRun.objects.create(manor=manor, mission=mission)
    participation = GuildPersonalMissionParticipation.objects.create(
        mission_run=run, guild=guild, member=member, user=user, contribution_reward=100
    )
    unrelated = MissionTemplate.objects.create(key="legend_unrelated", name="其他任务", daily_limit=7)
    schema = SimpleNamespace(connection=connection)
    for _ in range(2):
        rules.sync_folk_legend_missions(apps, schema)
        names.rename_personal_guild_missions(apps, schema)
    mission.refresh_from_db()
    run.refresh_from_db()
    participation.refresh_from_db()
    unrelated.refresh_from_db()
    assert mission.name == "白蛇盗仙草"
    assert run.mission_id == mission.pk
    assert participation.mission_run_id == run.pk and participation.contribution_reward == 100
    assert unrelated.name == "其他任务" and unrelated.daily_limit == 7
    assert GuestTemplate.objects.filter(key__in=rules.ENEMY_DEFAULTS).count() == 8


@pytest.mark.django_db
@pytest.mark.parametrize("key", sorted(PERSONAL_GUILD_MISSION_KEYS))
def test_legend_enemy_rosters_build_with_their_declared_skills(key, monkeypatch):
    templates = {
        enemy_key: GuestTemplate.objects.update_or_create(key=enemy_key, defaults=defaults)[0]
        for enemy_key, defaults in rules.ENEMY_DEFAULTS.items()
    }
    monkeypatch.setattr("battle.combatants_pkg.ai_generator.get_all_guest_templates", lambda: templates)
    mission = MISSIONS[key]
    enemies = build_named_ai_guests(
        mission["enemy_guests"], mission["enemy_technology"]["guest_level"], rng=random.Random(42)
    )
    assert len(enemies) == len(mission["enemy_guests"])
    for enemy, config in zip(enemies, mission["enemy_guests"]):
        assert enemy.current_hp == enemy.max_hp and enemy.current_hp > 0
        assert enemy._override_skills == config["skills"]
        assert enemy._display_name_override == config["label"]


@pytest.mark.django_db
def test_legend_board_keeps_original_table_and_detail_layout(manor_with_user):
    _manor, client = manor_with_user
    key = "guild_personal_zhenlong_qipu"
    defaults = {**rules.MISSION_DEFAULTS[key], "name": names.MISSION_NAMES[key]}
    MissionTemplate.objects.update_or_create(key=key, defaults=defaults)
    response = client.get(reverse("gameplay:tasks") + f"?tab=guild&mission={key}")
    assert response.status_code == 200
    body = response.content.decode()
    for text in ("白蛇盗仙草", "本周剩余", "任务情报", "任务简介", "加入帮会后参加"):
        assert text in body
    assert body.count('class="tw-mission-name-col">任务名称</th>') == 4
    assert "personal-guild-legends.css" not in body
    assert "legend-book" not in body
    assert "此行所为" not in body
    assert "天龙旧闻" not in body
    assert response.context["active_tab"] == "guild"
