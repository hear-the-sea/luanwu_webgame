from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.urls import reverse
from django.utils import timezone

import gameplay.selectors.map as map_selector
from gameplay.models import BotProfile
from gameplay.services.manor.core import ensure_manor


def _fake_guest_queryset():
    class _QS:
        def select_related(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return []

    return _QS()


def _fake_guests_manager():
    class _Manager:
        def filter(self, *_args, **_kwargs):
            return _fake_guest_queryset()

    return _Manager()


def test_get_raid_config_context_reuses_attack_fields_from_target_info(monkeypatch):
    manor = SimpleNamespace(
        max_squad_size=3,
        guests=_fake_guests_manager(),
    )
    target_manor = SimpleNamespace()

    monkeypatch.setattr(
        map_selector,
        "get_manor_public_info",
        lambda *_args, **_kwargs: {"can_attack": True, "attack_reason": "ok"},
    )
    monkeypatch.setattr(map_selector, "get_player_troops", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(map_selector, "get_scout_count", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        map_selector,
        "can_attack_target",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fallback should not be called")),
    )

    context = map_selector.get_raid_config_context(manor, target_manor)

    assert context["can_attack"] is True
    assert context["attack_reason"] == "ok"


@pytest.mark.django_db
def test_manor_detail_api_reuses_attack_fields_from_public_info(monkeypatch, django_user_model, client):
    user = django_user_model.objects.create_user(username="map_detail_reuse", password="pass123")
    manor = ensure_manor(user)
    client.force_login(user)

    monkeypatch.setattr(
        "gameplay.views.map.get_manor_public_info",
        lambda *_args, **_kwargs: {"id": manor.id, "can_attack": False, "attack_reason": "cached"},
    )
    monkeypatch.setattr(
        "gameplay.views.map.can_attack_target",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fallback should not be called")),
    )

    response = client.get(reverse("gameplay:manor_detail_api", kwargs={"manor_id": manor.id}))
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["can_attack"] is False
    assert payload["attack_reason"] == "cached"


@pytest.mark.django_db
def test_manor_detail_api_shows_retired_virtual_player(django_user_model, client):
    viewer_user = django_user_model.objects.create_user(username="map_detail_viewer", password="pass123")
    viewer_manor = ensure_manor(viewer_user)
    retired_user = django_user_model.objects.create_user(username="map_detail_retired", password="pass123")
    retired_manor = ensure_manor(retired_user)
    now = timezone.now()
    BotProfile.objects.create(
        manor=retired_manor,
        state=BotProfile.State.RETIRED,
        prestige_band="newbie",
        target_prestige_band="newbie",
        current_prestige_band="newbie",
        growth_seed=retired_manor.id,
        next_growth_at=now,
        abandon_at=now,
        retire_at=now,
    )
    client.force_login(viewer_user)

    response = client.get(reverse("gameplay:manor_detail_api", kwargs={"manor_id": retired_manor.id}))

    assert viewer_manor.id != retired_manor.id
    assert response.status_code == 200
