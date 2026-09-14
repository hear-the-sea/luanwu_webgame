from datetime import timedelta

import pytest
from django.utils import timezone

from gameplay.models import ArenaTournament, BotRuntimeRoutingState
from gameplay.services import runtime_configs
from gameplay.services.arena.virtual_reserve_pool import ReserveReplenishmentResult, replenish_virtual_reserve
from gameplay.services.arena.virtual_reserve_reconcile import reconcile_tournament_demand
from tests.arena_services.test_virtual_backfill import _add_real_arena_entry


def _create_tournament_demand():
    BotRuntimeRoutingState.objects.get_or_create(
        key=BotRuntimeRoutingState.GLOBAL_KEY,
        defaults={
            "bootstrap_mode": BotRuntimeRoutingState.BootstrapMode.V2_ACTIVE,
            "maintenance_mode": BotRuntimeRoutingState.MaintenanceMode.V2_ACTIVE,
            "calibration_routes": [],
        },
    )
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=2,
        virtual_fill_at=timezone.now() - timedelta(minutes=1),
    )
    _add_real_arena_entry(
        tournament,
        f"routing_lock_reference_{tournament.id}",
        attack=200,
        defense=200,
        max_hp=2000,
    )
    demand = reconcile_tournament_demand(tournament.id)
    assert demand is not None
    return demand


@pytest.mark.django_db
def test_replenishment_fails_closed_when_routing_lock_cannot_be_acquired(monkeypatch):
    demand = _create_tournament_demand()
    monkeypatch.setattr(
        runtime_configs,
        "lock_virtual_player_routing",
        lambda: (_ for _ in ()).throw(runtime_configs.RuntimeRoutingError("routing lock unavailable")),
    )

    result = replenish_virtual_reserve(demand.id, now=timezone.now())

    assert result == ReserveReplenishmentResult(0, 0, 0, 0, 0)
    assert not demand.reserve_members.exists()
