from __future__ import annotations

import json
from datetime import timedelta
from statistics import median
from time import perf_counter
from types import SimpleNamespace

import pytest
from django.db import connection, transaction
from django.db.models import Q
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import User
from gameplay.models import BotBackfillDemand, BotProfile, Manor
from gameplay.services.virtual_player_core import population_runtime as runtime
from gameplay.services.virtual_player_core.config import V2_PRESTIGE_BAND_NAMES
from gameplay.services.virtual_player_core.population import (
    ArenaHandoffSupply,
    PopulationCell,
    arena_materialization_deficit,
)

pytestmark = pytest.mark.django_db

REGIONS = ("north", "east", "west", "south", "overseas")
ARENA_DEMANDS = {("north", "junior"): 5}
HANDOFF_SUPPLY = {("north", "junior"): ArenaHandoffSupply(available=2)}


@pytest.fixture
def population_world(monkeypatch):
    now = timezone.now()
    monkeypatch.setattr(runtime, "_regions", lambda: list(REGIONS))
    monkeypatch.setattr(
        runtime,
        "assess_virtual_player_runtime",
        lambda: SimpleNamespace(
            v2_population_activation_allowed=True,
            ready_handoff_allowed=True,
            training_admission_allowed=True,
        ),
    )
    monkeypatch.setattr(
        runtime.arena_population, "active_arena_population_demand_by_cell", lambda _config: ARENA_DEMANDS
    )
    monkeypatch.setattr(
        runtime.arena_population, "arena_handoff_supply_by_cell", lambda *_args, **_kwargs: HANDOFF_SUPPLY
    )

    def add_manor(*, region="north", prestige=0, active_days=0, **user_fields):
        username = f"population-aggregate-{User.objects.count()}"
        # Avoid registration/bootstrap side effects: these are count-only fixtures.
        User.objects.bulk_create([User(username=username, **user_fields)])
        return Manor.objects.create(
            user=User.objects.get(username=username),
            region=region,
            prestige=prestige,
            last_active_at=now - timedelta(days=active_days),
        )

    for region in REGIONS:
        for prestige in (0, 99, 100, 499, 500, 1000):
            add_manor(region=region, prestige=prestige)
    add_manor(active_days=7)
    add_manor(active_days=8)
    add_manor(is_staff=True)
    add_manor(is_superuser=True)
    add_manor(region="unknown")

    bot_specs = (
        ("active", 2, 2, "junior", "newbie", 0, None, None),
        ("slowing", 2, 2, "newbie", "", 100, now, now),
        ("abandoned", 2, 2, "junior", "junior", 500, now + timedelta(seconds=1), None),
        ("active", 2, 2, "junior", "newbie", 1000, None, now + timedelta(seconds=1)),
        ("active", 2, 2, "newbie", "unknown", 99, now - timedelta(seconds=1), None),
        ("active", 2, 1, "junior", "junior", 499, None, None),
        ("active", 1, 0, "junior", "junior", 100, None, None),
        ("retired", 2, 2, "newbie", "newbie", 0, None, None),
        ("stale", 2, 2, "newbie", "newbie", 0, None, None),
    )
    for region in REGIONS:
        for state, engine, policy, band, target_band, prestige, newbie, peace in bot_specs:
            manor = add_manor(region=region, prestige=prestige)
            manor.newbie_protection_until = newbie
            manor.peace_shield_until = peace
            manor.save(update_fields=["newbie_protection_until", "peace_shield_until"])
            BotProfile.objects.create(
                manor=manor,
                state=state,
                engine_version=engine,
                policy_version=policy,
                rng_version=1,
                plan_schema_version=1,
                policy_checksum="a" * 64,
                last_strength_increase_at=now,
                v2_enrolled_at=now,
                prestige_band=band,
                target_prestige_band=target_band,
                current_prestige_band=band,
                growth_seed=manor.id,
                next_growth_at=now,
                abandon_at=now,
                retire_at=now,
            )
    BotBackfillDemand.objects.create(region="east", prestige_band="junior", needed=6)
    return now


def _legacy_cells(config, *, now, target_based, required_engine):
    """Independent reference: the previous per-cell ORM count behavior."""
    maintained = runtime._maintained_bot_queryset()
    if required_engine is not None:
        maintained = maintained.filter(engine_version=required_engine)
        if required_engine == 2:
            maintained = maintained.filter(policy_version=2)
    attackable = maintained.filter(
        Q(manor__newbie_protection_until__isnull=True) | Q(manor__newbie_protection_until__lte=now),
        Q(manor__peace_shield_until__isnull=True) | Q(manor__peace_shield_until__lte=now),
    )
    demands = {
        (row["region"], row["prestige_band"]): row["needed"]
        for row in BotBackfillDemand.objects.values("region", "prestige_band", "needed")
    }
    cells = []
    for region in REGIONS:
        for band, (low, high) in config["prestige_bands"].items():
            prestige_filter = {"prestige__gte": low}
            if high is not None:
                prestige_filter["prestige__lt"] = high
            membership = (
                Q(target_prestige_band=band) | Q(target_prestige_band="", prestige_band=band)
                if target_based
                else Q(**{f"manor__{key}": value for key, value in prestige_filter.items()})
            )
            cells.append(
                PopulationCell(
                    region=region,
                    prestige_band=band,
                    active_real=Manor.objects.filter(
                        bot_profile__isnull=True,
                        user__is_staff=False,
                        user__is_superuser=False,
                        region=region,
                        last_active_at__gte=now - timedelta(days=7),
                        **prestige_filter,
                    ).count(),
                    maintained_supply=maintained.filter(manor__region=region).filter(membership).count(),
                    attackable_supply=attackable.filter(
                        manor__region=region,
                        **{f"manor__{key}": value for key, value in prestige_filter.items()},
                    ).count(),
                    search_demand=demands.get((region, band), 0),
                    arena_handoff_supply=HANDOFF_SUPPLY.get((region, band), ArenaHandoffSupply()).available,
                    arena_materialization_additional=arena_materialization_deficit(
                        required_handoff=ARENA_DEMANDS.get((region, band), 0) if required_engine == 2 else 0,
                        handoff_supply=HANDOFF_SUPPLY.get((region, band), ArenaHandoffSupply()),
                    ),
                )
            )
    return cells


@pytest.mark.parametrize("regional", [False, True])
@pytest.mark.parametrize("target_based", [False, True, None])
@pytest.mark.parametrize("required_engine", [None, 1, 2])
def test_population_aggregation_matches_previous_complete_plan(
    population_world, monkeypatch, regional, target_based, required_engine
):
    monkeypatch.setattr(runtime, "_uses_regional_population_planning", lambda: regional)
    # Include an overlapping band and an unbounded band; counts must remain
    # independent rather than assigning each row to a single CASE branch.
    config = {
        "population": {"hard_cap": 1000},
        "prestige_bands": {"newbie": [0, 100], "junior": [100, 500], "overlap": [99, None]},
    }
    original_planner = runtime.plan_population_cells
    planner_kwargs = {}

    def record_planner(cells, **kwargs):
        planner_kwargs.update(kwargs)
        return original_planner(cells, **kwargs)

    monkeypatch.setattr(runtime, "plan_population_cells", record_planner)
    actual = runtime._build_population_plan(
        config,
        now=population_world,
        target_based_membership=target_based,
        required_engine_version=required_engine,
    )
    expected_cells = _legacy_cells(
        config,
        now=population_world,
        target_based=regional if target_based is None else target_based,
        required_engine=required_engine,
    )
    assert actual == original_planner(expected_cells, **planner_kwargs)


@pytest.mark.parametrize("band_count", [1, 8])
def test_population_aggregation_uses_two_count_queries_independent_of_cells(population_world, band_count):
    config = {
        "population": {"hard_cap": 1000},
        "prestige_bands": {f"band_{index}": [index * 100, (index + 1) * 100] for index in range(band_count)},
    }
    with CaptureQueriesContext(connection) as actual_queries:
        runtime._build_population_plan(config, now=population_world, required_engine_version=2)
    with CaptureQueriesContext(connection) as previous_queries:
        _legacy_cells(config, now=population_world, target_based=True, required_engine=2)
    # The third query in either implementation reads existing backfill demand.
    assert len(actual_queries) == 3
    assert len(previous_queries) == 3 * len(REGIONS) * band_count + 1


def test_population_aggregation_requeries_after_changes_in_same_transaction(population_world):
    config = {"population": {"hard_cap": 1000}, "prestige_bands": {"newbie": [0, 100]}}
    with transaction.atomic():
        before = runtime._build_population_plan(config, now=population_world, required_engine_version=2)
        manor_id = (
            Manor.objects.filter(
                region="north", bot_profile__isnull=True, user__is_staff=False, user__is_superuser=False, prestige=0
            )
            .order_by("id")
            .values_list("id", flat=True)
            .first()
        )
        Manor.objects.filter(id=manor_id).update(prestige=100)
        after = runtime._build_population_plan(config, now=population_world, required_engine_version=2)
    assert after.by_key[("north", "newbie")].active_real == before.by_key[("north", "newbie")].active_real - 1


def test_population_aggregation_handles_empty_database_and_bands(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "assess_virtual_player_runtime",
        lambda: SimpleNamespace(
            v2_population_activation_allowed=False, ready_handoff_allowed=False, training_admission_allowed=False
        ),
    )
    monkeypatch.setattr(runtime.arena_population, "arena_handoff_supply_by_cell", lambda *_args, **_kwargs: {})
    now = timezone.now()
    empty = runtime._build_population_plan({"prestige_bands": {"newbie": [0, None]}}, now=now)
    assert len(empty.cells) == len(REGIONS)
    assert all(cell.active_real == cell.maintained_supply == cell.attackable_supply == 0 for cell in empty.cells)
    with CaptureQueriesContext(connection) as queries:
        no_bands = runtime._build_population_plan({"prestige_bands": {}}, now=now)
    assert no_bands.cells == ()
    assert len(queries) == 1


@pytest.mark.skipif(connection.vendor != "mysql", reason="requires actual MySQL region collation semantics")
@pytest.mark.parametrize("stored_region", ["NORTH", "nórth", "north "])
def test_population_aggregation_matches_mysql_region_collation(population_world, stored_region):
    config = {"population": {"hard_cap": 1000}, "prestige_bands": {"newbie": [0, None]}}
    before = runtime._build_population_plan(
        config, now=population_world, target_based_membership=True, required_engine_version=2
    )
    real_id = (
        Manor.objects.filter(
            region="north",
            bot_profile__isnull=True,
            last_active_at=population_world,
            user__is_staff=False,
            user__is_superuser=False,
        )
        .order_by("id")
        .values_list("id", flat=True)
        .first()
    )
    bot_id = (
        BotProfile.objects.filter(manor__region="north", engine_version=2, policy_version=2)
        .order_by("id")
        .first()
        .manor_id
    )
    Manor.objects.filter(id__in=[real_id, bot_id]).update(region=stored_region)
    # Use the database's own equality rather than asserting that all MySQL 8
    # collations ignore trailing spaces (NO PAD collations need not do so).
    matched_ids = set(Manor.objects.filter(id__in=[real_id, bot_id], region="north").values_list("id", flat=True))
    previous = _legacy_cells(config, now=population_world, target_based=True, required_engine=2)
    with CaptureQueriesContext(connection) as queries:
        actual = runtime._build_population_plan(
            config, now=population_world, target_based_membership=True, required_engine_version=2
        )
    assert len(queries) == 3
    for expected in previous:
        cell = actual.by_key[(expected.region, expected.prestige_band)]
        assert (cell.active_real, cell.maintained_supply, cell.attackable_supply) == (
            expected.active_real,
            expected.maintained_supply,
            expected.attackable_supply,
        )
    before_north = before.by_key[("north", "newbie")]
    after_north = actual.by_key[("north", "newbie")]
    assert after_north.active_real == before_north.active_real - int(real_id not in matched_ids)
    assert after_north.maintained_supply == before_north.maintained_supply - int(bot_id not in matched_ids)


@pytest.fixture
def population_benchmark_world(population_world):
    """Populate all 40 cells, including target/current and engine mismatches."""
    now = population_world
    rows = []
    for region in REGIONS:
        for index, band in enumerate(V2_PRESTIGE_BAND_NAMES):
            rows.append((region, index * 100, None))
            for engine, policy, state, target in (
                (2, 2, "active", V2_PRESTIGE_BAND_NAMES[(index + 1) % 8]),
                (2, 1, "slowing", ""),
                (1, 0, "active", band),
                (2, 2, "abandoned", band),
            ):
                rows.append((region, index * 100, (engine, policy, state, band, target)))
    usernames = [f"population-benchmark-{index}" for index in range(len(rows))]
    User.objects.bulk_create([User(username=username) for username in usernames])
    users = {user.username: user for user in User.objects.filter(username__in=usernames)}
    Manor.objects.bulk_create(
        [
            Manor(user=users[username], region=region, prestige=prestige, last_active_at=now)
            for username, (region, prestige, _bot_spec) in zip(usernames, rows, strict=True)
        ]
    )
    manors = {manor.user_id: manor for manor in Manor.objects.filter(user_id__in=[user.id for user in users.values()])}
    profiles = []
    for username, (_region, _prestige, bot_spec) in zip(usernames, rows, strict=True):
        if bot_spec is None:
            continue
        engine, policy, state, band, target = bot_spec
        manor = manors[users[username].id]
        profiles.append(
            BotProfile(
                manor=manor,
                state=state,
                engine_version=engine,
                policy_version=policy,
                rng_version=1,
                plan_schema_version=1,
                policy_checksum="a" * 64,
                last_strength_increase_at=now,
                v2_enrolled_at=now,
                prestige_band=band,
                target_prestige_band=target,
                current_prestige_band=band,
                growth_seed=manor.id,
                next_growth_at=now,
                abandon_at=now,
                retire_at=now,
            )
        )
    BotProfile.objects.bulk_create(profiles)
    return now


@pytest.mark.parametrize("target_based", [False, True])
@pytest.mark.parametrize("required_engine", [None, 1, 2])
def test_population_aggregation_benchmark(population_benchmark_world, monkeypatch, target_based, required_engine):
    """Run with -s to report a repeatable SQL-count and warm timing comparison.

    The supplied arena assessment is fixed in both paths: this benchmark
    isolates population counting, and makes no claim about whole-task speed.
    It runs on the normal hermetic gate or root's isolated MySQL test database.
    """
    monkeypatch.setattr(runtime, "_uses_regional_population_planning", lambda: True)
    now = population_benchmark_world
    config = {
        "population": {"hard_cap": 1000},
        "prestige_bands": {
            band: [index * 100, (index + 1) * 100 if index < 7 else None]
            for index, band in enumerate(V2_PRESTIGE_BAND_NAMES)
        },
    }
    original_planner = runtime.plan_population_cells
    planner_kwargs = {}

    def record_planner(cells, **kwargs):
        planner_kwargs.update(kwargs)
        return original_planner(cells, **kwargs)

    monkeypatch.setattr(runtime, "plan_population_cells", record_planner)

    def current():
        return runtime._build_population_plan(
            config, now=now, target_based_membership=target_based, required_engine_version=required_engine
        )

    def previous():
        return original_planner(
            _legacy_cells(config, now=now, target_based=target_based, required_engine=required_engine),
            **planner_kwargs,
        )

    # Warm both paths before recording at least five paired observations.
    assert current() == previous()
    elapsed = {"current": [], "previous": []}
    counts = {"current": [], "previous": []}
    for _repeat in range(5):
        results = {}
        for name, build in (("previous", previous), ("current", current)):
            with CaptureQueriesContext(connection) as queries:
                started_at = perf_counter()
                results[name] = build()
                elapsed[name].append((perf_counter() - started_at) * 1000)
            counts[name].append(len(queries))
        assert results["current"] == results["previous"]
    assert counts["current"] == [3] * 5
    assert counts["previous"] == [121] * 5
    print(
        json.dumps(
            {
                "benchmark": "population_plan_count_queries",
                "database_vendor": connection.vendor,
                "regions": 5,
                "bands": 8,
                "target_based": target_based,
                "required_engine": required_engine,
                "warm_repetitions": 5,
                "previous_queries": 121,
                "current_queries": 3,
                "previous_median_ms": round(median(elapsed["previous"]), 3),
                "current_median_ms": round(median(elapsed["current"]), 3),
            },
            sort_keys=True,
        )
    )
