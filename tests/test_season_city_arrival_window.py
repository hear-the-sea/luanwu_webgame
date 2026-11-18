"""古城派遣按预计抵达时刻校验周末窗口，不白占门客和额度。"""

from copy import deepcopy
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from core.exceptions import GameError
from gameplay.models import SeasonExpedition, SeasonRally
from gameplay.services.season.quotas import daily_dispatches_used
from guests.models import Guest, GuestStatus
from tests.test_season_rallies import army_payload, command, join_rally
from tests.test_season_rallies import rally_world as _rally_world  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def rally_world(request):
    return request.getfixturevalue("_rally_world")


def sunday_at(clock, hour, minute):
    local = timezone.localtime(clock[0], ZoneInfo("Asia/Shanghai"))
    sunday = local.date() + timedelta(days=(6 - local.weekday()) % 7)
    clock[0] = datetime.combine(sunday, time(hour, minute), tzinfo=ZoneInfo("Asia/Shanghai"))


def create(member, guests, target_members):
    return command(
        member,
        "rally_create",
        {
            **army_payload(guests, key="river:town"),
            "rally_target_members": target_members,
        },
    )


def test_sunday_late_rally_rejects_wait_that_crosses_closing_and_rolls_back(rally_world, monkeypatch):
    season, player, clock = rally_world
    sunday_at(clock, 23, 40)
    monkeypatch.setattr("gameplay.services.season.expeditions.travel_time_seconds", lambda *args: 900)
    member, guests = player()
    stock = deepcopy(season.faction_states.get(faction=member.faction).troops)
    with pytest.raises(GameError, match="古城开放时间内抵达.*减少集结出发人数"):
        create(member, guests, target_members=5)
    assert not SeasonRally.objects.filter(season=season).exists()
    assert not SeasonExpedition.objects.filter(member=member).exists()
    assert Guest.objects.get(pk=guests[0].pk).status == GuestStatus.IDLE
    member.refresh_from_db()
    assert member.troops_drawn == 0 and daily_dispatches_used(member, timezone.localdate(clock[0])) == 0
    assert season.faction_states.get(faction=member.faction).troops == stock
    result = create(member, guests, target_members=1)
    run = SeasonExpedition.objects.get(pk=result["run_id"])
    assert run.status == "outbound" and run.due_at == clock[0] + timedelta(minutes=15)


@pytest.mark.parametrize("travel,accepted", [(599, True), (600, False)])
def test_city_arrival_immediately_before_or_at_monday_boundary(rally_world, monkeypatch, travel, accepted):
    season, player, clock = rally_world
    sunday_at(clock, 23, 50)
    monkeypatch.setattr("gameplay.services.season.expeditions.travel_time_seconds", lambda *args: travel)
    member, guests = player()
    if accepted:
        result = create(member, guests, target_members=1)
        run = SeasonExpedition.objects.get(pk=result["run_id"])
        assert run.due_at == clock[0] + timedelta(seconds=599)
    else:
        with pytest.raises(GameError, match="古城开放时间内抵达"):
            create(member, guests, target_members=1)
        assert not member.expeditions.exists()


@pytest.mark.parametrize("target_members", [2, 3])
def test_join_city_rally_uses_immediate_departure_only_if_join_fills_target(rally_world, monkeypatch, target_members):
    season, player, clock = rally_world
    sunday_at(clock, 23, 25)
    monkeypatch.setattr("gameplay.services.season.expeditions.travel_time_seconds", lambda *args: 60)
    leader, guests = player()
    result = create(leader, guests, target_members)
    rally = SeasonRally.objects.get(pk=result["rally_id"])
    original_deadline = rally.gathers_until
    clock[0] += timedelta(minutes=10)
    monkeypatch.setattr("gameplay.services.season.expeditions.travel_time_seconds", lambda *args: 1200)
    teammate, own_guests = player()
    if target_members == 2:
        run = join_rally(teammate, own_guests, rally)
        rally.refresh_from_db()
        assert rally.status == "marching"
        assert run.due_at == clock[0] + timedelta(minutes=20)
        assert timezone.localtime(run.due_at, ZoneInfo("Asia/Shanghai")).weekday() == 6
    else:
        with pytest.raises(GameError, match="古城开放时间内抵达"):
            join_rally(teammate, own_guests, rally)
        rally.refresh_from_db()
        assert rally.status == "gathering" and rally.gathers_until == original_deadline
        assert not teammate.expeditions.exists()
        assert Guest.objects.get(pk=own_guests[0].pk).status == GuestStatus.IDLE


def test_rejecting_new_late_request_does_not_modify_existing_city_march(rally_world, monkeypatch):
    season, player, clock = rally_world
    sunday_at(clock, 23, 20)
    monkeypatch.setattr("gameplay.services.season.expeditions.travel_time_seconds", lambda *args: 600)
    leader, guests = player()
    result = create(leader, guests, target_members=1)
    run = SeasonExpedition.objects.get(pk=result["run_id"])
    before = (run.status, run.travel_seconds, run.launched_at, run.due_at, run.result.copy())
    clock[0] += timedelta(minutes=35)
    late, late_guests = player()
    with pytest.raises(GameError, match="古城开放时间内抵达"):
        create(late, late_guests, target_members=1)
    run.refresh_from_db()
    assert (run.status, run.travel_seconds, run.launched_at, run.due_at, run.result) == before


def test_legal_sunday_arrival_is_not_cancelled_when_scan_runs_after_midnight(rally_world, monkeypatch):
    from django.utils.dateparse import parse_datetime

    from battle.models import BattleReport
    from gameplay.services.season.expeditions import process_expedition

    season, player, clock = rally_world
    sunday_at(clock, 23, 50)
    monkeypatch.setattr("gameplay.services.season.expeditions.travel_time_seconds", lambda *args: 599)
    member, guests = player(force=8000)
    result = create(member, guests, target_members=1)
    run = SeasonExpedition.objects.get(pk=result["run_id"])
    expected_arrival = run.due_at
    clock[0] += timedelta(minutes=11)
    assert process_expedition(run.pk, clock[0])
    run.refresh_from_db()
    rally = SeasonRally.objects.get(pk=result["rally_id"])
    assert run.report_id and BattleReport.objects.filter(pk=run.report_id).exists()
    assert parse_datetime(rally.result["scheduled_arrival_at"]) == expected_arrival
    assert run.report.created_at == clock[0]


def test_delayed_city_departure_keeps_real_time_and_qualifies_only_first_wave(rally_world, monkeypatch):
    from django.utils.dateparse import parse_datetime

    from battle.models import BattleReport
    from gameplay.services.season.expeditions import process_expedition

    season, player, clock = rally_world
    sunday_at(clock, 23, 20)
    monkeypatch.setattr("gameplay.services.season.expeditions.travel_time_seconds", lambda *args: 60)
    member, guests = player(force=8000)
    result = create(member, guests, target_members=5)
    run = SeasonExpedition.objects.get(pk=result["run_id"])
    rally = SeasonRally.objects.get(pk=result["rally_id"])
    expected_arrival = rally.gathers_until + timedelta(seconds=60)
    clock[0] += timedelta(minutes=45)  # 周一00:05才处理原定周日23:50的发车。
    assert process_expedition(run.pk, clock[0])
    run.refresh_from_db()
    rally.refresh_from_db()
    assert rally.status == "marching"
    assert rally.launched_at == run.launched_at == clock[0]
    assert rally.due_at == clock[0] + timedelta(seconds=60)
    assert parse_datetime(rally.result["scheduled_arrival_at"]) == expected_arrival
    clock[0] = run.due_at
    assert process_expedition(run.pk, clock[0])
    run.refresh_from_db()
    rally.refresh_from_db()
    assert run.report_id and rally.result["wave"] == 1
    assert rally.status == "marching"
    clock[0] = rally.due_at
    assert process_expedition(run.pk, clock[0])
    rally.refresh_from_db()
    assert rally.status == "resolved"
    assert BattleReport.objects.count() == 1
    assert "古城仅在" in rally.result["message"]


def test_old_gathering_plan_that_would_arrive_monday_is_not_grandfathered(rally_world, monkeypatch):
    from gameplay.services.season.expeditions import process_expedition

    season, player, clock = rally_world
    sunday_at(clock, 23, 20)
    monkeypatch.setattr("gameplay.services.season.expeditions.travel_time_seconds", lambda *args: 60)
    member, guests = player()
    result = create(member, guests, target_members=5)
    run = SeasonExpedition.objects.get(pk=result["run_id"])
    rally = SeasonRally.objects.get(pk=result["rally_id"])
    rally.gathers_until = clock[0] + timedelta(minutes=30)  # 旧版 30 分钟集结窗口。
    rally.save(update_fields=["gathers_until"])
    run.travel_seconds = 1200  # 模拟更新前曾允许加入慢队员的旧集结。
    run.save(update_fields=["travel_seconds"])
    clock[0] += timedelta(minutes=45)
    assert process_expedition(run.pk, clock[0])
    run.refresh_from_db()
    rally = SeasonRally.objects.get(pk=result["rally_id"])
    assert rally.status == "cancelled" and run.status == "completed"
    assert not run.report_id
    assert Guest.objects.get(pk=guests[0].pk).status == GuestStatus.IDLE


def test_old_marching_city_uses_frozen_due_time_when_no_qualification_snapshot(rally_world, monkeypatch):
    from gameplay.services.season.expeditions import process_expedition

    season, player, clock = rally_world
    sunday_at(clock, 23, 50)
    monkeypatch.setattr("gameplay.services.season.expeditions.travel_time_seconds", lambda *args: 599)
    member, guests = player(force=8000)
    result = create(member, guests, target_members=1)
    rally = SeasonRally.objects.get(pk=result["rally_id"])
    rally.result.pop("scheduled_arrival_at")
    rally.save(update_fields=["result"])
    run = SeasonExpedition.objects.get(pk=result["run_id"])
    clock[0] += timedelta(minutes=11)
    assert process_expedition(run.pk, clock[0])
    run.refresh_from_db()
    assert run.report_id


def test_expired_season_is_not_reopened_for_previously_legal_city_arrival(rally_world, monkeypatch):
    from gameplay.services.season.expeditions import process_expedition

    season, player, clock = rally_world
    sunday_at(clock, 23, 50)
    monkeypatch.setattr("gameplay.services.season.expeditions.travel_time_seconds", lambda *args: 300)
    season.ends_at = clock[0] + timedelta(minutes=8)
    season.save(update_fields=["ends_at"])
    member, guests = player()
    result = create(member, guests, target_members=1)
    run = SeasonExpedition.objects.get(pk=result["run_id"])
    clock[0] += timedelta(minutes=11)
    assert process_expedition(run.pk, clock[0])
    run.refresh_from_db()
    assert run.status == "returning" and not run.report_id


def test_legal_city_garrison_arrival_qualifies_by_eta_but_starts_service_at_real_now(rally_world, monkeypatch):
    from django.utils.dateparse import parse_datetime

    from gameplay.services.season.expeditions import process_expedition

    season, player, clock = rally_world
    sunday_at(clock, 23, 50)
    monkeypatch.setattr("gameplay.services.season.expeditions.travel_time_seconds", lambda *args: 599)
    member, guests = player()
    site = season.sites.get(key="river:town")
    site.owner = member.faction
    site.save(update_fields=["owner"])
    response = command(member, "dispatch", army_payload(guests, key="river:town", mode="garrison"))
    run = SeasonExpedition.objects.get(pk=response["run_id"])
    clock[0] += timedelta(minutes=11)
    assert process_expedition(run.pk, clock[0])
    run.refresh_from_db()
    assert run.status == "stationed"
    assert parse_datetime(run.result["stationed_at"]) == clock[0]
    assert run.stationed_until == clock[0] + timedelta(seconds=3600)
