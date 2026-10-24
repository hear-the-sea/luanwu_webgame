from __future__ import annotations

import pytest
from django.db import DatabaseError
from django.urls import reverse

from core.exceptions import GameError
from tests.guest_view_error_boundaries.support import create_gear, create_guest, login_client, stub_equip_form


@pytest.mark.django_db
@pytest.mark.parametrize("error", [None, GameError("装备受限"), DatabaseError("db down")])
@pytest.mark.parametrize("safe_next", [True, False])
def test_equipment_actions_preserve_safe_return_target(django_user_model, monkeypatch, error, safe_next):
    client, manor = login_client(django_user_model, prefix="equipment_return")
    guest = create_guest(manor)
    gear = create_gear(manor)
    stub_equip_form(monkeypatch, guest, gear)

    def equip(*_args, **_kwargs):
        if error is not None:
            raise error
        return gear

    monkeypatch.setattr("guests.services.equipment.equip_guest", equip)
    origin = reverse("guests:roster") + "?rarity=green&page=2"
    response = client.post(
        reverse("guests:equip"),
        {
            "guest": guest.pk,
            "gear": gear.pk,
            "slot": gear.template.slot,
            "next": origin if safe_next else "https://example.org/unrelated/",
        },
    )

    assert response.status_code == 302
    assert response.url == (origin if safe_next else reverse("guests:detail", args=[guest.pk]))


@pytest.mark.django_db
def test_invalid_equipment_selection_stays_on_guest_detail(django_user_model):
    client, manor = login_client(django_user_model, prefix="equipment_invalid")
    guest = create_guest(manor)

    response = client.post(reverse("guests:equip"), {"guest": guest.pk, "gear": ""})

    assert response.status_code == 302
    assert response.url == reverse("guests:detail", args=[guest.pk])
