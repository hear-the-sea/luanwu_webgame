from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gameplay.models import (
    VirtualPlayerGrowthControlPointer,
    VirtualPlayerGrowthControlRun,
    VirtualPlayerGrowthControlSnapshot,
)
from gameplay.services.virtual_player_core import growth_control
from gameplay.services.virtual_player_core.config import VirtualPlayerConfigError, clear_virtual_player_config_cache
from gameplay.services.virtual_player_core.growth_control import (
    GrowthControlAggregate,
    GrowthControlPolicy,
    RealPlayerGrowthSample,
    _with_derived_growth_metrics,
    aggregate_growth_control_samples,
    configured_growth_control_policy,
    effective_growth_control_snapshot,
    growth_control_digest_for_route,
    refresh_growth_control_snapshots,
)


def test_growth_control_aggregates_cells_and_clamps_daily_change() -> None:
    policy = GrowthControlPolicy(minimum_sample_count=2, maximum_daily_delta_bps=100)
    previous = {
        ("east", "newbie"): GrowthControlAggregate(
            region="east",
            prestige_band="newbie",
            sample_count=2,
            strength_p50=100,
            strength_p75=120,
            growth_24h_bps=50,
            growth_7d_bps=80,
            component_statistics={},
        )
    }

    aggregates = aggregate_growth_control_samples(
        (
            RealPlayerGrowthSample("east", "newbie", 100, growth_24h_bps=500),
            RealPlayerGrowthSample("east", "newbie", 120, growth_24h_bps=500),
        ),
        policy=policy,
        previous=previous,
    )

    aggregate = aggregates[("east", "newbie")]
    assert aggregate.strength_p50 == 100
    assert aggregate.strength_p75 == 120
    assert aggregate.growth_24h_bps == 150
    assert aggregate.is_fallback is False


def test_underfilled_cell_reuses_previous_values_as_fallback() -> None:
    previous = GrowthControlAggregate(
        region="east",
        prestige_band="newbie",
        sample_count=8,
        strength_p50=100,
        strength_p75=120,
        growth_24h_bps=20,
        growth_7d_bps=40,
        component_statistics={"guest_count": {"p50": 3, "p75": 4}},
    )

    aggregate = aggregate_growth_control_samples(
        (RealPlayerGrowthSample("east", "newbie", 999),),
        policy=GrowthControlPolicy(minimum_sample_count=2),
        previous={("east", "newbie"): previous},
    )[("east", "newbie")]

    assert aggregate.is_fallback is True
    assert aggregate.strength_p75 == previous.strength_p75
    assert aggregate.component_statistics == previous.component_statistics


def test_growth_metrics_are_derived_from_aggregate_baselines_without_identity() -> None:
    prior = GrowthControlAggregate(
        region="east",
        prestige_band="newbie",
        sample_count=8,
        strength_p50=100,
        strength_p75=120,
        growth_24h_bps=0,
        growth_7d_bps=0,
        component_statistics={},
    )

    samples = _with_derived_growth_metrics(
        (RealPlayerGrowthSample("east", "newbie", 110),),
        previous={("east", "newbie"): prior},
        weekly_previous={("east", "newbie"): prior},
    )

    assert len(samples) == 1
    assert samples[0].growth_24h_bps == 1000
    assert samples[0].growth_7d_bps == 1000


def test_growth_control_policy_reads_typed_runtime_configuration() -> None:
    clear_virtual_player_config_cache()
    policy = configured_growth_control_policy()

    assert policy.minimum_sample_count == 5
    assert policy.smoothing_alpha == 0.35
    assert policy.maximum_daily_delta_bps == 500
    assert policy.active_sample_days == 30
    assert policy.ttl_days == 2


@pytest.mark.django_db
def test_growth_control_refresh_keeps_prior_digest_immutable() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)
    refresh_growth_control_snapshots(
        now=now,
        samples=tuple(RealPlayerGrowthSample("east", "newbie", 100 + index) for index in range(5)),
    )
    first = VirtualPlayerGrowthControlSnapshot.objects.get(
        region="east",
        prestige_band="newbie",
    )

    refresh_growth_control_snapshots(
        now=now,
        samples=tuple(RealPlayerGrowthSample("east", "newbie", 200 + index) for index in range(5)),
    )
    rows = VirtualPlayerGrowthControlSnapshot.objects.filter(
        region="east",
        prestige_band="newbie",
    ).order_by("id")
    assert rows.count() == 2
    second = rows.last()
    assert second is not None
    assert first.snapshot_digest != second.snapshot_digest
    current = effective_growth_control_snapshot(region="east", prestige_band="newbie", now=now)
    assert current is not None
    assert current.snapshot_digest == second.snapshot_digest
    assert growth_control_digest_for_route(region="east", prestige_band="newbie", now=now) == (second.snapshot_digest)


@pytest.mark.django_db
def test_growth_control_publishes_a_complete_run_before_switching_pointer() -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)
    result = refresh_growth_control_snapshots(now=now, samples=())

    run = VirtualPlayerGrowthControlRun.objects.get(run_digest=result["run_digest"])
    pointer = VirtualPlayerGrowthControlPointer.objects.get(key=VirtualPlayerGrowthControlPointer.GLOBAL_KEY)

    assert run.status == VirtualPlayerGrowthControlRun.Status.COMPLETE
    assert run.cell_count == 0
    assert pointer.current_run_id == run.id
    assert not VirtualPlayerGrowthControlSnapshot.objects.filter(run=run).exists()
    assert effective_growth_control_snapshot(region="east", prestige_band="newbie", now=now) is None
    assert (
        growth_control_digest_for_route(region="east", prestige_band="newbie", now=now)
        == growth_control.FIXED_DEFAULT_CONTROL_DIGEST
    )


@pytest.mark.django_db
def test_growth_control_failed_run_does_not_replace_the_previous_pointer(monkeypatch) -> None:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)
    first = refresh_growth_control_snapshots(
        now=now,
        samples=tuple(RealPlayerGrowthSample("east", "newbie", 100 + index) for index in range(5)),
    )
    pointer_before = VirtualPlayerGrowthControlPointer.objects.get(
        key=VirtualPlayerGrowthControlPointer.GLOBAL_KEY,
    ).current_run_id

    def fail_write(**_kwargs):
        raise RuntimeError("injected growth-control write failure")

    monkeypatch.setattr(growth_control, "_write_growth_control_snapshots_locked", fail_write)
    with pytest.raises(RuntimeError, match="injected growth-control write failure"):
        refresh_growth_control_snapshots(
            now=now,
            samples=tuple(RealPlayerGrowthSample("east", "newbie", 200 + index) for index in range(5)),
        )

    pointer_after = VirtualPlayerGrowthControlPointer.objects.get(
        key=VirtualPlayerGrowthControlPointer.GLOBAL_KEY,
    )
    assert pointer_after.current_run_id == pointer_before
    assert VirtualPlayerGrowthControlRun.objects.get(run_digest=first["run_digest"]).status == (
        VirtualPlayerGrowthControlRun.Status.COMPLETE
    )
    failed = VirtualPlayerGrowthControlRun.objects.get(status=VirtualPlayerGrowthControlRun.Status.FAILED)
    assert failed.failure_digest
    assert "injected growth-control write failure" in failed.failure_reason


@pytest.mark.django_db
def test_growth_control_policy_checksum_failure_is_not_converted_to_zero_success(monkeypatch) -> None:
    monkeypatch.setattr(growth_control, "load_virtual_player_v2_config", lambda: None)

    with pytest.raises(VirtualPlayerConfigError, match="configured V2 policy"):
        growth_control.refresh_growth_control_snapshots(
            now=datetime(2026, 8, 9, 8, 0, tzinfo=UTC),
            samples=(),
        )

    assert not VirtualPlayerGrowthControlPointer.objects.exists()
    assert not VirtualPlayerGrowthControlRun.objects.exists()
