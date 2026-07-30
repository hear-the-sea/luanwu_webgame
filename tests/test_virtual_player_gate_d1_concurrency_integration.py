from __future__ import annotations

import math
import threading
from dataclasses import replace
from datetime import timedelta
from io import StringIO
from time import perf_counter

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.utils import timezone

from gameplay.models import (
    BotBackfillDemand,
    BotInventoryDailyCounter,
    BotPopulationRecomputeDemand,
    BotProfile,
    BotRuntimeRoutingState,
    Building,
    InventoryItem,
    ItemTemplate,
    Manor,
    PlayerTechnology,
    PlayerTroop,
)
from gameplay.services.virtual_player_core import bootstrap, population_runtime
from gameplay.services.virtual_player_core.config import clear_virtual_player_config_cache, load_virtual_player_config
from gameplay.services.virtual_player_core.policy_registry import release_configured_policy_operation
from gameplay.services.virtual_player_core.population_runtime import (
    PopulationCellReconcileResult,
    PopulationCellReconcileStatus,
    merge_population_recompute_demand,
    merge_population_recompute_demands,
    try_merge_already_classified_mysql_prestige_transition_cells,
)
from gameplay.services.virtual_player_core.projection import BootstrapInventoryTarget
from guests.models import GearItem, Guest, GuestSkill

pytestmark = [pytest.mark.integration]


def _require_mysql() -> None:
    if connection.vendor != "mysql":
        pytest.skip("Gate D1 concurrency evidence requires MySQL row locks")


def _nearest_rank_p95(values: list[float]) -> float:
    assert values
    return sorted(values)[math.ceil(len(values) * 0.95) - 1]


def _bootstrap_graph_counts() -> dict[str, int]:
    return {
        "users": get_user_model().objects.count(),
        "manors": Manor.objects.count(),
        "profiles": BotProfile.objects.count(),
        "buildings": Building.objects.count(),
        "technologies": PlayerTechnology.objects.count(),
        "guests": Guest.objects.count(),
        "gear": GearItem.objects.count(),
        "skills": GuestSkill.objects.count(),
        "troops": PlayerTroop.objects.count(),
        "inventory": InventoryItem.objects.count(),
        "inventory_counters": BotInventoryDailyCounter.objects.count(),
    }


def _create_internal_manor_owner(django_user_model, *, username: str) -> Manor:
    owner = django_user_model(username=username, is_active=False)
    owner.set_unusable_password()
    setattr(owner, "_virtual_player_internal", True)
    owner.save()
    return owner.manor


def _materialize_with_test_permit(
    plan: bootstrap.V2BootstrapPlan,
    *,
    now,
) -> BotProfile:
    with transaction.atomic():
        permit = bootstrap._issue_v2_bootstrap_population_permit(
            region=plan.region,
            prestige_band=plan.prestige_band,
        )
        return bootstrap.create_virtual_player_v2(
            plan=plan,
            population_permit=permit,
            now=now,
        )


@pytest.fixture
def released_v2_policy(db):
    return release_configured_policy_operation(version=1, apply=True)


@pytest.fixture(autouse=True)
def _reset_virtual_player_config_cache():
    clear_virtual_player_config_cache()
    yield
    clear_virtual_player_config_cache()


@pytest.fixture
def v2_bootstrap_game_data(django_db_setup, django_db_blocker, monkeypatch) -> None:
    monkeypatch.setattr(
        "gameplay.management.commands.load_item_templates._load_item_image",
        lambda *_args, **_kwargs: None,
    )
    output = StringIO()
    with django_db_blocker.unblock():
        call_command("load_building_templates", verbosity=0, stdout=output)
        call_command("load_item_templates", verbosity=0, stdout=output)
        call_command(
            "load_troop_templates",
            verbosity=0,
            stdout=output,
            skip_images=True,
        )
        call_command(
            "load_guest_templates",
            verbosity=0,
            stdout=output,
            skip_images=True,
        )


@pytest.mark.django_db(transaction=True)
def test_concurrent_same_cell_merges_preserve_both_revisions() -> None:
    _require_mysql()
    now = timezone.now()
    start = threading.Barrier(2)
    revisions: list[int] = []
    errors: list[BaseException] = []
    results_guard = threading.Lock()

    def _worker() -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            demand = merge_population_recompute_demand(
                region="north",
                prestige_band="newbie",
                now=now,
            )
            with results_guard:
                revisions.append(int(demand.requested_revision))
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=_worker, daemon=True) for _index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    demand = BotPopulationRecomputeDemand.objects.get(
        region="north",
        prestige_band="newbie",
    )
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert sorted(revisions) == [1, 2]
    assert demand.requested_revision == 2
    assert demand.completed_revision == 0


@pytest.mark.django_db(transaction=True)
def test_concurrent_multi_cell_merges_preserve_every_revision() -> None:
    _require_mysql()
    now = timezone.now()
    start = threading.Barrier(2)
    revision_sets: list[tuple[int, int]] = []
    errors: list[BaseException] = []
    results_guard = threading.Lock()

    def _worker() -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            demands = merge_population_recompute_demands(
                [("north", "junior"), ("north", "newbie")],
                now=now,
            )
            with results_guard:
                revision_sets.append(tuple(int(demand.requested_revision) for demand in demands))
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=_worker, daemon=True) for _index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    final_revisions = dict(
        BotPopulationRecomputeDemand.objects.filter(region="north").values_list(
            "prestige_band",
            "requested_revision",
        )
    )
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert sorted(revision_sets) == [(1, 1), (2, 2)]
    assert final_revisions == {"newbie": 2, "junior": 2}


@pytest.mark.django_db(transaction=True)
def test_concurrent_fast_prestige_handoffs_preserve_every_revision() -> None:
    _require_mysql()
    now = timezone.now()
    manor = _create_internal_manor_owner(
        get_user_model(),
        username="gate_d1_fast_prestige_handoff",
    )
    manor.region = "north"
    manor.prestige = 500
    manor.save(update_fields=["region", "prestige"])
    BotProfile.objects.create(
        manor=manor,
        archetype=BotProfile.Archetype.BALANCED,
        state=BotProfile.State.ACTIVE,
        prestige_band="newbie",
        target_prestige_band="newbie",
        current_prestige_band="junior",
        growth_seed=71_001,
        next_growth_at=now + timedelta(hours=1),
        abandon_at=now + timedelta(days=30),
        retire_at=now + timedelta(days=60),
    )
    start = threading.Barrier(2)
    merged_cells: list[tuple[tuple[str, str], ...] | None] = []
    errors: list[BaseException] = []
    results_guard = threading.Lock()

    def _worker() -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            result = try_merge_already_classified_mysql_prestige_transition_cells(
                manor_id=int(manor.id),
                region="north",
                before_prestige=499,
                after_prestige=500,
            )
            with results_guard:
                merged_cells.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=_worker, daemon=True) for _index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    final_revisions = dict(
        BotPopulationRecomputeDemand.objects.filter(region="north").values_list(
            "prestige_band",
            "requested_revision",
        )
    )
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert merged_cells == [
        (("north", "newbie"), ("north", "junior")),
        (("north", "newbie"), ("north", "junior")),
    ]
    assert final_revisions == {"newbie": 2, "junior": 2}


@pytest.mark.django_db(transaction=True)
def test_two_population_workers_materialize_only_one_profile_for_one_deficit(
    settings,
    released_v2_policy,
    v2_bootstrap_game_data,
) -> None:
    _require_mysql()
    settings.VIRTUAL_PLAYER_CONFIG = {
        "population": {
            "region_floor": 0,
            "region_active_multiplier": 0,
            "global_floor": 1,
            "global_active_multiplier": 0,
            "hard_cap": 1,
        }
    }
    clear_virtual_player_config_cache()
    now = timezone.now()
    BotRuntimeRoutingState.objects.create(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        bootstrap_mode=BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        maintenance_mode=BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE,
    )
    BotBackfillDemand.objects.create(
        region="north",
        prestige_band="newbie",
        needed=1,
    )
    merge_population_recompute_demand(
        region="north",
        prestige_band="newbie",
        now=now,
    )
    start = threading.Barrier(2)
    results: list[PopulationCellReconcileResult] = []
    errors: list[BaseException] = []
    results_guard = threading.Lock()

    def _worker() -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            result = population_runtime.reconcile_virtual_player_population_cell(
                region="north",
                prestige_band="newbie",
                limit=1,
                now=now,
            )
            with results_guard:
                results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=_worker, daemon=True) for _index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert sorted(result.status.value for result in results) == sorted(
        [
            PopulationCellReconcileStatus.COMPLETED.value,
            PopulationCellReconcileStatus.NO_DEMAND.value,
        ]
    )
    assert sum(result.created_count for result in results) == 1
    assert BotProfile.objects.filter(engine_version=2).count() == 1
    demand = BotPopulationRecomputeDemand.objects.get(
        region="north",
        prestige_band="newbie",
    )
    assert demand.requested_revision == demand.completed_revision == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_v2_inventory_reservations_roll_back_the_cap_loser(
    released_v2_policy,
    v2_bootstrap_game_data,
) -> None:
    _require_mysql()
    now = timezone.now()
    rare_template = ItemTemplate.objects.create(
        key="aa_v2_concurrent_rare_cap",
        name="V2 concurrent rare cap",
        effect_type=ItemTemplate.EffectType.TOOL,
        rarity="purple",
        tradeable=True,
        price=1,
        storage_space=1,
    )
    config = load_virtual_player_config()
    rare_cap = int((config.get("projection") or {}).get("rare_item_daily_global_cap") or 0)
    assert rare_cap > 0
    counter_date = timezone.localtime(now).date()
    BotInventoryDailyCounter.objects.create(
        category="rare",
        counter_date=counter_date,
        quantity=rare_cap - 1,
    )
    plans: list[bootstrap.V2BootstrapPlan] = []
    for index in range(2):
        plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
            region="south",
            prestige_band="middle",
            archetype=BotProfile.Archetype.RICH,
            growth_seed=882_000 + index,
            now=now,
        )
        rare_target = BootstrapInventoryTarget(
            template_key=rare_template.key,
            quantity=1,
            acquired_day_offset=plan.blueprint.historical_age_days,
        )
        assets = replace(
            plan.blueprint.assets,
            inventory=(*plan.blueprint.assets.inventory, rare_target),
        )
        plans.append(
            replace(
                plan,
                blueprint=replace(plan.blueprint, assets=assets),
            )
        )

    start = threading.Barrier(2)
    profile_ids: list[int] = []
    cap_errors: list[bootstrap.V2BootstrapError] = []
    unexpected_errors: list[BaseException] = []
    results_guard = threading.Lock()

    def _worker(plan: bootstrap.V2BootstrapPlan) -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            profile = _materialize_with_test_permit(plan, now=now)
            with results_guard:
                profile_ids.append(int(profile.id))
        except bootstrap.V2BootstrapError as exc:
            with results_guard:
                cap_errors.append(exc)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                unexpected_errors.append(exc)
        finally:
            close_old_connections()

    threads = [threading.Thread(target=_worker, args=(plan,), daemon=True) for plan in plans]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert all(not thread.is_alive() for thread in threads)
    assert unexpected_errors == []
    assert len(profile_ids) == 1
    assert len(cap_errors) == 1
    assert "inventory daily cap" in str(cap_errors[0])
    counter = BotInventoryDailyCounter.objects.get(
        category="rare",
        counter_date=counter_date,
    )
    assert counter.quantity == rare_cap
    assert BotProfile.objects.filter(engine_version=2).count() == 1
    assert (
        InventoryItem.objects.filter(
            manor__bot_profile__engine_version=2,
            template=rare_template,
        ).count()
        == 1
    )


@pytest.mark.django_db(transaction=True)
def test_v2_bootstrap_retries_real_name_conflict_without_replanning_blueprint(
    released_v2_policy,
    v2_bootstrap_game_data,
    django_user_model,
    monkeypatch,
) -> None:
    _require_mysql()
    now = timezone.now()
    plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        region="north",
        prestige_band="middle",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=884_100,
        now=now,
    )
    occupied_name = "occupied_d1"
    available_name = "available_d1"
    occupied_manor = _create_internal_manor_owner(
        django_user_model,
        username="gate_d1_name_conflict_owner",
    )
    occupied_manor.name = occupied_name
    occupied_manor.save(update_fields=["name"])
    counts_before = _bootstrap_graph_counts()
    names = iter((occupied_name, available_name))
    name_salts: list[int] = []
    materialization_calls = 0
    original_materialize = bootstrap.materialize_bootstrap_assets

    def _next_name(*, growth_seed: int, salt: int = 0) -> str:
        assert growth_seed == plan.growth_seed
        name_salts.append(salt)
        return next(names)

    def _unexpected_replan(*args, **kwargs):
        raise AssertionError("name retry must reuse the original bootstrap plan")

    def _count_materialization(*args, **kwargs):
        nonlocal materialization_calls
        materialization_calls += 1
        return original_materialize(*args, **kwargs)

    monkeypatch.setattr(bootstrap, "_generate_bot_manor_name", _next_name)
    monkeypatch.setattr(
        bootstrap,
        "build_virtual_player_v2_bootstrap_plan",
        _unexpected_replan,
    )
    monkeypatch.setattr(
        bootstrap,
        "materialize_bootstrap_assets",
        _count_materialization,
    )

    profile = _materialize_with_test_permit(plan, now=now)
    profile.manor.refresh_from_db(fields=["name"])

    assert profile.manor.name == available_name
    assert name_salts == [0, 1]
    assert materialization_calls == 1
    assert get_user_model().objects.count() == counts_before["users"] + 1
    assert Manor.objects.count() == counts_before["manors"] + 1
    assert BotProfile.objects.count() == counts_before["profiles"] + 1


@pytest.mark.django_db(transaction=True)
def test_v2_bootstrap_retries_real_coordinate_conflict_before_materializing_assets(
    released_v2_policy,
    v2_bootstrap_game_data,
    django_user_model,
    monkeypatch,
) -> None:
    _require_mysql()
    now = timezone.now()
    plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        region="south",
        prestige_band="middle",
        archetype=BotProfile.Archetype.RICH,
        growth_seed=884_101,
        now=now,
    )
    occupied_coordinate = (941, 942)
    available_coordinate = (943, 944)
    occupied_manor = _create_internal_manor_owner(
        django_user_model,
        username="gate_d1_coordinate_conflict_owner",
    )
    occupied_manor.region = plan.region
    occupied_manor.coordinate_x, occupied_manor.coordinate_y = occupied_coordinate
    occupied_manor.save(update_fields=["region", "coordinate_x", "coordinate_y"])
    coordinates = iter((occupied_coordinate, available_coordinate))
    coordinate_calls = 0
    materialization_calls = 0
    original_materialize = bootstrap.materialize_bootstrap_assets

    def _next_coordinate(region: str) -> tuple[int, int]:
        nonlocal coordinate_calls
        assert region == plan.region
        coordinate_calls += 1
        return next(coordinates)

    def _count_materialization(*args, **kwargs):
        nonlocal materialization_calls
        materialization_calls += 1
        return original_materialize(*args, **kwargs)

    monkeypatch.setattr(bootstrap, "generate_unique_coordinate", _next_coordinate)
    monkeypatch.setattr(
        bootstrap,
        "materialize_bootstrap_assets",
        _count_materialization,
    )

    profile = _materialize_with_test_permit(plan, now=now)
    profile.manor.refresh_from_db(fields=["region", "coordinate_x", "coordinate_y"])

    assert (
        profile.manor.region,
        profile.manor.coordinate_x,
        profile.manor.coordinate_y,
    ) == (plan.region, *available_coordinate)
    assert coordinate_calls == 2
    assert materialization_calls == 1


@pytest.mark.django_db(transaction=True)
def test_v2_bootstrap_coordinate_retry_exhaustion_rolls_back_complete_graph(
    released_v2_policy,
    v2_bootstrap_game_data,
    django_user_model,
    monkeypatch,
) -> None:
    _require_mysql()
    now = timezone.now()
    plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        region="east",
        prestige_band="middle",
        archetype=BotProfile.Archetype.GUARD,
        growth_seed=884_102,
        now=now,
    )
    occupied_coordinate = (945, 946)
    occupied_manor = _create_internal_manor_owner(
        django_user_model,
        username="gate_d1_coordinate_exhaustion_owner",
    )
    occupied_manor.region = plan.region
    occupied_manor.coordinate_x, occupied_manor.coordinate_y = occupied_coordinate
    occupied_manor.save(update_fields=["region", "coordinate_x", "coordinate_y"])
    counts_before = _bootstrap_graph_counts()
    coordinate_calls = 0
    materialization_calls = 0
    original_materialize = bootstrap.materialize_bootstrap_assets

    def _occupied_coordinate(region: str) -> tuple[int, int]:
        nonlocal coordinate_calls
        assert region == plan.region
        coordinate_calls += 1
        return occupied_coordinate

    def _track_materialization(*args, **kwargs):
        nonlocal materialization_calls
        materialization_calls += 1
        return original_materialize(*args, **kwargs)

    monkeypatch.setattr(
        bootstrap,
        "generate_unique_coordinate",
        _occupied_coordinate,
    )
    monkeypatch.setattr(
        bootstrap,
        "materialize_bootstrap_assets",
        _track_materialization,
    )

    with pytest.raises(IntegrityError):
        _materialize_with_test_permit(plan, now=now)

    assert coordinate_calls == bootstrap.VIRTUAL_PLAYER_COORDINATE_RETRY_LIMIT
    assert materialization_calls == 0
    assert _bootstrap_graph_counts() == counts_before


@pytest.mark.django_db(transaction=True)
def test_v2_bootstrap_meets_frozen_mysql_p95_thresholds(
    released_v2_policy,
    v2_bootstrap_game_data,
) -> None:
    _require_mysql()
    warmup_runs = 5
    measured_runs = 30
    planning_durations_ms: list[float] = []
    materialization_durations_ms: list[float] = []
    now = timezone.now()

    for index in range(warmup_runs + measured_runs):
        planning_started = perf_counter()
        plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
            region="west",
            prestige_band="mythic",
            archetype=BotProfile.Archetype.GUARD,
            growth_seed=883_000 + index,
            now=now,
        )
        planning_elapsed_ms = (perf_counter() - planning_started) * 1000

        materialization_started = perf_counter()
        _materialize_with_test_permit(plan, now=now)
        materialization_elapsed_ms = (perf_counter() - materialization_started) * 1000
        if index >= warmup_runs:
            planning_durations_ms.append(planning_elapsed_ms)
            materialization_durations_ms.append(materialization_elapsed_ms)

    planning_p95_ms = _nearest_rank_p95(planning_durations_ms)
    materialization_p95_ms = _nearest_rank_p95(materialization_durations_ms)
    print(
        "gate_d1_bootstrap_p95 "
        f"planning_ms={planning_p95_ms:.3f} "
        f"materialization_ms={materialization_p95_ms:.3f} "
        f"measured_runs={measured_runs}"
    )
    assert planning_p95_ms <= 250
    assert materialization_p95_ms <= 2_000
