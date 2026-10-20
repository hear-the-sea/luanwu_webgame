from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from battle.models import BattleReport
from gameplay.models import ArenaWheelBout, ArenaWheelEntry, ArenaWheelEntryGuest, ArenaWheelEvent, Manor, Message
from gameplay.services.arena import wheel_core
from gameplay.services.arena.snapshots import build_entry_guest_snapshot
from guests.models import GuestStatus
from tests.arena_services.support import create_guest, create_guest_template

pytestmark = pytest.mark.django_db


def test_wheel_registration_and_manual_cancellation_release_guests_without_refund(manor_factory):
    manor, _user = manor_factory(username="wheel_registration_cancel")
    manor.silver = wheel_core.ARENA_REGISTRATION_SILVER_COST * 2
    manor.save(update_fields=["silver"])
    template = create_guest_template("wheel_registration_cancel_guest")
    guests = [create_guest(manor, template, str(index)) for index in range(10)]

    result = wheel_core.register_arena_wheel_entry(manor, [guest.pk for guest in guests])

    assert result.event.status == ArenaWheelEvent.Status.RECRUITING
    assert result.entry.status == ArenaWheelEntry.Status.REGISTERED
    assert result.entry.entry_guests.count() == 10
    assert Manor.objects.get(pk=manor.pk).silver == wheel_core.ARENA_REGISTRATION_SILVER_COST
    assert set(manor.guests.filter(pk__in=[guest.pk for guest in guests]).values_list("status", flat=True)) == {
        GuestStatus.ARENA
    }

    assert wheel_core.cancel_arena_wheel_entry(manor) == 1

    result.entry.refresh_from_db()
    assert result.entry.status == ArenaWheelEntry.Status.CANCELLED
    assert Manor.objects.get(pk=manor.pk).silver == wheel_core.ARENA_REGISTRATION_SILVER_COST
    assert set(manor.guests.filter(pk__in=[guest.pk for guest in guests]).values_list("status", flat=True)) == {
        GuestStatus.IDLE
    }


@pytest.fixture
def wheel_report_world(manor_factory):
    first, _first_user = manor_factory(username="wheel_report_first")
    second, _second_user = manor_factory(username="wheel_report_second")
    spectator, spectator_user = manor_factory(username="wheel_report_spectator")
    now = timezone.now()
    event = ArenaWheelEvent.objects.create(
        status=ArenaWheelEvent.Status.RUNNING,
        player_limit=6,
        guest_limit_per_entry=10,
        current_bout_number=1,
        **wheel_core.current_replay_metadata(),
    )
    first_entry = ArenaWheelEntry.objects.create(
        event=event,
        manor=first,
        team=ArenaWheelEvent.Team.A,
        team_order=1,
        status=ArenaWheelEntry.Status.ACTIVE,
    )
    second_entry = ArenaWheelEntry.objects.create(
        event=event,
        manor=second,
        team=ArenaWheelEvent.Team.B,
        team_order=1,
        status=ArenaWheelEntry.Status.ACTIVE,
    )
    event.team_a_active_entry_id = first_entry.pk
    event.team_b_active_entry_id = second_entry.pk
    event.save(update_fields=["team_a_active_entry_id", "team_b_active_entry_id", "updated_at"])
    report = BattleReport.objects.create(
        manor=first,
        opponent_name=second.display_name,
        battle_type="arena",
        winner="attacker",
        starts_at=now,
        completed_at=now,
    )
    bout = ArenaWheelBout.objects.create(
        event=event,
        bout_number=1,
        team_a_entry=first_entry,
        team_b_entry=second_entry,
        winner_entry=first_entry,
        battle_report=report,
    )
    ArenaWheelEntry.objects.create(
        event=event,
        manor=spectator,
        status=ArenaWheelEntry.Status.CANCELLED,
    )
    client = Client()
    client.force_login(spectator_user)
    return event, bout, report, spectator, client


def test_wheel_bouts_wait_five_minutes_and_advance_one_at_a_time(manor_factory, monkeypatch):
    now = timezone.now()
    event = ArenaWheelEvent.objects.create(
        status=ArenaWheelEvent.Status.PREPARING,
        player_limit=6,
        guest_limit_per_entry=10,
        prepare_ends_at=now - timedelta(seconds=1),
        **wheel_core.current_replay_metadata(),
    )
    entries = {ArenaWheelEvent.Team.A: [], ArenaWheelEvent.Team.B: []}
    for team in (ArenaWheelEvent.Team.A, ArenaWheelEvent.Team.B):
        for order in range(1, 4):
            manor, _user = manor_factory(username=f"wheel_{team.lower()}_{order}")
            entries[team].append(
                ArenaWheelEntry.objects.create(
                    event=event,
                    manor=manor,
                    team=team,
                    team_order=order,
                )
            )
    cancelled_manor, _cancelled_user = manor_factory(username="wheel_cancelled_entry")
    cancelled_entry = ArenaWheelEntry.objects.create(
        event=event,
        manor=cancelled_manor,
        status=ArenaWheelEntry.Status.CANCELLED,
    )

    created_bout_numbers: list[int] = []

    def resolve_bout(*, event, team_a_entry, team_b_entry, bout_number, context):
        del context
        created_bout_numbers.append(bout_number)
        ArenaWheelBout.objects.create(
            event=event,
            bout_number=bout_number,
            team_a_entry=team_a_entry,
            team_b_entry=team_b_entry,
            winner_entry=team_a_entry,
        )
        return team_a_entry, "甲队胜"

    monkeypatch.setattr(wheel_core, "_run_bout", resolve_bout)

    assert wheel_core.run_due_arena_wheel_events(now=now) == 1
    event.refresh_from_db()
    assert event.status == ArenaWheelEvent.Status.RUNNING
    assert event.current_bout_number == 1
    assert created_bout_numbers == [1]
    cancelled_entry.refresh_from_db()
    assert cancelled_entry.status == ArenaWheelEntry.Status.CANCELLED
    first_bout = ArenaWheelBout.objects.get(event=event, bout_number=1)

    before_interval = first_bout.created_at + timedelta(seconds=wheel_core.ARENA_WHEEL_BOUT_INTERVAL_SECONDS - 1)
    assert wheel_core.run_due_arena_wheel_events(now=before_interval) == 0
    assert created_bout_numbers == [1]

    due_at = first_bout.created_at + timedelta(seconds=wheel_core.ARENA_WHEEL_BOUT_INTERVAL_SECONDS)
    assert wheel_core.run_due_arena_wheel_events(now=due_at) == 1
    event.refresh_from_db()
    assert event.current_bout_number == 2
    assert created_bout_numbers == [1, 2]
    active_survivor = ArenaWheelEntry.objects.get(pk=entries[ArenaWheelEvent.Team.A][0].pk)
    assert active_survivor.debuff_stacks == 2


def test_recruiting_event_lock_is_released_after_outer_transaction_commit(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    released = []
    monkeypatch.setattr(wheel_core, "acquire_best_effort_lock", lambda *args, **kwargs: (True, True, "lock-token"))
    monkeypatch.setattr(
        wheel_core,
        "release_best_effort_lock",
        lambda *args, **kwargs: released.append((args, kwargs)),
    )

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        with transaction.atomic():
            event = wheel_core._get_or_create_recruiting_event_locked()
            assert event.status == ArenaWheelEvent.Status.RECRUITING
            assert not released
        assert not released

    assert len(callbacks) == 1
    callbacks[0]()
    assert len(released) == 1


def test_wheel_defense_debuff_reduces_max_hp_from_defense_loss():
    assert wheel_core.get_arena_wheel_debuffed_defense_and_max_hp(
        defense_stat=100,
        max_hp=6000,
        debuff_stacks=0,
    ) == (100, 6000)
    assert wheel_core.get_arena_wheel_debuffed_defense_and_max_hp(
        defense_stat=100,
        max_hp=6000,
        debuff_stacks=1,
    ) == (95, 5750)
    assert wheel_core.get_arena_wheel_debuffed_defense_and_max_hp(
        defense_stat=100,
        max_hp=6000,
        debuff_stacks=2,
    ) == (90, 5500)


def test_real_wheel_bout_persists_report_and_remaining_guest_hp(manor_factory):
    team_a_manor, _team_a_user = manor_factory(username="wheel_simulated_a")
    team_b_manor, _team_b_user = manor_factory(username="wheel_simulated_b")
    event = ArenaWheelEvent.objects.create(
        status=ArenaWheelEvent.Status.RUNNING,
        player_limit=6,
        guest_limit_per_entry=10,
        **wheel_core.current_replay_metadata(),
    )
    team_a_entry = ArenaWheelEntry.objects.create(
        event=event,
        manor=team_a_manor,
        team=ArenaWheelEvent.Team.A,
        team_order=1,
        status=ArenaWheelEntry.Status.ACTIVE,
    )
    team_b_entry = ArenaWheelEntry.objects.create(
        event=event,
        manor=team_b_manor,
        team=ArenaWheelEvent.Team.B,
        team_order=1,
        status=ArenaWheelEntry.Status.ACTIVE,
    )
    template = create_guest_template("wheel_simulated_guest")
    team_a_guest = create_guest(team_a_manor, template, "A")
    team_b_guest = create_guest(team_b_manor, template, "B")
    team_a_link = ArenaWheelEntryGuest.objects.create(
        entry=team_a_entry,
        guest=team_a_guest,
        slot_index=0,
        snapshot=build_entry_guest_snapshot(team_a_guest),
    )
    team_b_link = ArenaWheelEntryGuest.objects.create(
        entry=team_b_entry,
        guest=team_b_guest,
        slot_index=0,
        snapshot=build_entry_guest_snapshot(team_b_guest),
    )
    recipients = [team_a_entry, team_b_entry]
    for team in (ArenaWheelEvent.Team.A, ArenaWheelEvent.Team.B):
        for order, status in ((2, ArenaWheelEntry.Status.REGISTERED), (3, ArenaWheelEntry.Status.ELIMINATED)):
            manor, _user = manor_factory(username=f"wheel_recipient_{team}_{order}")
            recipients.append(
                ArenaWheelEntry.objects.create(event=event, manor=manor, team=team, team_order=order, status=status)
            )
    cancelled_manor, _user = manor_factory(username="wheel_cancelled_recipient")
    ArenaWheelEntry.objects.create(
        event=event, manor=cancelled_manor, team=ArenaWheelEvent.Team.A, status=ArenaWheelEntry.Status.CANCELLED
    )

    wheel_core._run_bout(
        event=event,
        team_a_entry=team_a_entry,
        team_b_entry=team_b_entry,
        bout_number=1,
        context=wheel_core.replay_context(event),
    )

    bout = ArenaWheelBout.objects.get(event=event, bout_number=1)
    assert bout.battle_report_id is not None
    report = bout.battle_report
    messages = Message.objects.filter(battle_report=report, kind=Message.Kind.BATTLE)
    assert messages.count() == 6
    assert set(messages.values_list("manor_id", flat=True)) == {entry.manor_id for entry in recipients}
    for message in messages:
        recipient = next(entry for entry in recipients if entry.manor_id == message.manor_id)
        my_names = "、".join(entry.manor.display_name for entry in recipients if entry.team == recipient.team)
        enemy_names = "、".join(entry.manor.display_name for entry in recipients if entry.team != recipient.team)
        assert f"我方队伍：{my_names}" in message.body
        assert f"敌方队伍：{enemy_names}" in message.body
        assert message.title == f"金羽擂台 #{event.pk} 第 1 轮战报"
        assert team_a_manor.display_name in message.body
        assert team_b_manor.display_name in message.body
        assert bout.winner_note in message.body
        assert message.is_read is False
        assert message.has_attachments is False
        assert message.is_deletion_protected is False
    team_a_link.refresh_from_db()
    team_b_link.refresh_from_db()
    team_a_report = report.attacker_team if bout.team_a_is_attacker else report.defender_team
    team_b_report = report.defender_team if bout.team_a_is_attacker else report.attacker_team
    for link, members in ((team_a_link, team_a_report), (team_b_link, team_b_report)):
        report_member = next(member for member in members if member["combatant_slot"] == link.slot_index)
        remaining_hp = max(0, int(report_member["remaining_hp"]))
        assert link.snapshot["current_hp"] == max(1, min(link.snapshot["max_hp"], remaining_hp))
        assert link.snapshot["wheel_knocked_out"] is (remaining_hp <= 0)

    event.status = ArenaWheelEvent.Status.COMPLETED
    event.ended_at = timezone.now() - timedelta(days=2)
    event.save(update_fields=["status", "ended_at"])
    assert wheel_core.cleanup_expired_arena_wheel_events(now=timezone.now()) == 1
    assert not ArenaWheelBout.objects.filter(event_id=event.pk).exists()
    client = Client()
    for recipient in recipients:
        client.force_login(recipient.manor.user)
        response = client.get(reverse("battle:report_detail", args=[report.pk]))
        assert response.status_code == 200
        expected_attacker = (recipient.team == ArenaWheelEvent.Team.A) == bout.team_a_is_attacker
        assert response.context["player_side"] == ("attacker" if expected_attacker else "defender")
        assert response.context["is_spectator"] is False
        assert recipient.manor.display_name in response.context["arena_wheel_teams"][0]["members"]
        assert "我方队伍" in response.content.decode()
        assert "敌方队伍" in response.content.decode()
    client.force_login(cancelled_manor.user)
    assert client.get(reverse("battle:report_detail", args=[report.pk])).status_code == 404


@pytest.mark.parametrize("surviving_team", [None, ArenaWheelEvent.Team.A, ArenaWheelEvent.Team.B])
def test_wheel_forfeit_notifies_both_players_without_report(wheel_report_world, monkeypatch, surviving_team):
    event, previous_bout, _report, spectator, _client = wheel_report_world
    monkeypatch.setattr(
        wheel_core,
        "_load_active_guest_proxies",
        lambda entry: ([object()] if entry.team == surviving_team else [], {}),
    )

    winner, note = wheel_core._run_bout(
        event=event,
        team_a_entry=previous_bout.team_a_entry,
        team_b_entry=previous_bout.team_b_entry,
        bout_number=2,
        context=wheel_core.replay_context(event),
    )

    assert (winner.team if winner else None) == surviving_team
    messages = Message.objects.filter(title=f"金羽擂台 #{event.pk} 第 2 轮战报")
    assert messages.count() == 2
    assert set(messages.values_list("manor_id", flat=True)) == {
        previous_bout.team_a_entry.manor_id,
        previous_bout.team_b_entry.manor_id,
    }
    assert not messages.filter(manor=spectator).exists()
    for message in messages:
        assert message.battle_report_id is None
        assert message.kind == Message.Kind.BATTLE
        assert note in message.body


def test_wheel_scheduler_does_not_resend_bout_messages(wheel_report_world):
    event, previous_bout, _report, _spectator, _client = wheel_report_world
    due_at = previous_bout.created_at + timedelta(seconds=wheel_core.ARENA_WHEEL_BOUT_INTERVAL_SECONDS)

    assert wheel_core.run_due_arena_wheel_events(now=due_at) == 1
    assert wheel_core.run_due_arena_wheel_events(now=due_at) == 0

    assert ArenaWheelBout.objects.filter(event=event, bout_number=2).count() == 1
    assert Message.objects.filter(title=f"金羽擂台 #{event.pk} 第 2 轮战报").count() == 2


def test_every_wheel_round_notifies_all_six_members_including_eliminated_players(wheel_report_world, manor_factory):
    event, previous_bout, _report, spectator, _client = wheel_report_world
    for team in (ArenaWheelEvent.Team.A, ArenaWheelEvent.Team.B):
        for order in (2, 3):
            manor, _user = manor_factory(username=f"wheel_all_rounds_{team}_{order}")
            ArenaWheelEntry.objects.create(event=event, manor=manor, team=team, team_order=order)
    recipient_ids = set(
        event.entries.exclude(status=ArenaWheelEntry.Status.CANCELLED).values_list("manor_id", flat=True)
    )
    for number in (2, 3, 4):
        latest = event.bouts.order_by("-bout_number").first()
        due_at = latest.created_at + timedelta(seconds=wheel_core.ARENA_WHEEL_BOUT_INTERVAL_SECONDS)
        assert wheel_core.run_due_arena_wheel_events(now=due_at) == 1
        messages = Message.objects.filter(title=f"金羽擂台 #{event.pk} 第 {number} 轮战报")
        assert messages.count() == 6
        assert set(messages.values_list("manor_id", flat=True)) == recipient_ids
        assert not messages.filter(manor=spectator).exists()
        ArenaWheelBout.objects.filter(event=event, bout_number=number).update(created_at=due_at)
        assert wheel_core.run_due_arena_wheel_events(now=due_at) == 0
    event.refresh_from_db()
    assert event.status == ArenaWheelEvent.Status.COMPLETED


def test_wheel_message_failure_rolls_back_bout_and_can_retry(wheel_report_world, monkeypatch):
    event, previous_bout, _report, _spectator, _client = wheel_report_world
    due_at = previous_bout.created_at + timedelta(seconds=wheel_core.ARENA_WHEEL_BOUT_INTERVAL_SECONDS)
    create_messages = wheel_core.bulk_create_messages

    def fail_after_creating_messages(data):
        create_messages(data)
        raise RuntimeError("message delivery failed")

    monkeypatch.setattr(wheel_core, "bulk_create_messages", fail_after_creating_messages)
    with pytest.raises(RuntimeError, match="message delivery failed"):
        wheel_core.run_due_arena_wheel_events(now=due_at)
    event.refresh_from_db()
    assert event.current_bout_number == 1
    assert not ArenaWheelBout.objects.filter(event=event, bout_number=2).exists()
    assert not Message.objects.filter(title=f"金羽擂台 #{event.pk} 第 2 轮战报").exists()

    monkeypatch.setattr(wheel_core, "bulk_create_messages", create_messages)
    assert wheel_core.run_due_arena_wheel_events(now=due_at) == 1
    assert Message.objects.filter(title=f"金羽擂台 #{event.pk} 第 2 轮战报").count() == 2


@pytest.mark.parametrize("team", [ArenaWheelEvent.Team.A, ArenaWheelEvent.Team.B])
@pytest.mark.parametrize("team_a_is_attacker", [True, False])
@pytest.mark.parametrize("status", [ArenaWheelEntry.Status.REGISTERED, ArenaWheelEntry.Status.ELIMINATED])
def test_wheel_teammates_view_reports_from_their_team_perspective(
    wheel_report_world, manor_factory, team, team_a_is_attacker, status
):
    event, bout, report, _spectator, client = wheel_report_world
    manor, user = manor_factory(username="wheel_viewing_teammate")
    ArenaWheelEntry.objects.create(event=event, manor=manor, team=team, team_order=2, status=status)
    bout.team_a_is_attacker = team_a_is_attacker
    bout.save(update_fields=["team_a_is_attacker"])
    client.force_login(user)

    response = client.get(reverse("battle:report_detail", args=[report.pk]))

    assert response.status_code == 200
    assert response.context["is_spectator"] is False
    expected_attacker = (team == ArenaWheelEvent.Team.A) == team_a_is_attacker
    assert response.context["player_side"] == ("attacker" if expected_attacker else "defender")
    teams = response.context["arena_wheel_teams"]
    assert teams[0]["label"].startswith("我方队伍")
    assert teams[1]["label"].startswith("敌方队伍")
    assert manor.display_name in teams[0]["members"]
    own_entry = bout.team_a_entry if team == ArenaWheelEvent.Team.A else bout.team_b_entry
    enemy_entry = bout.team_b_entry if team == ArenaWheelEvent.Team.A else bout.team_a_entry
    assert teams[0]["active_player"] == own_entry.manor.display_name
    assert teams[1]["active_player"] == enemy_entry.manor.display_name
    body = response.content.decode()
    assert "我方队伍" in body
    assert "敌方队伍" in body
    assert "本轮出战" in body


def test_wheel_settlement_awards_each_player_their_team_reward(manor_factory, monkeypatch):
    event = ArenaWheelEvent.objects.create(
        status=ArenaWheelEvent.Status.RUNNING,
        winner_team=ArenaWheelEvent.Team.A,
        **wheel_core.current_replay_metadata(),
    )
    entries = []
    rewards: dict[int, int] = {}
    for team in (ArenaWheelEvent.Team.A, ArenaWheelEvent.Team.B):
        for order in range(1, 4):
            manor, _user = manor_factory(username=f"wheel_reward_{team.lower()}_{order}")
            entries.append(
                ArenaWheelEntry.objects.create(
                    event=event,
                    manor=manor,
                    team=team,
                    team_order=order,
                    status=ArenaWheelEntry.Status.ACTIVE,
                )
            )

    monkeypatch.setattr(wheel_core, "_release_entry_guest_statuses", lambda _entry: None)
    monkeypatch.setattr(
        wheel_core,
        "credit_coins",
        lambda manor, amount, _note: rewards.update({manor.pk: amount}),
    )

    with transaction.atomic():
        wheel_core._settle_event_locked(event=event, entries=entries, now=timezone.now())

    event.refresh_from_db()
    assert event.status == ArenaWheelEvent.Status.COMPLETED
    assert sorted(rewards.values()) == [50, 50, 50, 200, 200, 200]
    assert all(entry.status == ArenaWheelEntry.Status.COMPLETED for entry in entries if entry.team == "A")
    assert all(entry.status == ArenaWheelEntry.Status.ELIMINATED for entry in entries if entry.team == "B")


@pytest.mark.parametrize(
    "status,hours_ago,visible",
    [
        (ArenaWheelEvent.Status.RUNNING, None, True),
        (ArenaWheelEvent.Status.COMPLETED, 23.9, True),
        (ArenaWheelEvent.Status.COMPLETED, 24.1, False),
    ],
)
def test_wheel_reports_and_event_pages_allow_spectators_in_retention_window(
    wheel_report_world,
    status,
    hours_ago,
    visible,
):
    event, bout, report, _spectator, client = wheel_report_world
    event.status = status
    if hours_ago is not None:
        event.ended_at = timezone.now() - timedelta(hours=hours_ago)
    event.save(update_fields=["status", "ended_at", "updated_at"])

    report_response = client.get(reverse("battle:report_detail", args=[report.pk]))
    expected_status = 200 if visible else 404
    assert report_response.status_code == expected_status
    if visible:
        assert report_response.context["is_spectator"] is True

    event_response = client.get(reverse("gameplay:arena_wheel_detail", args=[event.pk]))
    assert event_response.status_code == expected_status
    if status == ArenaWheelEvent.Status.RUNNING:
        body = event_response.content.decode()
        assert "下一场预计" in body
        assert 'http-equiv="refresh"' in body
        assert reverse("battle:report_detail", args=[report.pk]) in body
        events_response = client.get(reverse("gameplay:arena_events"))
        assert "参赛 2/6" in events_response.content.decode()
