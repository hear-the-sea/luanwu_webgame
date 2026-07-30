from __future__ import annotations

import logging
import random
from datetime import timedelta
from itertools import count

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from common.constants.resources import ResourceType
from gameplay.constants import BuildingKeys
from gameplay.models import BotBackfillDemand, BotProfile, BuildingType, Manor, RaidRun, ScoutRecord
from gameplay.services.manor.core import ensure_manor
from gameplay.services.virtual_player_core import maintenance as virtual_player_maintenance
from gameplay.services.virtual_player_core import population_runtime as virtual_player_population
from gameplay.services.virtual_player_core.backfill import (
    consume_virtual_player_backfill_demands,
    record_virtual_player_backfill_demand,
)

_COUNTER = count(1)


def _unique(prefix: str) -> str:
    return f"{prefix}_{next(_COUNTER)}"


def _bootstrap_building_types() -> None:
    for key in (
        BuildingKeys.SILVER_VAULT,
        BuildingKeys.GRANARY,
        BuildingKeys.JUXIAN_ZHUANG,
        BuildingKeys.JIADING_FANG,
        BuildingKeys.YOUXIA_BAOTA,
        BuildingKeys.LIANGGONG_CHANG,
    ):
        BuildingType.objects.get_or_create(
            key=key,
            defaults={
                "name": key,
                "resource_type": ResourceType.SILVER,
                "base_cost": {},
            },
        )


def _create_real_manor(django_user_model, *, username: str, region: str, prestige: int) -> Manor:
    user = django_user_model.objects.create_user(username=_unique(username), password="pass123")
    manor = ensure_manor(user)
    manor.region = region
    manor.coordinate_x = 10 + next(_COUNTER)
    manor.coordinate_y = 20 + next(_COUNTER)
    manor.prestige = prestige
    manor.newbie_protection_until = None
    manor.defeat_protection_until = None
    manor.peace_shield_until = None
    manor.last_active_at = timezone.now()
    manor.save(
        update_fields=[
            "region",
            "coordinate_x",
            "coordinate_y",
            "prestige",
            "newbie_protection_until",
            "defeat_protection_until",
            "peace_shield_until",
            "last_active_at",
        ]
    )
    return manor


@pytest.fixture(autouse=True)
def _clear_backfill_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
def test_region_search_is_read_only_and_does_not_create_bots(settings, django_user_model):
    from gameplay.services.raid.map_search import search_manors_by_region

    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 0,
            "min_per_region": 0,
            "min_attackable_per_band": 2,
            "hard_cap": 10,
        },
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    searcher = _create_real_manor(django_user_model, username="backfill_searcher", region="north", prestige=900)

    rows, total = search_manors_by_region(searcher, "north", page=1, page_size=20)
    search_manors_by_region(searcher, "north", page=1, page_size=20)

    assert total == 1
    assert [row["id"] for row in rows] == [searcher.id]
    assert BotProfile.objects.count() == 0
    assert not BotBackfillDemand.objects.exists()


@pytest.mark.django_db
def test_region_backfill_request_records_aggregated_demand_without_creating_bots(settings, django_user_model):
    from gameplay.services.virtual_players import request_virtual_player_backfill_for_region_search

    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 0,
            "min_per_region": 0,
            "min_attackable_per_band": 2,
            "hard_cap": 10,
        },
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    searcher = _create_real_manor(django_user_model, username="backfill_requester", region="north", prestige=900)

    request_virtual_player_backfill_for_region_search(searcher=searcher, region="north")
    request_virtual_player_backfill_for_region_search(searcher=searcher, region="north")

    assert BotProfile.objects.count() == 0
    assert consume_virtual_player_backfill_demands(limit=10) == [
        {"region": "north", "prestige_band": "junior", "needed": 2}
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("protection_field", ["newbie_protection_until", "peace_shield_until"])
def test_region_backfill_request_does_not_record_false_demand_from_searcher_protection(
    settings,
    django_user_model,
    protection_field,
):
    from gameplay.services.raid.map_search import search_manors_by_region
    from gameplay.services.virtual_players import request_virtual_player_backfill_for_region_search

    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {"min_attackable_per_band": 2},
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    searcher = _create_real_manor(django_user_model, username="protected_searcher", region="north", prestige=900)
    _create_real_manor(django_user_model, username="healthy_target_a", region="north", prestige=950)
    _create_real_manor(django_user_model, username="healthy_target_b", region="north", prestige=1000)
    setattr(searcher, protection_field, timezone.now() + timedelta(hours=1))
    searcher.save(update_fields=[protection_field])

    rows, _total = search_manors_by_region(searcher, "north", page=1, page_size=20)
    request_virtual_player_backfill_for_region_search(searcher=searcher, region="north")

    assert not any(row["can_attack"] for row in rows)
    assert not BotBackfillDemand.objects.exists()


@pytest.mark.django_db
def test_record_backfill_demand_reconciles_down_and_clears_zero():
    record_virtual_player_backfill_demand(region="north", prestige_band="junior", needed=3)
    record_virtual_player_backfill_demand(region="north", prestige_band="junior", needed=1)

    assert BotBackfillDemand.objects.get(region="north", prestige_band="junior").needed == 1

    record_virtual_player_backfill_demand(region="north", prestige_band="junior", needed=0)

    assert not BotBackfillDemand.objects.filter(region="north", prestige_band="junior").exists()


@pytest.mark.django_db
def test_record_backfill_demand_handles_racing_first_insert(monkeypatch):
    from django.db.models.query import QuerySet

    BotBackfillDemand.objects.create(region="north", prestige_band="junior", needed=5)
    original_first = QuerySet.first
    hidden_once = False

    def hide_existing_demand_once(queryset):
        nonlocal hidden_once
        if not hidden_once and queryset.model is BotBackfillDemand:
            hidden_once = True
            return None
        return original_first(queryset)

    monkeypatch.setattr(QuerySet, "first", hide_existing_demand_once)

    record_virtual_player_backfill_demand(region="north", prestige_band="junior", needed=2)

    assert BotBackfillDemand.objects.get(region="north", prestige_band="junior").needed == 2


@pytest.mark.django_db
def test_region_backfill_request_counts_candidates_beyond_first_page(settings, django_user_model):
    from gameplay.services.virtual_players import request_virtual_player_backfill_for_region_search

    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 0,
            "min_per_region": 0,
            "min_attackable_per_band": 2,
            "hard_cap": 10,
        },
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    searcher = _create_real_manor(django_user_model, username="paged_searcher", region="north", prestige=900)
    _create_real_manor(django_user_model, username="paged_target_a", region="north", prestige=950)
    _create_real_manor(django_user_model, username="paged_target_b", region="north", prestige=1000)

    request_virtual_player_backfill_for_region_search(searcher=searcher, region="north")

    assert not BotBackfillDemand.objects.exists()


@pytest.mark.django_db
def test_region_backfill_request_uses_full_attack_check(settings, django_user_model):
    from gameplay.services.virtual_players import request_virtual_player_backfill_for_region_search

    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 0,
            "min_per_region": 0,
            "min_attackable_per_band": 1,
            "hard_cap": 10,
        },
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    searcher = _create_real_manor(django_user_model, username="target_protected_searcher", region="north", prestige=900)
    target = _create_real_manor(django_user_model, username="target_protected_target", region="north", prestige=950)
    target.newbie_protection_until = timezone.now() + timedelta(hours=1)
    target.save(update_fields=["newbie_protection_until"])

    request_virtual_player_backfill_for_region_search(searcher=searcher, region="north")

    assert BotBackfillDemand.objects.get(region="north", prestige_band="junior").needed == 1


@pytest.mark.django_db
def test_region_backfill_request_avoids_per_candidate_bot_profile_queries(settings, django_user_model):
    from gameplay.models import BotRuntimeRoutingState
    from gameplay.services.virtual_players import request_virtual_player_backfill_for_region_search

    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 0,
            "min_per_region": 0,
            "min_attackable_per_band": 2,
            "hard_cap": 10,
        },
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    searcher = _create_real_manor(django_user_model, username="bounded_searcher", region="north", prestige=900)
    for index in range(12):
        _create_real_manor(
            django_user_model,
            username=f"bounded_target_{index}",
            region="north",
            prestige=950,
        )
    BotRuntimeRoutingState.objects.create(
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.LEGACY_BEFORE_GATE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE,
    )

    with CaptureQueriesContext(connection) as captured:
        request_virtual_player_backfill_for_region_search(searcher=searcher, region="north")

    bot_profile_queries = [
        query for query in captured.captured_queries if 'from "gameplay_botprofile"' in query["sql"].lower()
    ]
    assert bot_profile_queries == []


@pytest.mark.django_db
def test_population_plan_distinguishes_maintained_and_attackable_supply(settings, django_user_model):
    from gameplay.constants import PVPConstants
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        plan_virtual_player_population,
    )

    _bootstrap_building_types()
    now = timezone.now()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 0,
            "active_window_days": 7,
            "cell_floor": 1,
            "cell_active_multiplier": 1,
            "exploration_supply": 0,
            "hard_cap": 20,
        },
        "prestige_bands": {"junior": [500, 2_000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": [], "troop_template_keys": []},
    }
    attacker = _create_real_manor(
        django_user_model,
        username="supply_attacker",
        region="north",
        prestige=900,
    )
    profiles = [
        create_virtual_player(
            region="north",
            prestige_band="junior",
            growth_seed=5_000 + index,
            now=now,
            projection=BotProjectionConfig(900, 3, 0, 3),
        )
        for index in range(3)
    ]
    protected = profiles[1].manor
    protected.defeat_protection_until = now + timedelta(hours=1)
    protected.save(update_fields=["defeat_protection_until"])
    exhausted = profiles[2].manor
    for _index in range(PVPConstants.RAID_MAX_DAILY_ATTACKS_RECEIVED):
        RaidRun.objects.create(
            attacker=attacker,
            defender=exhausted,
            status=RaidRun.Status.RETURNING,
        )

    plan = plan_virtual_player_population(now=now)
    cell = next(row for row in plan["cells"] if row["region"] == "north" and row["prestige_band"] == "junior")

    assert plan["maintained_bots"] == 3
    assert plan["attackable_bots"] == 1
    assert cell["maintained_supply"] == 3
    assert cell["attackable_supply"] == 1


@pytest.mark.django_db
def test_public_manor_info_does_not_record_backfill_demand_from_read_path(settings, django_user_model):
    from gameplay.services.raid.map_search import get_manor_public_info

    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 0,
            "min_per_region": 0,
            "min_attackable_per_band": 3,
            "hard_cap": 10,
        },
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    viewer = _create_real_manor(django_user_model, username="public_info_viewer", region="north", prestige=900)
    target = _create_real_manor(django_user_model, username="public_info_target", region="north", prestige=950)

    info = get_manor_public_info(target, viewer=viewer)

    assert info["id"] == target.id
    assert BotProfile.objects.count() == 0
    assert consume_virtual_player_backfill_demands(limit=10) == []


@pytest.mark.django_db
def test_start_scout_does_not_record_population_backfill_demand(settings, django_user_model, monkeypatch):
    from gameplay.services.raid.scout import start_scout

    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 0,
            "min_per_region": 0,
            "min_attackable_per_band": 3,
            "hard_cap": 10,
        },
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    attacker = _create_real_manor(django_user_model, username="scout_backfill_attacker", region="north", prestige=900)
    defender = _create_real_manor(django_user_model, username="scout_backfill_defender", region="north", prestige=950)
    monkeypatch.setattr(
        "gameplay.services.raid.scout.scout_start_command.start_scout_command",
        lambda *args, **kwargs: object(),
    )

    start_scout(attacker, defender)

    assert BotProfile.objects.count() == 0
    assert consume_virtual_player_backfill_demands(limit=10) == []


@pytest.mark.django_db
def test_high_band_virtual_player_starts_in_its_actual_prestige_band(settings):
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "prestige_bands": {"newbie": [0, 500], "senior": [8000, 30000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }

    profile = create_virtual_player(
        region="north",
        prestige_band="senior",
        growth_seed=7711,
        projection=BotProjectionConfig(prestige=9000, building_level=14, guest_count=0, guest_level=14),
    )

    profile.refresh_from_db()
    profile.manor.refresh_from_db()
    assert profile.current_prestige_band == "senior"
    assert profile.manor.prestige >= 8000


@pytest.mark.django_db
def test_retired_virtual_player_remains_listed_and_attackable_while_stale_is_hidden(settings, django_user_model):
    from gameplay.services.raid.map_search import search_manors_by_region
    from gameplay.services.raid.utils import can_attack_target
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "prestige_bands": {"newbie": [0, 500]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    attacker = _create_real_manor(django_user_model, username="retired_bot_attacker", region="north", prestige=100)
    profile = create_virtual_player(
        region="north",
        prestige_band="newbie",
        growth_seed=7712,
        projection=BotProjectionConfig(prestige=100, building_level=1, guest_count=0, guest_level=1),
    )
    BotProfile.objects.filter(pk=profile.pk).update(state=BotProfile.State.RETIRED)
    profile.refresh_from_db()
    stale_profile = create_virtual_player(
        region="north",
        prestige_band="newbie",
        growth_seed=7713,
        projection=BotProjectionConfig(prestige=100, building_level=1, guest_count=0, guest_level=1),
    )
    BotProfile.objects.filter(pk=stale_profile.pk).update(state=BotProfile.State.STALE)
    stale_profile.refresh_from_db()

    rows, _total = search_manors_by_region(attacker, "north", page=1, page_size=20)
    listed_ids = {row["id"] for row in rows}

    assert profile.manor_id in listed_ids
    assert can_attack_target(attacker, profile.manor)[0] is True
    assert stale_profile.manor_id not in listed_ids
    assert can_attack_target(attacker, stale_profile.manor)[0] is False


@pytest.mark.django_db
def test_population_roll_provisions_backfill_demand_for_region_and_band(settings, caplog):
    from gameplay.services.virtual_players import roll_virtual_player_population

    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 0,
            "min_per_region": 0,
            "min_attackable_per_band": 0,
            "hard_cap": 10,
            "rolling_batch_size": [1, 1],
        },
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    record_virtual_player_backfill_demand(region="south", prestige_band="junior", needed=2)

    caplog.set_level(logging.INFO, logger="gameplay.services.virtual_players")
    assert roll_virtual_player_population(limit=5, now=timezone.now()) == 2

    created = list(BotProfile.objects.select_related("manor").order_by("id"))
    assert len(created) == 2
    assert {profile.manor.region for profile in created} == {"south"}
    assert {profile.target_prestige_band for profile in created} == {"junior"}
    assert all(profile.manor.prestige == 0 for profile in created)
    assert consume_virtual_player_backfill_demands(limit=10) == [
        {"region": "south", "prestige_band": "junior", "needed": 2}
    ]
    backfill_log = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "virtual_player_backfill_demand_provisioned"
    )
    assert backfill_log.region == "south"
    assert backfill_log.prestige_band == "junior"
    assert backfill_log.created_count == 2
    assert backfill_log.needed == 2


@pytest.mark.django_db
def test_population_roll_requeues_backfill_demand_when_creation_fails(settings, monkeypatch):
    from gameplay.services.virtual_players import roll_virtual_player_population

    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 0,
            "min_per_region": 0,
            "min_attackable_per_band": 0,
            "hard_cap": 10,
            "rolling_batch_size": [1, 1],
        },
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    record_virtual_player_backfill_demand(region="south", prestige_band="junior", needed=2)

    def fail_create(*args, **kwargs):
        raise RuntimeError("creation failed")

    monkeypatch.setattr(
        virtual_player_population,
        "_create_virtual_player_v1",
        fail_create,
    )

    with pytest.raises(RuntimeError, match="creation failed"):
        roll_virtual_player_population(limit=5, now=timezone.now())

    assert consume_virtual_player_backfill_demands(limit=10) == [
        {"region": "south", "prestige_band": "junior", "needed": 2}
    ]


@pytest.mark.django_db
def test_population_roll_keeps_observed_backfill_demand_after_partial_provisioning(settings):
    from gameplay.services.virtual_players import roll_virtual_player_population

    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 0,
            "min_per_region": 0,
            "min_attackable_per_band": 0,
            "hard_cap": 10,
            "rolling_batch_size": [1, 1],
        },
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    record_virtual_player_backfill_demand(region="south", prestige_band="junior", needed=3)

    assert roll_virtual_player_population(limit=1, now=timezone.now()) == 1

    assert consume_virtual_player_backfill_demands(limit=10) == [
        {"region": "south", "prestige_band": "junior", "needed": 3}
    ]


@pytest.mark.django_db
def test_repeated_high_band_shortage_does_not_duplicate_zero_prestige_pipeline(settings):
    from gameplay.services.virtual_players import roll_virtual_player_population

    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "region_floor": 0,
            "region_active_multiplier": 0,
            "global_floor": 10,
            "global_active_multiplier": 0,
            "rolling_batch_size": [2, 2],
        },
        "prestige_bands": {"newbie": [0, 500], "junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": [], "troop_template_keys": []},
    }
    now = timezone.now()
    record_virtual_player_backfill_demand(region="south", prestige_band="junior", needed=2)

    assert roll_virtual_player_population(limit=2, now=now) == 2
    record_virtual_player_backfill_demand(region="south", prestige_band="junior", needed=2)
    assert roll_virtual_player_population(limit=2, now=now + timedelta(hours=1)) == 0

    profiles = BotProfile.objects.select_related("manor").order_by("id")
    assert profiles.count() == 2
    assert {profile.target_prestige_band for profile in profiles} == {"junior"}
    assert {profile.manor.prestige for profile in profiles} == {0}


@pytest.mark.django_db
def test_lost_population_lock_preserves_invalid_backfill_demand():
    demand = BotBackfillDemand.objects.create(region="north", prestige_band="junior", needed=1)

    def _lost_ownership():
        raise virtual_player_population.VirtualPlayerPopulationLockLostError(
            "virtual player population roll lock ownership was lost"
        )

    with pytest.raises(virtual_player_population.VirtualPlayerPopulationLockLostError):
        virtual_player_population._create_backfill_demanded_players(
            demands=[
                {
                    "id": demand.id,
                    "region": demand.region,
                    "prestige_band": demand.prestige_band,
                    "needed": demand.needed,
                }
            ],
            bands={},
            hard_cap=10,
            limit=1,
            now=timezone.now(),
            rng=random.Random(1),
            ownership_guard=_lost_ownership,
        )

    assert BotBackfillDemand.objects.filter(pk=demand.pk, needed=1).exists()


@pytest.mark.django_db
def test_lost_population_lock_stops_before_backfill_create_transaction():
    demand = BotBackfillDemand.objects.create(region="north", prestige_band="junior", needed=1)

    def _lost_ownership():
        raise virtual_player_population.VirtualPlayerPopulationLockLostError(
            "virtual player population roll lock ownership was lost"
        )

    with pytest.raises(virtual_player_population.VirtualPlayerPopulationLockLostError):
        virtual_player_population._create_backfill_demanded_players(
            demands=[
                {
                    "id": demand.id,
                    "region": demand.region,
                    "prestige_band": demand.prestige_band,
                    "needed": demand.needed,
                }
            ],
            bands={"junior": (500, 2000)},
            hard_cap=10,
            limit=1,
            now=timezone.now(),
            rng=random.Random(1),
            ownership_guard=_lost_ownership,
        )

    assert BotBackfillDemand.objects.filter(pk=demand.pk, needed=1).exists()
    assert BotProfile.objects.count() == 0


@pytest.mark.django_db
def test_backfill_rechecks_lost_lock_after_transaction_reads(settings):
    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 0,
            "min_per_region": 0,
            "min_attackable_per_band": 0,
            "hard_cap": 10,
        },
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    demand = BotBackfillDemand.objects.create(region="north", prestige_band="junior", needed=1)
    guard_checks = 0

    def _lose_after_transaction_starts():
        nonlocal guard_checks
        guard_checks += 1
        if guard_checks >= 2:
            raise virtual_player_population.VirtualPlayerPopulationLockLostError(
                "virtual player population roll lock ownership was lost"
            )

    with pytest.raises(virtual_player_population.VirtualPlayerPopulationLockLostError):
        virtual_player_population._create_backfill_demanded_players(
            demands=[
                {
                    "id": demand.id,
                    "region": demand.region,
                    "prestige_band": demand.prestige_band,
                    "needed": demand.needed,
                }
            ],
            bands={"junior": (500, 2000)},
            hard_cap=10,
            limit=1,
            now=timezone.now(),
            rng=random.Random(1),
            ownership_guard=_lose_after_transaction_starts,
        )

    assert guard_checks == 2
    assert BotBackfillDemand.objects.filter(pk=demand.pk, needed=1).exists()
    assert BotProfile.objects.count() == 0


@pytest.mark.django_db
def test_overpopulation_marks_old_active_bots_stale_without_deleting_manors(settings, caplog):
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        roll_virtual_player_population,
    )

    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 0,
            "min_per_region": 0,
            "min_attackable_per_band": 0,
            "hard_cap": 1,
            "rolling_batch_size": [1, 1],
        },
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    now = timezone.now()
    profiles = [
        create_virtual_player(
            region="east",
            prestige_band="junior",
            growth_seed=1000 + idx,
            now=now - timedelta(days=idx + 1),
            projection=BotProjectionConfig(prestige=900 + idx, building_level=3, guest_count=0, guest_level=1),
        )
        for idx in range(3)
    ]
    manor_ids = [profile.manor_id for profile in profiles]

    caplog.set_level(logging.INFO, logger="gameplay.services.virtual_players")
    assert roll_virtual_player_population(limit=5, now=now) == 0

    assert BotProfile.objects.filter(state=BotProfile.State.RETIRED).count() >= 2
    assert Manor.objects.filter(id__in=manor_ids).count() == 3
    overpopulation_log = next(
        record for record in caplog.records if getattr(record, "event", None) == "virtual_player_overpopulation_retired"
    )
    assert overpopulation_log.target == 0
    assert overpopulation_log.excess == 3
    assert overpopulation_log.retired_count == 3


@pytest.mark.django_db
def test_population_retirement_only_marks_bots_in_an_excess_cell(settings):
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        roll_virtual_player_population,
    )

    _bootstrap_building_types()
    now = timezone.now()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 0,
            "cell_floor": 1,
            "cell_active_multiplier": 0,
            "exploration_supply": 0,
            "hard_cap": 10,
            "rolling_batch_size": [1, 1],
        },
        "prestige_bands": {"junior": [500, 2_000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": [], "troop_template_keys": []},
    }
    south = create_virtual_player(
        region="south",
        prestige_band="junior",
        growth_seed=6_001,
        now=now - timedelta(days=30),
        projection=BotProjectionConfig(900, 3, 0, 3),
    )
    north = [
        create_virtual_player(
            region="north",
            prestige_band="junior",
            growth_seed=6_100 + index,
            now=now,
            projection=BotProjectionConfig(900, 3, 0, 3),
        )
        for index in range(2)
    ]

    roll_virtual_player_population(limit=1, now=now)

    south.refresh_from_db()
    assert south.state == BotProfile.State.ACTIVE
    assert (
        BotProfile.objects.filter(
            id__in=[profile.id for profile in north],
            state=BotProfile.State.RETIRED,
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_lost_population_lock_stops_before_overpopulation_bulk_retire(settings):
    from gameplay.services import virtual_players

    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_player_multiplier": 0,
            "min_per_region": 0,
            "min_attackable_per_band": 0,
            "hard_cap": 10,
        },
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    profile = virtual_players.create_virtual_player(
        region="east",
        prestige_band="junior",
        growth_seed=9191,
        now=timezone.now(),
        projection=virtual_players.BotProjectionConfig(
            prestige=900,
            building_level=3,
            guest_count=0,
            guest_level=1,
        ),
    )

    def _lost_ownership():
        raise virtual_player_population.VirtualPlayerPopulationLockLostError(
            "virtual player population roll lock ownership was lost"
        )

    now = timezone.now()
    config = virtual_players.load_virtual_player_config()
    population_plan = virtual_player_population._build_population_plan(config, now=now)
    with pytest.raises(virtual_player_population.VirtualPlayerPopulationLockLostError):
        virtual_player_population._retire_excess_population_cells(
            population_plan,
            config=config,
            now=now,
            ownership_guard=_lost_ownership,
        )

    profile.refresh_from_db()
    assert profile.state == BotProfile.State.ACTIVE


@pytest.mark.django_db
def test_due_maintenance_moves_profiles_from_slowing_directly_to_retired(settings):
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        maintain_due_virtual_players,
    )

    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {"min_per_region": 0, "min_attackable_per_band": 0, "hard_cap": 10},
        "lifecycle": {"stale_no_interaction_days": 0},
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    now = timezone.now()
    profile = create_virtual_player(
        region="west",
        prestige_band="junior",
        growth_seed=2200,
        now=now - timedelta(days=20),
        projection=BotProjectionConfig(prestige=1000, building_level=3, guest_count=0, guest_level=1),
    )
    BotProfile.objects.filter(pk=profile.pk).update(
        next_growth_at=now - timedelta(minutes=1),
        abandon_at=now + timedelta(days=1),
        retire_at=now + timedelta(days=5),
    )

    assert maintain_due_virtual_players(now=now, limit=10) == 1
    profile.refresh_from_db()
    assert profile.state == BotProfile.State.SLOWING

    BotProfile.objects.filter(pk=profile.pk).update(
        next_growth_at=now - timedelta(minutes=1),
        abandon_at=now - timedelta(days=2),
        retire_at=now - timedelta(minutes=1),
    )
    assert maintain_due_virtual_players(now=now, limit=10) == 1
    profile.refresh_from_db()
    assert profile.state == BotProfile.State.RETIRED
    assert profile.maintenance_stopped_at == now

    BotProfile.objects.filter(pk=profile.pk).update(next_growth_at=now - timedelta(minutes=1))
    assert maintain_due_virtual_players(now=now, limit=10) == 0
    profile.refresh_from_db()
    assert profile.state == BotProfile.State.RETIRED
    assert profile.maintenance_stopped_at == now


@pytest.mark.django_db
def test_due_maintenance_marks_bot_stale_after_repeated_empty_raids(settings, django_user_model):
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        maintain_due_virtual_players,
    )

    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "lifecycle": {"empty_hit_stale_threshold": 3},
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    now = timezone.now()
    attacker = _create_real_manor(django_user_model, username="empty_hit_attacker", region="west", prestige=900)
    profile = create_virtual_player(
        region="west",
        prestige_band="junior",
        growth_seed=3300,
        now=now - timedelta(days=2),
        projection=BotProjectionConfig(prestige=1000, building_level=3, guest_count=0, guest_level=1),
    )
    Manor.objects.filter(pk=profile.manor_id).update(silver=0, grain=0)
    BotProfile.objects.filter(pk=profile.pk).update(next_growth_at=now - timedelta(minutes=1))
    for idx in range(3):
        run = RaidRun.objects.create(
            attacker=attacker,
            defender=profile.manor,
            status=RaidRun.Status.RETURNING,
            is_attacker_victory=True,
            loot_resources={},
        )
        RaidRun.objects.filter(pk=run.pk).update(started_at=now - timedelta(hours=idx + 1))

    assert maintain_due_virtual_players(now=now, limit=10) == 1

    profile.refresh_from_db()
    assert profile.state == BotProfile.State.RETIRED
    assert profile.maintenance_stopped_at == now


@pytest.mark.django_db
def test_due_maintenance_marks_bot_stale_after_long_no_interaction(settings):
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        maintain_due_virtual_players,
    )

    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "lifecycle": {"stale_no_interaction_days": 30},
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": []},
    }
    now = timezone.now()
    profile = create_virtual_player(
        region="west",
        prestige_band="junior",
        growth_seed=3400,
        now=now - timedelta(days=60),
        projection=BotProjectionConfig(prestige=1000, building_level=3, guest_count=0, guest_level=1),
    )
    BotProfile.objects.filter(pk=profile.pk).update(
        next_growth_at=now - timedelta(minutes=1),
        created_at=now - timedelta(days=60),
        maintenance_started_at=None,
        last_planned_at=now - timedelta(days=60),
    )
    ScoutRecord.objects.filter(defender=profile.manor).delete()
    RaidRun.objects.filter(defender=profile.manor).delete()

    assert maintain_due_virtual_players(now=now, limit=10) == 1

    profile.refresh_from_db()
    assert profile.state == BotProfile.State.RETIRED
    assert profile.maintenance_stopped_at == now


def test_retired_reactivation_decision_is_stable_and_honors_probability_boundaries():
    now = timezone.now()
    kwargs = {
        "region": "north",
        "prestige_band": "junior",
        "profile_id": 42,
    }

    first = virtual_player_maintenance._should_reactivate_retired_player(now=now, chance=0.70, **kwargs)
    assert virtual_player_maintenance._should_reactivate_retired_player(now=now, chance=0.70, **kwargs) is first
    assert virtual_player_maintenance._should_reactivate_retired_player(now=now, chance=0.0, **kwargs) is False
    assert virtual_player_maintenance._should_reactivate_retired_player(now=now, chance=1.0, **kwargs) is True
    decisions = {
        virtual_player_maintenance._should_reactivate_retired_player(
            now=now + timedelta(days=offset),
            chance=0.50,
            **kwargs,
        )
        for offset in range(30)
    }
    assert decisions == {False, True}


@pytest.mark.django_db
def test_new_bot_initializes_maintenance_cycle_start(settings):
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": [], "troop_template_keys": []},
    }
    now = timezone.now()

    profile = create_virtual_player(
        region="north",
        prestige_band="junior",
        growth_seed=7_901,
        now=now,
        projection=BotProjectionConfig(900, 3, 0, 3),
    )

    assert profile.maintenance_started_at == now


@pytest.mark.django_db
def test_automatic_high_band_creation_starts_at_zero_and_keeps_growth_target(settings):
    from gameplay.services.virtual_players import BotProjectionConfig, create_virtual_player

    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "prestige_bands": {"newbie": [0, 500], "senior": [8000, 30000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": [], "troop_template_keys": []},
    }

    profile = create_virtual_player(
        region="north",
        prestige_band="senior",
        growth_seed=99_101,
        projection=BotProjectionConfig(12_000, 10, 4, 18),
        start_from_zero=True,
    )

    assert profile.manor.prestige == 0
    assert profile.growth_stage == 1
    assert profile.target_prestige_band == "senior"
    assert profile.current_prestige_band == "newbie"


@pytest.mark.django_db
def test_shortage_recovery_reactivates_retired_profile_deterministically(settings):
    from gameplay.services import virtual_players

    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": [], "troop_template_keys": []},
    }
    now = timezone.now()
    retired = virtual_players.create_virtual_player(
        region="north",
        prestige_band="junior",
        growth_seed=99_102,
        now=now,
        projection=virtual_players.BotProjectionConfig(900, 3, 0, 1),
    )
    BotProfile.objects.filter(pk=retired.pk).update(state=BotProfile.State.RETIRED)

    recovered = virtual_players.reactivate_virtual_player_profile(retired.pk, now=now)

    assert recovered is not None
    assert recovered.state == BotProfile.State.ACTIVE
    assert recovered.next_growth_at == now
    assert BotProfile.objects.count() == 1


@pytest.mark.django_db
def test_accelerated_growth_keeps_the_existing_normal_growth_schedule(settings):
    from gameplay.services import virtual_players

    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "prestige_bands": {"newbie": [0, 500]},
        "projection": {"guest_template_keys": [], "gear_template_keys": [], "troop_template_keys": []},
    }
    now = timezone.now()
    profile = virtual_players.create_virtual_player(
        region="north",
        prestige_band="newbie",
        growth_seed=99_103,
        now=now,
        projection=virtual_players.BotProjectionConfig(0, 1, 0, 1),
    )
    scheduled_at = now - timedelta(minutes=1)
    BotProfile.objects.filter(pk=profile.pk).update(next_growth_at=scheduled_at)

    assert (
        virtual_players.accelerate_virtual_player_growth(profile.pk, now=now)
        is virtual_players.AcceleratedGrowthOutcome.GROWN
    )

    profile.refresh_from_db()
    assert profile.next_growth_at == scheduled_at
    assert profile.last_planned_at == now


@pytest.mark.django_db
def test_accelerated_growth_does_not_advance_a_future_normal_growth_schedule(settings):
    from gameplay.services import virtual_players

    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "prestige_bands": {"newbie": [0, 500]},
        "projection": {"guest_template_keys": [], "gear_template_keys": [], "troop_template_keys": []},
    }
    now = timezone.now()
    profile = virtual_players.create_virtual_player(
        region="north",
        prestige_band="newbie",
        growth_seed=99_104,
        now=now,
        projection=virtual_players.BotProjectionConfig(0, 1, 0, 1),
    )
    scheduled_at = now + timedelta(days=7)
    BotProfile.objects.filter(pk=profile.pk).update(next_growth_at=scheduled_at)

    assert (
        virtual_players.accelerate_virtual_player_growth(profile.pk, now=now)
        is virtual_players.AcceleratedGrowthOutcome.GROWN
    )

    profile.refresh_from_db()
    assert profile.next_growth_at == scheduled_at
    assert profile.last_planned_at == now


@pytest.mark.django_db
def test_reactivated_bot_ignores_empty_raids_from_previous_maintenance_cycle(settings, django_user_model):
    from gameplay.services import virtual_players

    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {"retired_reactivation_chance": 1.0},
        "lifecycle": {
            "empty_hit_stale_threshold": 3,
            "empty_hit_window_hours": 24,
            "stale_no_interaction_days": 0,
        },
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": [], "troop_template_keys": []},
    }
    now = timezone.now()
    attacker = _create_real_manor(
        django_user_model,
        username="reactivated_empty_hit_attacker",
        region="north",
        prestige=900,
    )
    profile = virtual_players.create_virtual_player(
        region="north",
        prestige_band="junior",
        growth_seed=7_902,
        now=now - timedelta(days=90),
        projection=virtual_players.BotProjectionConfig(900, 3, 0, 3),
    )
    Manor.objects.filter(pk=profile.manor_id).update(silver=0, grain=0)
    BotProfile.objects.filter(pk=profile.pk).update(
        state=BotProfile.State.RETIRED,
        created_at=now - timedelta(days=90),
        maintenance_stopped_at=now - timedelta(days=1),
    )
    for hours_ago in range(1, 4):
        run = RaidRun.objects.create(
            attacker=attacker,
            defender=profile.manor,
            status=RaidRun.Status.RETURNING,
            is_attacker_victory=True,
            loot_resources={},
        )
        RaidRun.objects.filter(pk=run.pk).update(started_at=now - timedelta(hours=hours_ago))

    reactivated = virtual_player_population._try_reactivate_retired_player(
        region="north",
        prestige_band="junior",
        low=500,
        high=2000,
        now=now,
        config=virtual_players.load_virtual_player_config(),
        evaluated_profile_ids=set(),
    )
    assert reactivated is not None
    assert reactivated.maintenance_started_at == now
    assert (
        virtual_player_maintenance._has_repeated_empty_raids(
            reactivated,
            now=now,
            config=virtual_players.load_virtual_player_config(),
        )
        is False
    )
    assert virtual_players.maintain_due_virtual_players(now=now, limit=10) == 1

    profile.refresh_from_db()
    assert profile.state == BotProfile.State.ACTIVE


@pytest.mark.django_db
def test_reactivated_bot_starts_a_new_no_interaction_period(settings):
    from gameplay.services import virtual_players

    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {"retired_reactivation_chance": 1.0},
        "lifecycle": {
            "empty_hit_stale_threshold": 0,
            "stale_no_interaction_days": 30,
        },
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": [], "troop_template_keys": []},
    }
    now = timezone.now()
    profile = virtual_players.create_virtual_player(
        region="north",
        prestige_band="junior",
        growth_seed=7_903,
        now=now - timedelta(days=90),
        projection=virtual_players.BotProjectionConfig(900, 3, 0, 3),
    )
    BotProfile.objects.filter(pk=profile.pk).update(
        state=BotProfile.State.RETIRED,
        created_at=now - timedelta(days=90),
        maintenance_stopped_at=now - timedelta(days=1),
    )

    reactivated = virtual_player_population._try_reactivate_retired_player(
        region="north",
        prestige_band="junior",
        low=500,
        high=2000,
        now=now,
        config=virtual_players.load_virtual_player_config(),
        evaluated_profile_ids=set(),
    )
    assert reactivated is not None
    assert reactivated.maintenance_started_at == now
    assert (
        virtual_player_maintenance._has_long_no_interaction(
            reactivated,
            now=now,
            config=virtual_players.load_virtual_player_config(),
        )
        is False
    )
    assert virtual_players.maintain_due_virtual_players(now=now, limit=10) == 1

    profile.refresh_from_db()
    assert profile.state == BotProfile.State.ACTIVE


@pytest.mark.django_db
def test_backfill_reactivates_most_recent_retired_player_in_matching_cell(settings, caplog):
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        roll_virtual_player_population,
    )

    _bootstrap_building_types()
    now = timezone.now()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "cell_floor": 0,
            "cell_active_multiplier": 0,
            "exploration_supply": 0,
            "retired_reactivation_chance": 1.0,
            "hard_cap": 10,
            "rolling_batch_size": [1, 1],
        },
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": [], "troop_template_keys": []},
    }
    older = create_virtual_player(
        region="north",
        prestige_band="junior",
        growth_seed=8_001,
        now=now - timedelta(days=30),
        projection=BotProjectionConfig(900, 3, 0, 3),
    )
    recent = create_virtual_player(
        region="north",
        prestige_band="junior",
        growth_seed=8_002,
        now=now - timedelta(days=20),
        projection=BotProjectionConfig(900, 3, 0, 3),
    )
    wrong_region = create_virtual_player(
        region="south",
        prestige_band="junior",
        growth_seed=8_003,
        now=now - timedelta(days=10),
        projection=BotProjectionConfig(900, 3, 0, 3),
    )
    BotProfile.objects.filter(pk=older.pk).update(
        state=BotProfile.State.RETIRED,
        maintenance_stopped_at=now - timedelta(days=10),
    )
    BotProfile.objects.filter(pk=recent.pk).update(
        state=BotProfile.State.RETIRED,
        maintenance_stopped_at=now - timedelta(days=1),
    )
    BotProfile.objects.filter(pk=wrong_region.pk).update(
        state=BotProfile.State.RETIRED,
        maintenance_stopped_at=now,
    )
    profile_count = BotProfile.objects.count()
    record_virtual_player_backfill_demand(region="north", prestige_band="junior", needed=1)
    caplog.set_level(logging.INFO, logger="gameplay.services.virtual_players")

    assert roll_virtual_player_population(limit=1, now=now) == 1

    older.refresh_from_db()
    recent.refresh_from_db()
    wrong_region.refresh_from_db()
    assert BotProfile.objects.count() == profile_count
    assert recent.state == BotProfile.State.ACTIVE
    assert recent.maintenance_stopped_at is None
    assert recent.next_growth_at == now
    assert recent.abandon_at > now
    assert recent.retire_at > recent.abandon_at
    assert older.state == BotProfile.State.RETIRED
    assert wrong_region.state == BotProfile.State.RETIRED
    reactivation_log = next(
        record for record in caplog.records if getattr(record, "event", None) == "virtual_player_reactivated"
    )
    assert reactivation_log.profile_id == recent.id
    backfill_log = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "virtual_player_backfill_demand_provisioned"
    )
    assert backfill_log.processed_count == 1
    assert backfill_log.created_count == 0
    assert backfill_log.reactivated_count == 1


@pytest.mark.django_db
def test_backfill_reactivates_retired_even_when_legacy_probability_is_zero(settings):
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        roll_virtual_player_population,
    )

    _bootstrap_building_types()
    now = timezone.now()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "cell_floor": 0,
            "cell_active_multiplier": 0,
            "exploration_supply": 0,
            "retired_reactivation_chance": 0.0,
            "hard_cap": 10,
            "rolling_batch_size": [1, 1],
        },
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": [], "troop_template_keys": []},
    }
    retired = create_virtual_player(
        region="north",
        prestige_band="junior",
        growth_seed=8_101,
        now=now - timedelta(days=20),
        projection=BotProjectionConfig(900, 3, 0, 3),
    )
    BotProfile.objects.filter(pk=retired.pk).update(
        state=BotProfile.State.RETIRED,
        maintenance_stopped_at=now - timedelta(days=1),
    )
    profile_count = BotProfile.objects.count()
    record_virtual_player_backfill_demand(region="north", prestige_band="junior", needed=1)

    assert roll_virtual_player_population(limit=1, now=now) == 1

    retired.refresh_from_db()
    assert retired.state == BotProfile.State.ACTIVE
    assert BotProfile.objects.count() == profile_count


@pytest.mark.django_db
def test_population_deficit_reactivates_retired_player_before_creating(settings, django_user_model):
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        roll_virtual_player_population,
    )

    _bootstrap_building_types()
    now = timezone.now()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "active_window_days": 7,
            "cell_floor": 0,
            "cell_active_multiplier": 1,
            "exploration_supply": 0,
            "retired_reactivation_chance": 1.0,
            "hard_cap": 10,
            "rolling_batch_size": [1, 1],
        },
        "prestige_bands": {"junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": [], "troop_template_keys": []},
    }
    _create_real_manor(django_user_model, username="reactivation_active_real", region="north", prestige=900)
    retired = create_virtual_player(
        region="north",
        prestige_band="junior",
        growth_seed=8_201,
        now=now - timedelta(days=20),
        projection=BotProjectionConfig(900, 3, 0, 3),
    )
    BotProfile.objects.filter(pk=retired.pk).update(
        state=BotProfile.State.RETIRED,
        maintenance_stopped_at=now - timedelta(days=1),
    )
    profile_count = BotProfile.objects.count()

    assert roll_virtual_player_population(limit=1, now=now) == 1

    retired.refresh_from_db()
    assert retired.state == BotProfile.State.ACTIVE
    assert BotProfile.objects.count() == profile_count


@pytest.mark.django_db
def test_dynamic_population_deficit_reactivates_target_band_profile_before_creating(
    settings,
    django_user_model,
):
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        roll_virtual_player_population,
    )

    _bootstrap_building_types()
    now = timezone.now()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "region_floor": 0,
            "region_active_multiplier": 1,
            "global_floor": 1,
            "global_active_multiplier": 20,
            "rolling_batch_size": [1, 1],
        },
        "prestige_bands": {"newbie": [0, 500], "junior": [500, 2000]},
        "projection": {"guest_template_keys": [], "gear_template_keys": [], "troop_template_keys": []},
    }
    _create_real_manor(
        django_user_model,
        username="target_band_reactivation_real",
        region="north",
        prestige=900,
    )
    retired = create_virtual_player(
        region="north",
        prestige_band="junior",
        growth_seed=8_301,
        now=now - timedelta(days=20),
        projection=BotProjectionConfig(900, 3, 0, 3),
        start_from_zero=True,
    )
    BotProfile.objects.filter(pk=retired.pk).update(
        state=BotProfile.State.RETIRED,
        maintenance_stopped_at=now - timedelta(days=1),
    )
    profile_count = BotProfile.objects.count()

    assert roll_virtual_player_population(limit=1, now=now) == 1

    retired.refresh_from_db()
    assert retired.state == BotProfile.State.ACTIVE
    assert retired.manor.prestige == 0
    assert retired.target_prestige_band == "junior"
    assert BotProfile.objects.count() == profile_count
