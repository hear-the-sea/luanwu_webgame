"""赛季敌军波次与真实战斗、旧队伍快照兼容。"""

from copy import deepcopy
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from django.apps import apps
from django.db import connection, transaction
from django.urls import reverse

from battle.combatants_pkg.cache import clear_guest_template_cache
from core.exceptions import GameError
from gameplay.services.season import expeditions
from gameplay.services.season.campaign_rules import load_campaign_rules, validate_campaign_rules
from gameplay.services.season.combat import build_lineup, prepare_combat, pve_lineup
from gameplay.services.season.expeditions import process_expedition
from gameplay.services.season.npc_encounters import snapshot_npc_encounter
from guests.models import GuestTemplate, Skill
from tests.test_season_rallies import create_rally, launch
from tests.test_season_rallies import rally_world as _rally_world  # noqa: F401
from tests.test_season_resource_integration import advance_resource_run, issue
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture(name="resource_campaign")
def resource_world(request):
    return request.getfixturevalue("_resource_campaign")


@pytest.fixture(name="rally_world")
def npc_rally_world(request):
    return request.getfixturevalue("_rally_world")


@pytest.fixture
def npc_data():
    config = deepcopy(load_campaign_rules()["npc_encounters"])
    for rarity, keys in config["pools"].items():
        for key in keys:
            GuestTemplate.objects.create(
                key=key,
                name=key,
                rarity=rarity,
                archetype="military",
                base_attack=41,
                base_intellect=12,
                base_defense=35,
                base_agility=29,
                base_luck=47,
                base_hp=400,
                growth_range=[3, 7],
            )
    raw = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/guest_skills.yaml").read_text())
    fields = {field.name for field in Skill._meta.concrete_fields}
    for row in raw["skills"]:
        if row["key"] in {
            "iron_wall_heart",
            "guild_wulinzhai_shadow_raid",
            "fatal_chain_sword",
            "endless_chatter",
            "prison_break_blade",
            "meteor_pierce_moon",
        }:
            Skill.objects.create(**{key: value for key, value in row.items() if key in fields})
    clear_guest_template_cache()
    yield config
    clear_guest_template_cache()


def test_collection_npc_progression_and_skills_reach_battle(resource_campaign, npc_data):
    season, player, _ = resource_campaign
    season.rules["campaign"]["npc_encounters"] = npc_data
    encounter = snapshot_npc_encounter(season, "mountain:inn", "pve")
    lineups = [pve_lineup("mountain:inn", "敌军", 0, 12, encounter=encounter, wave_index=i) for i in range(5)]
    assert [len(row["guests"]) for row in lineups] == [1, 1, 2, 2, 2]
    assert [row["guests"][0]["rarity"] for row in lineups] == ["green", "green", "blue", "blue", "purple"]
    assert [row["guests"][0]["level"] for row in lineups] == [20, 40, 70, 85, 110]
    assert [sum(row["troops"].values()) for row in lineups] == [0, 0, 100, 125, 200]
    assert len({row["template_key"] for row in lineups[-1]["guests"]}) == 2
    assert not lineups[0]["guests"][0]["skill_keys"]
    assert lineups[1]["guests"][0]["skill_keys"] == ["endless_chatter"]
    assert [guest["skill_keys"] for guest in lineups[2]["guests"]] == [["fatal_chain_sword"], ["endless_chatter"]]
    assert all(Skill.objects.get(key=key).kind == "active" for key in ("endless_chatter", "fatal_chain_sword"))
    items = yaml.safe_load((Path(__file__).resolve().parents[1] / "data/item_templates.yaml").read_text())["items"]
    learnable = {
        item.get("effect_payload", {}).get("skill_key") for item in items if item.get("effect_type") == "skill_book"
    }
    assert {
        skill
        for wave in npc_data["collection"]["waves"]
        for group in wave.get("skill_sets", [wave["skills"]])
        for skill in group
    } <= learnable
    assert set(npc_data["garrison"]["skills"]) <= learnable
    assert pve_lineup("mountain:inn", "敌军", 0, 12, encounter=encounter) == lineups[0]
    assert (
        len(
            {
                pve_lineup("mountain:inn", "敌军", 0, seed, encounter=encounter)["guests"][0]["template_key"]
                for seed in range(12)
            }
        )
        > 1
    )
    member, guest = player()
    with transaction.atomic():
        attacker = build_lineup(member.manor, [guest.pk])
    first = prepare_combat(member.manor, attacker, lineups[0], "敌军", 1)["prepared"]
    last = prepare_combat(member.manor, attacker, lineups[-1], "敌军", 1)["prepared"]
    assert not first.defender_guests[0].skills
    assert {skill["key"] for skill in last.defender_guests[0].skills} == {"iron_wall_heart", "meteor_pierce_moon"}
    assert {skill["key"] for skill in last.defender_guests[1].skills} == {"iron_wall_heart", "prison_break_blade"}
    assert last.defender_troops


def test_missing_templates_fail_explicitly(resource_campaign, npc_data):
    season, _, _ = resource_campaign
    season.rules["campaign"]["npc_encounters"] = npc_data
    encounter = snapshot_npc_encounter(season, "mountain:inn", "pve")
    GuestTemplate.objects.filter(key=encounter["pools"]["green"][0]).delete()
    with pytest.raises(GameError, match="模板"):
        pve_lineup("mountain:inn", "敌军", 0, 1, encounter=encounter)


def test_missing_later_wave_data_is_rejected_before_dispatch(resource_campaign, npc_data):
    season, player, clock = resource_campaign
    season.rules["campaign"]["npc_encounters"] = npc_data
    season.rules["campaign"]["resource_economy"]["pve"]["collection_waves"] = {
        "enabled": True,
        "max_waves": 5,
        "interval_seconds": 300,
        "level_step": 2,
        "win_percent": 25,
    }
    season.save(update_fields=["rules"])
    GuestTemplate.objects.filter(key=npc_data["pools"]["purple"][0]).delete()
    member, guest = player()
    state = season.faction_states.get(faction=member.faction)
    before = deepcopy(state.resources)
    with pytest.raises(GameError, match="模板"):
        issue(member, guest, clock)
    assert not member.expeditions.exists()
    state.refresh_from_db()
    guest.refresh_from_db()
    assert state.resources == before and guest.status != "deployed"


def test_missing_individual_skill_is_rejected_before_dispatch(resource_campaign, npc_data):
    season, player, clock = resource_campaign
    season.rules["campaign"]["npc_encounters"] = npc_data
    season.rules["campaign"]["resource_economy"]["pve"]["collection_waves"] = {
        "enabled": True,
        "max_waves": 5,
        "interval_seconds": 300,
        "level_step": 2,
        "win_percent": 25,
    }
    season.save(update_fields=["rules"])
    Skill.objects.filter(key="meteor_pierce_moon").delete()
    member, guest = player()
    with pytest.raises(GameError, match="技能"):
        issue(member, guest, clock)
    assert not member.expeditions.exists()


def test_shortened_collection_only_requires_active_wave_assets(resource_campaign, npc_data):
    season, _, _ = resource_campaign
    season.rules["campaign"]["npc_encounters"] = npc_data
    season.rules["campaign"]["resource_economy"]["pve"]["collection_waves"] = {"max_waves": 2}
    GuestTemplate.objects.filter(key__in=npc_data["pools"]["purple"] + npc_data["pools"]["blue"]).delete()
    Skill.objects.filter(key="guild_wulinzhai_shadow_raid").delete()
    assert snapshot_npc_encounter(season, "mountain:inn", "pve")["category"] == "collection"


def test_full_collection_uses_frozen_five_waves(resource_campaign, npc_data, monkeypatch):
    season, player, clock = resource_campaign
    season.rules["campaign"]["npc_encounters"] = npc_data
    season.rules["campaign"]["resource_economy"]["pve"]["collection_waves"] = {
        "enabled": True,
        "max_waves": 5,
        "interval_seconds": 300,
        "level_step": 2,
        "win_percent": 25,
    }
    season.save(update_fields=["rules"])
    member, guest = player()
    run = issue(member, guest, clock)
    season.rules["campaign"]["npc_encounters"]["collection"]["waves"][4]["count"] = 1
    season.rules["campaign"]["npc_encounters"]["collection"]["waves"][4]["skill_sets"].pop()
    season.save(update_fields=["rules"])
    captured = []
    original = expeditions.pve_lineup

    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr(expeditions, "pve_lineup", capture)
    advance_resource_run(run, clock)
    for _ in range(5):
        advance_resource_run(run, clock)
    assert run.status == "returning" and run.result["collection_waves"]["wins"] == 5
    assert [len(row["guests"]) for row in captured] == [1, 1, 2, 2, 2]
    assert [row["guests"][0]["rarity"] for row in captured] == ["green", "green", "blue", "blue", "purple"]


def test_garrison_scales_by_players_and_uses_launch_snapshot(rally_world, npc_data):
    season, player, clock = rally_world
    season.rules["campaign"]["npc_encounters"] = npc_data
    season.save(update_fields=["rules"])
    member, guests = player(count=3, force=8000)
    rally, run = create_rally(member, guests, troops=0)
    launch(rally, run)
    rally.refresh_from_db()
    encounter = rally.result["npc_encounter"]
    assert encounter["player_count"] == 1 and encounter["attacker_guest_count"] == 3
    lineup = pve_lineup("mountain:gate", "守军", 0, 10, encounter=encounter)
    assert len(lineup["guests"]) == 3 and lineup["troops"] == {"dao_sheng": 500}
    assert all(row["rarity"] == "blue" and row["level"] == 120 for row in lineup["guests"])
    scaled = pve_lineup(
        "mountain:gate",
        "守军",
        0,
        10,
        encounter=encounter,
        opening_profile={"level": 1, "count": 1, "rules": {"level_percent_by_players": {"1": 70}}},
    )
    assert len(scaled["guests"]) == 3 and all(row["level"] == 84 for row in scaled["guests"])
    assert process_expedition(run.pk, run.due_at)
    run.refresh_from_db()
    assert run.report is not None and run.report.defender_troops["dao_sheng"] == 500
    assert sum(run.report.defender_troops.values()) == 500
    assert len(run.report.defender_team) == 3
    encounter.update(player_count=5, attacker_guest_count=15)
    lineup = pve_lineup("mountain:gate", "守军", 0, 10, encounter=encounter)
    assert len(lineup["guests"]) == 15 and lineup["troops"] == {"dao_sheng": 2500}


def test_migration_matches_default_and_preserves_active_progress(resource_campaign):
    module = import_module("gameplay.migrations.0269_season_npc_encounters")
    expected = deepcopy(module.DEFAULT_ENCOUNTERS)
    update = import_module("gameplay.migrations.0270_season_collection_difficulty")
    for index, row in update.NEW_WAVES.items():
        expected["collection"]["waves"][index] = row
    expected["garrison"]["skills"] = update.PLAYER_SKILLS
    varied = import_module("gameplay.migrations.0271_season_collection_varied_skills")
    for index, row in varied.NEW_WAVES.items():
        expected["collection"]["waves"][index] = row
    assert expected == load_campaign_rules()["npc_encounters"]
    season, _, _ = resource_campaign
    before = deepcopy(season.rules)
    with transaction.atomic():
        module.enable_npc_encounters(apps, SimpleNamespace(connection=connection))
    season.refresh_from_db()
    assert season.rules["campaign"].pop("npc_encounters") == module.DEFAULT_ENCOUNTERS
    assert season.rules == before


def test_migration_preserves_longer_custom_collection(resource_campaign):
    module = import_module("gameplay.migrations.0269_season_npc_encounters")
    season, _, _ = resource_campaign
    season.rules["campaign"]["resource_economy"]["pve"]["collection_waves"] = {"max_waves": 6}
    season.save(update_fields=["rules"])
    before = deepcopy(season.rules)
    with transaction.atomic():
        module.enable_npc_encounters(apps, SimpleNamespace(connection=connection))
    season.refresh_from_db()
    assert season.rules == before


def test_difficulty_migration_preserves_in_flight_and_other_progress(resource_campaign, npc_data):
    old = import_module("gameplay.migrations.0269_season_npc_encounters")
    update = import_module("gameplay.migrations.0270_season_collection_difficulty")
    season, player, clock = resource_campaign
    season.rules["campaign"]["npc_encounters"] = deepcopy(old.DEFAULT_ENCOUNTERS)
    season.rules["campaign"]["resource_economy"]["pve"]["collection_waves"] = {
        "enabled": True,
        "max_waves": 5,
        "interval_seconds": 300,
        "level_step": 2,
        "win_percent": 25,
    }
    season.save(update_fields=["rules"])
    member, guest = player()
    run = issue(member, guest, clock)
    frozen = deepcopy(run.result)
    before = deepcopy(season.rules)
    with transaction.atomic():
        update.soften_collection(apps, SimpleNamespace(connection=connection))
        update.soften_collection(apps, SimpleNamespace(connection=connection))
    season.refresh_from_db()
    run.refresh_from_db()
    for index, row in update.NEW_WAVES.items():
        before["campaign"]["npc_encounters"]["collection"]["waves"][index] = deepcopy(row)
    before["campaign"]["npc_encounters"]["garrison"]["skills"] = deepcopy(update.PLAYER_SKILLS)
    assert season.rules == before and run.result == frozen
    assert run.result["npc_encounter"]["waves"][4]["count"] == 3


def test_difficulty_migration_preserves_custom_wave(resource_campaign):
    old = import_module("gameplay.migrations.0269_season_npc_encounters")
    update = import_module("gameplay.migrations.0270_season_collection_difficulty")
    season, _, _ = resource_campaign
    season.rules["campaign"]["npc_encounters"] = deepcopy(old.DEFAULT_ENCOUNTERS)
    waves = season.rules["campaign"]["npc_encounters"]["collection"]["waves"]
    waves[4]["count"] = 4
    season.save(update_fields=["rules"])
    with transaction.atomic():
        update.soften_collection(apps, SimpleNamespace(connection=connection))
    season.refresh_from_db()
    waves = season.rules["campaign"]["npc_encounters"]["collection"]["waves"]
    assert waves[3] == update.NEW_WAVES[3] and waves[4]["count"] == 4


def test_varied_skill_migration_preserves_snapshots_and_custom_waves(resource_campaign, npc_data):
    base = import_module("gameplay.migrations.0269_season_npc_encounters")
    old = import_module("gameplay.migrations.0270_season_collection_difficulty")
    varied = import_module("gameplay.migrations.0271_season_collection_varied_skills")
    season, player, clock = resource_campaign
    encounters = deepcopy(base.DEFAULT_ENCOUNTERS)
    for index, row in old.NEW_WAVES.items():
        encounters["collection"]["waves"][index] = deepcopy(row)
    encounters["garrison"]["skills"] = deepcopy(old.PLAYER_SKILLS)
    encounters["collection"]["waves"][4]["level_bonus"] = 95
    season.rules["campaign"]["npc_encounters"] = encounters
    season.rules["campaign"]["resource_economy"]["pve"]["collection_waves"] = {
        "enabled": True,
        "max_waves": 5,
        "interval_seconds": 300,
        "level_step": 2,
        "win_percent": 25,
    }
    season.save(update_fields=["rules"])
    member, guest = player()
    run = issue(member, guest, clock)
    frozen = deepcopy(run.result)
    with transaction.atomic():
        varied.vary_collection_skills(apps, SimpleNamespace(connection=connection))
        varied.vary_collection_skills(apps, SimpleNamespace(connection=connection))
    season.refresh_from_db()
    run.refresh_from_db()
    waves = season.rules["campaign"]["npc_encounters"]["collection"]["waves"]
    assert waves[2] == varied.NEW_WAVES[2] and waves[4]["level_bonus"] == 95
    assert "skill_sets" not in waves[4] and run.result == frozen


def test_hitpoint_siege_uses_new_npc_lineup(rally_world, npc_data):
    season, player, _ = rally_world
    season.rules["campaign"]["npc_encounters"] = npc_data
    season.rules["campaign"]["fortifications"]["enabled"] = True
    season.rules["campaign"]["fortifications"]["hitpoints"]["enabled"] = True
    season.save(update_fields=["rules"])
    member, guests = player(count=3, force=8000)
    rally, run = create_rally(member, guests, troops=0)
    launch(rally, run)
    assert process_expedition(run.pk, run.due_at)
    run.refresh_from_db()
    assert run.report is not None and run.report.defender_troops["dao_sheng"] == 500
    assert len(run.report.defender_team) == 3


def test_invalid_first_wave_is_rejected():
    from django.core.exceptions import ImproperlyConfigured

    config = load_campaign_rules()
    config["npc_encounters"]["collection"]["waves"][0]["count"] = 2
    with pytest.raises(ImproperlyConfigured):
        validate_campaign_rules(config)


def test_invalid_skill_sets_are_rejected():
    from django.core.exceptions import ImproperlyConfigured

    config = load_campaign_rules()
    config["npc_encounters"]["collection"]["waves"][2]["skill_sets"].pop()
    with pytest.raises(ImproperlyConfigured):
        validate_campaign_rules(config)
    config = load_campaign_rules()
    config["npc_encounters"]["collection"]["waves"][0]["skill_sets"] = [["endless_chatter"]]
    with pytest.raises(ImproperlyConfigured):
        validate_campaign_rules(config)


def test_collection_preview_shows_configured_enemies(resource_campaign, npc_data, client):
    season, player, _ = resource_campaign
    season.rules["campaign"]["npc_encounters"] = npc_data
    season.rules["campaign"]["resource_economy"]["pve"]["collection_waves"] = {
        "enabled": True,
        "max_waves": 5,
        "interval_seconds": 300,
        "level_step": 2,
        "win_percent": 25,
    }
    season.save(update_fields=["rules"])
    member, _ = player()
    client.force_login(member.manor.user)
    html = client.get(reverse("gameplay:season"), {"season_id": season.pk}).content.decode()
    assert "第1场 1名绿将 Lv.20" in html
    assert "第5场 2名紫将 Lv.110" in html
