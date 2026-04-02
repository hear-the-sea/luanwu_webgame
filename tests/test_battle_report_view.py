from __future__ import annotations

import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from battle.models import BattleReport
from gameplay.models import (
    ArenaCoopEntry,
    ArenaCoopEvent,
    ArenaEntry,
    ArenaMatch,
    ArenaTournament,
    ItemTemplate,
    Message,
    RaidRun,
)
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate, Skill, SkillBook


@pytest.mark.django_db
def test_arena_coop_report_is_visible_to_participant(client, django_user_model):
    owner_user = django_user_model.objects.create_user(username="arena_coop_report_owner", password="pass123")
    user = django_user_model.objects.create_user(username="arena_coop_report_user", password="pass123")
    owner_manor = ensure_manor(owner_user)
    manor = ensure_manor(user)
    now = timezone.now()
    report = BattleReport.objects.create(
        manor=owner_manor,
        opponent_name="张无忌",
        battle_type="arena_coop",
        attacker_team=[{"name": "甲", "guest_id": 1, "template_key": "a"}],
        attacker_troops={},
        defender_team=[{"name": "张无忌", "guest_id": None, "template_key": "arena_gl_top_zhang_wuji_boss"}],
        defender_troops={},
        rounds=[],
        losses={"attacker": {}, "defender": {}},
        drops={},
        winner="attacker",
        starts_at=now,
        completed_at=now,
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

    now = timezone.now()
    report = BattleReport.objects.create(
        manor=attacker_manor,
        opponent_name=defender_manor.display_name,
        battle_type="arena",
        attacker_team=[{"name": "A", "guest_id": 1, "template_key": "a"}],
        attacker_troops={},
        defender_team=[{"name": "D", "guest_id": 2, "template_key": "d"}],
        defender_troops={},
        rounds=[],
        losses={"attacker": {}, "defender": {}},
        drops={},
        winner="attacker",
        starts_at=now,
        completed_at=now,
        seed=1,
    )
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=10,
        round_interval_seconds=600,
        started_at=now,
        next_round_at=now,
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
        resolved_at=now,
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

    now = timezone.now()
    report = BattleReport.objects.create(
        manor=attacker_manor,
        opponent_name=defender_manor.display_name,
        battle_type="arena",
        attacker_team=[{"name": "A", "guest_id": 1, "template_key": "a"}],
        attacker_troops={},
        defender_team=[{"name": "D", "guest_id": 2, "template_key": "d"}],
        defender_troops={},
        rounds=[],
        losses={"attacker": {}, "defender": {}},
        drops={},
        winner="attacker",
        starts_at=now,
        completed_at=now,
        seed=1,
    )
    tournament = ArenaTournament.objects.create(
        status=ArenaTournament.Status.RUNNING,
        player_limit=10,
        round_interval_seconds=600,
        started_at=now,
        next_round_at=now,
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
        resolved_at=now,
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

    now = timezone.now()
    report = BattleReport.objects.create(
        manor=attacker_manor,
        opponent_name=defender_manor.display_name,
        battle_type="arena",
        attacker_team=[{"name": "A", "guest_id": 1, "template_key": "a"}],
        attacker_troops={},
        defender_team=[{"name": "D", "guest_id": 2, "template_key": "d"}],
        defender_troops={},
        rounds=[],
        losses={"attacker": {}, "defender": {}},
        drops={},
        winner="attacker",
        starts_at=now,
        completed_at=now,
        seed=1,
    )
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


@pytest.mark.django_db
def test_raid_report_without_run_relation_uses_defender_perspective_from_message(client, django_user_model):
    attacker_user = django_user_model.objects.create_user(
        username="raid_report_attacker_msg",
        password="pass123",
        email="raid_report_attacker_msg@test.local",
    )
    defender_user = django_user_model.objects.create_user(
        username="raid_report_defender_msg",
        password="pass123",
        email="raid_report_defender_msg@test.local",
    )
    attacker_manor = ensure_manor(attacker_user)
    defender_manor = ensure_manor(defender_user)

    now = timezone.now()
    report = BattleReport.objects.create(
        manor=attacker_manor,
        opponent_name=defender_manor.display_name,
        battle_type="raid",
        attacker_team=[{"name": "A", "guest_id": 101, "template_key": "a"}],
        attacker_troops={},
        defender_team=[{"name": "D", "guest_id": 202, "template_key": "d"}],
        defender_troops={},
        rounds=[],
        losses={"attacker": {}, "defender": {}},
        drops={},
        winner="attacker",
        starts_at=now,
        completed_at=now,
        seed=3,
    )
    Message.objects.create(
        manor=defender_manor,
        kind=Message.Kind.BATTLE,
        title="踢馆战报 - 防守失败",
        battle_report=report,
    )

    assert client.login(username="raid_report_defender_msg", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))

    assert response.status_code == 200
    assert response.context["player_side"] == "defender"
    assert response.context["my_side"] == "defender"
    assert response.context["attacker_team_display"][0]["name"] == "D"
    assert response.context["defender_team_display"][0]["name"] == "A"
    assert response.context["report_title"] == f"{attacker_manor.display_name} 战报"


@pytest.mark.django_db
def test_defense_mission_report_without_run_relation_infers_defender_by_guest_ownership(client, django_user_model):
    user = django_user_model.objects.create_user(
        username="mission_report_defense_owner",
        password="pass123",
        email="mission_report_defense_owner@test.local",
    )
    manor = ensure_manor(user)

    template = GuestTemplate.objects.create(
        key=f"battle_view_guest_{uuid.uuid4().hex[:10]}",
        name="守将",
        archetype="military",
        rarity="blue",
        base_attack=100,
        base_intellect=80,
        base_defense=100,
        base_agility=80,
        base_luck=50,
        base_hp=1000,
    )
    my_guest = Guest.objects.create(manor=manor, template=template, level=10)

    now = timezone.now()
    report = BattleReport.objects.create(
        manor=manor,
        opponent_name="防守任务",
        battle_type="task1",
        attacker_team=[{"name": "敌将", "guest_id": None, "template_key": "enemy"}],
        attacker_troops={},
        defender_team=[{"name": my_guest.display_name, "guest_id": my_guest.id, "template_key": template.key}],
        defender_troops={},
        rounds=[],
        losses={"attacker": {}, "defender": {}},
        drops={},
        winner="defender",
        starts_at=now,
        completed_at=now,
        seed=11,
    )

    assert client.login(username="mission_report_defense_owner", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))

    assert response.status_code == 200
    assert response.context["player_side"] == "defender"
    assert response.context["my_side"] == "defender"
    assert response.context["attacker_team_display"][0]["name"] == my_guest.display_name
    assert response.context["defender_team_display"][0]["name"] == "敌将"


@pytest.mark.django_db
def test_raid_defender_failure_shows_pvp_loss_items(client, django_user_model):
    attacker_user = django_user_model.objects.create_user(
        username="raid_report_attacker",
        password="pass123",
        email="raid_report_attacker@test.local",
    )
    defender_user = django_user_model.objects.create_user(
        username="raid_report_defender",
        password="pass123",
        email="raid_report_defender@test.local",
    )
    attacker_manor = ensure_manor(attacker_user)
    defender_manor = ensure_manor(defender_user)

    now = timezone.now()
    report = BattleReport.objects.create(
        manor=attacker_manor,
        opponent_name=defender_manor.display_name,
        battle_type="raid",
        attacker_team=[{"name": "A", "guest_id": 1, "template_key": "a"}],
        attacker_troops={},
        defender_team=[{"name": "D", "guest_id": 2, "template_key": "d"}],
        defender_troops={},
        rounds=[],
        losses={"attacker": {}, "defender": {}},
        drops={},
        winner="attacker",
        starts_at=now,
        completed_at=now,
        seed=7,
    )
    RaidRun.objects.create(
        attacker=attacker_manor,
        defender=defender_manor,
        battle_report=report,
        status=RaidRun.Status.RETURNING,
        loot_resources={"silver": 321, "grain": 123},
        loot_items={"mysterious_stone": 2},
        battle_rewards={"capture": {"guest_name": "赵云", "from": "defender"}},
        is_attacker_victory=True,
    )
    Message.objects.create(
        manor=defender_manor,
        kind=Message.Kind.BATTLE,
        title="踢馆战报 - 防守失败",
        battle_report=report,
    )

    assert client.login(username="raid_report_defender", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "战斗损失" in body
    assert "银两 -321" in body
    assert "粮食 -123" in body
    assert "mysterious_stone -2" in body
    assert "门客被俘（赵云）" in body
    assert "战斗失败不获得奖励" not in body


@pytest.mark.django_db
def test_raid_report_reuses_label_queries_for_loss_items(client, django_user_model):
    attacker_user = django_user_model.objects.create_user(
        username="raid_report_query_attacker",
        password="pass123",
        email="raid_report_query_attacker@test.local",
    )
    defender_user = django_user_model.objects.create_user(
        username="raid_report_query_defender",
        password="pass123",
        email="raid_report_query_defender@test.local",
    )
    attacker_manor = ensure_manor(attacker_user)
    defender_manor = ensure_manor(defender_user)

    ItemTemplate.objects.create(
        key="battle_view_mysterious_stone",
        name="神秘石",
        effect_type=ItemTemplate.EffectType.TOOL,
    )
    skill = Skill.objects.create(key="battle_view_skill_alpha", name="技能A")
    SkillBook.objects.create(key="battle_view_skill_book", name="战报技能书", skill=skill)

    now = timezone.now()
    report = BattleReport.objects.create(
        manor=attacker_manor,
        opponent_name=defender_manor.display_name,
        battle_type="raid",
        attacker_team=[{"name": "A", "guest_id": 1, "template_key": "a"}],
        attacker_troops={},
        defender_team=[{"name": "D", "guest_id": 2, "template_key": "d"}],
        defender_troops={},
        rounds=[],
        losses={"attacker": {}, "defender": {}},
        drops={},
        winner="attacker",
        starts_at=now,
        completed_at=now,
        seed=17,
    )
    RaidRun.objects.create(
        attacker=attacker_manor,
        defender=defender_manor,
        battle_report=report,
        status=RaidRun.Status.RETURNING,
        loot_resources={"silver": 321},
        loot_items={
            "battle_view_mysterious_stone": 2,
            "battle_view_skill_book": 1,
        },
        is_attacker_victory=True,
    )
    Message.objects.create(
        manor=defender_manor,
        kind=Message.Kind.BATTLE,
        title="踢馆战报 - 查询优化",
        battle_report=report,
    )

    assert client.login(username="raid_report_query_defender", password="pass123")
    with CaptureQueriesContext(connection) as captured:
        response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))
        body = response.content.decode("utf-8")

    item_template_queries = [
        query for query in captured.captured_queries if 'from "gameplay_itemtemplate"' in query["sql"].lower()
    ]
    skill_book_queries = [
        query for query in captured.captured_queries if 'from "guests_skillbook"' in query["sql"].lower()
    ]

    assert response.status_code == 200
    assert "神秘石 -2" in body
    assert "战报技能书 -1" in body
    assert len(item_template_queries) == 1
    assert len(skill_book_queries) == 1
