from __future__ import annotations

import pytest
from django.db import DatabaseError
from django.urls import reverse
from django_redis.exceptions import ConnectionInterrupted

from core.exceptions import GameError
from gameplay.models import ItemTemplate
from tests.guest_view_error_boundaries.support import create_gear, create_guest, login_client, messages, stub_equip_form


@pytest.mark.django_db
def test_equip_view_game_error_shows_business_message(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="equip_game")
    guest = create_guest(manor, prefix="equip_game")
    gear = create_gear(manor)
    stub_equip_form(monkeypatch, guest, gear)

    monkeypatch.setattr(
        "guests.services.equipment.equip_guest", lambda *_a, **_k: (_ for _ in ()).throw(GameError("装备受限"))
    )

    response = client.post(
        reverse("guests:equip"),
        {"guest": str(guest.pk), "gear": str(gear.pk), "slot": gear.template.slot},
    )

    assert response.status_code == 302
    assert response.url == reverse("gameplay:recruitment_hall")
    assert "装备受限" in messages(response)


@pytest.mark.django_db
def test_equip_view_database_error_degrades_with_message(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="equip_db")
    guest = create_guest(manor, prefix="equip_db")
    gear = create_gear(manor)
    stub_equip_form(monkeypatch, guest, gear)

    monkeypatch.setattr(
        "guests.services.equipment.equip_guest",
        lambda *_a, **_k: (_ for _ in ()).throw(DatabaseError("db down")),
    )

    response = client.post(
        reverse("guests:equip"),
        {"guest": str(guest.pk), "gear": str(gear.pk), "slot": gear.template.slot},
    )

    assert response.status_code == 302
    assert response.url == reverse("gameplay:recruitment_hall")
    assert "操作失败，请稍后重试" in messages(response)


@pytest.mark.django_db
def test_equip_view_runtime_error_bubbles_up(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="equip_runtime")
    guest = create_guest(manor, prefix="equip_runtime")
    gear = create_gear(manor)
    stub_equip_form(monkeypatch, guest, gear)

    monkeypatch.setattr(
        "guests.services.equipment.equip_guest", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    with pytest.raises(RuntimeError, match="boom"):
        client.post(
            reverse("guests:equip"),
            {"guest": str(guest.pk), "gear": str(gear.pk), "slot": gear.template.slot},
        )


@pytest.mark.django_db
def test_equip_view_legacy_value_error_bubbles_up(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="equip_value_error")
    guest = create_guest(manor, prefix="equip_value_error")
    gear = create_gear(manor)
    stub_equip_form(monkeypatch, guest, gear)

    monkeypatch.setattr(
        "guests.services.equipment.equip_guest",
        lambda *_a, **_k: (_ for _ in ()).throw(ValueError("legacy equip")),
    )

    with pytest.raises(ValueError, match="legacy equip"):
        client.post(
            reverse("guests:equip"),
            {"guest": str(guest.pk), "gear": str(gear.pk), "slot": gear.template.slot},
        )


@pytest.mark.django_db(transaction=True)
def test_equip_view_cache_invalidation_failure_does_not_hide_success(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="equip_cache")
    guest = create_guest(manor, prefix="equip_cache")
    gear = create_gear(manor)
    stub_equip_form(monkeypatch, guest, gear)

    monkeypatch.setattr(
        "guests.services.equipment._clear_gear_options_cache",
        lambda *_a, **_k: (_ for _ in ()).throw(ConnectionInterrupted("cache down")),
    )

    response = client.post(
        reverse("guests:equip"),
        {"guest": str(guest.pk), "gear": str(gear.pk), "slot": gear.template.slot},
    )

    assert response.status_code == 302
    assert response.url == reverse("guests:detail", args=[guest.pk])
    assert any("已装备" in message for message in messages(response))


@pytest.mark.django_db
def test_unequip_view_database_error_degrades_with_message(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="unequip_db")
    guest = create_guest(manor, prefix="unequip_db")
    gear = create_gear(manor, guest=guest)

    monkeypatch.setattr(
        "guests.services.equipment.unequip_guest_item",
        lambda *_a, **_k: (_ for _ in ()).throw(DatabaseError("db down")),
    )

    response = client.post(reverse("guests:unequip"), {"guest": str(guest.pk), "gear": [str(gear.pk)]})

    assert response.status_code == 302
    assert response.url == reverse("guests:roster")
    assert "操作失败，请稍后重试" in messages(response)


@pytest.mark.django_db
def test_unequip_view_runtime_error_bubbles_up(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="unequip_runtime")
    guest = create_guest(manor, prefix="unequip_runtime")
    gear = create_gear(manor, guest=guest)

    monkeypatch.setattr(
        "guests.services.equipment.unequip_guest_item",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        client.post(reverse("guests:unequip"), {"guest": str(guest.pk), "gear": [str(gear.pk)]})


@pytest.mark.django_db(transaction=True)
def test_unequip_view_cache_invalidation_failure_does_not_hide_success(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="unequip_cache")
    guest = create_guest(manor, prefix="unequip_cache")
    gear = create_gear(manor, guest=guest)
    ItemTemplate.objects.create(
        key=gear.template.key,
        name="测试装备道具",
        effect_type=ItemTemplate.EffectType.TOOL,
        effect_payload={},
        is_usable=True,
    )

    monkeypatch.setattr(
        "guests.services.equipment._clear_gear_options_cache",
        lambda *_a, **_k: (_ for _ in ()).throw(ConnectionInterrupted("cache down")),
    )

    response = client.post(reverse("guests:unequip"), {"guest": str(guest.pk), "gear": [str(gear.pk)]})

    assert response.status_code == 302
    assert response.url == reverse("guests:roster")
    assert any("卸下 1 件装备" in message for message in messages(response))


@pytest.mark.django_db(transaction=True)
def test_equip_view_runtime_marker_cache_invalidation_error_bubbles_up(django_user_model, monkeypatch):
    client, manor = login_client(django_user_model, prefix="equip_cache_runtime")
    guest = create_guest(manor, prefix="equip_cache_runtime")
    gear = create_gear(manor)
    stub_equip_form(monkeypatch, guest, gear)

    monkeypatch.setattr(
        "guests.services.equipment._clear_gear_options_cache",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("cache down")),
    )

    with pytest.raises(RuntimeError, match="cache down"):
        client.post(
            reverse("guests:equip"),
            {"guest": str(guest.pk), "gear": str(gear.pk), "slot": gear.template.slot},
        )
