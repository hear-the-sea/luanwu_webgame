"""一次离线授权只在下一场匹配集结使用，失败不遗留资源与门客占用。"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from core.exceptions import GameError
from gameplay.models import SeasonAction, SeasonExpedition
from gameplay.services.season.quotas import daily_dispatches_used
from gameplay.services.season.reservations import (
    cancel_reservation_locked,
    consume_reservations_locked,
    create_reservation_locked,
)
from guests.models import GuestStatus
from tests.test_season_rallies import army_payload, command
from tests.test_season_rallies import rally_world as _rally_world  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def reservation_world(_rally_world):  # noqa: F811
    season, player, clock = _rally_world
    season.rules["campaign"]["playability"] = {
        "enabled": True,
        "reservation": {"maximum_hours": 24, "guest_limit": 3},
    }
    season.save(update_fields=["rules"])
    return season, player, clock


def authorize(member, guests, now, **extra):
    with transaction.atomic():
        return create_reservation_locked(member, {**army_payload(guests), "hours": 2, **extra}, now)


def reservation(member):
    member.refresh_from_db()
    return member.lineup["reservation"]


def test_next_matching_rally_uses_exact_authorization_once(reservation_world):
    season, player, clock = reservation_world
    leader, leaders = player()
    member, guests = player(count=3)
    state = season.faction_states.get(faction="fiction")
    before = dict(state.troops)
    member.lineup = {"other": "preserved"}
    member.save(update_fields=["lineup"])
    authorize(member, guests, clock[0], troops={"dao_sheng": 10})
    state.refresh_from_db()
    assert state.troops == before
    assert not member.expeditions.exists()
    for guest in guests:
        guest.refresh_from_db()
        assert guest.status == GuestStatus.IDLE
    result = command(leader, "rally_create", army_payload(leaders))
    run = member.expeditions.get()
    assert run.rally_id == result["rally_id"]
    assert run.guest_ids == [guest.pk for guest in guests]
    assert run.initial_troops == {"dao_sheng": 10}
    assert reservation(member)["status"] == "used"
    assert member.lineup["other"] == "preserved"
    assert daily_dispatches_used(member, timezone.localdate(clock[0])) == 1
    with transaction.atomic():
        consume_reservations_locked(run.rally, clock[0])
    assert member.expeditions.count() == 1
    assert member.actions.filter(kind="reservation_used").count() == 1


def test_changed_guest_fails_once_without_consuming_troops_or_quota(reservation_world):
    season, player, clock = reservation_world
    leader, leaders = player()
    member, guests = player()
    authorize(member, guests, clock[0])
    guest = guests[0]
    guest.status = GuestStatus.DEPLOYED
    guest.save(update_fields=["status"])
    state = season.faction_states.get(faction="fiction")
    before = dict(state.troops)
    command(leader, "rally_create", army_payload(leaders))
    assert reservation(member)["status"] == "failed"
    assert not member.expeditions.exists()
    assert daily_dispatches_used(member, timezone.localdate(clock[0])) == 0
    state.refresh_from_db()
    assert state.troops["dao_sheng"] == before["dao_sheng"] - 10  # 仅发起人实际领兵。
    assert member.troops_drawn == 0
    guest.status = GuestStatus.IDLE
    guest.save(update_fields=["status"])
    other, others = player()
    command(other, "rally_create", army_payload(others))
    assert not member.expeditions.exists()


def test_failure_after_troop_reservation_rolls_back_all_debits(reservation_world):
    season, player, clock = reservation_world
    leader, leaders = player()
    member, guests = player()
    authorize(member, guests, clock[0])
    # 预约时容量合法；真正集结到来前门客属性变化，旧授权不能绕过容量校验。
    # 阵营兵库充足，但把授权修改为超出当前门客带兵容量，模拟存量授权与角色成长变更。
    saved = reservation(member)
    saved["troops"] = {"dao_sheng": sum(guest.troop_capacity for guest in guests) + 1}
    member.lineup["reservation"] = saved
    member.save(update_fields=["lineup"])
    state = season.faction_states.get(faction="fiction")
    state.troops["dao_sheng"] = 100000
    state.save(update_fields=["troops"])
    before = state.troops["dao_sheng"]
    command(leader, "rally_create", army_payload(leaders))
    assert reservation(member)["status"] == "failed"
    state.refresh_from_db()
    assert state.troops["dao_sheng"] == before - 10
    member.refresh_from_db()
    assert member.troops_drawn == 0
    assert not member.expeditions.exists()
    guests[0].refresh_from_db()
    assert guests[0].status == GuestStatus.IDLE


def test_single_player_departure_does_not_consume_waiting_authorization(reservation_world):
    _, player, clock = reservation_world
    leader, leaders = player()
    member, guests = player()
    authorize(member, guests, clock[0])
    command(leader, "rally_create", {**army_payload(leaders), "rally_target_members": 1})
    assert reservation(member)["status"] == "pending"
    assert not member.expeditions.exists()


def test_other_destination_or_faction_does_not_use_authorization(reservation_world):
    _, player, clock = reservation_world
    member, guests = player()
    authorize(member, guests, clock[0])
    enemy, enemies = player(faction="history")
    command(enemy, "rally_create", army_payload(enemies))
    other, others = player()
    command(other, "rally_create", army_payload(others, key="forest:granary"))
    assert reservation(member)["status"] == "pending"
    assert not member.expeditions.exists()


def test_expired_and_cancelled_authorizations_do_not_dispatch(reservation_world):
    _, player, clock = reservation_world
    leader, leaders = player()
    member, guests = player()
    authorize(member, guests, clock[0], hours=1)
    clock[0] += timedelta(hours=1)
    command(leader, "rally_create", army_payload(leaders))
    assert reservation(member)["status"] == "expired"
    assert not member.expeditions.exists()
    authorize(member, guests, clock[0])
    with transaction.atomic():
        cancel_reservation_locked(member, clock[0])
    other, others = player()
    command(other, "rally_create", army_payload(others))
    assert reservation(member)["status"] == "cancelled"
    assert not member.expeditions.exists()


def test_pending_cannot_be_overwritten_and_special_troops_need_separate_consent(reservation_world):
    season, player, clock = reservation_world
    member, guests = player()
    special = next(iter(season.rules["campaign"]["economy"]["special_troops"]))
    with pytest.raises(GameError, match="特殊铁骑"):
        authorize(member, guests, clock[0], troops={special: 10})
    authorize(member, guests, clock[0])
    with pytest.raises(GameError, match="已有"):
        authorize(member, guests, clock[0])
    assert SeasonExpedition.objects.count() == 0


def test_full_rally_leaves_later_authorizations_pending(reservation_world):
    _, player, clock = reservation_world
    leader, leaders = player()
    later, later_guests = player()
    earlier, earlier_guests = player()
    authorize(earlier, earlier_guests, clock[0])
    clock[0] += timedelta(seconds=1)
    authorize(later, later_guests, clock[0])
    result = command(leader, "rally_create", {**army_payload(leaders), "rally_target_members": 2})
    assert reservation(earlier)["status"] == "used"
    assert reservation(later)["status"] == "pending"
    run = earlier.expeditions.get()
    assert run.rally_id == result["rally_id"]
    assert run.status == "outbound"
    assert not later.expeditions.exists()


def test_other_real_expedition_blocks_authorized_join(reservation_world):
    _, player, clock = reservation_world
    leader, leaders = player()
    member, guests = player(count=2)
    authorize(member, guests[:1], clock[0])
    command(member, "dispatch", army_payload(guests[1:], key="mountain:inn", mode="pve", troops=0))
    command(leader, "rally_create", army_payload(leaders))
    assert reservation(member)["status"] == "failed"
    assert member.expeditions.count() == 1
    assert member.expeditions.get().kind == "pve"
    guests[0].refresh_from_db()
    assert guests[0].status == GuestStatus.IDLE


def test_reservation_command_replay_is_idempotent_and_cancellable(reservation_world):
    _, player, _ = reservation_world
    member, guests = player()
    payload = {**army_payload(guests), "hours": 2}
    token = uuid4()
    first = command(member, "reservation_create", payload, token)
    repeated = command(member, "reservation_create", payload, token)
    assert first == repeated
    assert reservation(member)["id"] == first["reservation"]["id"]
    command(member, "reservation_cancel", {})
    assert reservation(member)["status"] == "cancelled"
    assert member.actions.filter(kind="reservation_create").count() == 1
    assert not member.expeditions.exists()


def test_offline_authorization_cannot_bypass_daily_dispatch_limit(reservation_world):
    season, player, clock = reservation_world
    leader, leaders = player()
    member, guests = player()
    authorize(member, guests, clock[0])
    limit = season.rules["campaign"]["expeditions"]["daily_actions"]
    for _ in range(limit):
        SeasonAction.objects.create(
            member=member,
            token=uuid4(),
            request_hash="used-quota",
            kind="dispatch",
            date=timezone.localdate(clock[0]),
        )
    command(leader, "rally_create", army_payload(leaders))
    assert reservation(member)["status"] == "failed"
    assert not member.expeditions.exists()
    assert daily_dispatches_used(member, timezone.localdate(clock[0])) == limit
