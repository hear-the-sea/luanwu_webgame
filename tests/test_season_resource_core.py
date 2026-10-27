"""资源产消的数量守恒、首次开仓和真实总兵力约束。"""

from datetime import timedelta

import pytest
from django.db import transaction

from gameplay.services.season.economy import produce_locked
from gameplay.services.season.resource_production import open_new_faction_locked
from gameplay.services.season.resource_rules import daily_resource_output, resource_rules
from tests.test_season_resource_integration import issue
from tests.test_season_resource_integration import resource_campaign as _resource_campaign  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def resource_campaign(request):
    return request.getfixturevalue("_resource_campaign")


def ready(season, now, count=1):
    for profile in season.population["factions"].values():
        profile["count"] = profile["economic_count"] = count
    season.save(update_fields=["population"])
    state = season.faction_states.get(faction="fiction")
    state.economy_state = {"opened": True}
    state.resources = {"grain": 0, "wood": 0, "iron": 0}
    state.troops = {key: (120 if key != "bloodthirsty_cavalry" else 50) for key in state.troops}
    state.produced_at = now
    state.save(update_fields=["economy_state", "resources", "troops", "produced_at"])
    return state


def test_fractional_single_player_production_exact_day_and_no_duplicate_tick(resource_campaign):
    season, _, clock = resource_campaign
    state = ready(season, clock[0])
    for step in range(1, 289):
        with transaction.atomic():
            produce_locked(season, clock[0] + timedelta(seconds=300 * step))
    state.refresh_from_db()
    expected = {k: int(v) for k, v in daily_resource_output(season, "fiction").items()}
    assert state.resources == expected == {"grain": 1100, "wood": 650, "iron": 450}
    before = dict(state.resources)
    with transaction.atomic():
        produce_locked(season, clock[0] + timedelta(days=1))
    state.refresh_from_db()
    assert state.resources == before


def test_actual_training_consumes_all_three_resources_in_ten_person_batches(resource_campaign):
    season, _, clock = resource_campaign
    state = ready(season, clock[0])
    state.resources = dict.fromkeys(state.resources, 2000)
    state.troops["dao_sheng"] -= 10
    state.save(update_fields=["resources", "troops"])
    with transaction.atomic():
        produce_locked(season, clock[0] + timedelta(hours=2))
    state.refresh_from_db()
    assert sum(state.troops.values()) == 650
    assert state.economy_state["flow_totals"]["training"] == {"grain": -60, "wood": -15, "iron": -15}


def test_deployed_survivors_count_towards_target_not_just_warehouse(resource_campaign):
    season, player, clock = resource_campaign
    member, guest = player()
    state = ready(season, clock[0])
    state.resources = dict.fromkeys(state.resources, 3000)
    state.save(update_fields=["resources"])
    run = issue(member, guest, clock)
    run.troops = run.initial_troops = {"dao_sheng": 100}
    run.save(update_fields=["troops", "initial_troops"])
    state.troops["dao_sheng"] -= 100
    state.save(update_fields=["troops"])
    with transaction.atomic():
        produce_locked(season, clock[0] + timedelta(hours=12))
    state.refresh_from_db()
    assert sum(state.troops.values()) == 550
    assert not state.economy_state.get("flow_totals", {}).get("training")


def test_zero_effective_population_stops_production_and_training(resource_campaign):
    season, _, clock = resource_campaign
    state = ready(season, clock[0], 0)
    with transaction.atomic():
        produce_locked(season, clock[0] + timedelta(days=1))
    state.refresh_from_db()
    assert state.resources == {"grain": 0, "wood": 0, "iron": 0}
    assert state.produced_at == clock[0] + timedelta(days=1)


def test_new_faction_initial_grant_once_and_existing_balance_never_reinitialized(resource_campaign):
    season, _, clock = resource_campaign
    state = ready(season, clock[0])
    state.resources = dict.fromkeys(state.resources, 0)
    state.troops = dict.fromkeys(state.troops, 0)
    state.economy_state = {"new_economy": True, "opened": False}
    state.save(update_fields=["resources", "troops", "economy_state"])
    with transaction.atomic():
        assert open_new_faction_locked(state, clock[0])
        assert not open_new_faction_locked(state, clock[0])
    assert state.resources == {"grain": 2400, "wood": 1400, "iron": 1000}
    assert sum(state.troops.values()) == 400
    state.economy_state = {}
    with transaction.atomic():
        assert not open_new_faction_locked(state, clock[0])


def test_full_warehouse_preserves_existing_overflow_and_does_not_train_above_target(resource_campaign):
    season, _, clock = resource_campaign
    state = ready(season, clock[0])
    state.resources = dict.fromkeys(state.resources, 1000000)
    state.save(update_fields=["resources"])
    with transaction.atomic():
        produce_locked(season, clock[0] + timedelta(hours=1))
    state.refresh_from_db()
    assert all(v == 1000000 for v in state.resources.values())
    assert any(state.economy_state["production_overflow_total"].values())


def test_missing_resource_feature_snapshot_keeps_legacy_rules(resource_campaign):
    season, _, _ = resource_campaign
    season.rules["campaign"].pop("resource_economy")
    assert not resource_rules(season)["enabled"]


@pytest.mark.parametrize(
    "path,value",
    [
        (("population", "smoothing_previous_weight"), 1),
        (("transport", "cargo_capacity"), 0),
        (("production", "logistics_multipliers"), []),
        (("training", "normal_daily_capacity_per_active"), -1),
        (("operations", "march_grain_per_100_per_10_minutes"), float("nan")),
    ],
)
def test_invalid_economic_parameters_fail_before_activation(resource_campaign, path, value):
    from django.core.exceptions import ImproperlyConfigured

    from gameplay.services.season.campaign_rules import validate_campaign_rules

    season, _, _ = resource_campaign
    season.rules["campaign"]["resource_economy"][path[0]][path[1]] = value
    with pytest.raises(ImproperlyConfigured):
        validate_campaign_rules(season.rules["campaign"])
