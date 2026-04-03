from __future__ import annotations

import pytest
from django.urls import reverse

from gameplay.models import ArenaCoopEntry, ArenaCoopEvent, ArenaEntry, ArenaMatch, ArenaTournament, Message
from gameplay.services.manor.core import ensure_manor
from tests.battle_report_view.support import create_report


@pytest.mark.django_db
def test_arena_coop_report_is_visible_to_participant(client, django_user_model):
    owner_user = django_user_model.objects.create_user(username="arena_coop_report_owner", password="pass123")
    user = django_user_model.objects.create_user(username="arena_coop_report_user", password="pass123")
    owner_manor = ensure_manor(owner_user)
    manor = ensure_manor(user)
    report = create_report(
        manor=owner_manor,
        opponent_name="张无忌",
        battle_type="arena_coop",
        attacker_team=[{"name": "甲", "guest_id": 1, "template_key": "a"}],
        defender_team=[{"name": "张无忌", "guest_id": None, "template_key": "arena_gl_top_zhang_wuji_boss"}],
        seed=9,
    )
    event = ArenaCoopEvent.objects.create(
        status=ArenaCoopEvent.Status.COMPLETED,
        player_limit=5,
        guest_limit_per_entry=3,
        battle_report=report,
    )
    ArenaCoopEntry.objects.create(event=event, manor=owner_manor, status=ArenaCoopEntry.Status.COMPLETED)
    ArenaCoopEntry.objects.create(event=event, manor=manor, status=ArenaCoopEntry.Status.COMPLETED)

    assert client.login(username="arena_coop_report_user", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))

    assert response.status_code == 200


@pytest.mark.django_db
def test_arena_report_uses_defender_perspective_for_defender_viewer(client, django_user_model):
    attacker_user = django_user_model.objects.create_user(
        username="arena_report_attacker",
        password="pass123",
        email="arena_report_attacker@test.local",
    )
    defender_user = django_user_model.objects.create_user(
        username="arena_report_defender",
        password="pass123",
        email="arena_report_defender@test.local",
    )
    attacker_manor = ensure_manor(attacker_user)
    defender_manor = ensure_manor(defender_user)

    report = create_report(manor=attacker_manor, opponent_name=defender_manor.display_name, battle_type="arena")
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=10,
        round_interval_seconds=600,
        started_at=report.starts_at,
        next_round_at=report.starts_at,
    )
    attacker_entry = ArenaEntry.objects.create(tournament=tournament, manor=attacker_manor)
    defender_entry = ArenaEntry.objects.create(tournament=tournament, manor=defender_manor)
    ArenaMatch.objects.create(
        tournament=tournament,
        round_number=1,
        match_index=0,
        attacker_entry=attacker_entry,
        defender_entry=defender_entry,
        winner_entry=attacker_entry,
        status=ArenaMatch.Status.COMPLETED,
        battle_report=report,
        resolved_at=report.starts_at,
    )

    assert client.login(username="arena_report_defender", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))

    assert response.status_code == 200
    assert response.context["player_side"] == "defender"
    assert response.context["my_side"] == "defender"
    assert response.context["attacker_team_display"][0]["name"] == "D"
    assert response.context["defender_team_display"][0]["name"] == "A"
    assert response.context["report_title"] == f"{attacker_manor.display_name} 战报"


@pytest.mark.django_db
def test_arena_report_uses_spectator_perspective_for_non_participant_viewer(client, django_user_model):
    attacker_user = django_user_model.objects.create_user(
        username="arena_report_attacker_2",
        password="pass123",
        email="arena_report_attacker_2@test.local",
    )
    defender_user = django_user_model.objects.create_user(
        username="arena_report_defender_2",
        password="pass123",
        email="arena_report_defender_2@test.local",
    )
    spectator_user = django_user_model.objects.create_user(
        username="arena_report_spectator",
        password="pass123",
        email="arena_report_spectator@test.local",
    )
    attacker_manor = ensure_manor(attacker_user)
    defender_manor = ensure_manor(defender_user)
    ensure_manor(spectator_user)

    report = create_report(manor=attacker_manor, opponent_name=defender_manor.display_name, battle_type="arena")
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=10,
        round_interval_seconds=600,
        started_at=report.starts_at,
        next_round_at=report.starts_at,
    )
    attacker_entry = ArenaEntry.objects.create(tournament=tournament, manor=attacker_manor)
    defender_entry = ArenaEntry.objects.create(tournament=tournament, manor=defender_manor)
    ArenaMatch.objects.create(
        tournament=tournament,
        round_number=1,
        match_index=0,
        attacker_entry=attacker_entry,
        defender_entry=defender_entry,
        winner_entry=attacker_entry,
        status=ArenaMatch.Status.COMPLETED,
        battle_report=report,
        resolved_at=report.starts_at,
    )

    assert client.login(username="arena_report_spectator", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))

    assert response.status_code == 200
    assert response.context["player_side"] == "spectator"
    assert response.context["is_spectator"] is True
    assert response.context["left_team_title"] == "进攻方"
    assert attacker_manor.display_name in response.context["report_title"]
    assert defender_manor.display_name in response.context["report_title"]


@pytest.mark.django_db
def test_arena_report_without_match_relation_uses_defender_perspective_from_message(client, django_user_model):
    attacker_user = django_user_model.objects.create_user(
        username="arena_report_attacker_msg",
        password="pass123",
        email="arena_report_attacker_msg@test.local",
    )
    defender_user = django_user_model.objects.create_user(
        username="arena_report_defender_msg",
        password="pass123",
        email="arena_report_defender_msg@test.local",
    )
    attacker_manor = ensure_manor(attacker_user)
    defender_manor = ensure_manor(defender_user)

    report = create_report(manor=attacker_manor, opponent_name=defender_manor.display_name, battle_type="arena")
    Message.objects.create(
        manor=defender_manor,
        kind=Message.Kind.BATTLE,
        title="竞技场战报",
        battle_report=report,
    )

    assert client.login(username="arena_report_defender_msg", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))

    assert response.status_code == 200
    assert response.context["player_side"] == "defender"
    assert response.context["my_side"] == "defender"
    assert response.context["attacker_team_display"][0]["name"] == "D"
    assert response.context["defender_team_display"][0]["name"] == "A"
    assert response.context["report_title"] == f"{attacker_manor.display_name} 战报"
