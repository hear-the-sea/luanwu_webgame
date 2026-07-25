from __future__ import annotations

import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from gameplay.models import ItemTemplate, Message, RaidRun
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestTemplate, Skill, SkillBook
from tests.battle_report_view.support import create_report


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

    report = create_report(manor=attacker_manor, opponent_name=defender_manor.display_name, battle_type="raid", seed=3)
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
    assert response.context["report_title"] == "踢馆战报 - 防守失败"


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

    report = create_report(
        manor=manor,
        opponent_name="防守任务",
        battle_type="task1",
        attacker_team=[{"name": "敌将", "guest_id": None, "template_key": "enemy"}],
        defender_team=[{"name": my_guest.display_name, "guest_id": my_guest.id, "template_key": template.key}],
        winner="defender",
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

    report = create_report(manor=attacker_manor, opponent_name=defender_manor.display_name, battle_type="raid", seed=7)
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
    assert "未知奖励 -2" in body
    assert "mysterious_stone" not in body
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

    report = create_report(manor=attacker_manor, opponent_name=defender_manor.display_name, battle_type="raid", seed=17)
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
