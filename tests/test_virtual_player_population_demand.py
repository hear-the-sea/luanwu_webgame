from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

import pytest
from django.utils import timezone

from gameplay.models import BotPopulationRecomputeDemand
from gameplay.services.virtual_player_core import population_runtime
from gameplay.services.virtual_player_core.population_runtime import (
    PopulationCellReconcileStatus,
    PopulationRecomputeDemandError,
    claim_next_population_recompute_demand,
    claim_population_recompute_demand,
    fail_population_recompute_demand,
    finalize_population_recompute_demand,
    merge_population_recompute_demand,
    merge_population_recompute_demands,
)

pytestmark = pytest.mark.django_db


def test_population_demand_merge_coalesces_cells_in_canonical_lock_order() -> None:
    now = timezone.now()

    rows = merge_population_recompute_demands(
        [
            ("north", "middle"),
            ("east", "mythic"),
            ("east", "newbie"),
            ("east", "newbie"),
        ],
        now=now,
    )

    assert [(row.region, row.prestige_band) for row in rows] == [
        ("east", "newbie"),
        ("east", "mythic"),
        ("north", "middle"),
    ]
    assert [row.requested_revision for row in rows] == [1, 1, 1]
    assert BotPopulationRecomputeDemand.objects.count() == 3


@pytest.mark.parametrize(
    ("region", "prestige_band"),
    [
        ("unknown", "newbie"),
        ("north", "missing"),
    ],
)
def test_population_demand_merge_rejects_invalid_cells_without_creating_rows(
    region: str,
    prestige_band: str,
) -> None:
    with pytest.raises(PopulationRecomputeDemandError):
        merge_population_recompute_demand(
            region=region,
            prestige_band=prestige_band,
            now=timezone.now(),
        )

    assert not BotPopulationRecomputeDemand.objects.exists()


def test_population_demand_merge_accepts_overseas_cell() -> None:
    demand = merge_population_recompute_demand(
        region="overseas",
        prestige_band="newbie",
        now=timezone.now(),
    )

    assert (demand.region, demand.prestige_band) == ("overseas", "newbie")
    assert BotPopulationRecomputeDemand.objects.filter(
        region="overseas",
        prestige_band="newbie",
    ).exists()


def test_merge_during_claim_remains_pending_after_fenced_finalize() -> None:
    now = timezone.now()
    merge_population_recompute_demand(region="north", prestige_band="newbie", now=now)
    claim = claim_population_recompute_demand(
        region="north",
        prestige_band="newbie",
        now=now,
    )
    assert claim is not None

    merge_population_recompute_demand(
        region="north",
        prestige_band="newbie",
        now=now + timedelta(seconds=1),
    )
    assert finalize_population_recompute_demand(
        claim,
        now=now + timedelta(seconds=2),
    )

    demand = BotPopulationRecomputeDemand.objects.get()
    assert demand.requested_revision == 2
    assert demand.completed_revision == 1
    assert demand.available_at == now + timedelta(seconds=2)
    assert demand.claim_token is None


def test_expired_claim_is_reclaimed_and_old_worker_cannot_finalize() -> None:
    now = timezone.now()
    merge_population_recompute_demand(region="north", prestige_band="junior", now=now)
    old_claim = claim_population_recompute_demand(
        region="north",
        prestige_band="junior",
        now=now,
    )
    assert old_claim is not None

    reclaimed_at = now + timedelta(minutes=5, seconds=1)
    new_claim = claim_population_recompute_demand(
        region="north",
        prestige_band="junior",
        now=reclaimed_at,
    )
    assert new_claim is not None
    assert new_claim.claimed_revision == old_claim.claimed_revision
    assert new_claim.claim_token != old_claim.claim_token
    assert not finalize_population_recompute_demand(
        old_claim,
        now=reclaimed_at + timedelta(seconds=1),
    )
    assert finalize_population_recompute_demand(
        new_claim,
        now=reclaimed_at + timedelta(seconds=2),
    )


def test_failure_backoff_is_exponential_capped_and_merge_does_not_shorten_it() -> None:
    now = timezone.now()
    merge_population_recompute_demand(region="south", prestige_band="elite", now=now)
    next_attempt = now

    for expected_count in range(1, 8):
        claim = claim_population_recompute_demand(
            region="south",
            prestige_band="elite",
            now=next_attempt,
        )
        assert claim is not None
        failed_at = next_attempt + timedelta(seconds=1)
        assert fail_population_recompute_demand(
            claim,
            error=RuntimeError("transient infrastructure failure"),
            now=failed_at,
        )
        demand = BotPopulationRecomputeDemand.objects.get()
        expected_backoff = min(3600, 60 * (2 ** (expected_count - 1)))
        assert demand.consecutive_failure_count == expected_count
        assert demand.available_at == failed_at + timedelta(seconds=expected_backoff)
        assert len(demand.last_error_digest) == 64
        next_attempt = demand.available_at

    available_at = BotPopulationRecomputeDemand.objects.get().available_at
    merge_population_recompute_demand(
        region="south",
        prestige_band="elite",
        now=available_at - timedelta(minutes=10),
    )
    demand = BotPopulationRecomputeDemand.objects.get()
    assert demand.available_at == available_at
    assert demand.requested_revision == 2


def test_success_resets_failure_state_and_continuation_adds_revision() -> None:
    now = timezone.now()
    demand = BotPopulationRecomputeDemand.objects.create(
        region="west",
        prestige_band="legend",
        requested_revision=3,
        completed_revision=2,
        available_at=now,
        consecutive_failure_count=4,
        last_error_digest="a" * 64,
    )
    claim = claim_population_recompute_demand(
        region=demand.region,
        prestige_band=demand.prestige_band,
        now=now,
    )
    assert claim is not None

    assert finalize_population_recompute_demand(
        claim,
        executable_deficit_remains=True,
        now=now + timedelta(seconds=1),
    )

    demand.refresh_from_db()
    assert demand.requested_revision == 4
    assert demand.completed_revision == 3
    assert demand.consecutive_failure_count == 0
    assert demand.last_error_digest == ""
    assert demand.available_at == now + timedelta(seconds=1)
    assert BotPopulationRecomputeDemand.objects.filter(id=demand.id).exists()


def test_claim_next_orders_by_available_region_and_v2_band_ordinal() -> None:
    now = timezone.now()
    merge_population_recompute_demands(
        [
            ("north", "newbie"),
            ("east", "mythic"),
            ("east", "newbie"),
        ],
        now=now,
    )

    claims = []
    for offset in range(3):
        claim = claim_next_population_recompute_demand(now=now + timedelta(seconds=offset))
        assert claim is not None
        claims.append((claim.region, claim.prestige_band))
        assert finalize_population_recompute_demand(
            claim,
            now=now + timedelta(seconds=offset, microseconds=1),
        )

    assert claims == [
        ("east", "newbie"),
        ("east", "mythic"),
        ("north", "newbie"),
    ]


def test_reconcile_uses_fresh_database_time_for_finalization_fencing(
    monkeypatch,
) -> None:
    claimed_at = timezone.now()
    expired_at = claimed_at + timedelta(minutes=5, seconds=1)
    finalized_at = expired_at + timedelta(seconds=1)
    merge_population_recompute_demand(
        region="north",
        prestige_band="newbie",
        now=claimed_at,
    )
    database_times = iter((claimed_at, expired_at, finalized_at))
    revalidation_times = []

    monkeypatch.setattr(
        population_runtime,
        "_database_utc_now",
        lambda: next(database_times),
    )
    monkeypatch.setattr(
        population_runtime,
        "_v2_bootstrap_routing_is_active",
        lambda: True,
    )

    @contextmanager
    def population_ownership():
        yield lambda: None

    def cell_has_executable_deficit(**kwargs) -> bool:
        revalidation_times.append(kwargs["now"])
        return False

    monkeypatch.setattr(
        population_runtime,
        "_population_ownership",
        population_ownership,
    )
    monkeypatch.setattr(
        population_runtime,
        "_v2_population_cell_has_executable_deficit",
        cell_has_executable_deficit,
    )

    result = population_runtime.reconcile_virtual_player_population_cell(
        region="north",
        prestige_band="newbie",
        limit=0,
    )

    assert result.status is PopulationCellReconcileStatus.CLAIM_LOST
    assert revalidation_times == [expired_at]
    demand = BotPopulationRecomputeDemand.objects.get()
    assert demand.completed_revision == 0
    assert demand.claim_token is not None
