from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from gameplay.constants import REGION_CHOICES
from gameplay.models import (
    BotInventoryDailyCounter,
    BotPopulationRecomputeDemand,
    BotProfile,
    BotRuntimeRoutingState,
    Building,
    InventoryItem,
    Manor,
    PlayerTechnology,
    PlayerTroop,
)
from gameplay.services import virtual_players
from gameplay.services.virtual_player_core import bootstrap, population_runtime
from gameplay.services.virtual_player_core.config import V2_PRESTIGE_BAND_NAMES
from gameplay.services.virtual_player_core.contracts import BotProjectionConfig, PopulationMutationStatus
from gameplay.services.virtual_player_core.policy_registry import release_configured_policy_operation
from guests.models import GearItem, Guest, GuestSkill

pytestmark = pytest.mark.django_db

FIXED_NOW = datetime(2026, 7, 28, 8, 0, tzinfo=UTC)


@pytest.fixture
def released_v2_policy(db):
    return release_configured_policy_operation(version=2, apply=True)


def _set_bootstrap_mode(mode: str) -> None:
    maintenance_mode = {
        BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE: BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
        BotRuntimeRoutingState.BootstrapMode.V2_PAUSED: BotRuntimeRoutingState.MaintenanceMode.V2_PAUSED,
    }.get(mode, BotRuntimeRoutingState.MaintenanceMode.LEGACY_BEFORE_GATE)
    BotRuntimeRoutingState.objects.update_or_create(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        defaults={
            "bootstrap_mode": mode,
            "maintenance_mode": maintenance_mode,
            "calibration_routes": [],
        },
    )


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


def _captured_dml(captured: CaptureQueriesContext) -> list[str]:
    prefixes = ("INSERT ", "UPDATE ", "DELETE ")
    return [query["sql"] for query in captured.captured_queries if query["sql"].lstrip().upper().startswith(prefixes)]


def _create_through_public_facade(*, seed: int) -> BotProfile:
    return virtual_players.create_virtual_player(
        region="north",
        prestige_band="newbie",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=seed,
        now=FIXED_NOW,
        projection=BotProjectionConfig(
            prestige=0,
            building_level=1,
            guest_count=0,
            guest_level=1,
        ),
        start_from_zero=True,
    )


def _retire(profile: BotProfile) -> None:
    BotProfile.objects.filter(pk=profile.pk).update(
        state=BotProfile.State.RETIRED,
        maintenance_stopped_at=FIXED_NOW,
    )
    profile.refresh_from_db()


def _create_with_capacity(*, seed: int):
    return population_runtime.create_virtual_player_with_capacity(
        region="north",
        prestige_band="newbie",
        archetype=BotProfile.Archetype.BALANCED,
        growth_seed=seed,
        now=FIXED_NOW,
    )


def _materialize_v2_profile(
    *,
    region: str,
    prestige_band: str,
    seed: int,
    archetype: str = BotProfile.Archetype.BALANCED,
) -> BotProfile:
    plan = bootstrap.build_virtual_player_v2_bootstrap_plan(
        region=region,
        prestige_band=prestige_band,
        archetype=archetype,
        growth_seed=seed,
        now=FIXED_NOW,
    )
    with transaction.atomic():
        population_permit = bootstrap._issue_v2_bootstrap_population_permit(
            region=region,
            prestige_band=prestige_band,
        )
        return bootstrap.create_virtual_player_v2(
            plan=plan,
            population_permit=population_permit,
            now=FIXED_NOW,
        )


def test_raw_public_bootstrap_is_retired_before_gate_d1(game_data) -> None:
    _set_bootstrap_mode(BotRuntimeRoutingState.BootstrapMode.LEGACY_BEFORE_GATE)

    with pytest.raises(bootstrap.V2BootstrapError, match="legacy virtual-player bootstrap is retired"):
        _create_through_public_facade(seed=900_001)


@pytest.mark.parametrize(
    "bootstrap_mode",
    [
        BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        BotRuntimeRoutingState.BootstrapMode.V2_PAUSED,
    ],
)
def test_raw_public_bootstrap_blocks_v2_routing_without_bootstrap_dml(
    bootstrap_mode: str,
) -> None:
    _set_bootstrap_mode(bootstrap_mode)
    before = _bootstrap_graph_counts()

    with CaptureQueriesContext(connection) as captured:
        with pytest.raises(bootstrap.V2BootstrapError, match="legacy virtual-player bootstrap is retired"):
            _create_through_public_facade(seed=900_002)

    assert _captured_dml(captured) == []
    assert _bootstrap_graph_counts() == before


def test_raw_public_bootstrap_blocks_missing_routing_after_v2_without_dml(
    released_v2_policy,
    game_data,
) -> None:
    v2_profile = _materialize_v2_profile(
        region="north",
        prestige_band="newbie",
        seed=900_003,
    )
    assert v2_profile.engine_version == 2
    BotRuntimeRoutingState.objects.all().delete()
    before = _bootstrap_graph_counts()

    with CaptureQueriesContext(connection) as captured:
        with pytest.raises(bootstrap.V2BootstrapError, match="legacy virtual-player bootstrap is retired"):
            _create_through_public_facade(seed=900_004)

    assert _captured_dml(captured) == []
    assert _bootstrap_graph_counts() == before


def test_raw_public_bootstrap_blocks_corrupt_routing_without_dml() -> None:
    _set_bootstrap_mode(BotRuntimeRoutingState.BootstrapMode.LEGACY_BEFORE_GATE)
    BotRuntimeRoutingState.objects.update(calibration_routes=[{}])
    before = _bootstrap_graph_counts()

    with CaptureQueriesContext(connection) as captured:
        with pytest.raises(bootstrap.V2BootstrapError, match="legacy virtual-player bootstrap is retired"):
            _create_through_public_facade(seed=900_005)

    assert _captured_dml(captured) == []
    assert _bootstrap_graph_counts() == before


def test_v2_population_reactivation_is_the_only_supported_profile_reuse(
    released_v2_policy,
    game_data,
) -> None:
    _set_bootstrap_mode(BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE)
    v2_profile = _materialize_v2_profile(
        region="north",
        prestige_band="newbie",
        seed=901_001,
    )
    assert v2_profile.engine_version == 2
    _retire(v2_profile)

    capacity_result = _create_with_capacity(seed=901_002)
    batch = population_runtime.create_virtual_players_for_band(
        region="north",
        prestige_band="newbie",
        count=1,
        now=FIXED_NOW,
    )

    reactivated = population_runtime.reactivate_retired_virtual_player_with_capacity(
        v2_profile.pk,
        now=FIXED_NOW,
    )

    assert capacity_result.status is PopulationMutationStatus.UNAVAILABLE
    assert capacity_result.profile is None
    assert batch == []
    assert reactivated.status is PopulationMutationStatus.REACTIVATED
    assert reactivated.profile is not None
    assert reactivated.profile.engine_version == 2
    v2_profile.refresh_from_db()
    assert v2_profile.state == BotProfile.State.ACTIVE
    assert v2_profile.next_growth_at > FIXED_NOW


def test_v2_active_public_creation_requires_the_population_consumer(
    released_v2_policy,
    game_data,
) -> None:
    _set_bootstrap_mode(BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE)
    profile_count = BotProfile.objects.count()

    capacity_result = _create_with_capacity(seed=902_001)
    batch = population_runtime.create_virtual_players_for_band(
        region="south",
        prestige_band="mythic",
        count=1,
        archetype=BotProfile.Archetype.GUARD,
        now=FIXED_NOW,
    )

    assert capacity_result.status is PopulationMutationStatus.UNAVAILABLE
    assert capacity_result.profile is None
    assert batch == []
    assert BotProfile.objects.count() == profile_count


@pytest.mark.parametrize(
    "bootstrap_mode",
    [
        BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
        BotRuntimeRoutingState.BootstrapMode.V2_PAUSED,
    ],
)
def test_generate_command_fails_closed_when_direct_creation_is_unavailable(
    bootstrap_mode: str,
    released_v2_policy,
    game_data,
) -> None:
    _set_bootstrap_mode(bootstrap_mode)
    before = _bootstrap_graph_counts()

    with pytest.raises(
        CommandError,
        match="(V2 creation must run through population reconciliation|unknown prestige band|direct virtual-player generation is retired)",
    ):
        call_command(
            "generate_virtual_players",
            "--region",
            "north",
            "--prestige-band",
            "newbie",
            "--count",
            "2",
            stdout=StringIO(),
        )

    assert _bootstrap_graph_counts() == before


def test_v2_active_public_reactivation_requires_the_population_consumer(
    released_v2_policy,
    game_data,
) -> None:
    _set_bootstrap_mode(BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE)
    v2_profile = _materialize_v2_profile(
        region="north",
        prestige_band="newbie",
        seed=903_002,
    )
    _retire(v2_profile)

    reactivated_v2 = population_runtime.reactivate_retired_virtual_player_with_capacity(
        v2_profile.pk,
        now=FIXED_NOW,
    )

    assert reactivated_v2.status is PopulationMutationStatus.REACTIVATED
    assert reactivated_v2.profile is not None
    assert reactivated_v2.profile.engine_version == 2
    v2_profile.refresh_from_db()
    assert v2_profile.state == BotProfile.State.ACTIVE


def test_v2_paused_public_creation_and_reactivation_fail_closed(
    released_v2_policy,
    game_data,
) -> None:
    _set_bootstrap_mode(BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE)
    v2_profile = _materialize_v2_profile(
        region="north",
        prestige_band="newbie",
        seed=904_002,
    )
    _retire(v2_profile)
    profile_count = BotProfile.objects.count()

    _set_bootstrap_mode(BotRuntimeRoutingState.BootstrapMode.V2_PAUSED)
    capacity_result = _create_with_capacity(seed=904_003)
    batch = population_runtime.create_virtual_players_for_band(
        region="north",
        prestige_band="newbie",
        count=1,
        now=FIXED_NOW,
    )
    v2_reactivation = population_runtime.reactivate_retired_virtual_player_with_capacity(
        v2_profile.pk,
        now=FIXED_NOW,
    )

    assert capacity_result.status is PopulationMutationStatus.UNAVAILABLE
    assert capacity_result.profile is None
    assert batch == []
    assert v2_reactivation.status is PopulationMutationStatus.UNAVAILABLE
    assert v2_reactivation.profile is None
    assert BotProfile.objects.count() == profile_count
    assert set(BotProfile.objects.filter(pk=v2_profile.pk).values_list("state", flat=True)) == {
        BotProfile.State.RETIRED
    }


@contextmanager
def _owned_population():
    yield lambda: None


def test_v2_active_hourly_roll_merges_every_v2_cell_without_legacy_roll(
    released_v2_policy,
) -> None:
    _set_bootstrap_mode(BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE)

    processed = population_runtime.roll_virtual_player_population(
        limit=0,
        now=FIXED_NOW,
    )

    expected_cells = {
        (region, prestige_band) for region, _label in REGION_CHOICES for prestige_band in V2_PRESTIGE_BAND_NAMES
    }
    actual_cells = set(BotPopulationRecomputeDemand.objects.values_list("region", "prestige_band"))
    assert processed == 0
    assert actual_cells == expected_cells
    assert all(
        requested == 1 and completed == 0
        for requested, completed in BotPopulationRecomputeDemand.objects.values_list(
            "requested_revision",
            "completed_revision",
        )
    )


def test_v2_paused_hourly_roll_fails_closed_without_demand_or_legacy_roll() -> None:
    _set_bootstrap_mode(BotRuntimeRoutingState.BootstrapMode.V2_PAUSED)

    assert population_runtime.roll_virtual_player_population(limit=8, now=FIXED_NOW) == 0
    assert not BotPopulationRecomputeDemand.objects.exists()
