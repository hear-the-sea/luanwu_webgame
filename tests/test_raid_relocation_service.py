from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.utils import timezone

from core.exceptions import ItemInsufficientError, RelocationError
from gameplay.constants import REGION_DICT
from gameplay.models import Manor
from gameplay.services.manor.core import ensure_manor
from gameplay.services.raid.relocation import _generate_unique_coordinate, relocate_manor

SQLITE_OCCUPIED_MANOR_LOCATION_CONFLICT = (
    "UNIQUE constraint failed: gameplay_manor.occupied_region, "
    "gameplay_manor.coordinate_x, gameplay_manor.coordinate_y"
)
MYSQL_OCCUPIED_MANOR_LOCATION_CONFLICT = (
    "Duplicate entry 'north-321-654' for key " "'gameplay_manor.unique_occupied_manor_location'"
)


def _mysql_occupied_manor_location_conflict() -> IntegrityError:
    error = IntegrityError("Django wrapped database integrity error")
    error.__cause__ = Exception(1062, MYSQL_OCCUPIED_MANOR_LOCATION_CONFLICT)
    return error


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
    monkeypatch.setattr(
        "trade.services.auction.gold_bars.consume_available_gold_bars_locked",
        lambda *_a, **_k: (_ for _ in ()).throw(ItemInsufficientError("金条", 1, 0)),
    )

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
    monkeypatch.setattr("gameplay.services.raid.relocation._generate_unique_coordinate", lambda *_a, **_k: (321, 654))
    monkeypatch.setattr(
        "trade.services.auction.gold_bars.consume_available_gold_bars_locked",
        lambda *_a, **_k: None,
    )

    new_x, new_y = relocate_manor(manor, target_region)

    manor.refresh_from_db(fields=["region", "coordinate_x", "coordinate_y", "last_relocation_at"])
    assert (new_x, new_y) == (321, 654)
    assert manor.region == target_region
    assert manor.coordinate_x == 321
    assert manor.coordinate_y == 654
    assert manor.last_relocation_at is not None


@pytest.mark.django_db
def test_relocate_manor_retries_after_coordinate_unique_conflict(monkeypatch):
    manor = _create_manor("relocate_coordinate_retry")
    target_region = next(key for key in REGION_DICT.keys() if key != manor.region)
    coordinates = iter([(321, 654), (322, 655)])
    save_attempts = 0
    original_save = Manor.save

    monkeypatch.setattr("gameplay.services.raid.relocation.get_active_raid_count", lambda *_a, **_k: 0)
    monkeypatch.setattr("gameplay.services.raid.relocation.get_incoming_raids", lambda *_a, **_k: [])
    monkeypatch.setattr(
        "gameplay.services.raid.relocation._generate_unique_coordinate",
        lambda *_a, **_k: next(coordinates),
    )
    monkeypatch.setattr(
        "trade.services.auction.gold_bars.consume_available_gold_bars_locked",
        lambda *_a, **_k: None,
    )

    def save_with_one_coordinate_conflict(self, *args, **kwargs):
        nonlocal save_attempts
        update_fields = kwargs.get("update_fields") or []
        if self.pk == manor.pk and "last_relocation_at" in update_fields:
            save_attempts += 1
            if save_attempts == 1:
                raise IntegrityError(SQLITE_OCCUPIED_MANOR_LOCATION_CONFLICT)
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(Manor, "save", save_with_one_coordinate_conflict)

    new_x, new_y = relocate_manor(manor, target_region)

    manor.refresh_from_db(fields=["region", "coordinate_x", "coordinate_y", "last_relocation_at"])
    assert save_attempts == 2
    assert (new_x, new_y) == (322, 655)
    assert (manor.coordinate_x, manor.coordinate_y) == (322, 655)
    assert manor.region == target_region


@pytest.mark.parametrize(
    "error_message",
    [
        pytest.param(
            "NOT NULL constraint failed: gameplay_manor.region",
            id="not-null",
        ),
        pytest.param("FOREIGN KEY constraint failed", id="foreign-key"),
        pytest.param(
            "Duplicate entry 'taken-name' for key 'gameplay_manor.name'",
            id="other-unique",
        ),
        pytest.param(
            "Duplicate entry 'north-1-2' for key " "'gameplay_manor.unique_occupied_manor_location_shadow'",
            id="mysql-similar-unique",
        ),
        pytest.param(
            f"{SQLITE_OCCUPIED_MANOR_LOCATION_CONFLICT}, gameplay_manor.user_id",
            id="sqlite-four-column-unique",
        ),
    ],
)
@pytest.mark.django_db
def test_relocate_manor_propagates_non_coordinate_integrity_error_once(
    error_message,
    monkeypatch,
):
    manor = _create_manor(f"relocate_non_target_{error_message[:8]}")
    target_region = next(key for key in REGION_DICT if key != manor.region)
    integrity_error = IntegrityError(error_message)
    save_attempts = 0
    coordinate_calls = 0
    original_save = Manor.save

    monkeypatch.setattr("gameplay.services.raid.relocation.get_active_raid_count", lambda *_a, **_k: 0)
    monkeypatch.setattr("gameplay.services.raid.relocation.get_incoming_raids", lambda *_a, **_k: [])
    monkeypatch.setattr(
        "trade.services.auction.gold_bars.consume_available_gold_bars_locked",
        lambda *_a, **_k: None,
    )

    def _next_coordinate(*_args, **_kwargs):
        nonlocal coordinate_calls
        coordinate_calls += 1
        return 401, 501

    def _raise_non_coordinate_error(self, *args, **kwargs):
        nonlocal save_attempts
        update_fields = kwargs.get("update_fields") or []
        if self.pk == manor.pk and "last_relocation_at" in update_fields:
            save_attempts += 1
            raise integrity_error
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr("gameplay.services.raid.relocation._generate_unique_coordinate", _next_coordinate)
    monkeypatch.setattr(Manor, "save", _raise_non_coordinate_error)

    with pytest.raises(IntegrityError) as exc_info:
        relocate_manor(manor, target_region)

    assert exc_info.value is integrity_error
    assert save_attempts == 1
    assert coordinate_calls == 1


@pytest.mark.django_db
def test_relocate_manor_preserves_last_coordinate_conflict_when_retries_exhausted(monkeypatch):
    manor = _create_manor("relocate_coordinate_retry_exhausted")
    target_region = next(key for key in REGION_DICT if key != manor.region)
    coordinate_values = iter((value, value + 100) for value in range(401, 406))
    coordinate_conflicts: list[IntegrityError] = []
    original_save = Manor.save

    monkeypatch.setattr("gameplay.services.raid.relocation.get_active_raid_count", lambda *_a, **_k: 0)
    monkeypatch.setattr("gameplay.services.raid.relocation.get_incoming_raids", lambda *_a, **_k: [])
    monkeypatch.setattr(
        "trade.services.auction.gold_bars.consume_available_gold_bars_locked",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "gameplay.services.raid.relocation._generate_unique_coordinate",
        lambda *_a, **_k: next(coordinate_values),
    )

    def _save_with_coordinate_conflict(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields") or []
        if self.pk == manor.pk and "last_relocation_at" in update_fields:
            conflict = _mysql_occupied_manor_location_conflict()
            coordinate_conflicts.append(conflict)
            raise conflict
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(Manor, "save", _save_with_coordinate_conflict)

    with pytest.raises(RelocationError, match="迁移坐标发生冲突") as exc_info:
        relocate_manor(manor, target_region)

    assert len(coordinate_conflicts) == 5
    assert exc_info.value.__cause__ is coordinate_conflicts[-1]
