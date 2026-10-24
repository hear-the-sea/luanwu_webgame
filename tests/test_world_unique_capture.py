"""唯一门客的俘虏、收编与释放均保持同一个全服身份。"""

from __future__ import annotations

import random
from types import SimpleNamespace

import pytest
from django.utils import timezone

from core.exceptions import GuestAlreadyOwnedError, WorldUniqueGuestError
from gameplay.models import InventoryItem, ItemTemplate, JailPrisoner, RaidRun
from gameplay.services import jail as jail_service
from gameplay.services.jail_expiration import (
    JAIL_MAX_HOLD_DURATION,
    release_expired_prisoner_if_needed,
    release_expired_prisoners_for_captor,
)
from gameplay.services.manor.core import ensure_manor
from gameplay.services.raid.combat import capture as capture_service
from guests.models import Guest, GuestTemplate, WorldUniqueGuest
from guests.services.world_unique import (
    WORLD_UNIQUE_LUBU_SCROLL_ITEM_KEY,
    WORLD_UNIQUE_LUBU_TEMPLATE_KEY,
    claim_world_unique_guest_from_scroll,
    get_world_unique_guest_status,
)
from tests.jail_persuasion.test_recruitment_service import FixedRng

pytestmark = pytest.mark.django_db


@pytest.fixture(params=[("hero_yang_zheng_purple", "杨铮"), ("hero_di_qinglin", "狄青麟")])
def unique_capture_world(request, django_user_model, monkeypatch):
    key, name = request.param
    template, _ = GuestTemplate.objects.update_or_create(
        key=key,
        defaults={
            "name": name,
            "rarity": "purple",
            "archetype": "military",
            "is_world_unique": True,
            "recruitable": False,
        },
    )
    owner = ensure_manor(django_user_model.objects.create_user(username="unique_capture_original"))
    captor = ensure_manor(django_user_model.objects.create_user(username="unique_capture_captor"))
    owner.name = "原属庄园"
    owner.save(update_fields=["name"])
    captor.name = "俘获庄园"
    captor.save(update_fields=["name"])
    guest = claim_world_unique_guest_from_scroll(owner, "chiyou_card", template, rng=random.Random(7))
    monkeypatch.setattr(capture_service, "_can_attempt_capture", lambda *_args, **_kwargs: True)
    return SimpleNamespace(template=template, owner=owner, captor=captor, guest=guest)


def _capture(world, *, attacker_victory=True):
    run = RaidRun.objects.create(
        attacker=world.captor if attacker_victory else world.owner,
        defender=world.owner if attacker_victory else world.captor,
    )
    report = SimpleNamespace(
        winner="attacker" if attacker_victory else "defender",
        attacker_team=[] if attacker_victory else [{"guest_id": world.guest.pk}],
        defender_team=[{"guest_id": world.guest.pk}] if attacker_victory else [],
    )
    payload = capture_service._try_capture_guest(run, report, attacker_victory, rng=random.Random(1))
    assert payload is not None
    assert payload["template_key"] == world.template.key
    return JailPrisoner.objects.get(raid_run=run, guest_template=world.template)


def _fund_recruitment(world, prisoner):
    prisoner.loyalty = 30
    prisoner.affinity = 0
    prisoner.milestone_stage = 0
    prisoner.save(update_fields=["loyalty", "affinity", "milestone_stage"])
    gold, _ = ItemTemplate.objects.get_or_create(key="gold_bar", defaults={"name": "金条", "effect_type": "tool"})
    return InventoryItem.objects.create(
        manor=world.captor, template=gold, quantity=10, storage_location=InventoryItem.StorageLocation.WAREHOUSE
    )


@pytest.mark.parametrize("attacker_victory", [True, False])
def test_unique_guest_capture_reserves_identity_and_blocks_new_claim(unique_capture_world, attacker_victory):
    world = unique_capture_world
    prisoner = _capture(world, attacker_victory=attacker_victory)
    state = WorldUniqueGuest.objects.get(template=world.template)
    assert state.status == WorldUniqueGuest.Status.CAPTURED
    assert state.owner_guest_id is None
    assert state.owner_prisoner_id == prisoner.pk
    assert state.owner_manor_id == world.captor.pk
    assert state.version == 2
    assert prisoner.original_manor_id == world.owner.pk
    assert prisoner.captor_id == world.captor.pk
    assert not Guest.objects.filter(template=world.template).exists()
    status = get_world_unique_guest_status(template_key=world.template.key)
    assert status["is_available"] is False
    assert status["status_display"] == "被俘"
    assert world.captor.display_name in status["summary"]
    assert world.captor.location_display in status["summary"]
    with pytest.raises(GuestAlreadyOwnedError, match="被俘"):
        claim_world_unique_guest_from_scroll(world.owner, "chiyou_card", world.template, rng=random.Random(2))
    state.refresh_from_db()
    assert state.owner_prisoner_id == prisoner.pk
    assert not Guest.objects.filter(template=world.template).exists()


@pytest.mark.parametrize("roll,recruited", [(1, True), (100, False)])
def test_recruitment_updates_unique_owner_only_on_success(unique_capture_world, roll, recruited):
    world = unique_capture_world
    prisoner = _capture(world)
    gold = _fund_recruitment(world, prisoner)
    result = jail_service.recruit_prisoner(world.captor, prisoner.pk, rng=FixedRng(roll))
    state = WorldUniqueGuest.objects.get(template=world.template)
    prisoner.refresh_from_db()
    gold.refresh_from_db()
    assert result.recruited is recruited
    assert gold.quantity == 9
    assert state.owner_manor_id == world.captor.pk
    if recruited:
        assert state.status == WorldUniqueGuest.Status.SERVING
        assert state.owner_prisoner_id is None
        assert state.owner_guest_id == result.guest.pk
        assert state.version == 3
        assert prisoner.status == JailPrisoner.Status.RECRUITED
        assert Guest.objects.filter(template=world.template).count() == 1
        assert result.guest.manor_id == world.captor.pk
        assert result.guest.level == 1
    else:
        assert state.status == WorldUniqueGuest.Status.CAPTURED
        assert state.owner_prisoner_id == prisoner.pk
        assert state.owner_guest_id is None
        assert state.version == 2
        assert prisoner.status == JailPrisoner.Status.HELD
        assert not Guest.objects.filter(template=world.template).exists()
    with pytest.raises(GuestAlreadyOwnedError):
        claim_world_unique_guest_from_scroll(world.owner, "chiyou_card", world.template, rng=random.Random(2))


@pytest.mark.parametrize("release_path", ["manual", "single_expiration", "captor_expiration", "cleanup"])
def test_all_release_paths_restore_unique_guest_to_wild(unique_capture_world, release_path):
    world = unique_capture_world
    prisoner = _capture(world)
    as_of = timezone.now()
    if release_path != "manual":
        JailPrisoner.objects.filter(pk=prisoner.pk).update(captured_at=as_of - JAIL_MAX_HOLD_DURATION)
        prisoner.refresh_from_db()
    if release_path == "manual":
        jail_service.release_prisoner(world.captor, prisoner.pk)
    elif release_path == "single_expiration":
        assert release_expired_prisoner_if_needed(prisoner, as_of=as_of) is True
    elif release_path == "captor_expiration":
        assert release_expired_prisoners_for_captor(world.captor, as_of=as_of) == 1
    else:
        result = jail_service.cleanup_expired_jail_prisoners(as_of=as_of, batch_size=1)
        assert result.released >= 1
    prisoner.refresh_from_db()
    state = WorldUniqueGuest.objects.get(template=world.template)
    assert prisoner.status == JailPrisoner.Status.RELEASED
    assert state.status == WorldUniqueGuest.Status.WILD
    assert state.owner_guest_id is None
    assert state.owner_prisoner_id is None
    assert state.owner_manor_id is None
    assert state.version == 3
    assert get_world_unique_guest_status(template_key=world.template.key)["is_available"] is True
    new_guest = claim_world_unique_guest_from_scroll(world.owner, "chiyou_card", world.template, rng=random.Random(3))
    state.refresh_from_db()
    assert state.owner_guest_id == new_guest.pk
    assert Guest.objects.filter(template=world.template).count() == 1


def test_unlinked_unique_prisoner_cannot_create_a_second_guest(unique_capture_world):
    world = unique_capture_world
    real_prisoner = _capture(world)
    fake = JailPrisoner.objects.create(
        captor=world.captor,
        original_manor=world.owner,
        guest_template=world.template,
        loyalty=30,
        captured_loyalty=30,
    )
    gold = _fund_recruitment(world, fake)
    with pytest.raises(WorldUniqueGuestError, match="归属状态异常"):
        jail_service.recruit_prisoner(world.captor, fake.pk, rng=FixedRng(1))
    state = WorldUniqueGuest.objects.get(template=world.template)
    gold.refresh_from_db()
    fake.refresh_from_db()
    assert gold.quantity == 10
    assert fake.status == JailPrisoner.Status.HELD
    assert state.owner_prisoner_id == real_prisoner.pk
    assert not Guest.objects.filter(template=world.template).exists()


def test_capture_failure_rolls_back_prisoner_and_unique_ownership(unique_capture_world, monkeypatch):
    world = unique_capture_world
    guest_id = world.guest.pk

    def fail_delete(*args, **kwargs):
        raise RuntimeError("捕获结算失败")

    monkeypatch.setattr(Guest, "delete", fail_delete)
    with pytest.raises(RuntimeError, match="捕获结算失败"):
        _capture(world)
    state = WorldUniqueGuest.objects.get(template=world.template)
    assert state.status == WorldUniqueGuest.Status.SERVING
    assert state.owner_guest_id == guest_id
    assert state.owner_manor_id == world.owner.pk
    assert state.owner_prisoner_id is None
    assert state.version == 1
    assert Guest.objects.filter(pk=guest_id).exists()
    assert not JailPrisoner.objects.filter(guest_template=world.template).exists()


def test_capture_applies_to_other_unique_templates_too(unique_capture_world):
    world = unique_capture_world
    world.template.key = "future_world_unique_guest"
    world.template.save(update_fields=["key"])
    prisoner = _capture(world)
    state = WorldUniqueGuest.objects.get(template=world.template)
    assert state.owner_prisoner_id == prisoner.pk
    assert state.status == WorldUniqueGuest.Status.CAPTURED


def test_lubu_stays_excluded_from_both_capture_selection_guards(django_user_model, monkeypatch):
    template, _ = GuestTemplate.objects.update_or_create(
        key=WORLD_UNIQUE_LUBU_TEMPLATE_KEY,
        defaults={"name": "吕布", "rarity": "orange", "is_world_unique": True, "recruitable": False},
    )
    owner = ensure_manor(django_user_model.objects.create_user(username="unique_capture_lubu_owner"))
    captor = ensure_manor(django_user_model.objects.create_user(username="unique_capture_lubu_captor"))
    guest = claim_world_unique_guest_from_scroll(
        owner, WORLD_UNIQUE_LUBU_SCROLL_ITEM_KEY, template, rng=random.Random(7)
    )
    monkeypatch.setattr(capture_service, "_can_attempt_capture", lambda *_args, **_kwargs: True)
    assert capture_service._filter_capture_candidates([guest.pk]) == []
    assert capture_service._select_capture_target([guest.pk], owner, rng=random.Random(1)) is None
    world = SimpleNamespace(template=template, owner=owner, captor=captor, guest=guest)
    run = RaidRun.objects.create(attacker=captor, defender=owner)
    report = SimpleNamespace(defender_team=[{"guest_id": guest.pk}], attacker_team=[])
    assert capture_service._try_capture_guest(run, report, True, rng=random.Random(1)) is None
    assert Guest.objects.filter(pk=guest.pk).exists()
    assert not JailPrisoner.objects.filter(guest_template=template).exists()
    state = WorldUniqueGuest.objects.get(template=template)
    assert state.status == WorldUniqueGuest.Status.SERVING
    assert state.owner_guest_id == world.guest.pk
