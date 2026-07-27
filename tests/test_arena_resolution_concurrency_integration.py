from __future__ import annotations

import threading
import uuid

import pytest
from django.db import close_old_connections, connection
from django.utils import timezone

from battle.models import BattleReport
from battle.random_context import current_replay_metadata
from core.exceptions import ArenaCancellationError
from gameplay.models import (
    ArenaCoopContribution,
    ArenaCoopEntry,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaMatch,
    ArenaTournament,
)
from gameplay.services.arena.coop_core import cancel_arena_coop_entry, run_due_arena_coop_events
from gameplay.services.arena.core import run_due_arena_rounds
from gameplay.services.arena.match_helpers import create_scheduled_match
from gameplay.services.arena.snapshots import build_entry_guest_snapshot
from gameplay.services.manor.core import ensure_manor
from guests.models import GuestStatus
from tests.arena_services.support import create_guest, create_guest_template, fund_manor

pytestmark = [pytest.mark.integration]


@pytest.mark.django_db(transaction=True)
def test_concurrent_arena_round_scans_resolve_each_match_once(django_user_model):
    if connection.vendor != "mysql":
        pytest.skip("arena round concurrency requires MySQL select_for_update semantics")

    suffix = uuid.uuid4().hex[:8]
    manors = []
    for index in range(2):
        user = django_user_model.objects.create_user(
            username=f"arena_round_race_{suffix}_{index}",
            password="pass123",
        )
        manors.append(ensure_manor(user))

    now = timezone.now()
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=2,
        current_round=1,
        round_interval_seconds=60,
        next_round_at=now,
        started_at=now,
        **current_replay_metadata(base_seed=12001),
    )
    entries = [ArenaEntry.objects.create(tournament=tournament, manor=manor) for manor in manors]
    match = create_scheduled_match(
        tournament=tournament,
        round_number=1,
        match_index=0,
        attacker_entry=entries[0],
        defender_entry=entries[1],
    )

    start = threading.Barrier(2)
    results: list[int] = []
    errors: list[BaseException] = []
    results_guard = threading.Lock()

    def _worker() -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            result = run_due_arena_rounds(now=now, limit=10)
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
        thread.join(timeout=30)

    tournament.refresh_from_db()
    match.refresh_from_db()
    refreshed_entries = list(ArenaEntry.objects.filter(pk__in=[entry.pk for entry in entries]).order_by("pk"))
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert sorted(results) == [0, 1]
    assert ArenaMatch.objects.filter(tournament=tournament, round_number=1).count() == 1
    assert match.status == ArenaMatch.Status.FORFEIT
    assert match.winner_entry_id is not None
    assert match.resolved_at is not None
    assert tournament.status == ArenaTournament.Status.COMPLETED
    assert sorted(entry.status for entry in refreshed_entries) == [
        ArenaEntry.Status.ELIMINATED,
        ArenaEntry.Status.WINNER,
    ]
    assert sum(entry.matches_won for entry in refreshed_entries) == 1


@pytest.mark.django_db(transaction=True)
def test_coop_cancellation_racing_due_settlement_is_linearized(monkeypatch, django_user_model):
    if connection.vendor != "mysql":
        pytest.skip("arena coop settlement concurrency requires MySQL select_for_update semantics")

    suffix = uuid.uuid4().hex[:8]
    user = django_user_model.objects.create_user(
        username=f"arena_coop_settle_race_{suffix}",
        password="pass123",
    )
    manor = ensure_manor(user)
    fund_manor(manor)
    template = create_guest_template(f"arena_coop_settle_race_tpl_{suffix}")
    guest = create_guest(manor, template, "A")
    guest.status = GuestStatus.ARENA
    guest.save(update_fields=["status"])

    now = timezone.now()
    event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.PREPARING,
        player_limit=1,
        guest_limit_per_entry=1,
        prepare_duration_seconds=1,
        prepare_ends_at=now,
        boss_name="并发首领",
        boss_template_key="arena_gl_top_zhang_wuji_boss",
        boss_initial_hp=100,
        boss_remaining_hp=100,
        **current_replay_metadata(base_seed=12002),
    )
    entry = ArenaCoopEntry.objects.create(event=event, manor=manor)
    entry.entry_guests.create(
        guest=guest,
        slot_index=0,
        snapshot=build_entry_guest_snapshot(guest),
    )
    report = BattleReport.objects.create(
        manor=manor,
        opponent_name=event.boss_name,
        battle_type="arena_coop",
        attacker_team=[],
        attacker_troops={},
        defender_team=[],
        defender_troops={},
        rounds=[
            {
                "round": 1,
                "events": [
                    {
                        "damage": 100,
                        "applied_damage": 100,
                        "actor_owner_entry_id": entry.id,
                        "target_template_key": event.boss_template_key,
                        "target_is_boss": True,
                    }
                ],
            }
        ],
        losses={"attacker": {}, "defender": {}},
        drops={},
        winner="attacker",
        starts_at=now,
        completed_at=now,
        seed=event.base_seed,
        rng_version=event.rng_version,
        battle_engine_version=event.battle_engine_version,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.coop_core._run_coop_battle_locked",
        lambda _locked_event, _now: report,
    )

    start = threading.Barrier(2)
    settle_results: list[int] = []
    cancellation_errors: list[BaseException] = []
    unexpected_errors: list[BaseException] = []
    results_guard = threading.Lock()

    def _settle_worker() -> None:
        close_old_connections()
        try:
            start.wait(timeout=10)
            result = run_due_arena_coop_events(now=now, limit=10)
            with results_guard:
                settle_results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                unexpected_errors.append(exc)
        finally:
            close_old_connections()

    def _cancel_worker() -> None:
        close_old_connections()
        try:
            local_manor = type(manor).objects.get(pk=manor.pk)
            start.wait(timeout=10)
            cancel_arena_coop_entry(local_manor)
        except ArenaCancellationError as exc:
            with results_guard:
                cancellation_errors.append(exc)
        except BaseException as exc:  # pragma: no cover - asserted below
            with results_guard:
                unexpected_errors.append(exc)
        finally:
            close_old_connections()

    threads = [
        threading.Thread(target=_settle_worker, daemon=True),
        threading.Thread(target=_cancel_worker, daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    event.refresh_from_db()
    entry.refresh_from_db()
    guest.refresh_from_db()
    assert all(not thread.is_alive() for thread in threads)
    assert unexpected_errors == []
    assert settle_results == [1]
    assert len(cancellation_errors) == 1
    assert event.status == ArenaCoopEvent.Status.COMPLETED
    assert entry.status == ArenaCoopEntry.Status.COMPLETED
    assert guest.status == GuestStatus.IDLE
    assert ArenaCoopContribution.objects.filter(event=event, entry=entry).count() == 1
