from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.db import DatabaseError
from django.urls import reverse

from core.exceptions import ArenaError
from tests.arena_views.helpers import _build_guest, _build_guest_template

pytest_plugins = ("tests.arena_views.support",)


@pytest.mark.django_db
def test_arena_register_view_known_error_shows_message(arena_client, monkeypatch):
    client, manor = arena_client
    template = _build_guest_template("arena_view_register_known_tpl")
    guest = _build_guest(manor, template, "K")

    monkeypatch.setattr(
        "gameplay.views.arena.arena_core.register_arena_entry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ArenaError("arena blocked")),
    )

    response = client.post(
        reverse("gameplay:arena_register"),
        {"guest_ids": [str(guest.id)]},
    )

    assert response.status_code == 302
    assert response.url == reverse("gameplay:arena")
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert any("arena blocked" in m for m in messages)


@pytest.mark.django_db
def test_arena_register_view_raw_value_error_bubbles_up(arena_client, monkeypatch):
    client, manor = arena_client
    template = _build_guest_template("arena_view_register_value_error_tpl")
    guest = _build_guest(manor, template, "V")

    monkeypatch.setattr(
        "gameplay.views.arena.arena_core.register_arena_entry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("arena legacy")),
    )

    with pytest.raises(ValueError, match="arena legacy"):
        client.post(
            reverse("gameplay:arena_register"),
            {"guest_ids": [str(guest.id)]},
        )


@pytest.mark.django_db
def test_arena_register_view_database_error_does_not_500(arena_client, monkeypatch):
    client, manor = arena_client
    template = _build_guest_template("arena_view_register_exc_tpl")
    guest = _build_guest(manor, template, "X")

    monkeypatch.setattr(
        "gameplay.views.arena.arena_core.register_arena_entry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
    )

    response = client.post(
        reverse("gameplay:arena_register"),
        {"guest_ids": [str(guest.id)]},
    )

    assert response.status_code == 302
    assert response.url == reverse("gameplay:arena")
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert any("操作失败，请稍后重试" in m for m in messages)


@pytest.mark.django_db
def test_arena_register_view_programming_error_bubbles_up(arena_client, monkeypatch):
    client, manor = arena_client
    template = _build_guest_template("arena_view_register_runtime_tpl")
    guest = _build_guest(manor, template, "Y")

    monkeypatch.setattr(
        "gameplay.views.arena.arena_core.register_arena_entry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        client.post(
            reverse("gameplay:arena_register"),
            {"guest_ids": [str(guest.id)]},
        )


@pytest.mark.django_db
def test_arena_exchange_view_database_error_does_not_500(arena_client, monkeypatch):
    client, manor = arena_client
    manor.arena_coins = 300
    manor.save(update_fields=["arena_coins"])

    monkeypatch.setattr(
        "gameplay.views.arena.arena_core.exchange_arena_reward",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("db down")),
    )

    response = client.post(
        reverse("gameplay:arena_exchange"),
        {"reward_key": "grain_pack_small", "quantity": "1"},
    )

    assert response.status_code == 302
    assert response.url == reverse("gameplay:arena")
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert any("操作失败，请稍后重试" in m for m in messages)
