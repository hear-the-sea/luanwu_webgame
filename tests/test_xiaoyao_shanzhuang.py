from __future__ import annotations

import pytest
from django.urls import reverse

from core.exceptions import (
    BuildingMaxLevelError,
    GameError,
    GuestCapacityFullError,
    RaidStartError,
    WorldUniqueGuestError,
)
from gameplay.constants import BUILDING_MAX_LEVELS, BuildingKeys
from gameplay.models import Building
from gameplay.services.manor.core import quote_building_upgrade
from gameplay.services.pvp_runtime.lineups import select_player_defender_lineup
from gameplay.services.raid.combat.raid_inputs import _load_and_validate_attacker_guests
from guests.models import Guest, GuestArchetype, GuestRarity, GuestStatus, GuestTemplate
from guests.services.recruitment_finalize_helpers import remaining_guest_capacity
from guests.services.recruitment_queries import available_guests
from guests.services.salary import get_unpaid_guests
from guests.services.vacation import move_guest_to_xiaoyao, return_guest_from_xiaoyao


def _guest(manor, key: str, *, unique: bool = False) -> Guest:
    template = GuestTemplate.objects.create(
        key=key,
        name=key,
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GRAY,
        is_world_unique=unique,
    )
    return Guest.objects.create(manor=manor, template=template)


def _enable_xiaoyao(manor, level: int = 1) -> Building:
    building = manor.buildings.select_related("building_type").get(building_type__key=BuildingKeys.XIAOYAO_SHANZHUANG)
    building.level = level
    building.save(update_fields=["level"])
    manor.invalidate_building_cache()
    return building


@pytest.mark.django_db
def test_xiaoyao_starts_at_zero_and_matches_juxianzhuang_last_ten_upgrades(manor_factory):
    manor, _user = manor_factory(username="xiaoyao_upgrade_curve")
    xiaoyao = manor.buildings.select_related("building_type").get(building_type__key=BuildingKeys.XIAOYAO_SHANZHUANG)
    juxian = manor.buildings.select_related("building_type").get(building_type__key=BuildingKeys.JUXIAN_ZHUANG)

    assert xiaoyao.level == 0
    assert manor.xiaoyao_capacity == 0
    assert BUILDING_MAX_LEVELS[BuildingKeys.XIAOYAO_SHANZHUANG] == 10
    for level in range(10):
        xiaoyao.level = level
        juxian.level = level + 5
        assert xiaoyao.next_level_cost() == juxian.next_level_cost()
        assert xiaoyao.next_level_duration() == juxian.next_level_duration()
    xiaoyao.level = 10
    xiaoyao.save(update_fields=["level"])
    manor.invalidate_building_cache()
    assert manor.xiaoyao_capacity == 10
    with pytest.raises(BuildingMaxLevelError):
        quote_building_upgrade(manor, xiaoyao)


@pytest.mark.django_db
def test_xiaoyao_frees_active_slot_but_keeps_salary_and_blocks_battle(manor_factory):
    manor, _user = manor_factory(username="xiaoyao_guest_state")
    _enable_xiaoyao(manor)
    guests = [_guest(manor, f"xiaoyao_state_{index}") for index in range(manor.guest_capacity)]
    vacation_guest = guests[0]

    assert remaining_guest_capacity(manor) == 0
    move_guest_to_xiaoyao(manor, vacation_guest.pk)
    vacation_guest.refresh_from_db()

    assert vacation_guest.status == GuestStatus.VACATION
    assert manor.active_guest_count == manor.guest_capacity - 1
    assert remaining_guest_capacity(manor) == 1
    assert vacation_guest.pk not in [guest.pk for guest in available_guests(manor)]
    assert vacation_guest.pk not in [guest.pk for guest in select_player_defender_lineup(manor)]
    with pytest.raises(RaidStartError, match="当前不可出征"):
        _load_and_validate_attacker_guests(manor, [vacation_guest.pk])
    assert vacation_guest.pk in [guest.pk for guest in get_unpaid_guests(manor)]

    with pytest.raises(GameError, match="位置已满"):
        move_guest_to_xiaoyao(manor, guests[1].pk)

    replacement = _guest(manor, "xiaoyao_replacement")
    with pytest.raises(GuestCapacityFullError):
        return_guest_from_xiaoyao(manor, vacation_guest.pk)
    replacement.delete()

    return_guest_from_xiaoyao(manor, vacation_guest.pk)
    vacation_guest.refresh_from_db()
    assert vacation_guest.status == GuestStatus.IDLE
    assert manor.active_guest_count == manor.guest_capacity


@pytest.mark.django_db
def test_dismiss_vacation_guest_without_recall_when_active_roster_is_full(manor_factory, client):
    manor, user = manor_factory(username="xiaoyao_dismiss_full_roster")
    _enable_xiaoyao(manor)
    guests = [_guest(manor, f"xiaoyao_dismiss_guest_{index}") for index in range(manor.guest_capacity)]
    vacation_guest = guests[0]
    move_guest_to_xiaoyao(manor, vacation_guest.pk)
    vacation_guest.refresh_from_db()
    _guest(manor, "xiaoyao_dismiss_replacement")
    assert manor.active_guest_count == manor.guest_capacity
    assert manor.guests.filter(status=GuestStatus.VACATION).count() == manor.xiaoyao_capacity

    client.force_login(user)
    response = client.get(reverse("guests:detail", args=[vacation_guest.pk]))
    assert response.status_code == 200
    assert reverse("guests:dismiss", args=[vacation_guest.pk]) in response.content.decode()
    response = client.post(reverse("guests:dismiss", args=[vacation_guest.pk]))
    assert response.status_code == 302
    assert response.url == reverse("guests:roster")
    assert not Guest.objects.filter(pk=vacation_guest.pk).exists()
    assert manor.active_guest_count == manor.guest_capacity
    assert not manor.guests.filter(status=GuestStatus.VACATION).exists()

    move_guest_to_xiaoyao(manor, guests[1].pk)
    guests[1].refresh_from_db()
    assert guests[1].status == GuestStatus.VACATION


@pytest.mark.django_db
def test_world_unique_guest_cannot_enter_xiaoyao(manor_factory):
    manor, _user = manor_factory(username="xiaoyao_unique_guest")
    _enable_xiaoyao(manor)
    guest = _guest(manor, "xiaoyao_unique_template", unique=True)

    with pytest.raises(WorldUniqueGuestError, match="不能归闲"):
        move_guest_to_xiaoyao(manor, guest.pk)
    guest.refresh_from_db()
    assert guest.status == GuestStatus.IDLE


@pytest.mark.django_db
def test_xiaoyao_page_and_roster_link(manor_factory, client):
    manor, user = manor_factory(username="xiaoyao_page")
    _enable_xiaoyao(manor)
    guest = _guest(manor, "xiaoyao_page_guest")
    client.force_login(user)

    roster = client.get(reverse("guests:roster"))
    detail = client.get(reverse("guests:detail", args=[guest.pk]))
    buildings = client.get(reverse("gameplay:buildings_category", args=["special"]))
    assert roster.status_code == 200
    assert "前往逍遥山庄" in roster.content.decode()
    assert (
        f'<a class="tw-btn-primary" href="{reverse("gameplay:xiaoyao_shanzhuang")}">前往逍遥山庄</a>'
        in roster.content.decode()
    )
    assert detail.status_code == 200
    assert "归闲" in detail.content.decode()
    assert buildings.status_code == 200
    assert "逍遥山庄" in buildings.content.decode()
    assert "查看门客" in buildings.content.decode()

    response = client.post(reverse("gameplay:move_guest_to_xiaoyao", args=[guest.pk]))
    page = client.get(reverse("gameplay:xiaoyao_shanzhuang"))
    assert response.status_code == 302
    assert page.status_code == 200
    assert guest.display_name in page.content.decode()
    assert "放回庄园" in page.content.decode()
    assert "防守战败时仍有较低概率被俘" in page.content.decode()

    response = client.post(reverse("gameplay:return_guest_from_xiaoyao", args=[guest.pk]))
    guest.refresh_from_db()
    assert response.status_code == 302
    assert guest.status == GuestStatus.IDLE
