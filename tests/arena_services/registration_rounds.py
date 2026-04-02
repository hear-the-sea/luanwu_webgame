from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

import gameplay.services.arena.core as arena_core
from core.exceptions import (
    ArenaBusyError,
    ArenaCancellationError,
    ArenaGuestSelectionError,
    ArenaParticipationLimitError,
    InsufficientSilverError,
)
from gameplay.models import ArenaEntry, ArenaEntryGuest, ArenaMatch, ArenaTournament, Message
from gameplay.services.arena.core import cancel_arena_entry, register_arena_entry, run_due_arena_rounds
from gameplay.services.manor.core import ensure_manor
from guests.models import GuestStatus
from tests.arena_services.support import User, create_guest, create_guest_template, fund_manor


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


@pytest.mark.django_db
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
