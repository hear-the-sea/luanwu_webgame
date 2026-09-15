from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from gameplay.models import (
    ArenaCoopEvent,
    ArenaReserveTrainingAssignment,
    ArenaRuntimeBudget,
    ArenaVirtualReserveMember,
    BotRuntimeRoutingState,
)
from gameplay.services.arena import virtual_reserve_fill as fill
from gameplay.services.arena import virtual_reserve_pool as pool
from gameplay.services.arena.virtual_reserve_demand import BLOCKED_DEMAND_RETRY_COOLDOWN
from gameplay.services.arena.virtual_reserve_reconcile import reconcile_coop_demand, reconcile_tournament_demand
from gameplay.services.arena.virtual_reserve_scan import scan_virtual_reserve_demands
from tests.arena_services.test_virtual_backfill import _add_real_coop_entry
from tests.arena_services.test_virtual_reserve import _create_bot_profile, _create_tournament_demand

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["tournament", "coop"])
def demand(request, settings):
    settings.ARENA_LOW_POPULATION_MODE = True
    for key in ("materialization", "training", "growth"):
        ArenaRuntimeBudget.objects.update_or_create(key=key, defaults={"entries": []})
    BotRuntimeRoutingState.objects.get_or_create(
        defaults={
            "bootstrap_mode": "v2_active",
            "maintenance_mode": "v2_active",
            "calibration_routes": [],
        }
    )
    if request.param == "tournament":
        return _create_tournament_demand()
    event = ArenaCoopEvent.objects.create(
        status="recruiting",
        player_limit=2,
        guest_limit_per_entry=1,
        virtual_fill_at=timezone.now() - timedelta(minutes=1),
    )
    _add_real_coop_entry(event, "ready_recovery_reference")
    return reconcile_coop_demand(event.id)


def _reconcile(demand, now):
    if demand.tournament_id:
        return reconcile_tournament_demand(demand.tournament_id, now=now)
    return reconcile_coop_demand(demand.coop_event_id, now=now)


def test_ready_supply_fills_during_demand_backoff(demand):
    now = timezone.now()
    demand.next_retry_at = now + timedelta(hours=1)
    demand.last_failure_reason = "insufficient_ready_members"
    demand.save()
    _create_bot_profile("new_ready_during_backoff")
    result = scan_virtual_reserve_demands(now=now)
    assert result["filled_entries"] == 1
    demand.refresh_from_db()
    assert demand.status == "satisfied"


def test_fill_tries_spare_ready_after_first_candidate_invalid(demand, monkeypatch):
    members = [
        ArenaVirtualReserveMember.objects.create(
            demand=demand,
            profile=_create_bot_profile(f"fallback_{index}"),
            state="ready",
        )
        for index in range(2)
    ]
    monkeypatch.setattr(fill, "_ordered_ready_members", lambda *args, **kwargs: members)
    original = fill._select_bot_lineup
    monkeypatch.setattr(
        fill,
        "_select_bot_lineup",
        lambda profile, **kwargs: [] if profile.pk == members[0].profile_id else original(profile, **kwargs),
    )
    service = fill.fill_due_tournament_reserve if demand.tournament_id else fill.fill_due_coop_reserve
    assert service(demand.tournament_id or demand.coop_event_id) == 1


def test_fill_rolls_back_when_spare_candidates_cannot_cover_whole_gap(demand, monkeypatch):
    event = demand.tournament or demand.coop_event
    event.player_limit = 3
    event.save(update_fields=["player_limit"])
    demand = _reconcile(demand, timezone.now())
    members = [
        ArenaVirtualReserveMember.objects.create(
            demand=demand,
            profile=_create_bot_profile(f"partial_{index}"),
            state="ready",
        )
        for index in range(2)
    ]
    original = fill._select_bot_lineup
    monkeypatch.setattr(
        fill,
        "_select_bot_lineup",
        lambda profile, **kwargs: [] if profile.pk == members[1].profile_id else original(profile, **kwargs),
    )
    service = fill.fill_due_tournament_reserve if demand.tournament_id else fill.fill_due_coop_reserve
    assert service(event.pk) == 0
    event.refresh_from_db()
    assert event.entries.filter(source="virtual").count() == 0
    assert event.status == "recruiting"


@pytest.mark.parametrize("protected", [False, True, "assignment"])
def test_ready_can_replace_idle_training_but_not_active_claim(demand, protected):
    now = timezone.now()
    demand.warm_target_count = 1
    demand.admission_attempt_high_water = demand.max_reserve_target_count
    demand.next_retry_at = now + timedelta(hours=1)
    demand.save()
    member = ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=_create_bot_profile("slow_training", guest_stats=[(1, 1, 1)]),
        state="training",
        evaluated_version=demand.version,
        current_lineup_power=1,
        next_acceleration_at=now + timedelta(hours=1),
    )
    if protected is True:
        member.growth_claim_token = uuid4()
        member.growth_claimed_at = now
        member.growth_claim_expires_at = now + timedelta(minutes=5)
        member.growth_requested_at = now
        member.growth_demand_version = demand.version
        member.growth_member_version = demand.version
        member.growth_power_before = 1
        member.growth_eligible_guest_count_before = 1
        member.growth_minimum_guest_count = 1
        member.growth_minimum_guest_level = 1
        member.growth_guest_rarity_cap = "gray"
        member.growth_operation_id = "protected-growth"
        member.growth_attempt_ordinal = 1
        member.save()
    elif protected == "assignment":
        ArenaReserveTrainingAssignment.objects.create(
            member=member,
            guest=member.profile.manor.guests.first(),
            round_ordinal=1,
            action_ordinal_in_round=1,
            operation_id="protected-assignment",
        )
    ready = _create_bot_profile("replacement_ready")
    result = pool.replenish_virtual_reserve(demand.id, now=now)
    assert result.ready_count == (0 if protected else 1)
    assert demand.reserve_members.filter(profile=ready).exists() is not bool(protected)
    assert ArenaVirtualReserveMember.objects.filter(pk=member.pk).exists() is bool(protected)
    demand.refresh_from_db()
    assert demand.admission_attempt_high_water == demand.max_reserve_target_count
    assert result.creation_needed == 0


def test_blocked_refresh_preserves_budget_and_probe_can_use_existing_ready(demand):
    now = timezone.now()
    high_water = demand.max_reserve_target_count
    demand.admission_attempt_high_water = high_water
    demand.save()
    with transaction.atomic():
        pool._block_replacement_budget_exhausted(demand, now=now)
    assert _reconcile(demand, now + timedelta(minutes=1)) is None
    demand.refresh_from_db()
    assert demand.admission_attempt_high_water == high_water
    assert demand.status == "blocked"
    _create_bot_profile("ready_after_block")
    assert scan_virtual_reserve_demands(now=now + BLOCKED_DEMAND_RETRY_COOLDOWN)["filled_entries"] == 1
    demand.refresh_from_db()
    assert demand.status == "satisfied"
    assert demand.admission_attempt_high_water == high_water


def test_no_progress_probe_does_not_fabricate_progress_or_reset_budget(demand):
    now = timezone.now()
    stale = now - timedelta(hours=13)
    demand.last_progress_at = stale
    demand.last_input_change_at = stale
    demand.admission_attempt_high_water = 2
    demand.save()
    assert _reconcile(demand, now) is None
    demand.refresh_from_db()
    assert demand.last_failure_reason == "no_progress_timeout"
    assert _reconcile(demand, now + timedelta(minutes=1)) is None
    assert _reconcile(demand, now + BLOCKED_DEMAND_RETRY_COOLDOWN) is not None
    assert _reconcile(demand, now + BLOCKED_DEMAND_RETRY_COOLDOWN) is not None
    demand.refresh_from_db()
    assert demand.last_progress_at == stale
    assert demand.last_input_change_at == stale
    assert demand.admission_attempt_high_water == 2
    assert demand.admission_probe_target_ordinal == 3


def test_supply_backoff_does_not_reactivate_or_start_new_training(demand):
    now = timezone.now()
    retry_at = now + timedelta(hours=1)
    demand.next_retry_at = retry_at
    demand.last_failure_reason = "dynamic_population_cap_reached"
    demand.save()
    _create_bot_profile("backoff_training", guest_stats=[(1, 1, 1)])
    abandoned = _create_bot_profile("backoff_abandoned", state="abandoned")
    result = pool.replenish_virtual_reserve(demand.id, now=now)
    abandoned.refresh_from_db()
    demand.refresh_from_db()
    assert abandoned.state == "abandoned"
    assert not demand.reserve_members.exists()
    assert result.creation_needed == 0
    assert demand.next_retry_at == retry_at
    assert demand.last_failure_reason == "dynamic_population_cap_reached"


def test_active_scan_rotates_demands_during_backoff():
    tournament_demand = _create_tournament_demand()
    event = ArenaCoopEvent.objects.create(
        status="recruiting",
        player_limit=2,
        guest_limit_per_entry=1,
        virtual_fill_at=timezone.now() - timedelta(minutes=1),
    )
    _add_real_coop_entry(event, "round_robin_reference")
    demands = [tournament_demand, reconcile_coop_demand(event.pk)]
    now = timezone.now()
    for item in demands:
        item.next_retry_at = now + timedelta(hours=1)
        item.last_failure_reason = "insufficient_ready_members"
        item.save()
    for minute in range(2):
        assert scan_virtual_reserve_demands(now=now + timedelta(minutes=minute), limit=1)["scanned"] == 1
    for item in demands:
        item.refresh_from_db()
        assert item.last_checked_at >= now
