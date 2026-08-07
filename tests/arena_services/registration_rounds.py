from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

import gameplay.services.arena.core as arena_core
import gameplay.services.arena.match_helpers as arena_match_helpers
from core.exceptions import (
    ArenaBusyError,
    ArenaCancellationError,
    ArenaGuestSelectionError,
    ArenaParticipationLimitError,
    InsufficientSilverError,
)
from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaEntryGuest,
    ArenaMatch,
    ArenaTournament,
    ArenaVirtualDemand,
    ArenaVirtualReserveMember,
    Message,
)
from gameplay.services.arena.core import (
    cancel_arena_entry,
    register_arena_entry,
    run_due_arena_rounds,
    start_tournament_if_ready,
)
from gameplay.services.manor.core import ensure_manor
from guests.models import GuestStatus
from tests.arena_services.support import User, create_guest, create_guest_template, fund_manor
from tests.arena_services.test_virtual_backfill import _create_bot_profile


@pytest.mark.django_db
def test_register_arena_entry_respects_daily_limit():
    user = User.objects.create_user(
        username="arena_daily_limit",
        password="pass123",
        email="arena_daily_limit@test.local",
    )
    manor = ensure_manor(user)
    template = create_guest_template("arena_daily_limit_tpl")
    guest = create_guest(manor, template, "A")

    now = timezone.now()
    for idx in range(arena_core.ARENA_DAILY_PARTICIPATION_LIMIT):
        tournament = ArenaTournament.objects.create(
            status=ArenaTournament.Status.COMPLETED,
            player_limit=10,
            round_interval_seconds=600,
            ended_at=now,
        )
        ArenaEntry.objects.create(
            tournament=tournament,
            manor=manor,
            status=ArenaEntry.Status.ELIMINATED,
            final_rank=(idx % 10) + 1,
            coin_reward=10,
        )

    with pytest.raises(ArenaParticipationLimitError, match="每日最多参加"):
        register_arena_entry(manor, [guest.id])


@pytest.mark.django_db
def test_register_arena_entry_rejects_more_than_guest_limit():
    user = User.objects.create_user(
        username="arena_guest_limit",
        password="pass123",
        email="arena_guest_limit@test.local",
    )
    manor = ensure_manor(user)
    template = create_guest_template("arena_guest_limit_tpl")
    guests = [create_guest(manor, template, str(i)) for i in range(arena_core.ARENA_MAX_GUESTS_PER_ENTRY + 1)]

    with pytest.raises(ArenaGuestSelectionError, match=f"最多选择 {arena_core.ARENA_MAX_GUESTS_PER_ENTRY} 名门客"):
        register_arena_entry(manor, [guest.id for guest in guests])


@pytest.mark.django_db
def test_register_arena_entry_requires_idle_guests():
    user = User.objects.create_user(
        username="arena_guest_status",
        password="pass123",
        email="arena_guest_status@test.local",
    )
    manor = ensure_manor(user)
    template = create_guest_template("arena_guest_status_tpl")
    guest = create_guest(manor, template, "A")
    guest.status = GuestStatus.WORKING
    guest.save(update_fields=["status"])

    with pytest.raises(ArenaGuestSelectionError, match="仅空闲门客可报名竞技场"):
        register_arena_entry(manor, [guest.id])


@pytest.mark.django_db
def test_register_arena_entry_returns_busy_error_when_recruiting_lock_not_acquired(monkeypatch):
    user = User.objects.create_user(
        username="arena_lock_busy",
        password="pass123",
        email="arena_lock_busy@test.local",
    )
    manor = ensure_manor(user)
    fund_manor(manor)
    template = create_guest_template("arena_lock_busy_tpl")
    guest = create_guest(manor, template, "A")

    monkeypatch.setattr(arena_core, "acquire_best_effort_lock", lambda *args, **kwargs: (False, False, None))

    with pytest.raises(ArenaBusyError, match="竞技场报名繁忙，请稍后重试"):
        register_arena_entry(manor, [guest.id])


@pytest.mark.django_db
def test_register_arena_entry_auto_starts_when_reaching_player_limit():
    template = create_guest_template("arena_auto_start_tpl")

    tournament_id = None
    for idx in range(arena_core.ARENA_TOURNAMENT_PLAYER_LIMIT):
        user = User.objects.create_user(
            username=f"arena_auto_{idx}",
            password="pass123",
            email=f"arena_auto_{idx}@test.local",
        )
        manor = ensure_manor(user)
        fund_manor(manor)
        guest = create_guest(manor, template, str(idx))
        result = register_arena_entry(manor, [guest.id])
        guest.refresh_from_db(fields=["status"])
        assert guest.status == GuestStatus.ARENA

        if tournament_id is None:
            tournament_id = result.tournament.id
        assert result.tournament.id == tournament_id

        if idx < arena_core.ARENA_TOURNAMENT_PLAYER_LIMIT - 1:
            assert result.auto_started is False
        else:
            assert result.auto_started is True

    tournament = ArenaTournament.objects.get(pk=tournament_id)
    assert tournament.status == ArenaTournament.Status.RUNNING
    assert tournament.base_seed > 0
    assert tournament.rng_version > 0
    assert tournament.battle_engine_version != "legacy"
    assert tournament.virtual_fill_completed is True
    assert tournament.current_round == 1
    assert tournament.entries.count() == arena_core.ARENA_TOURNAMENT_PLAYER_LIMIT
    assert (
        ArenaMatch.objects.filter(
            tournament=tournament,
            round_number=1,
            status=ArenaMatch.Status.SCHEDULED,
        ).count()
        == (arena_core.ARENA_TOURNAMENT_PLAYER_LIMIT + 1) // 2
    )


@pytest.mark.django_db
def test_refresh_arena_activity_consumes_only_prepared_virtual_reserves_for_current_manor(monkeypatch):
    user = User.objects.create_user(username="arena_refresh_backfill", password="pass123")
    manor = ensure_manor(user)
    now = timezone.now()
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=3,
        virtual_fill_at=now,
    )
    ArenaEntry.objects.create(tournament=tournament, manor=manor)
    event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.RECRUITING,
        player_limit=3,
        guest_limit_per_entry=1,
        virtual_fill_at=now,
    )
    ArenaCoopEntry.objects.create(event=event, manor=manor)
    calls: list[tuple[str, int]] = []

    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_fill.fill_due_tournament_reserve",
        lambda tournament_id, *, now: calls.append(("tournament", tournament_id)) or 2,
    )
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_fill.fill_due_coop_reserve",
        lambda event_id, *, now: calls.append(("coop", event_id)) or 3,
    )

    processed = arena_core.refresh_arena_activity(manor, now=now, limit=7)

    assert processed == 5
    assert calls == [("tournament", tournament.id), ("coop", event.id)]


@pytest.mark.django_db
def test_refresh_arena_activity_ignores_cancelled_coop_entry(monkeypatch):
    user = User.objects.create_user(username="arena_refresh_cancelled_coop", password="pass123")
    manor = ensure_manor(user)
    other_user = User.objects.create_user(username="arena_refresh_active_coop", password="pass123")
    other_manor = ensure_manor(other_user)
    now = timezone.now()
    event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.RECRUITING,
        player_limit=3,
        guest_limit_per_entry=1,
        virtual_fill_at=now,
    )
    ArenaCoopEntry.objects.create(
        event=event,
        manor=manor,
        status=ArenaCoopEntry.Status.CANCELLED,
    )
    ArenaCoopEntry.objects.create(event=event, manor=other_manor)
    calls: list[int] = []
    monkeypatch.setattr(
        "gameplay.services.arena.virtual_reserve_fill.fill_due_coop_reserve",
        lambda event_id, *, now: calls.append(event_id) or 1,
    )

    assert arena_core.refresh_arena_activity(manor, now=now, limit=7) == 0
    assert calls == []


@pytest.mark.django_db
def test_registration_persists_demand_and_queues_reconcile(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    user = User.objects.create_user(username="arena_reserve_hook_register", password="pass123")
    manor = ensure_manor(user)
    fund_manor(manor)
    template = create_guest_template("arena_reserve_hook_register_tpl")
    guest = create_guest(manor, template, "A")
    queued: list[tuple[str, int]] = []
    monkeypatch.setattr(
        arena_core,
        "queue_virtual_reserve_reconcile",
        lambda mode, event_id: queued.append((mode, event_id)),
    )

    with django_capture_on_commit_callbacks(execute=True):
        result = register_arena_entry(manor, [guest.id])

    assert ArenaVirtualDemand.objects.filter(tournament=result.tournament).exists()
    assert queued == [("tournament", result.tournament.id)]


@pytest.mark.django_db
def test_cancellation_updates_existing_demand(monkeypatch, django_capture_on_commit_callbacks):
    user = User.objects.create_user(username="arena_reserve_hook_cancel", password="pass123")
    manor = ensure_manor(user)
    fund_manor(manor)
    template = create_guest_template("arena_reserve_hook_cancel_tpl")
    guest = create_guest(manor, template, "A")
    queued: list[tuple[str, int]] = []
    monkeypatch.setattr(
        arena_core,
        "queue_virtual_reserve_reconcile",
        lambda mode, event_id: queued.append((mode, event_id)),
    )

    with django_capture_on_commit_callbacks(execute=True):
        result = register_arena_entry(manor, [guest.id])
    queued.clear()
    with django_capture_on_commit_callbacks(execute=True):
        cancel_arena_entry(manor)

    assert queued == [("tournament", result.tournament.id)]


@pytest.mark.django_db
def test_full_real_start_closes_and_releases_reserve():
    tournament = ArenaTournament.objects.create(player_limit=1)
    real_user = User.objects.create_user(username="arena_reserve_hook_full", password="pass123")
    real_manor = ensure_manor(real_user)
    ArenaEntry.objects.create(tournament=tournament, manor=real_manor)
    demand = ArenaVirtualDemand.objects.create(
        tournament=tournament,
        missing_entry_count=1,
        reserve_target_count=6,
        warm_target_count=6,
        max_reserve_target_count=6,
    )
    profile = _create_bot_profile("arena_reserve_hook_full_bot")
    ArenaVirtualReserveMember.objects.create(
        demand=demand,
        profile=profile,
        state=ArenaVirtualReserveMember.State.READY,
    )

    assert start_tournament_if_ready(tournament) is True
    demand.refresh_from_db()
    assert demand.status == ArenaVirtualDemand.Status.CLOSED
    assert demand.reserve_members.count() == 0


@pytest.mark.django_db
def test_register_arena_entry_persists_five_hour_virtual_fill_deadline():
    user = User.objects.create_user(username="arena_virtual_deadline", password="pass123")
    manor = ensure_manor(user)
    fund_manor(manor)
    guest = create_guest(manor, create_guest_template("arena_virtual_deadline_tpl"), "A")
    before = timezone.now()

    result = register_arena_entry(manor, [guest.id])

    after = timezone.now()
    assert result.tournament.virtual_fill_at is not None
    assert before + timedelta(seconds=arena_core.ARENA_VIRTUAL_FILL_WAIT_SECONDS) <= result.tournament.virtual_fill_at
    assert result.tournament.virtual_fill_at <= after + timedelta(seconds=arena_core.ARENA_VIRTUAL_FILL_WAIT_SECONDS)


@pytest.mark.django_db
def test_cancel_arena_entry_releases_guests_and_does_not_consume_daily_quota():
    user = User.objects.create_user(
        username="arena_cancel_quota",
        password="pass123",
        email="arena_cancel_quota@test.local",
    )
    manor = ensure_manor(user)
    fund_manor(manor)
    template = create_guest_template("arena_cancel_quota_tpl")
    guest = create_guest(manor, template, "A")
    initial_silver = manor.silver

    for idx in range(5):
        register_arena_entry(manor, [guest.id])
        guest.refresh_from_db(fields=["status"])
        assert guest.status == GuestStatus.ARENA
        canceled = cancel_arena_entry(manor)
        assert canceled >= 1
        guest.refresh_from_db(fields=["status"])
        assert guest.status == GuestStatus.IDLE
        assert not ArenaEntry.objects.filter(manor=manor, tournament__status=ArenaTournament.Status.RECRUITING).exists()
        manor.refresh_from_db(fields=["silver", "arena_participations_today", "arena_participation_date"])
        assert manor.silver == initial_silver - arena_core.ARENA_REGISTRATION_SILVER_COST * (idx + 1)
        assert manor.arena_participations_today == 0
        assert manor.arena_participation_date == timezone.localdate()

    result = register_arena_entry(manor, [guest.id])
    assert result.entry is not None
    manor.refresh_from_db(fields=["silver", "arena_participations_today", "arena_participation_date"])
    assert manor.silver == initial_silver - arena_core.ARENA_REGISTRATION_SILVER_COST * 6
    assert manor.arena_participations_today == 1
    assert manor.arena_participation_date == timezone.localdate()


@pytest.mark.django_db
def test_cancel_arena_entry_requires_recruiting_entry():
    user = User.objects.create_user(
        username="arena_cancel_missing",
        password="pass123",
        email="arena_cancel_missing@test.local",
    )
    manor = ensure_manor(user)

    with pytest.raises(ArenaCancellationError, match="当前没有可撤销的报名"):
        cancel_arena_entry(manor)


@pytest.mark.django_db(transaction=True)
def test_run_due_arena_rounds_completes_tournament_and_grants_coins():
    template = create_guest_template("arena_round_tpl")

    user_a = User.objects.create_user(username="arena_round_a", password="pass123", email="arena_round_a@test.local")
    user_b = User.objects.create_user(username="arena_round_b", password="pass123", email="arena_round_b@test.local")
    manor_a = ensure_manor(user_a)
    manor_b = ensure_manor(user_b)
    fund_manor(manor_a)
    fund_manor(manor_b)
    guest_a = create_guest(manor_a, template, "A")
    guest_b = create_guest(manor_b, template, "B")

    now = timezone.now()
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=2,
        round_interval_seconds=600,
        current_round=0,
        started_at=now,
        next_round_at=now - timedelta(seconds=1),
    )
    entry_a = ArenaEntry.objects.create(tournament=tournament, manor=manor_a)
    entry_b = ArenaEntry.objects.create(tournament=tournament, manor=manor_b)
    ArenaEntryGuest.objects.create(entry=entry_a, guest=guest_a)
    ArenaEntryGuest.objects.create(entry=entry_b, guest=guest_b)

    processed = run_due_arena_rounds(now=now, limit=10)
    assert processed == 1

    tournament.refresh_from_db()
    assert tournament.status == ArenaTournament.Status.RUNNING
    assert tournament.current_round == 1
    assert (
        ArenaMatch.objects.filter(tournament=tournament, round_number=1, status=ArenaMatch.Status.SCHEDULED).count()
        == 1
    )

    processed = run_due_arena_rounds(now=now + timedelta(seconds=601), limit=10)
    assert processed == 1

    tournament.refresh_from_db()
    assert tournament.status == ArenaTournament.Status.COMPLETED
    assert tournament.winner_entry_id in {entry_a.id, entry_b.id}
    assert ArenaMatch.objects.filter(tournament=tournament).count() == 1

    entry_a.refresh_from_db()
    entry_b.refresh_from_db()
    guest_a.refresh_from_db(fields=["loyalty"])
    guest_b.refresh_from_db(fields=["loyalty"])
    assert {entry_a.final_rank, entry_b.final_rank} == {1, 2}
    assert sorted([guest_a.loyalty, guest_b.loyalty]) == [80, 81]
    assert entry_a.coin_reward > 0
    assert entry_b.coin_reward > 0

    manor_a.refresh_from_db(fields=["arena_coins"])
    manor_b.refresh_from_db(fields=["arena_coins"])
    assert manor_a.arena_coins > 0
    assert manor_b.arena_coins > 0
    assert Message.objects.filter(manor=manor_a).exists()
    assert Message.objects.filter(manor=manor_b).exists()


@pytest.mark.django_db(transaction=True)
def test_run_due_arena_rounds_recovers_when_round_finalize_was_skipped_after_match_message_error(monkeypatch):
    template = create_guest_template("arena_round_recovery_tpl")

    user_a = User.objects.create_user(
        username="arena_round_recovery_a",
        password="pass123",
        email="arena_round_recovery_a@test.local",
    )
    user_b = User.objects.create_user(
        username="arena_round_recovery_b",
        password="pass123",
        email="arena_round_recovery_b@test.local",
    )
    manor_a = ensure_manor(user_a)
    manor_b = ensure_manor(user_b)
    fund_manor(manor_a)
    fund_manor(manor_b)
    guest_a = create_guest(manor_a, template, "A")
    guest_b = create_guest(manor_b, template, "B")

    now = timezone.now()
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=2,
        round_interval_seconds=600,
        current_round=1,
        started_at=now,
        next_round_at=now - timedelta(seconds=1),
    )
    entry_a = ArenaEntry.objects.create(tournament=tournament, manor=manor_a)
    entry_b = ArenaEntry.objects.create(tournament=tournament, manor=manor_b)
    ArenaEntryGuest.objects.create(entry=entry_a, guest=guest_a)
    ArenaEntryGuest.objects.create(entry=entry_b, guest=guest_b)
    match = ArenaMatch.objects.create(
        tournament=tournament,
        round_number=1,
        match_index=0,
        attacker_entry=entry_a,
        defender_entry=entry_b,
        status=ArenaMatch.Status.SCHEDULED,
    )

    monkeypatch.setattr(
        arena_match_helpers,
        "create_message",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("message backend down")),
    )

    with pytest.raises(RuntimeError, match="message backend down"):
        run_due_arena_rounds(now=now, limit=10)

    tournament.refresh_from_db()
    match.refresh_from_db()
    assert tournament.status == ArenaTournament.Status.RUNNING
    assert match.status == ArenaMatch.Status.COMPLETED

    monkeypatch.setattr(arena_match_helpers, "create_message", lambda **_kwargs: object())

    processed = run_due_arena_rounds(now=now + timedelta(seconds=601), limit=10)

    tournament.refresh_from_db()
    entry_a.refresh_from_db()
    entry_b.refresh_from_db()

    assert processed == 1
    assert tournament.status == ArenaTournament.Status.COMPLETED
    assert tournament.winner_entry_id in {entry_a.id, entry_b.id}
    assert ArenaMatch.objects.filter(tournament=tournament, round_number=2).count() == 0


@pytest.mark.django_db(transaction=True)
def test_run_due_arena_rounds_skips_snapshot_only_winner_loyalty():
    template = create_guest_template("arena_snapshot_winner_tpl")
    user_loser = User.objects.create_user(username="arena_snapshot_loser", password="pass123")
    user_snapshot = User.objects.create_user(username="arena_snapshot_virtual", password="pass123")
    user_live = User.objects.create_user(username="arena_snapshot_live", password="pass123")
    manor_loser = ensure_manor(user_loser)
    manor_snapshot = ensure_manor(user_snapshot)
    manor_live = ensure_manor(user_live)
    guest_loser = create_guest(manor_loser, template, "Loser")
    guest_live = create_guest(manor_live, template, "LiveWinner")

    now = timezone.now()
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=3,
        round_interval_seconds=600,
        current_round=1,
        started_at=now,
        next_round_at=now - timedelta(seconds=1),
    )
    loser = ArenaEntry.objects.create(tournament=tournament, manor=manor_loser)
    snapshot_winner = ArenaEntry.objects.create(
        tournament=tournament,
        manor=manor_snapshot,
        source=ArenaEntry.Source.VIRTUAL,
    )
    live_winner = ArenaEntry.objects.create(tournament=tournament, manor=manor_live)
    ArenaEntryGuest.objects.create(entry=loser, guest=guest_loser)
    ArenaEntryGuest.objects.create(entry=snapshot_winner, guest=None, snapshot={"name": "SnapshotWinner"})
    ArenaEntryGuest.objects.create(entry=live_winner, guest=guest_live)
    ArenaMatch.objects.create(
        tournament=tournament,
        round_number=1,
        match_index=0,
        attacker_entry=loser,
        defender_entry=snapshot_winner,
        winner_entry=snapshot_winner,
        status=ArenaMatch.Status.COMPLETED,
        resolved_at=now,
    )
    ArenaMatch.objects.create(
        tournament=tournament,
        round_number=1,
        match_index=1,
        attacker_entry=live_winner,
        defender_entry=None,
        winner_entry=live_winner,
        status=ArenaMatch.Status.BYE,
        resolved_at=now,
    )

    processed = run_due_arena_rounds(now=now, limit=10)

    tournament.refresh_from_db()
    loser.refresh_from_db()
    snapshot_winner.refresh_from_db()
    live_winner.refresh_from_db()
    guest_loser.refresh_from_db(fields=["loyalty"])
    guest_live.refresh_from_db(fields=["loyalty"])
    assert processed == 1
    assert tournament.status == ArenaTournament.Status.RUNNING
    assert tournament.current_round == 2
    assert loser.status == ArenaEntry.Status.ELIMINATED
    assert loser.eliminated_round == 1
    assert snapshot_winner.matches_won == 1
    assert live_winner.matches_won == 1
    assert guest_loser.loyalty == 80
    assert guest_live.loyalty == 81
    assert (
        ArenaMatch.objects.filter(
            tournament=tournament,
            round_number=2,
            status=ArenaMatch.Status.SCHEDULED,
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_start_ready_tournaments_programming_error_bubbles_up(monkeypatch):
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RECRUITING,
        player_limit=2,
        round_interval_seconds=600,
    )
    ArenaEntry.objects.create(
        tournament=tournament,
        manor=ensure_manor(User.objects.create_user("arena_start_err", "arena_start_err@test.local", "pass123")),
    )
    ArenaEntry.objects.create(
        tournament=tournament,
        manor=ensure_manor(User.objects.create_user("arena_start_err2", "arena_start_err2@test.local", "pass123")),
    )

    monkeypatch.setattr(
        arena_core,
        "start_tournament_if_ready",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken arena start contract")),
    )

    with pytest.raises(AssertionError, match="broken arena start contract"):
        arena_core.start_ready_tournaments(limit=10)


@pytest.mark.django_db
def test_run_due_arena_rounds_programming_error_bubbles_up(monkeypatch):
    now = timezone.now()
    ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=2,
        round_interval_seconds=600,
        current_round=1,
        next_round_at=now - timedelta(seconds=1),
    )

    monkeypatch.setattr(
        arena_core,
        "_run_tournament_round",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken arena round contract")),
    )

    with pytest.raises(AssertionError, match="broken arena round contract"):
        arena_core.run_due_arena_rounds(now=now, limit=10)


@pytest.mark.django_db
def test_register_arena_entry_requires_registration_silver_cost():
    user = User.objects.create_user(
        username="arena_need_silver",
        password="pass123",
        email="arena_need_silver@test.local",
    )
    manor = ensure_manor(user)
    fund_manor(manor, silver=4999)
    template = create_guest_template("arena_need_silver_tpl")
    guest = create_guest(manor, template, "A")

    with pytest.raises(InsufficientSilverError, match="银两不足"):
        register_arena_entry(manor, [guest.id])
