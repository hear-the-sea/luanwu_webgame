from __future__ import annotations

import logging
import random
from datetime import timedelta
from itertools import count

import pytest
from django.core.cache import cache
from django.utils import timezone

from common.constants.resources import ResourceType
from gameplay.constants import BuildingKeys
from gameplay.models import BotBackfillDemand, BotProfile, BuildingType, Manor, RaidRun, ScoutRecord
from gameplay.services.manor.core import ensure_manor

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
def test_region_search_does_not_record_backfill_demand_from_read_path(settings, django_user_model):
    from gameplay.services.raid.map_search import search_manors_by_region
    from gameplay.services.virtual_players import consume_virtual_player_backfill_demands

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

    assert total == 1
    assert [row["id"] for row in rows] == [searcher.id]
    assert BotProfile.objects.count() == 0
    assert consume_virtual_player_backfill_demands(limit=10) == []


@pytest.mark.django_db
def test_public_manor_info_does_not_record_backfill_demand_from_read_path(settings, django_user_model):
    from gameplay.services.raid.map_search import get_manor_public_info
    from gameplay.services.virtual_players import consume_virtual_player_backfill_demands

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
def test_start_scout_records_backfill_demand_without_creating_bot(settings, django_user_model, monkeypatch):
    from gameplay.services.raid.scout import start_scout
    from gameplay.services.virtual_players import consume_virtual_player_backfill_demands

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
    assert consume_virtual_player_backfill_demands(limit=10) == [
        {"region": "north", "prestige_band": "junior", "needed": 2}
    ]


@pytest.mark.django_db
def test_population_roll_consumes_backfill_demand_for_region_and_band(settings, caplog):
    from gameplay.services.virtual_players import (
        consume_virtual_player_backfill_demands,
        record_virtual_player_backfill_demand,
        roll_virtual_player_population,
    )

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
    assert {profile.prestige_band for profile in created} == {"junior"}
    assert all(0 <= profile.manor.prestige <= 250 for profile in created)
    assert consume_virtual_player_backfill_demands(limit=10) == []
    backfill_log = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "virtual_player_backfill_demand_consumed"
    )
    assert backfill_log.region == "south"
    assert backfill_log.prestige_band == "junior"
    assert backfill_log.created_count == 2
    assert backfill_log.needed == 2


@pytest.mark.django_db
def test_population_roll_requeues_backfill_demand_when_creation_fails(settings, monkeypatch):
    from gameplay.services import virtual_players
    from gameplay.services.virtual_players import (
        consume_virtual_player_backfill_demands,
        record_virtual_player_backfill_demand,
        roll_virtual_player_population,
    )

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

    monkeypatch.setattr(virtual_players, "create_virtual_player", fail_create)

    with pytest.raises(RuntimeError, match="creation failed"):
        roll_virtual_player_population(limit=5, now=timezone.now())

    assert consume_virtual_player_backfill_demands(limit=10) == [
        {"region": "south", "prestige_band": "junior", "needed": 2}
    ]


@pytest.mark.django_db
def test_population_roll_decrements_backfill_demand_only_for_created_rows(settings):
    from gameplay.services.virtual_players import (
        consume_virtual_player_backfill_demands,
        record_virtual_player_backfill_demand,
        roll_virtual_player_population,
    )

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
        {"region": "south", "prestige_band": "junior", "needed": 2}
    ]


@pytest.mark.django_db
def test_lost_population_lock_preserves_invalid_backfill_demand():
    from gameplay.services import virtual_players

    demand = BotBackfillDemand.objects.create(region="north", prestige_band="junior", needed=1)

    def _lost_ownership():
        raise virtual_players.VirtualPlayerPopulationLockLostError(
            "virtual player population roll lock ownership was lost"
        )

    with pytest.raises(virtual_players.VirtualPlayerPopulationLockLostError):
        virtual_players._create_backfill_demanded_players(
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
    from gameplay.services import virtual_players

    demand = BotBackfillDemand.objects.create(region="north", prestige_band="junior", needed=1)

    def _lost_ownership():
        raise virtual_players.VirtualPlayerPopulationLockLostError(
            "virtual player population roll lock ownership was lost"
        )

    with pytest.raises(virtual_players.VirtualPlayerPopulationLockLostError):
        virtual_players._create_backfill_demanded_players(
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
    demand = BotBackfillDemand.objects.create(region="north", prestige_band="junior", needed=1)
    guard_checks = 0

    def _lose_after_transaction_starts():
        nonlocal guard_checks
        guard_checks += 1
        if guard_checks >= 2:
            raise virtual_players.VirtualPlayerPopulationLockLostError(
                "virtual player population roll lock ownership was lost"
            )

    with pytest.raises(virtual_players.VirtualPlayerPopulationLockLostError):
        virtual_players._create_backfill_demanded_players(
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

    inactive_count = BotProfile.objects.filter(state__in=[BotProfile.State.STALE, BotProfile.State.RETIRED]).count()
    assert inactive_count >= 2
    assert Manor.objects.filter(id__in=manor_ids).count() == 3
    overpopulation_log = next(
        record for record in caplog.records if getattr(record, "event", None) == "virtual_player_overpopulation_retired"
    )
    assert overpopulation_log.target == 0
    assert overpopulation_log.excess == 3
    assert overpopulation_log.retired_count == 3


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
        raise virtual_players.VirtualPlayerPopulationLockLostError(
            "virtual player population roll lock ownership was lost"
        )

    with pytest.raises(virtual_players.VirtualPlayerPopulationLockLostError):
        virtual_players._retire_excess_virtual_players(
            target=0,
            now=timezone.now(),
            ownership_guard=_lost_ownership,
        )

    profile.refresh_from_db()
    assert profile.state == BotProfile.State.ACTIVE


@pytest.mark.django_db
def test_due_maintenance_moves_profiles_through_slowing_stale_and_retired(settings):
    from gameplay.services.virtual_players import (
        BotProjectionConfig,
        create_virtual_player,
        maintain_due_virtual_players,
    )

    _bootstrap_building_types()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {"min_per_region": 0, "min_attackable_per_band": 0, "hard_cap": 10},
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
    assert profile.state == BotProfile.State.STALE
    assert profile.maintenance_stopped_at is None

    BotProfile.objects.filter(pk=profile.pk).update(next_growth_at=now - timedelta(minutes=1))
    assert maintain_due_virtual_players(now=now, limit=10) == 1
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
        now=now,
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
    assert profile.state == BotProfile.State.STALE


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
        last_planned_at=now - timedelta(days=60),
    )
    ScoutRecord.objects.filter(defender=profile.manor).delete()
    RaidRun.objects.filter(defender=profile.manor).delete()

    assert maintain_due_virtual_players(now=now, limit=10) == 1

    profile.refresh_from_db()
    assert profile.state == BotProfile.State.STALE
