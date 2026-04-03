from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from battle.models import BattleReport
from gameplay.models import (
    ArenaCoopContribution,
    ArenaCoopEntry,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaMatch,
    ArenaTournament,
)
from gameplay.services.manor.core import ensure_manor
from tests.arena_views.helpers import _build_guest, _build_guest_template

pytest_plugins = ("tests.arena_views.support",)


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
def test_arena_events_view_running_coop_summary_has_no_detail_action(arena_client):
    client, _manor = arena_client
    event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.RUNNING,
        player_limit=5,
        guest_limit_per_entry=3,
        boss_name="张无忌",
    )

    response = client.get(reverse("gameplay:arena_events"))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "进行中的共斗" not in body
    assert f"共斗 #{event.id}" not in body
    assert "查看共斗详情" not in body
    assert f"围攻光明顶 #{event.id}" not in body
    assert "Boss：张无忌" not in body


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
