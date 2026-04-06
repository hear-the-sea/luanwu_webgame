from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from gameplay.models import MissionTemplate, PlayerTechnology, WorkAssignment, WorkTemplate
from guests.models import GuestStatus, GuestTemplate
from guilds.models import Guild, GuildMember, GuildMissionRun, GuildMissionTemplate, GuildRaidRun

pytest_plugins = ["tests.arena_views.support"]


@pytest.mark.django_db
def test_refresh_building_upgrades_api_finalizes_due_building(manor_with_user):
    manor, client = manor_with_user
    building = manor.buildings.first()
    assert building is not None
    building.is_upgrading = True
    building.upgrade_complete_at = timezone.now() - timedelta(seconds=1)
    building.save(update_fields=["is_upgrading", "upgrade_complete_at"])

    response = client.post(reverse("gameplay:refresh_building_upgrades_api"))

    assert response.status_code == 200
    building.refresh_from_db()
    assert building.is_upgrading is False
    assert building.upgrade_complete_at is None


@pytest.mark.django_db
def test_refresh_technology_upgrades_api_finalizes_due_technology(manor_with_user):
    manor, client = manor_with_user
    tech = PlayerTechnology.objects.create(
        manor=manor,
        tech_key="dao_attack",
        level=0,
        is_upgrading=True,
        upgrade_complete_at=timezone.now() - timedelta(seconds=1),
    )

    response = client.post(reverse("gameplay:refresh_technology_upgrades_api"))

    assert response.status_code == 200
    tech.refresh_from_db()
    assert tech.level == 1
    assert tech.is_upgrading is False
    assert tech.upgrade_complete_at is None


@pytest.mark.django_db
def test_refresh_production_runtime_api_runs_all_refreshers(manor_with_user, monkeypatch):
    _manor, client = manor_with_user
    calls: list[str] = []

    monkeypatch.setattr(
        "gameplay.views.production.stable_service.refresh_horse_productions",
        lambda manor: calls.append(f"horse:{manor.id}") or 1,
    )
    monkeypatch.setattr(
        "gameplay.views.production.ranch_service.refresh_livestock_productions",
        lambda manor: calls.append(f"livestock:{manor.id}") or 2,
    )
    monkeypatch.setattr(
        "gameplay.views.production.smithy_service.refresh_smelting_productions",
        lambda manor: calls.append(f"smelting:{manor.id}") or 3,
    )
    monkeypatch.setattr(
        "gameplay.views.production.forge_service.refresh_equipment_forgings",
        lambda manor: calls.append(f"forge:{manor.id}") or 4,
    )

    response = client.post(reverse("gameplay:refresh_production_runtime_api"))

    assert response.status_code == 200
    assert [entry.split(":")[0] for entry in calls] == ["horse", "livestock", "smelting", "forge"]


@pytest.mark.django_db
def test_refresh_work_assignments_api_completes_due_assignment(manor_with_user):
    manor, client = manor_with_user
    guest_template = GuestTemplate.objects.create(
        key=f"refresh_work_guest_tpl_{manor.id}",
        name="打工刷新模板",
        archetype="civil",
        rarity="gray",
    )
    guest = manor.guests.create(template=guest_template, status=GuestStatus.WORKING)
    work_template = WorkTemplate.objects.create(
        key=f"refresh_work_template_{manor.id}",
        name="打工刷新岗位",
        tier=WorkTemplate.Tier.JUNIOR,
        required_level=1,
        required_force=0,
        required_intellect=0,
        reward_silver=100,
        work_duration=3600,
        display_order=1,
    )
    assignment = WorkAssignment.objects.create(
        manor=manor,
        guest=guest,
        work_template=work_template,
        status=WorkAssignment.Status.WORKING,
        complete_at=timezone.now() - timedelta(seconds=1),
    )

    response = client.post(reverse("gameplay:refresh_work_assignments_api"))

    assert response.status_code == 200
    assignment.refresh_from_db()
    guest.refresh_from_db()
    assert assignment.status == WorkAssignment.Status.COMPLETED
    assert guest.status == GuestStatus.IDLE


@pytest.mark.django_db
def test_refresh_troop_recruitments_api_delegates_to_service(manor_with_user, monkeypatch):
    manor, client = manor_with_user
    calls = {"count": 0}

    def _refresh(current_manor):
        assert current_manor == manor
        calls["count"] += 1
        return 1

    monkeypatch.setattr("gameplay.views.recruitment.refresh_troop_recruitments", _refresh)

    response = client.post(reverse("gameplay:refresh_troop_recruitments_api"))

    assert response.status_code == 200
    assert calls["count"] == 1


@pytest.mark.django_db
def test_refresh_recruitment_hall_api_delegates_and_invalidates_cache(manor_with_user, monkeypatch):
    manor, client = manor_with_user
    calls: list[str] = []

    monkeypatch.setattr(
        "gameplay.views.inventory.refresh_guest_recruitments",
        lambda current_manor: calls.append(f"refresh:{current_manor.id}") or 1,
    )
    monkeypatch.setattr(
        "gameplay.views.inventory.invalidate_recruitment_hall_cache",
        lambda manor_id: calls.append(f"invalidate:{manor_id}"),
    )

    response = client.post(reverse("gameplay:refresh_recruitment_hall_api"))

    assert response.status_code == 200
    assert calls == [f"refresh:{manor.id}", f"invalidate:{manor.id}"]


@pytest.mark.django_db
def test_refresh_arena_activity_api_delegates_to_service(arena_client, monkeypatch):
    client, manor = arena_client
    calls = {"count": 0}

    def _refresh(current_manor, *, now=None, limit=20):
        assert current_manor == manor
        assert now is None
        assert limit == 20
        calls["count"] += 1
        return 1

    monkeypatch.setattr("gameplay.views.arena.arena_core.refresh_arena_activity", _refresh)

    response = client.post(reverse("gameplay:refresh_arena_activity_api"))

    assert response.status_code == 200
    assert calls["count"] == 1


@pytest.mark.django_db
def test_refresh_mission_runs_api_finalizes_due_mission_run(manor_with_user):
    manor, client = manor_with_user
    mission = MissionTemplate.objects.create(
        key=f"refresh_mission_run_{manor.id}",
        name="运行期任务刷新",
        is_defense=False,
    )
    run = manor.mission_runs.create(
        mission=mission,
        status="active",
        return_at=timezone.now() - timedelta(seconds=1),
    )

    response = client.post(reverse("gameplay:refresh_mission_runs_api"))

    assert response.status_code == 200
    run.refresh_from_db()
    assert run.status == run.Status.COMPLETED


@pytest.mark.django_db
def test_refresh_guild_mission_runs_api_finalizes_due_run(client, django_user_model):
    user = django_user_model.objects.create_user(username="guild_refresh_mission_user", password="pass12345")
    guild = Guild.objects.create(name="帮会任务刷新帮", founder=user, is_active=True)
    GuildMember.objects.create(guild=guild, user=user, position="leader", is_active=True)
    template = GuildMissionTemplate.objects.create(
        key="guild_refresh_mission_task",
        name="帮会巡防",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=600,
        ruby_reward=2,
        recommended_guest_count=2,
        allow_troops=False,
        is_active=True,
        sort_weight=1,
    )
    run = GuildMissionRun.objects.create(
        guild=guild,
        template=template,
        status=GuildMissionRun.Status.ACTIVE,
        selected_guest_count=2,
        ruby_reward=2,
        return_at=timezone.now() - timedelta(seconds=1),
    )
    assert client.login(username="guild_refresh_mission_user", password="pass12345")

    response = client.post(reverse("guilds:refresh_mission_runs_api"))

    assert response.status_code == 200
    run.refresh_from_db()
    assert run.status == GuildMissionRun.Status.COMPLETED


@pytest.mark.django_db
def test_refresh_guild_pvp_activity_api_processes_due_incoming_raid(client, django_user_model):
    defender_user = django_user_model.objects.create_user(username="guild_refresh_pvp_defender", password="pass12345")
    attacker_user = django_user_model.objects.create_user(username="guild_refresh_pvp_attacker", password="pass12345")
    defender_guild = Guild.objects.create(name="帮会守方", founder=defender_user, is_active=True)
    attacker_guild = Guild.objects.create(name="帮会攻方", founder=attacker_user, is_active=True)
    GuildMember.objects.create(guild=defender_guild, user=defender_user, position="leader", is_active=True)
    GuildMember.objects.create(guild=attacker_guild, user=attacker_user, position="leader", is_active=True)
    run = GuildRaidRun.objects.create(
        attacker_guild=attacker_guild,
        defender_guild=defender_guild,
        started_by=attacker_user.guild_membership,
        status=GuildRaidRun.Status.MARCHING,
        selected_guest_count=1,
        travel_time=600,
        battle_at=timezone.now() - timedelta(seconds=1),
    )
    assert client.login(username="guild_refresh_pvp_defender", password="pass12345")

    response = client.post(reverse("guilds:refresh_pvp_activity_api"))

    assert response.status_code == 200
    run.refresh_from_db()
    assert run.status != GuildRaidRun.Status.MARCHING
