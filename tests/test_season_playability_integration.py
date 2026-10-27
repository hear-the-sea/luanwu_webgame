"""少人协作配置、命令路由和保留进度升级的验收。"""

from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import connection

from gameplay.selectors.season import get_season_context
from gameplay.services.season.actions import perform_action
from gameplay.services.season.playability_rules import DEFAULT_PLAYABILITY, playability_rules, validate_playability
from gameplay.services.season.site_benefits import season_travel_seconds
from tests.test_season_simplification import _resource_campaign as _resource_campaign  # noqa: F401
from tests.test_season_simplification import simple_world as _simple_world  # noqa: F401


@pytest.fixture
def world(request):
    return request.getfixturevalue("_simple_world")


@pytest.mark.django_db
def test_real_reservation_commands_are_idempotent_and_preserve_registration(world):
    season, player, _ = world
    member, guest = player()
    member.lineup = {"registration": {"playtime": "evening"}}
    member.save(update_fields=["lineup"])
    token = str(uuid4())
    payload = {
        "node_key": "mountain:gate",
        "mode": "siege",
        "hours": 2,
        "guest_ids": [guest.pk],
        "troops": {"dao_sheng": 1},
    }
    first = perform_action(member.manor, season.pk, "reservation_create", token, payload)
    assert first == perform_action(member.manor, season.pk, "reservation_create", token, payload)
    member.refresh_from_db()
    assert member.lineup["registration"]["playtime"] == "evening"
    assert not member.expeditions.exists()
    context = get_season_context(member.manor, season.pk)
    assert context["reservation"]["target_name"] == "青石关"
    assert context["weekly_campaign"]["enabled"]
    perform_action(member.manor, season.pk, "reservation_cancel", str(uuid4()), {})
    member.refresh_from_db()
    assert member.lineup["reservation"]["status"] == "cancelled"


@pytest.mark.django_db
def test_gate_speed_is_small_visible_and_never_changes_minimum(world):
    season, player, _ = world
    member, _ = player()
    gate = season.sites.get(key="mountain:gate")
    assert season_travel_seconds(season, member.faction, 300, 60) == 300
    gate.owner = member.faction
    gate.save(update_fields=["owner"])
    assert season_travel_seconds(season, member.faction, 300, 60) == 270
    assert season_travel_seconds(season, member.faction, 60, 60) == 60
    assert season_travel_seconds(season, "history", 300, 60) == 300
    assert get_season_context(member.manor, season.pk)["gate_march_bonus"] == 10


@pytest.mark.django_db
def test_playability_migration_does_not_move_factions_or_reset_existing_score(world):
    season, player, _ = world
    member, _ = player()
    member.merit, member.coins = 888, 444
    member.save(update_fields=["merit", "coins"])
    state = season.faction_states.get(faction=member.faction)
    state.score = 333
    state.save(update_fields=["score"])
    del season.rules["campaign"]["playability"]
    season.save(update_fields=["rules"])
    before_sites = list(season.sites.values())
    before_stock = list(season.faction_states.values("resources", "troops"))
    migration = import_module("gameplay.migrations.0254_season_playability")
    migration.enable_playability(apps, SimpleNamespace(connection=connection))
    season.refresh_from_db()
    assert playability_rules(season)["enabled"]
    state.refresh_from_db()
    assert state.score == state.economy_state["weekly_campaign"]["legacy_score"] == 333
    assert list(season.faction_states.values("resources", "troops")) == before_stock
    assert list(season.sites.values()) == before_sites
    member.refresh_from_db()
    assert (member.faction, member.merit, member.coins) == ("fiction", 888, 444)
    before = deepcopy(state.economy_state)
    migration.enable_playability(apps, SimpleNamespace(connection=connection))
    state.refresh_from_db()
    assert state.economy_state == before


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("balance", "activity_weight", -1),
        ("balance", "power_weight", 100),
        ("npc", "level_percent_by_players", {"1": 10}),
        ("reservation", "guest_limit", 4),
        ("reservation", "maximum_hours", True),
        ("weekly", "personal_items", {"experience_fruit": -1}),
        ("weekly", "goal_minimum", 99999),
        ("weekly", "draw_points", 11),
        ("site_effects", "gate_march_reduction_percent", 100),
    ],
)
def test_bad_playability_config_is_rejected(section, key, value):
    rules = deepcopy(DEFAULT_PLAYABILITY)
    rules[section][key] = value
    with pytest.raises(ImproperlyConfigured):
        validate_playability(rules)


def test_legacy_snapshot_remains_disabled_and_configuration_cannot_mutate_defaults():
    season = SimpleNamespace(rules={"campaign": {}})
    assert not playability_rules(season)["enabled"]
    config = playability_rules(season)
    config["weekly"]["personal_items"].clear()
    assert playability_rules(season)["weekly"]["personal_items"]
