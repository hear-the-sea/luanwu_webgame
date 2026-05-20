from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from core.config import BUILDING_KEYS
from gameplay.services.city_defense import (
    CITY_DEFENSE_HP_RECOVERY_SECONDS,
    apply_city_defense_battle_damage,
    city_defense_max_hp,
    refresh_city_defense_hp,
)


def _get_city_defense_building(manor, key: str):
    return manor.buildings.select_related("building_type").get(building_type__key=key)


@pytest.mark.django_db
def test_city_defense_dashboard_displays_hp_and_repair_action(manor_with_user):
    manor, client = manor_with_user
    wall = _get_city_defense_building(manor, BUILDING_KEYS.WALL)
    max_hp = city_defense_max_hp(BUILDING_KEYS.WALL, wall.level)
    wall.current_hp = max_hp - 100
    wall.hp_updated_at = timezone.now()
    wall.save(update_fields=["current_hp", "hp_updated_at"])

    response = client.get(reverse("gameplay:buildings_category", kwargs={"category": "city_defense"}))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "城防血量" in body
    assert f"{max_hp - 100} / {max_hp}" in body
    assert reverse("gameplay:repair_city_defense", kwargs={"pk": wall.pk}) in body
    assert "当前产能" not in body


@pytest.mark.django_db
def test_repair_city_defense_spends_silver_and_restores_hp(manor_with_user):
    manor, client = manor_with_user
    wall = _get_city_defense_building(manor, BUILDING_KEYS.WALL)
    max_hp = city_defense_max_hp(BUILDING_KEYS.WALL, wall.level)
    wall.current_hp = max_hp - 350
    wall.hp_updated_at = timezone.now()
    wall.save(update_fields=["current_hp", "hp_updated_at"])
    manor.silver = 1000
    manor.resource_updated_at = timezone.now() + timezone.timedelta(seconds=10)
    manor.save(update_fields=["silver", "resource_updated_at"])

    response = client.post(reverse("gameplay:repair_city_defense", kwargs={"pk": wall.pk}))

    assert response.status_code == 302
    wall.refresh_from_db()
    manor.refresh_from_db()
    assert wall.current_hp == max_hp
    assert manor.silver == 650
    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("修复完成" in message and "350" in message for message in messages)


@pytest.mark.django_db
def test_repair_city_defense_requires_enough_silver(manor_with_user):
    manor, client = manor_with_user
    wall = _get_city_defense_building(manor, BUILDING_KEYS.WALL)
    max_hp = city_defense_max_hp(BUILDING_KEYS.WALL, wall.level)
    wall.current_hp = max_hp - 350
    wall.hp_updated_at = timezone.now()
    wall.save(update_fields=["current_hp", "hp_updated_at"])
    manor.silver = 100
    manor.resource_updated_at = timezone.now() + timezone.timedelta(seconds=10)
    manor.save(update_fields=["silver", "resource_updated_at"])

    response = client.post(reverse("gameplay:repair_city_defense", kwargs={"pk": wall.pk}))

    assert response.status_code == 302
    wall.refresh_from_db()
    manor.refresh_from_db()
    assert wall.current_hp == max_hp - 350
    assert manor.silver == 100
    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert any("银两不足" in message for message in messages)


@pytest.mark.django_db
def test_city_defense_hp_recovers_over_time(manor_with_user):
    manor, _client = manor_with_user
    wall = _get_city_defense_building(manor, BUILDING_KEYS.WALL)
    max_hp = city_defense_max_hp(BUILDING_KEYS.WALL, wall.level)
    now = timezone.now()
    wall.current_hp = max_hp // 2
    wall.hp_updated_at = now - timezone.timedelta(seconds=CITY_DEFENSE_HP_RECOVERY_SECONDS // 2)
    wall.save(update_fields=["current_hp", "hp_updated_at"])

    refresh_city_defense_hp(wall, now=now)

    assert wall.current_hp == max_hp


@pytest.mark.django_db
def test_city_defense_hp_recovers_five_percent_per_hour(manor_with_user):
    manor, _client = manor_with_user
    wall = _get_city_defense_building(manor, BUILDING_KEYS.WALL)
    max_hp = city_defense_max_hp(BUILDING_KEYS.WALL, wall.level)
    now = timezone.now()
    wall.current_hp = max_hp // 2
    wall.hp_updated_at = now - timezone.timedelta(hours=1)
    wall.save(update_fields=["current_hp", "hp_updated_at"])

    refresh_city_defense_hp(wall, now=now)

    assert wall.current_hp == max_hp // 2 + int(max_hp * 0.05)


@pytest.mark.django_db
def test_city_defense_battle_damage_persists_with_minimum_one_hp(manor_with_user):
    manor, _client = manor_with_user
    wall = _get_city_defense_building(manor, BUILDING_KEYS.WALL)
    tower = _get_city_defense_building(manor, BUILDING_KEYS.ARROW_TOWER)

    apply_city_defense_battle_damage(
        manor,
        [
            {"key": BUILDING_KEYS.WALL, "hp": 0},
            {"key": BUILDING_KEYS.ARROW_TOWER, "hp": 123},
        ],
        now=timezone.now(),
    )

    wall.refresh_from_db()
    tower.refresh_from_db()
    assert wall.current_hp == 1
    assert tower.current_hp == 123


@pytest.mark.django_db
def test_city_defense_battle_damage_never_raises_existing_lower_hp(manor_with_user):
    manor, _client = manor_with_user
    wall = _get_city_defense_building(manor, BUILDING_KEYS.WALL)
    wall.current_hp = 10
    wall.hp_updated_at = timezone.now()
    wall.save(update_fields=["current_hp", "hp_updated_at"])

    apply_city_defense_battle_damage(
        manor,
        [{"key": BUILDING_KEYS.WALL, "hp": 500}],
        now=timezone.now(),
    )

    wall.refresh_from_db()
    assert wall.current_hp == 10
