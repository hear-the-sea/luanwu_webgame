from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEntryGuest,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaEntryGuest,
    ArenaTournament,
)
from gameplay.services.manor.core import ensure_manor
from guests.models import GuestStatus
from tests.arena_views.helpers import _build_guest, _build_guest_template, _ensure_gladiator_item_templates

pytest_plugins = ("tests.arena_views.support",)


@pytest.mark.django_db
def test_arena_view_renders(arena_client):
    client, _manor = arena_client
    response = client.get(reverse("gameplay:arena"))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "竞技场" in body
    assert "js/arena-registration.js" in body
    assert 'document.addEventListener("DOMContentLoaded"' not in body


@pytest.mark.django_db
def test_arena_registration_page_lists_guangming_top_card(arena_client):
    client, _manor = arena_client

    response = client.get(reverse("gameplay:arena"))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "围攻光明顶" in body
    assert "5 人共斗" in body
    assert "武林高手齐聚光明顶，请派遣3名主力门客参战" in body
    assert "查看共斗详情" not in body
    assert body.count("tw-building-card tw-building-card--manor tw-arena-hero-card") >= 2
    assert body.count('class="tw-arena-signup-panel"') >= 2
    assert body.count(">提交报名<") >= 2
    assert "报名围攻光明顶" not in body


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("view_name", "selector_attr"),
    [
        ("gameplay:arena", "get_arena_registration_context"),
        ("gameplay:arena_events", "get_arena_events_context"),
        ("gameplay:arena_exchange_page", "get_arena_exchange_context"),
    ],
)
def test_arena_pages_sync_resources_before_loading_context(arena_client, monkeypatch, view_name, selector_attr):
    client, manor = arena_client
    calls: list[str] = []

    monkeypatch.setattr(
        "gameplay.views.arena.project_manor_activity_for_read",
        lambda *_args, **_kwargs: calls.append("sync"),
    )
    monkeypatch.setattr(
        f"gameplay.views.arena.{selector_attr}",
        lambda current_manor: calls.append("context") or {"manor": current_manor},
    )

    response = client.get(reverse(view_name))

    assert response.status_code == 200
    assert response.context["manor"] == manor
    assert calls == ["sync", "context"]


@pytest.mark.django_db
def test_arena_events_view_renders(arena_client):
    client, _manor = arena_client
    response = client.get(reverse("gameplay:arena_events"))

    assert response.status_code == 200
    assert "进行中的赛事" in response.content.decode("utf-8")


@pytest.mark.django_db
def test_arena_pages_running_tournament_countdown_uses_refresh_endpoint(arena_client):
    client, manor = arena_client
    now = timezone.now()
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=10,
        round_interval_seconds=600,
        current_round=1,
        started_at=now - timedelta(minutes=5),
        next_round_at=now + timedelta(minutes=1),
    )
    ArenaEntry.objects.create(tournament=tournament, manor=manor, status=ArenaEntry.Status.REGISTERED)

    registration_response = client.get(reverse("gameplay:arena"))
    events_response = client.get(reverse("gameplay:arena_events"))

    assert registration_response.status_code == 200
    assert events_response.status_code == 200
    registration_body = registration_response.content.decode("utf-8")
    events_body = events_response.content.decode("utf-8")
    refresh_url = reverse("gameplay:refresh_arena_activity_api")
    assert refresh_url in registration_body
    assert refresh_url in events_body
    assert 'data-refresh-method="post"' in registration_body
    assert 'data-refresh-method="post"' in events_body


@pytest.mark.django_db
def test_arena_events_view_lists_recent_completed_coop_event(arena_client):
    client, manor = arena_client
    event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.COMPLETED,
        player_limit=5,
        guest_limit_per_entry=3,
        boss_name="张无忌",
        ended_at=timezone.now() - timedelta(hours=1),
    )
    ArenaCoopEntry.objects.create(event=event, manor=manor, status=ArenaCoopEntry.Status.COMPLETED)

    response = client.get(reverse("gameplay:arena_events"))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "查看共斗详情" not in body
    assert f"共斗 #{event.id}" not in body
    assert f"围攻光明顶 #{event.id}" not in body
    assert "最近结束的共斗" not in body
    assert "Boss：张无忌" not in body


@pytest.mark.django_db
def test_arena_exchange_page_view_renders(arena_client):
    client, _manor = arena_client
    response = client.get(reverse("gameplay:arena_exchange_page"))

    assert response.status_code == 200
    assert "奖励兑换" in response.content.decode("utf-8")


@pytest.mark.django_db
def test_arena_register_view_creates_entry(arena_client):
    client, manor = arena_client
    template = _build_guest_template("arena_view_register_tpl")
    guest1 = _build_guest(manor, template, "A")
    guest2 = _build_guest(manor, template, "B")

    response = client.post(
        reverse("gameplay:arena_register"),
        {"guest_ids": [str(guest1.id), str(guest2.id)]},
    )

    assert response.status_code == 302
    assert response.url == reverse("gameplay:arena")

    entry = ArenaEntry.objects.filter(manor=manor).first()
    assert entry is not None
    assert ArenaEntryGuest.objects.filter(entry=entry).count() == 2


@pytest.mark.django_db
def test_arena_coop_register_view_creates_entry(arena_client):
    client, manor = arena_client
    template = _build_guest_template("arena_coop_view_tpl")
    guest1 = _build_guest(manor, template, "A")
    guest2 = _build_guest(manor, template, "B")
    guest3 = _build_guest(manor, template, "C")

    response = client.post(
        reverse("gameplay:arena_coop_register"),
        {"guest_ids": [str(guest1.id), str(guest2.id), str(guest3.id)]},
    )

    assert response.status_code == 302
    assert ArenaCoopEntry.objects.filter(manor=manor).exists()


@pytest.mark.django_db
def test_arena_registration_page_shows_preparing_coop_without_cancel_action(arena_client):
    client, manor = arena_client
    event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.PREPARING,
        player_limit=5,
        guest_limit_per_entry=3,
        boss_name="张无忌",
        prepare_ends_at=timezone.now() + timedelta(minutes=2),
    )
    entry = ArenaCoopEntry.objects.create(event=event, manor=manor, status=ArenaCoopEntry.Status.REGISTERED)
    template = _build_guest_template("arena_coop_preparing_tpl")
    guest = _build_guest(manor, template, "P")
    ArenaCoopEntryGuest.objects.create(entry=entry, guest=guest, slot_index=0, snapshot={})

    response = client.get(reverse("gameplay:arena"))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "准备中" in body
    assert "撤销共斗报名" not in body


@pytest.mark.django_db
def test_arena_registration_page_counts_only_registered_coop_entries_in_recruiting_pool(
    arena_client, django_user_model
):
    client, manor = arena_client
    event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.RECRUITING,
        player_limit=5,
        guest_limit_per_entry=3,
        boss_name="张无忌",
    )
    ArenaCoopEntry.objects.create(event=event, manor=manor, status=ArenaCoopEntry.Status.REGISTERED)

    other_user = django_user_model.objects.create_user(
        username="arena_coop_cancelled_view_user",
        password="pass123",
        email="arena_coop_cancelled_view_user@test.local",
    )
    other_manor = ensure_manor(other_user)
    ArenaCoopEntry.objects.create(event=event, manor=other_manor, status=ArenaCoopEntry.Status.CANCELLED)

    response = client.get(reverse("gameplay:arena"))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert f"当前共斗池：场次 #{event.id}" in body
    assert "（1/5）" in body
    assert "（2/5）" not in body


@pytest.mark.django_db
def test_arena_cancel_view_removes_recruiting_entry(arena_client):
    client, manor = arena_client
    template = _build_guest_template("arena_view_cancel_tpl")
    guest1 = _build_guest(manor, template, "A")
    guest2 = _build_guest(manor, template, "B")

    register_response = client.post(
        reverse("gameplay:arena_register"),
        {"guest_ids": [str(guest1.id), str(guest2.id)]},
    )
    assert register_response.status_code == 302

    response = client.post(
        reverse("gameplay:arena_cancel"),
        {"next": reverse("gameplay:arena")},
    )

    assert response.status_code == 302
    assert response.url == reverse("gameplay:arena")
    assert not ArenaEntry.objects.filter(manor=manor, tournament__status=ArenaTournament.Status.RECRUITING).exists()
    guest1.refresh_from_db(fields=["status"])
    guest2.refresh_from_db(fields=["status"])
    assert guest1.status == GuestStatus.IDLE
    assert guest2.status == GuestStatus.IDLE


@pytest.mark.django_db
def test_arena_exchange_view_deducts_coins(arena_client):
    client, manor = arena_client
    manor.arena_coins = 300
    manor.save(update_fields=["arena_coins"])

    response = client.post(
        reverse("gameplay:arena_exchange"),
        {"reward_key": "grain_pack_small", "quantity": "1"},
    )

    assert response.status_code == 302
    assert response.url == reverse("gameplay:arena")
    manor.refresh_from_db(fields=["arena_coins"])
    assert manor.arena_coins == 220


@pytest.mark.django_db
def test_arena_exchange_view_redirects_to_safe_next(arena_client):
    client, manor = arena_client
    manor.arena_coins = 300
    manor.save(update_fields=["arena_coins"])

    response = client.post(
        reverse("gameplay:arena_exchange"),
        {
            "reward_key": "grain_pack_small",
            "quantity": "1",
            "next": reverse("gameplay:arena_exchange_page"),
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("gameplay:arena_exchange_page")


@pytest.mark.django_db
def test_arena_exchange_view_shows_drawn_gladiator_item(arena_client, monkeypatch):
    client, manor = arena_client
    _ensure_gladiator_item_templates()
    manor.arena_coins = 600
    manor.save(update_fields=["arena_coins"])
    monkeypatch.setattr("gameplay.services.arena.helpers.random.random", lambda: 0.0)

    response = client.post(
        reverse("gameplay:arena_exchange"),
        {"reward_key": "gladiator_chest", "quantity": "1"},
        follow=True,
    )

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "本次抽到：角斗士头盔x1" in body
