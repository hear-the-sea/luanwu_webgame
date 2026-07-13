from __future__ import annotations

import pytest
from django.urls import reverse

from gameplay.models import ArenaCoopEntry, ArenaCoopEvent, ArenaEntry, ArenaMatch, ArenaTournament, Message
from gameplay.services.manor.core import ensure_manor
from guilds.models import Guild, GuildMember, GuildMissionRun, GuildMissionTemplate, GuildRaidRun
from tests.battle_report_view.support import create_report


def _round_with_side_states():
    return [
        {
            "round": 1,
            "events": [
                {
                    "side": "attacker",
                    "order": 1,
                    "actor": "进攻门客",
                    "target": "防守门客",
                    "damage": 100,
                    "skills": [],
                    "status_inflicted": [],
                    "kills": 0,
                    "target_defeated": False,
                    "actor_state": {
                        "side": "attacker",
                        "percent": 80,
                        "status": "healthy",
                        "status_label": "状态充足",
                    },
                    "target_state": {
                        "side": "defender",
                        "percent": 35,
                        "status": "warning",
                        "status_label": "状态偏低",
                    },
                }
            ],
        }
    ]


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

    report = create_report(
        manor=attacker_manor,
        opponent_name=defender_manor.display_name,
        battle_type="arena",
        rounds=_round_with_side_states(),
    )
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
    body = response.content.decode("utf-8")
    assert 'data-unit-state-side="defender"' in body
    assert 'data-unit-state-side="attacker"' not in body


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

    report = create_report(
        manor=attacker_manor,
        opponent_name=defender_manor.display_name,
        battle_type="arena",
        rounds=_round_with_side_states(),
    )
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
    assert "battle-unit-state" not in response.content.decode("utf-8")


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


@pytest.mark.django_db
def test_guild_mission_report_uses_attacker_perspective_for_non_participant_member(client, django_user_model):
    leader_user = django_user_model.objects.create_user(
        username="guild_mission_report_leader_view",
        password="pass123",
    )
    member_user = django_user_model.objects.create_user(
        username="guild_mission_report_member_view",
        password="pass123",
    )
    leader_manor = ensure_manor(leader_user)
    member_manor = ensure_manor(member_user)
    guild = Guild.objects.create(name="战报视角帮会", founder=leader_user)
    leader_member = GuildMember.objects.create(guild=guild, user=leader_user, position="leader", is_active=True)
    GuildMember.objects.create(guild=guild, user=member_user, position="member", is_active=True)
    template = GuildMissionTemplate.objects.create(
        key="guild_report_view_case",
        name="围剿魔教据点",
        description="战报视角回归测试",
        recommended_guest_count=1,
        base_duration_seconds=60,
        enemy_guests=[{"key": "arena_gl_top_zhang_wuji_boss"}],
    )

    report = create_report(
        manor=leader_manor,
        opponent_name=template.name,
        battle_type="guild_mission",
        attacker_team=[{"name": "我方门客", "guest_id": 101, "template_key": "a"}],
        defender_team=[{"name": "张无忌", "guest_id": None, "template_key": "arena_gl_top_zhang_wuji_boss"}],
        seed=11,
    )
    GuildMissionRun.objects.create(
        guild=guild,
        template=template,
        started_by=leader_member,
        status=GuildMissionRun.Status.COMPLETED,
        selected_guest_count=1,
        guest_ids=[101],
        guest_snapshots=[],
        troop_loadout={},
        battle_report=report,
    )
    Message.objects.create(
        manor=member_manor,
        kind=Message.Kind.BATTLE,
        title="帮会任务战报",
        battle_report=report,
    )

    assert client.login(username="guild_mission_report_member_view", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))

    assert response.status_code == 200
    assert response.context["player_side"] == "attacker"
    assert response.context["left_team_title"] == "我方"
    assert response.context["attacker_team_display"][0]["name"] == "我方门客"
    assert response.context["defender_team_display"][0]["name"] == "张无忌"


@pytest.mark.django_db
def test_guild_raid_report_uses_attacker_perspective_for_attacker_guild_member(client, django_user_model):
    leader_user = django_user_model.objects.create_user(
        username="guild_raid_report_leader_view",
        password="pass123",
    )
    attacker_member_user = django_user_model.objects.create_user(
        username="guild_raid_report_attacker_member_view",
        password="pass123",
    )
    defender_user = django_user_model.objects.create_user(
        username="guild_raid_report_defender_view",
        password="pass123",
    )
    leader_manor = ensure_manor(leader_user)
    attacker_member_manor = ensure_manor(attacker_member_user)
    ensure_manor(defender_user)

    attacker_guild = Guild.objects.create(name="进攻战报帮会", founder=leader_user)
    defender_guild = Guild.objects.create(name="防守战报帮会", founder=defender_user)
    leader_member = GuildMember.objects.create(
        guild=attacker_guild, user=leader_user, position="leader", is_active=True
    )
    GuildMember.objects.create(guild=attacker_guild, user=attacker_member_user, position="member", is_active=True)
    GuildMember.objects.create(guild=defender_guild, user=defender_user, position="leader", is_active=True)

    report = create_report(
        manor=leader_manor,
        opponent_name=defender_guild.name,
        battle_type="guild_raid",
        attacker_team=[{"name": "进攻门客", "guest_id": 201, "template_key": "a"}],
        defender_team=[{"name": "守方门客", "guest_id": 301, "template_key": "d"}],
        seed=13,
    )
    GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=leader_member,
        status=GuildRaidRun.Status.RETURNING,
        selected_guest_count=1,
        guest_ids=[201],
        guest_snapshots=[],
        troop_loadout={},
        travel_time=60,
        battle_report=report,
        is_attacker_victory=True,
    )
    Message.objects.create(
        manor=attacker_member_manor,
        kind=Message.Kind.BATTLE,
        title="帮会掠夺战报 - 进攻胜利",
        battle_report=report,
    )

    assert client.login(username="guild_raid_report_attacker_member_view", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))

    assert response.status_code == 200
    assert response.context["player_side"] == "attacker"
    assert response.context["attacker_team_display"][0]["name"] == "进攻门客"
    assert response.context["defender_team_display"][0]["name"] == "守方门客"


@pytest.mark.django_db
def test_guild_raid_report_uses_defender_perspective_for_defender_guild_member(client, django_user_model):
    leader_user = django_user_model.objects.create_user(
        username="guild_raid_report_leader_view_2",
        password="pass123",
    )
    defender_user = django_user_model.objects.create_user(
        username="guild_raid_report_defender_view_2",
        password="pass123",
    )
    leader_manor = ensure_manor(leader_user)
    defender_manor = ensure_manor(defender_user)

    attacker_guild = Guild.objects.create(name="进攻战报帮会二", founder=leader_user)
    defender_guild = Guild.objects.create(name="防守战报帮会二", founder=defender_user)
    leader_member = GuildMember.objects.create(
        guild=attacker_guild, user=leader_user, position="leader", is_active=True
    )
    GuildMember.objects.create(guild=defender_guild, user=defender_user, position="leader", is_active=True)

    report = create_report(
        manor=leader_manor,
        opponent_name=defender_guild.name,
        battle_type="guild_raid",
        attacker_team=[{"name": "进攻门客", "guest_id": 202, "template_key": "a"}],
        defender_team=[{"name": "守方门客", "guest_id": 302, "template_key": "d"}],
        seed=14,
    )
    GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=leader_member,
        status=GuildRaidRun.Status.RETURNING,
        selected_guest_count=1,
        guest_ids=[202],
        guest_snapshots=[],
        troop_loadout={},
        travel_time=60,
        battle_report=report,
        is_attacker_victory=True,
    )
    Message.objects.create(
        manor=defender_manor,
        kind=Message.Kind.BATTLE,
        title="帮会掠夺战报 - 防守失利",
        battle_report=report,
    )

    assert client.login(username="guild_raid_report_defender_view_2", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))

    assert response.status_code == 200
    assert response.context["player_side"] == "defender"
    assert response.context["attacker_team_display"][0]["name"] == "守方门客"
    assert response.context["defender_team_display"][0]["name"] == "进攻门客"


@pytest.mark.django_db
def test_guild_raid_report_keeps_defender_perspective_for_historical_defender_message_recipient(
    client,
    django_user_model,
):
    leader_user = django_user_model.objects.create_user(
        username="guild_raid_report_leader_view_3",
        password="pass123",
    )
    defender_user = django_user_model.objects.create_user(
        username="guild_raid_report_defender_view_3",
        password="pass123",
    )
    leader_manor = ensure_manor(leader_user)
    defender_manor = ensure_manor(defender_user)

    attacker_guild = Guild.objects.create(name="进攻战报帮会三", founder=leader_user)
    defender_guild = Guild.objects.create(name="防守战报帮会三", founder=defender_user)
    leader_member = GuildMember.objects.create(
        guild=attacker_guild, user=leader_user, position="leader", is_active=True
    )
    GuildMember.objects.create(guild=defender_guild, user=defender_user, position="leader", is_active=False)

    report = create_report(
        manor=leader_manor,
        opponent_name=defender_guild.name,
        battle_type="guild_raid",
        attacker_team=[{"name": "进攻门客", "guest_id": 203, "template_key": "a"}],
        defender_team=[{"name": "守方门客", "guest_id": 303, "template_key": "d"}],
        seed=15,
    )
    GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=leader_member,
        status=GuildRaidRun.Status.RETURNING,
        selected_guest_count=1,
        guest_ids=[203],
        guest_snapshots=[],
        troop_loadout={},
        travel_time=60,
        battle_report=report,
        is_attacker_victory=True,
    )
    Message.objects.create(
        manor=defender_manor,
        kind=Message.Kind.BATTLE,
        title="帮会掠夺战报 - 防守失利",
        battle_report=report,
    )

    assert client.login(username="guild_raid_report_defender_view_3", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))

    assert response.status_code == 200
    assert response.context["player_side"] == "defender"
    assert response.context["attacker_team_display"][0]["name"] == "守方门客"
    assert response.context["defender_team_display"][0]["name"] == "进攻门客"


@pytest.mark.django_db
def test_guild_raid_report_shows_defender_losses_for_defender_message_recipient(client, django_user_model):
    leader_user = django_user_model.objects.create_user(
        username="guild_raid_report_leader_view_4",
        password="pass123",
    )
    defender_user = django_user_model.objects.create_user(
        username="guild_raid_report_defender_view_4",
        password="pass123",
    )
    leader_manor = ensure_manor(leader_user)
    defender_manor = ensure_manor(defender_user)

    attacker_guild = Guild.objects.create(name="进攻战报帮会四", founder=leader_user)
    defender_guild = Guild.objects.create(name="防守战报帮会四", founder=defender_user)
    leader_member = GuildMember.objects.create(
        guild=attacker_guild, user=leader_user, position="leader", is_active=True
    )
    GuildMember.objects.create(guild=defender_guild, user=defender_user, position="leader", is_active=True)

    report = create_report(
        manor=leader_manor,
        opponent_name=defender_guild.name,
        battle_type="guild_raid",
        attacker_team=[{"name": "进攻门客", "guest_id": 204, "template_key": "a"}],
        defender_team=[{"name": "守方门客", "guest_id": 304, "template_key": "d"}],
        seed=16,
    )
    GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=leader_member,
        status=GuildRaidRun.Status.RETURNING,
        selected_guest_count=1,
        guest_ids=[204],
        guest_snapshots=[],
        troop_loadout={},
        travel_time=60,
        battle_report=report,
        loot_silver=321,
        loot_items={"mysterious_stone": 2},
        battle_rewards={"capture": {"guest_name": "赵云", "from": "defender"}},
        is_attacker_victory=True,
    )
    Message.objects.create(
        manor=defender_manor,
        kind=Message.Kind.BATTLE,
        title="帮会掠夺战报 - 防守失利",
        battle_report=report,
    )

    assert client.login(username="guild_raid_report_defender_view_4", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "战斗损失" in body
    assert "银两 -321" in body
    assert "mysterious_stone -2" in body
    assert "门客被俘（赵云）" in body


@pytest.mark.django_db
def test_guild_raid_report_shows_attacker_rewards_for_attacker_message_recipient(client, django_user_model):
    leader_user = django_user_model.objects.create_user(
        username="guild_raid_report_leader_view_5",
        password="pass123",
    )
    attacker_member_user = django_user_model.objects.create_user(
        username="guild_raid_report_attacker_member_view_5",
        password="pass123",
    )
    defender_user = django_user_model.objects.create_user(
        username="guild_raid_report_defender_view_5",
        password="pass123",
    )
    leader_manor = ensure_manor(leader_user)
    attacker_member_manor = ensure_manor(attacker_member_user)
    ensure_manor(defender_user)

    attacker_guild = Guild.objects.create(name="进攻战报帮会五", founder=leader_user)
    defender_guild = Guild.objects.create(name="防守战报帮会五", founder=defender_user)
    leader_member = GuildMember.objects.create(
        guild=attacker_guild, user=leader_user, position="leader", is_active=True
    )
    GuildMember.objects.create(guild=attacker_guild, user=attacker_member_user, position="member", is_active=True)
    GuildMember.objects.create(guild=defender_guild, user=defender_user, position="leader", is_active=True)

    report = create_report(
        manor=leader_manor,
        opponent_name=defender_guild.name,
        battle_type="guild_raid",
        attacker_team=[{"name": "进攻门客", "guest_id": 205, "template_key": "a"}],
        defender_team=[{"name": "守方门客", "guest_id": 305, "template_key": "d"}],
        seed=17,
    )
    GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=leader_member,
        status=GuildRaidRun.Status.RETURNING,
        selected_guest_count=1,
        guest_ids=[205],
        guest_snapshots=[],
        troop_loadout={},
        travel_time=60,
        battle_report=report,
        loot_silver=456,
        loot_items={"mysterious_stone": 3},
        battle_rewards={"exp_fruit": 2, "equipment": {"bronze_sword": 1}},
        is_attacker_victory=True,
    )
    Message.objects.create(
        manor=attacker_member_manor,
        kind=Message.Kind.BATTLE,
        title="帮会掠夺战报 - 进攻胜利",
        battle_report=report,
    )

    assert client.login(username="guild_raid_report_attacker_member_view_5", password="pass123")
    response = client.get(reverse("battle:report_detail", kwargs={"pk": report.pk}))
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "战斗掉落" in body
    assert "银两 +456" in body
    assert "mysterious_stone +3" in body
    assert "experience_fruit +2" in body
    assert "bronze_sword +1" in body
