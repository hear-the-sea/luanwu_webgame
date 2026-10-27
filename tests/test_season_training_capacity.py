"""提高募训产能：实际日产、兵力上限和配置迁移边界。"""

from copy import deepcopy
from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection, transaction

from gameplay.services.season.campaign_rules import load_campaign_rules
from gameplay.services.season.resource_production import produce_resources_locked
from gameplay.services.season.resource_rules import training_summary, training_targets
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def training_world(request):
    season, _, clock = request.getfixturevalue("_resource_campaign")
    season.rules["campaign"]["resource_economy"]["training"] = load_campaign_rules()["resource_economy"]["training"]
    for profile in season.population["factions"].values():
        profile["count"] = profile["economic_count"] = 1
    season.save(update_fields=["rules", "population"])
    state = season.faction_states.get(faction="fiction")
    state.resources = dict.fromkeys(state.resources, 100000)
    state.troops = dict.fromkeys(state.troops, 0)
    state.levels = {"training": 1, "logistics": 1, "technology": 1}
    state.economy_state = {"opened": True}
    state.produced_at = clock[0]
    state.save()
    return season, state, clock[0]


def produce(world, cutoff):
    season, state, start = world
    with transaction.atomic():
        produce_resources_locked(
            state, season.rules["campaign"]["economy"], cutoff, {"bloodthirsty_cavalry": (5, start - timedelta(days=1))}
        )
    state.refresh_from_db()


def test_actual_daily_output_is_1000_normal_and_100_cavalry_with_real_costs(training_world):
    _, state, start = training_world
    produce(training_world, start + timedelta(days=1))
    assert sum(v for k, v in state.troops.items() if k != "bloodthirsty_cavalry") == 1000
    assert state.troops["bloodthirsty_cavalry"] == 100
    assert state.economy_state["flow_totals"]["training"] == {"grain": -7000, "wood": -1900, "iron": -2100}
    before = deepcopy(state.troops)
    produce(training_world, start + timedelta(days=1))
    assert state.troops == before


def test_increased_targets_stop_at_3000_normal_and_500_cavalry(training_world):
    season, state, start = training_world
    produce(training_world, start + timedelta(days=10))
    assert training_targets(season, "fiction") == {"normal": 3000, "cavalry": 500}
    assert sum(v for k, v in state.troops.items() if k != "bloodthirsty_cavalry") == 3000
    assert state.troops["bloodthirsty_cavalry"] == 500
    assert training_summary(season, "fiction", {"training": 6})["normal_daily"] == 1750


def test_migration_preserves_stocks_and_uses_old_rate_before_switch(training_world, monkeypatch):
    season, state, start = training_world
    season.rules["campaign"]["resource_economy"]["training"].update(
        {
            "normal_daily_capacity_per_active": 200,
            "normal_target_per_active": 600,
            "cavalry_daily_capacity_per_active": 10,
            "cavalry_target_per_active": 50,
        }
    )
    season.save(update_fields=["rules"])
    state.produced_at = start - timedelta(hours=12)
    state.save(update_fields=["produced_at"])
    migration = import_module("gameplay.migrations.0257_season_training_capacity")
    monkeypatch.setattr(migration.timezone, "now", lambda: start)
    before = (deepcopy(state.resources), deepcopy(state.troops), state.produced_at)
    migration.increase_training(apps, SimpleNamespace(connection=connection))
    migration.increase_training(apps, SimpleNamespace(connection=connection))
    state.refresh_from_db()
    season.refresh_from_db()
    assert (state.resources, state.troops, state.produced_at) == before
    state.season = season
    produce(training_world, start + timedelta(hours=12))
    assert sum(v for k, v in state.troops.items() if k != "bloodthirsty_cavalry") == 600
    assert state.troops["bloodthirsty_cavalry"] == 50
    assert "training_transition" not in state.economy_state
