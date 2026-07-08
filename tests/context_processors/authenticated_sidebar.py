from __future__ import annotations

from pathlib import Path

import pytest
from django.db import DatabaseError
from django.test import RequestFactory

from gameplay.context_processors import notifications
from gameplay.services.manor.core import ensure_manor
from guilds.models.base import Guild
from guilds.models.member import GuildMember

pytestmark = pytest.mark.django_db


def test_notifications_authenticated_falls_back_when_sidebar_cache_payload_invalid(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="ctx_user", password="pass")
    manor = ensure_manor(user)
    request = RequestFactory().get("/")
    request.user = user

    def fake_cache_get(key, default=None):
        if key == "stats:total_users_count":
            return 5
        if key == "stats:online_users_count":
            return 2
        if key == f"sidebar:rank:{manor.id}":
            return None
        return default

    monkeypatch.setattr("gameplay.selectors.stats.cache.get", fake_cache_get)
    monkeypatch.setattr("gameplay.selectors.stats.cache.set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("gameplay.selectors.sidebar.cache.get", fake_cache_get)
    monkeypatch.setattr("gameplay.selectors.sidebar.cache.set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("gameplay.context_processors.unread_message_count", lambda _manor: 4)
    monkeypatch.setattr("gameplay.services.ranking.get_player_rank", lambda _manor: 9)

    context = notifications(request)
    assert context["message_unread_count"] == 4
    assert context["sidebar_rank"] == 9


def test_notifications_home_page_includes_sidebar_action_points(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="ctx_action_points_user", password="pass")
    manor = ensure_manor(user)
    manor.action_points = 977
    manor.save(update_fields=["action_points"])
    user._state.fields_cache.pop("manor", None)
    request = RequestFactory().get("/")
    request.user = user

    def fake_cache_get(key, default=None):
        if key == "stats:total_users_count":
            return 5
        if key == "stats:online_users_count":
            return 2
        if key == f"sidebar:rank:{manor.id}":
            return None
        return default

    monkeypatch.setattr("gameplay.selectors.stats.cache.get", fake_cache_get)
    monkeypatch.setattr("gameplay.selectors.stats.cache.set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("gameplay.selectors.sidebar.cache.get", fake_cache_get)
    monkeypatch.setattr("gameplay.selectors.sidebar.cache.set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("gameplay.context_processors.unread_message_count", lambda _manor: 0)
    monkeypatch.setattr("gameplay.services.ranking.get_player_rank", lambda _manor: 9)

    context = notifications(request)

    assert context["sidebar_action_points"] == 977
    assert context["sidebar_action_points_label"] == "977/1000"


def test_home_sidebar_template_does_not_hardcode_action_points_fallback():
    template = Path("templates/base.html").read_text(encoding="utf-8")

    assert "sidebar_action_points|default:1000" not in template
    assert "{{ sidebar_action_points_label }}" in template


def test_notifications_authenticated_partial_sidebar_failures_do_not_hide_other_sections(
    monkeypatch, django_user_model
):
    user = django_user_model.objects.create_user(username="ctx_partial_user", password="pass")
    request = RequestFactory().get("/")
    request.user = user

    def fake_cache_get(key, default=None):
        if key == "stats:total_users_count":
            return 5
        if key == "stats:online_users_count":
            return 2
        return default

    monkeypatch.setattr("gameplay.selectors.stats.cache.get", fake_cache_get)
    monkeypatch.setattr("gameplay.selectors.stats.cache.set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("gameplay.selectors.sidebar.cache.get", fake_cache_get)
    monkeypatch.setattr("gameplay.selectors.sidebar.cache.set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "gameplay.context_processors.unread_message_count",
        lambda _manor: (_ for _ in ()).throw(DatabaseError("message db boom")),
    )
    monkeypatch.setattr(
        "gameplay.services.raid.get_protection_status",
        lambda _manor: {"is_protected": True, "type_display": "护盾", "remaining_display": "10m"},
    )
    monkeypatch.setattr(
        "gameplay.services.ranking.get_player_rank",
        lambda _manor: (_ for _ in ()).throw(DatabaseError("rank db boom")),
    )

    context = notifications(request)
    assert context["message_unread_count"] == 0
    assert context["sidebar_action_points_label"] == "1000/1000"
    assert context["header_protection_status"] == {
        "is_protected": True,
        "type_display": "护盾",
        "remaining_display": "10m",
    }
    assert "sidebar_rank" not in context


def test_notifications_authenticated_programming_error_in_unread_count_bubbles_up(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="ctx_unread_runtime_user", password="pass")
    request = RequestFactory().get("/")
    request.user = user

    monkeypatch.setattr("gameplay.selectors.stats.cache.get", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        "gameplay.context_processors.unread_message_count",
        lambda _manor: (_ for _ in ()).throw(RuntimeError("message boom")),
    )

    with pytest.raises(RuntimeError, match="message boom"):
        notifications(request)


def test_notifications_authenticated_programming_error_in_rank_bubbles_up(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="ctx_rank_runtime_user", password="pass")
    request = RequestFactory().get("/")
    request.user = user

    def fake_cache_get(key, default=None):
        if key in {"stats:total_users_count", "stats:online_users_count"}:
            return 0
        if key.startswith("sidebar:rank:"):
            return None
        return default

    monkeypatch.setattr("gameplay.selectors.stats.cache.get", fake_cache_get)
    monkeypatch.setattr("gameplay.selectors.sidebar.cache.get", fake_cache_get)
    monkeypatch.setattr("gameplay.context_processors.unread_message_count", lambda _manor: 1)
    monkeypatch.setattr(
        "gameplay.services.raid.get_protection_status",
        lambda _manor: {"is_protected": False, "type_display": "", "remaining_display": ""},
    )
    monkeypatch.setattr(
        "gameplay.services.ranking.get_player_rank",
        lambda _manor: (_ for _ in ()).throw(RuntimeError("rank boom")),
    )

    with pytest.raises(RuntimeError, match="rank boom"):
        notifications(request)


def test_notifications_authenticated_runtime_marker_sidebar_cache_error_bubbles_up(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="ctx_rank_cache_runtime_user", password="pass")
    manor = ensure_manor(user)
    request = RequestFactory().get("/")
    request.user = user

    def fake_stats_cache_get(key, default=None):
        if key in {"stats:total_users_count", "stats:online_users_count"}:
            return 0
        return default

    monkeypatch.setattr("gameplay.selectors.stats.cache.get", fake_stats_cache_get)
    monkeypatch.setattr("gameplay.context_processors.unread_message_count", lambda _manor: 1)
    monkeypatch.setattr(
        "gameplay.services.raid.get_protection_status",
        lambda _manor: {"is_protected": False, "type_display": "", "remaining_display": ""},
    )
    monkeypatch.setattr(
        "gameplay.selectors.sidebar.cache.get",
        lambda key, default=None: (
            (_ for _ in ()).throw(RuntimeError("cache read failed")) if key == f"sidebar:rank:{manor.id}" else default
        ),
    )

    with pytest.raises(RuntimeError, match="cache read failed"):
        notifications(request)


def test_notifications_authenticated_runtime_marker_sidebar_cache_set_error_bubbles_up(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="ctx_rank_cache_set_runtime_user", password="pass")
    manor = ensure_manor(user)
    request = RequestFactory().get("/")
    request.user = user

    def fake_stats_cache_get(key, default=None):
        if key in {"stats:total_users_count", "stats:online_users_count"}:
            return 0
        return default

    def fake_sidebar_cache_get(key, default=None):
        if key == f"sidebar:rank:{manor.id}":
            return None
        return default

    monkeypatch.setattr("gameplay.selectors.stats.cache.get", fake_stats_cache_get)
    monkeypatch.setattr("gameplay.context_processors.unread_message_count", lambda _manor: 1)
    monkeypatch.setattr(
        "gameplay.services.raid.get_protection_status",
        lambda _manor: {"is_protected": False, "type_display": "", "remaining_display": ""},
    )
    monkeypatch.setattr("gameplay.selectors.sidebar.cache.get", fake_sidebar_cache_get)
    monkeypatch.setattr(
        "gameplay.selectors.sidebar.cache.set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cache write failed")),
    )
    monkeypatch.setattr("gameplay.services.ranking.get_player_rank", lambda _manor: 9)

    with pytest.raises(RuntimeError, match="cache write failed"):
        notifications(request)


def test_notifications_non_home_pages_skip_home_sidebar_queries(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="ctx_non_home_user", password="pass")
    request = RequestFactory().get("/manor/warehouse/")
    request.user = user

    monkeypatch.setattr(
        "gameplay.selectors.stats.get_redis_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("redis down")),
    )
    monkeypatch.setattr("gameplay.context_processors.unread_message_count", lambda _manor: 2)
    monkeypatch.setattr(
        "gameplay.services.ranking.get_player_rank",
        lambda _manor: (_ for _ in ()).throw(AssertionError("sidebar rank should not be loaded")),
    )

    context = notifications(request)
    assert context["message_unread_count"] == 2
    assert "sidebar_rank" not in context
    assert "sidebar_prestige" not in context
    assert "sidebar_current_contribution_label" not in context


def test_notifications_reuses_request_scoped_authenticated_payload(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="ctx_request_scope_user", password="pass")
    _ = ensure_manor(user)
    request = RequestFactory().get("/manor/warehouse/")
    request.user = user

    unread_calls = {"count": 0}
    protection_calls = {"count": 0}

    monkeypatch.setattr("gameplay.selectors.stats.load_total_user_count", lambda: 0)
    monkeypatch.setattr("gameplay.selectors.stats.load_online_user_count", lambda: 0)

    def _load_unread(_manor):
        unread_calls["count"] += 1
        return 3

    def _load_protection(_manor):
        protection_calls["count"] += 1
        return {"is_protected": True, "type_display": "护盾", "remaining_display": "10m"}

    monkeypatch.setattr("gameplay.context_processors.unread_message_count", _load_unread)
    monkeypatch.setattr("gameplay.services.raid.get_protection_status", _load_protection)

    first = notifications(request)
    second = notifications(request)

    assert first["message_unread_count"] == 3
    assert second["message_unread_count"] == 3
    assert first["header_protection_status"] == second["header_protection_status"]
    assert unread_calls["count"] == 1
    assert protection_calls["count"] == 1


def test_notifications_home_page_includes_active_guild_contribution_label(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="ctx_guild_member_user", password="pass")
    manor = ensure_manor(user)
    guild = Guild.objects.create(name="上下文贡献帮", founder=user, is_active=True)
    GuildMember.objects.create(
        guild=guild,
        user=user,
        position="leader",
        current_contribution=128,
        total_contribution=128,
        is_active=True,
    )
    request = RequestFactory().get("/")
    request.user = user

    def fake_cache_get(key, default=None):
        if key == "stats:total_users_count":
            return 5
        if key == "stats:online_users_count":
            return 2
        if key == f"sidebar:rank:{manor.id}":
            return None
        return default

    monkeypatch.setattr("gameplay.selectors.stats.cache.get", fake_cache_get)
    monkeypatch.setattr("gameplay.selectors.stats.cache.set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("gameplay.selectors.sidebar.cache.get", fake_cache_get)
    monkeypatch.setattr("gameplay.selectors.sidebar.cache.set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("gameplay.context_processors.unread_message_count", lambda _manor: 1)
    monkeypatch.setattr(
        "gameplay.services.raid.get_protection_status",
        lambda _manor: {"is_protected": False, "type_display": "", "remaining_display": ""},
    )
    monkeypatch.setattr("gameplay.services.ranking.get_player_rank", lambda _manor: 9)

    context = notifications(request)

    assert context["sidebar_current_contribution_label"] == "128"
