from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.messages import get_messages
from django.db import DatabaseError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from battle.models import BattleReport
from core.exceptions import ArenaError
from gameplay.models import (
    ArenaCoopContribution,
    ArenaCoopEntry,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaEntryGuest,
    ArenaMatch,
    ArenaTournament,
    ItemTemplate,
)
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestStatus, GuestTemplate


def _build_guest_template(key: str) -> GuestTemplate:
    return GuestTemplate.objects.create(
        key=key,
        name=f"竞技场模板-{key}",
        archetype="military",
        rarity="green",
    )


def _build_guest(manor, template, suffix: str) -> Guest:
    return Guest.objects.create(
        manor=manor,
        template=template,
        custom_name=f"竞技{suffix}",
        level=20,
        force=160,
        intellect=110,
        defense_stat=120,
        agility=120,
    )


def _ensure_gladiator_item_templates() -> None:
    key_to_name = {
        "equip_jiaodoushitoukui": "角斗士头盔",
        "equip_jiaodoushixiongjia": "角斗士胸甲",
        "equip_jiaodoushizhixue": "角斗士之靴",
        "equip_jiaodoushizhichui": "角斗士之锤",
    }
    for key, name in key_to_name.items():
        ItemTemplate.objects.get_or_create(
            key=key,
            defaults={
                "name": name,
                "effect_type": ItemTemplate.EffectType.TOOL,
            },
        )


@pytest.fixture
def arena_client(django_user_model):
    user = django_user_model.objects.create_user(
        username="arena_view_user",
        password="testpass123",
        email="arena_view_user@test.local",
    )
    client = Client()
    client.login(username="arena_view_user", password="testpass123")
    manor = ensure_manor(user)
    manor.silver = 100000
    manor.save(update_fields=["silver"])
    return client, manor


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
    assert "最近结束的共斗" in body
    assert f"围攻光明顶 #{event.id}" in body


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
def test_arena_coop_detail_view_renders(arena_client):
    client, manor = arena_client
    now = timezone.now()
    report = BattleReport.objects.create(
        manor=manor,
        opponent_name="张无忌",
        battle_type="arena_coop",
        attacker_team=[],
        attacker_troops={},
        defender_team=[],
        defender_troops={},
        rounds=[],
        losses={"attacker": {}, "defender": {}},
        drops={},
        winner="attacker",
        starts_at=now,
        completed_at=now,
        seed=3,
    )
    event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.COMPLETED,
        player_limit=5,
        guest_limit_per_entry=3,
        boss_name="张无忌",
        boss_remaining_hp=0,
        battle_report=report,
    )
    entry = ArenaCoopEntry.objects.create(event=event, manor=manor, status=ArenaCoopEntry.Status.COMPLETED)
    ArenaCoopContribution.objects.create(
        event=event,
        entry=entry,
        total_damage=1234,
        boss_damage=1000,
        guard_damage=234,
        effective_damage=1234,
        damage_share_bps=5000,
        damage_rank=1,
        met_minimum_contribution=True,
        participation_coins=30,
        damage_coins=20,
        rank_coins=80,
        clear_coins=40,
        total_coins=170,
    )

    response = client.get(reverse("gameplay:arena_coop_detail", args=[event.id]))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "围攻光明顶" in body
    assert "Boss" in body
    assert "1234" in body


@pytest.mark.django_db
def test_arena_coop_detail_view_shows_live_boss_hp_for_registered_event(arena_client):
    client, manor = arena_client
    template = _build_guest_template("arena_coop_detail_live_hp_tpl")
    guests = [_build_guest(manor, template, suffix) for suffix in ["A", "B", "C"]]

    response = client.post(
        reverse("gameplay:arena_coop_register"),
        {"guest_ids": [str(guest.id) for guest in guests]},
        follow=True,
    )

    entry = ArenaCoopEntry.objects.get(manor=manor)
    detail_response = client.get(reverse("gameplay:arena_coop_detail", args=[entry.event_id]))

    assert response.status_code == 200
    assert detail_response.status_code == 200
    assert "剩余生命：300000" in detail_response.content.decode("utf-8")


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


@pytest.mark.django_db
def test_arena_event_detail_view_renders(arena_client, django_user_model):
    client, manor = arena_client
    opponent_user = django_user_model.objects.create_user(
        username="arena_detail_opponent",
        password="pass123",
        email="arena_detail_opponent@test.local",
    )
    opponent_manor = ensure_manor(opponent_user)

    now = timezone.now()
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=10,
        round_interval_seconds=600,
        current_round=1,
        started_at=now - timedelta(minutes=5),
        next_round_at=now + timedelta(minutes=5),
    )
    ArenaEntry.objects.create(tournament=tournament, manor=manor, status=ArenaEntry.Status.REGISTERED)
    ArenaEntry.objects.create(tournament=tournament, manor=opponent_manor, status=ArenaEntry.Status.REGISTERED)

    response = client.get(reverse("gameplay:arena_event_detail", args=[tournament.id]))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert f"赛事 #{tournament.id} 对阵与战报" in body
    assert "对阵与战报" in body


@pytest.mark.django_db
def test_arena_event_detail_view_syncs_resources_before_loading_context(arena_client, monkeypatch):
    client, manor = arena_client
    calls: list[str] = []

    monkeypatch.setattr(
        "gameplay.views.arena.project_manor_activity_for_read",
        lambda *_args, **_kwargs: calls.append("sync"),
    )
    monkeypatch.setattr(
        "gameplay.views.arena.get_arena_event_detail_context",
        lambda current_manor, **_kwargs: calls.append("context") or {"manor": current_manor},
    )

    response = client.get(reverse("gameplay:arena_event_detail", args=[1]))

    assert response.status_code == 200
    assert response.context["manor"] == manor
    assert calls == ["sync", "context"]


@pytest.mark.django_db
def test_arena_event_detail_view_supports_round_paging_and_inline_report(arena_client, django_user_model):
    client, manor = arena_client
    opponent_user_1 = django_user_model.objects.create_user(
        username="arena_round_opponent_1",
        password="pass123",
        email="arena_round_opponent_1@test.local",
    )
    opponent_user_2 = django_user_model.objects.create_user(
        username="arena_round_opponent_2",
        password="pass123",
        email="arena_round_opponent_2@test.local",
    )
    opponent_user_3 = django_user_model.objects.create_user(
        username="arena_round_opponent_3",
        password="pass123",
        email="arena_round_opponent_3@test.local",
    )
    manor_b = ensure_manor(opponent_user_1)
    manor_c = ensure_manor(opponent_user_2)
    manor_d = ensure_manor(opponent_user_3)

    now = timezone.now()
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=10,
        round_interval_seconds=600,
        current_round=2,
        started_at=now - timedelta(minutes=15),
        next_round_at=now + timedelta(minutes=5),
    )
    entry_a = ArenaEntry.objects.create(tournament=tournament, manor=manor, status=ArenaEntry.Status.REGISTERED)
    entry_b = ArenaEntry.objects.create(tournament=tournament, manor=manor_b, status=ArenaEntry.Status.REGISTERED)
    entry_c = ArenaEntry.objects.create(tournament=tournament, manor=manor_c, status=ArenaEntry.Status.REGISTERED)
    entry_d = ArenaEntry.objects.create(tournament=tournament, manor=manor_d, status=ArenaEntry.Status.REGISTERED)

    report = BattleReport.objects.create(
        manor=manor,
        opponent_name=manor_b.display_name,
        battle_type="arena",
        attacker_team=[{"name": "A", "guest_id": 1, "template_key": "a"}],
        attacker_troops={},
        defender_team=[{"name": "B", "guest_id": 2, "template_key": "b"}],
        defender_troops={},
        rounds=[],
        losses={"attacker": {}, "defender": {}},
        drops={},
        winner="attacker",
        starts_at=now,
        completed_at=now,
        seed=1,
    )
    ArenaMatch.objects.create(
        tournament=tournament,
        round_number=1,
        match_index=0,
        attacker_entry=entry_a,
        defender_entry=entry_b,
        winner_entry=entry_a,
        status=ArenaMatch.Status.COMPLETED,
        battle_report=report,
        resolved_at=now - timedelta(minutes=10),
    )
    ArenaMatch.objects.create(
        tournament=tournament,
        round_number=1,
        match_index=1,
        attacker_entry=entry_c,
        defender_entry=entry_d,
        winner_entry=entry_c,
        status=ArenaMatch.Status.COMPLETED,
        resolved_at=now - timedelta(minutes=10),
    )
    ArenaMatch.objects.create(
        tournament=tournament,
        round_number=2,
        match_index=0,
        attacker_entry=entry_a,
        defender_entry=entry_c,
        winner_entry=entry_a,
        status=ArenaMatch.Status.COMPLETED,
        resolved_at=now - timedelta(minutes=2),
    )

    response = client.get(f"{reverse('gameplay:arena_event_detail', args=[tournament.id])}?round=1")

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "每轮单独一页，共 2 轮" in body
    assert "第 1 轮对阵" in body
    assert "查看战报" in body
    assert reverse("battle:report_detail", kwargs={"pk": report.id}) in body
    assert "tw-arena-loser text-text-muted" in body
    assert ">结果<" not in body


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
