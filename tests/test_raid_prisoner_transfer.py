"""踢馆攻胜优先夺取敌方俘虏，保留囚徒身份和唯一门客状态。"""

from __future__ import annotations

import random
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import DatabaseError
from django.utils import timezone

from core.exceptions import GuestAlreadyOwnedError
from gameplay.models import Building, BuildingType, JailPrisoner, RaidRun
from gameplay.services import jail as jail_service
from gameplay.services.jail_expiration import JAIL_MAX_HOLD_DURATION
from gameplay.services.manor.core import ensure_manor
from gameplay.services.raid.combat import capture as capture_service
from guests.models import Guest, GuestTemplate, WorldUniqueGuest
from guests.services.world_unique import claim_world_unique_guest_from_scroll, get_world_unique_guest_status
from tests import test_world_unique_capture as unique_capture_tests
from tests.jail_persuasion.test_recruitment_service import FixedRng
from tests.test_world_unique_capture import _capture, _fund_recruitment

pytestmark = pytest.mark.django_db
unique_capture_world = unique_capture_tests.unique_capture_world


def _set_jail_capacity(manor, capacity):
    building_type, _ = BuildingType.objects.get_or_create(key="jail", defaults={"name": "监牢"})
    Building.objects.update_or_create(manor=manor, building_type=building_type, defaults={"level": capacity})
    manor.invalidate_building_cache()
    assert manor.jail_capacity == capacity


@pytest.fixture
def raid_world(django_user_model):
    winner = ensure_manor(django_user_model.objects.create_user(username="prisoner_transfer_attacker"))
    loser = ensure_manor(django_user_model.objects.create_user(username="prisoner_transfer_defender"))
    original = ensure_manor(django_user_model.objects.create_user(username="prisoner_transfer_original"))
    template = GuestTemplate.objects.create(key="prisoner_transfer_ordinary", name="原有囚徒", rarity="purple")
    active_template = GuestTemplate.objects.create(key="prisoner_transfer_active", name="出战门客", rarity="purple")
    active = Guest.objects.create(manor=loser, template=active_template)
    _set_jail_capacity(winner, 3)
    _set_jail_capacity(loser, 3)
    return SimpleNamespace(winner=winner, loser=loser, original=original, template=template, active=active)


def _prisoner(world, *, captor=None, age_days=1):
    prisoner = JailPrisoner.objects.create(
        captor=captor or world.loser,
        original_manor=world.original,
        guest_template=world.template,
        original_guest_name="旧监牢囚徒",
        original_level=21,
        rarity_pill_used=True,
        loyalty=45,
        captured_loyalty=75,
        affinity=51,
        milestone_stage=1,
        interactions_today=2,
        last_method="bribe",
        same_method_streak=2,
    )
    JailPrisoner.objects.filter(pk=prisoner.pk).update(captured_at=timezone.now() - timedelta(days=age_days))
    prisoner.refresh_from_db()
    return prisoner


def _run(winner, loser, *, attacker_victory=True, active_guest=None):
    run = RaidRun.objects.create(attacker=winner, defender=loser)
    report = SimpleNamespace(
        winner="attacker" if attacker_victory else "defender",
        attacker_team=[],
        defender_team=[{"guest_id": active_guest.pk}] if active_guest else [],
    )
    return capture_service._try_capture_guest(run, report, attacker_victory, rng=random.Random(1))


def _normal_capture_must_not_run(*_args, **_kwargs):
    raise AssertionError("夺取敌方囚徒成功后不应再进行普通俘虏概率判定")


def test_victory_takes_oldest_enemy_prisoner_before_normal_capture_and_preserves_record(raid_world, monkeypatch):
    world = raid_world
    first = _prisoner(world, age_days=5)
    second = _prisoner(world, age_days=2)
    before = JailPrisoner.objects.values().get(pk=first.pk)
    monkeypatch.setattr(capture_service, "_can_attempt_capture", _normal_capture_must_not_run)

    result = _run(world.winner, world.loser, active_guest=world.active)

    assert result["source"] == "enemy_jail"
    assert result["prisoner_id"] == first.pk
    assert result["guest_name"] == first.display_name
    after = JailPrisoner.objects.values().get(pk=first.pk)
    assert after == {**before, "captor_id": world.winner.pk}
    second.refresh_from_db()
    assert second.captor_id == world.loser.pk
    assert JailPrisoner.objects.filter(captor=world.winner, status="held").count() == 1
    assert JailPrisoner.objects.count() == 2
    assert Guest.objects.filter(pk=world.active.pk).exists()


@pytest.mark.parametrize("capacity,occupied", [(0, False), (1, True)])
def test_no_prison_is_taken_without_a_free_jail_slot(raid_world, capacity, occupied):
    world = raid_world
    _set_jail_capacity(world.winner, capacity)
    target = _prisoner(world)
    if occupied:
        _prisoner(world, captor=world.winner)
    assert _run(world.winner, world.loser, active_guest=world.active) is None
    target.refresh_from_db()
    assert target.captor_id == world.loser.pk
    assert Guest.objects.filter(pk=world.active.pk).exists()


def test_empty_enemy_jail_falls_back_to_normal_capture(raid_world, monkeypatch):
    world = raid_world
    calls = []

    def ordinary_gate(winner, *, losing_side, rng):
        calls.append((winner.pk, losing_side))
        return True

    monkeypatch.setattr(capture_service, "_can_attempt_capture", ordinary_gate)
    result = _run(world.winner, world.loser, active_guest=world.active)
    assert calls == [(world.winner.pk, "defender")]
    assert result["template_key"] == world.active.template.key
    assert result.get("source") != "enemy_jail"
    assert not Guest.objects.filter(pk=world.active.pk).exists()
    assert JailPrisoner.objects.get(captor=world.winner).guest_template_id == world.active.template_id


def test_defending_victory_does_not_take_attacking_manors_prisoners(raid_world, monkeypatch):
    world = raid_world
    target = _prisoner(world, captor=world.winner)
    monkeypatch.setattr(capture_service, "_can_attempt_capture", lambda *_args, **_kwargs: False)
    assert _run(world.winner, world.loser, attacker_victory=False) is None
    target.refresh_from_db()
    assert target.captor_id == world.winner.pk


def test_expired_enemy_prisoners_are_released_instead_of_transferred(raid_world, monkeypatch):
    world = raid_world
    expired = _prisoner(world)
    JailPrisoner.objects.filter(pk=expired.pk).update(captured_at=timezone.now() - JAIL_MAX_HOLD_DURATION)
    fresh = _prisoner(world)
    monkeypatch.setattr(capture_service, "_can_attempt_capture", _normal_capture_must_not_run)
    result = _run(world.winner, world.loser)
    assert result["prisoner_id"] == fresh.pk
    expired.refresh_from_db()
    fresh.refresh_from_db()
    assert expired.status == JailPrisoner.Status.RELEASED
    assert expired.captor_id == world.loser.pk
    assert fresh.captor_id == world.winner.pk


@pytest.fixture
def unique_transfer_world(unique_capture_world, django_user_model):
    world = unique_capture_world
    prisoner = _capture(world)
    winner = ensure_manor(django_user_model.objects.create_user(username="unique_prisoner_new_captor"))
    winner.name = "新关押庄园"
    winner.save(update_fields=["name"])
    _set_jail_capacity(winner, 3)
    return SimpleNamespace(
        previous=world, prisoner=prisoner, winner=winner, loser=world.captor, template=world.template
    )


@pytest.mark.parametrize("action", ["recruit", "release"])
def test_unique_prisoner_transfer_keeps_identity_and_can_be_handled_by_new_captor(
    unique_transfer_world, monkeypatch, action
):
    world = unique_transfer_world
    prisoner = world.prisoner
    before = JailPrisoner.objects.values().get(pk=prisoner.pk)
    monkeypatch.setattr(capture_service, "_can_attempt_capture", _normal_capture_must_not_run)
    result = _run(world.winner, world.loser)
    assert result["prisoner_id"] == prisoner.pk
    after = JailPrisoner.objects.values().get(pk=prisoner.pk)
    assert after == {**before, "captor_id": world.winner.pk}
    state = WorldUniqueGuest.objects.get(template=world.template)
    assert state.status == WorldUniqueGuest.Status.CAPTURED
    assert state.owner_prisoner_id == prisoner.pk
    assert state.owner_guest_id is None
    assert state.owner_manor_id == world.winner.pk
    assert state.version == 3
    status = get_world_unique_guest_status(template_key=world.template.key)
    assert status["is_available"] is False
    assert world.winner.display_name in status["summary"]
    with pytest.raises(GuestAlreadyOwnedError, match="被俘"):
        claim_world_unique_guest_from_scroll(world.previous.owner, "chiyou_card", world.template)
    prisoner.refresh_from_db()
    if action == "recruit":
        _fund_recruitment(SimpleNamespace(captor=world.winner), prisoner)
        recruited = jail_service.recruit_prisoner(world.winner, prisoner.pk, rng=FixedRng(1))
        assert recruited.recruited is True
        state.refresh_from_db()
        assert state.status == WorldUniqueGuest.Status.SERVING
        assert state.owner_guest_id == recruited.guest.pk
        assert state.owner_manor_id == world.winner.pk
        assert Guest.objects.filter(template=world.template).count() == 1
    else:
        jail_service.release_prisoner(world.winner, prisoner.pk)
        state.refresh_from_db()
        assert state.status == WorldUniqueGuest.Status.WILD
        assert state.owner_manor_id is None
        assert get_world_unique_guest_status(template_key=world.template.key)["is_available"] is True
    assert state.owner_prisoner_id is None


def test_unique_prisoner_transfer_failure_rolls_back_captor_and_world_state(unique_transfer_world, monkeypatch):
    world = unique_transfer_world
    state = WorldUniqueGuest.objects.get(template=world.template)
    before = JailPrisoner.objects.values().get(pk=world.prisoner.pk)
    state_before = WorldUniqueGuest.objects.values().get(pk=state.pk)
    real_transfer = capture_service.transfer_world_unique_prisoner

    def fail_after_state_write(state, captor):
        real_transfer(state, captor)
        raise DatabaseError("forced prisoner transfer failure")

    monkeypatch.setattr(capture_service, "transfer_world_unique_prisoner", fail_after_state_write)
    with pytest.raises(DatabaseError, match="forced prisoner transfer failure"):
        _run(world.winner, world.loser)
    assert JailPrisoner.objects.values().get(pk=world.prisoner.pk) == before
    assert WorldUniqueGuest.objects.values().get(pk=state.pk) == state_before
    assert not JailPrisoner.objects.filter(captor=world.winner).exists()
    assert not Guest.objects.filter(template=world.template).exists()
