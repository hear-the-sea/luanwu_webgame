from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.exceptions import RelocationError
from gameplay.constants import REGION_DICT
from gameplay.services.manor.core import ensure_manor
from gameplay.services.raid.relocation import _generate_unique_coordinate, relocate_manor


def _create_manor(username: str):
    user = get_user_model().objects.create_user(username=username, password="pass123")
    return ensure_manor(user)


def test_region_dict_contains_only_four_continents_and_overseas():
    assert REGION_DICT == {
        "north": "北俱芦洲",
        "east": "东胜神洲",
        "west": "西牛贺洲",
        "south": "南赡部洲",
        "overseas": "化外之地",
    }


@pytest.mark.django_db
def test_relocate_manor_rejects_invalid_region(monkeypatch):
    manor = _create_manor("relocate_invalid_region")

    monkeypatch.setattr("gameplay.services.raid.relocation.get_active_raid_count", lambda *_a, **_k: 0)
    monkeypatch.setattr("gameplay.services.raid.relocation.get_incoming_raids", lambda *_a, **_k: [])

    with pytest.raises(RelocationError, match="无效的地区"):
        relocate_manor(manor, "not_a_region")


@pytest.mark.django_db
def test_relocate_manor_rejects_newbie_protection(monkeypatch):
    manor = _create_manor("relocate_newbie")
    manor.newbie_protection_until = timezone.now() + timedelta(hours=1)
    manor.save(update_fields=["newbie_protection_until"])

    monkeypatch.setattr("gameplay.services.raid.relocation.get_active_raid_count", lambda *_a, **_k: 0)
    monkeypatch.setattr("gameplay.services.raid.relocation.get_incoming_raids", lambda *_a, **_k: [])

    with pytest.raises(RelocationError, match="新手保护期内无法迁移"):
        relocate_manor(manor, next(iter(REGION_DICT.keys())))


@pytest.mark.django_db
def test_relocate_manor_rejects_active_raids(monkeypatch):
    manor = _create_manor("relocate_active_raids")

    monkeypatch.setattr("gameplay.services.raid.relocation.get_active_raid_count", lambda *_a, **_k: 1)
    monkeypatch.setattr("gameplay.services.raid.relocation.get_incoming_raids", lambda *_a, **_k: [])

    with pytest.raises(RelocationError, match="出征中的队伍"):
        relocate_manor(manor, next(iter(REGION_DICT.keys())))


@pytest.mark.django_db
def test_relocate_manor_rejects_incoming_raids(monkeypatch):
    manor = _create_manor("relocate_incoming_raids")

    monkeypatch.setattr("gameplay.services.raid.relocation.get_active_raid_count", lambda *_a, **_k: 0)
    monkeypatch.setattr("gameplay.services.raid.relocation.get_incoming_raids", lambda *_a, **_k: [object()])

    with pytest.raises(RelocationError, match="敌军来袭"):
        relocate_manor(manor, next(iter(REGION_DICT.keys())))


@pytest.mark.django_db
def test_relocate_manor_rejects_insufficient_gold(monkeypatch):
    manor = _create_manor("relocate_gold")

    monkeypatch.setattr("gameplay.services.raid.relocation.get_active_raid_count", lambda *_a, **_k: 0)
    monkeypatch.setattr("gameplay.services.raid.relocation.get_incoming_raids", lambda *_a, **_k: [])
    monkeypatch.setattr("trade.services.auction_service.get_available_gold_bars", lambda *_a, **_k: 0)

    with pytest.raises(RelocationError, match="可用金条不足"):
        relocate_manor(manor, next(iter(REGION_DICT.keys())))


@pytest.mark.django_db
def test_generate_unique_coordinate_raises_relocation_error_when_exhausted(monkeypatch):
    region = next(iter(REGION_DICT.keys()))
    occupied = _create_manor("relocate_coordinate_occupied")
    occupied.region = region
    occupied.coordinate_x = 111
    occupied.coordinate_y = 222
    occupied.save(update_fields=["region", "coordinate_x", "coordinate_y"])

    sequence = iter([111, 222] * 120)
    monkeypatch.setattr("gameplay.services.raid.relocation.random.randint", lambda *_a, **_k: next(sequence))

    with pytest.raises(RelocationError, match="无法生成唯一坐标"):
        _generate_unique_coordinate(region, exclude_manor_id=None)


@pytest.mark.django_db
def test_relocate_manor_updates_region_and_coordinates(monkeypatch):
    manor = _create_manor("relocate_success")
    target_region = next(key for key in REGION_DICT.keys() if key != manor.region)

    monkeypatch.setattr("gameplay.services.raid.relocation.get_active_raid_count", lambda *_a, **_k: 0)
    monkeypatch.setattr("gameplay.services.raid.relocation.get_incoming_raids", lambda *_a, **_k: [])
    monkeypatch.setattr("trade.services.auction_service.get_available_gold_bars", lambda *_a, **_k: 9999)
    monkeypatch.setattr("gameplay.services.raid.relocation._generate_unique_coordinate", lambda *_a, **_k: (321, 654))
    monkeypatch.setattr(
        "gameplay.services.inventory.core.consume_inventory_item_for_manor_locked",
        lambda *_a, **_k: None,
    )

    new_x, new_y = relocate_manor(manor, target_region)

    manor.refresh_from_db(fields=["region", "coordinate_x", "coordinate_y", "last_relocation_at"])
    assert (new_x, new_y) == (321, 654)
    assert manor.region == target_region
    assert manor.coordinate_x == 321
    assert manor.coordinate_y == 654
    assert manor.last_relocation_at is not None
