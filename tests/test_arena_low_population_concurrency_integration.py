from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

import pytest
from django.db import close_old_connections, connection, transaction
from django.utils import timezone

from gameplay.models import ArenaRuntimeBudget, BotSafetyDecisionAudit
from gameplay.services import runtime_configs
from gameplay.services.arena import low_population_budget as budget
from gameplay.services.arena import virtual_fill_service as telemetry
from tests.arena_services.test_fill_service_telemetry import _add_real_arena_entry, _opening
from tests.test_virtual_player_safety_monitor import HOURLY_START, _routing_state

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]


def _race(count, action):
    assert connection.vendor == "mysql"
    start = Barrier(count)

    def worker(index):
        close_old_connections()
        try:
            start.wait(timeout=10)
            return action(index)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=count) as executor:
        futures = [executor.submit(worker, index) for index in range(count)]
        return [future.result(timeout=30) for future in futures]


def test_shared_mysql_materialization_ledger_cannot_overspend(settings):
    settings.ARENA_LOW_POPULATION_MODE = True
    ArenaRuntimeBudget.objects.update_or_create(pk="materialization", defaults={"entries": []})
    now = timezone.now()

    def reserve(index):
        with transaction.atomic():
            return budget.reserve_materialization_locked(demand_id=index // 2 + 1, now=now)

    tokens = _race(8, reserve)
    entries = ArenaRuntimeBudget.objects.get(pk="materialization").entries
    assert len(entries) == len([token for token in tokens if token])
    assert 1 <= len(entries) <= 4
    assert all(sum(e["demand_id"] == entry["demand_id"] for e in entries) <= 2 for entry in entries)


def test_two_monitor_writers_commit_one_cursor_and_one_audit(settings):
    settings.VIRTUAL_PLAYER_ARENA_SAFETY_POLICY = "scoped"
    _routing_state(hourly_cursor=HOURLY_START)

    def apply(_index):
        return runtime_configs.apply_virtual_player_safety_decision(
            expected_revision=4,
            window_id="concurrent-audit",
            window_kind="hourly",
            window_end_at=HOURLY_START + timedelta(hours=1),
            should_pause=True,
            pause_reason="economy_cap_breach",
        ).consumed

    assert sorted(_race(2, apply)) == [False, True]
    assert BotSafetyDecisionAudit.objects.count() == 1
    assert runtime_configs.read_virtual_player_routing().pause_reason == "economy_cap_breach"


def test_two_publishers_do_not_duplicate_committed_service_outcomes():
    event, episode, now = _opening()
    _add_real_arena_entry(event, "concurrent_fill_human")
    telemetry.observe_fill_service(mode="tournament", event_id=event.pk, now=now + timedelta(minutes=1))
    assert sum(_race(2, lambda _index: telemetry.publish_pending_fill_outcomes())) == 2
    assert episode.outcomes.count() == 2


def test_low_population_mode_preserves_growth_claim_fencing(settings, monkeypatch):
    from tests.test_arena_virtual_population_concurrency_integration import (
        test_two_growth_workers_issue_one_claim_and_execute_without_arena_locks,
    )

    settings.ARENA_LOW_POPULATION_MODE = True
    ArenaRuntimeBudget.objects.update_or_create(pk="growth", defaults={"entries": []})
    test_two_growth_workers_issue_one_claim_and_execute_without_arena_locks(monkeypatch)


def test_low_population_mode_preserves_population_lock_order(settings):
    from tests.test_arena_virtual_population_concurrency_integration import (
        test_concurrent_arena_demands_share_last_population_slot,
    )

    settings.ARENA_LOW_POPULATION_MODE = True
    for key in ("materialization", "training", "growth"):
        ArenaRuntimeBudget.objects.update_or_create(pk=key, defaults={"entries": []})
    test_concurrent_arena_demands_share_last_population_slot(settings)


def test_virtual_timer_sql_guard_does_not_reverse_routing_guest_locks(settings, monkeypatch):
    from gameplay.models import BotRuntimeRoutingState
    from guests.models import Guest
    from guests.services import training
    from tests.arena_services.test_virtual_reserve import _create_bot_profile
    from tests.test_virtual_player_recruitment import _seed_healthy_recruitment_runtime

    assert connection.vendor == "mysql"
    now = timezone.now()
    profile = _create_bot_profile("timer_lock_order")
    guest = Guest.objects.filter(manor=profile.manor).first()
    _seed_healthy_recruitment_runtime(now)
    Guest.objects.filter(pk=guest.pk).update(status="idle", training_complete_at=None, training_remaining_seconds=None)
    monkeypatch.setattr(training, "_try_enqueue_complete_guest_training", lambda *args, **kwargs: None)
    guest_locked, routing_locked = Event(), Event()

    def action(index):
        with transaction.atomic():
            if index == 0:
                owned_guest = Guest.objects.select_for_update().get(pk=guest.pk)
                guest_locked.set()
                assert routing_locked.wait(timeout=10)
                return training.ensure_auto_training(owned_guest, now=now)
            assert guest_locked.wait(timeout=10)
            routing = BotRuntimeRoutingState.objects.select_for_update().get(key=BotRuntimeRoutingState.GLOBAL_KEY)
            routing_locked.set()
            Guest.objects.select_for_update().get(pk=guest.pk)
            routing.maintenance_mode = "v2_paused"
            routing.revision += 1
            routing.save(update_fields=["maintenance_mode", "revision"])
            return True

    assert _race(2, action) == [True, True]
