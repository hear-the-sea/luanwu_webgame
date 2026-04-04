from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from gameplay.services.manor.core import ensure_manor
from guilds.models import Guild, GuildMember, GuildMissionRun, GuildMissionTemplate


@pytest.mark.django_db
def test_home_page_shows_active_guild_mission_event(client, django_user_model):
    user = django_user_model.objects.create_user(username="guild_home_event_user", password="pass12345")
    ensure_manor(user)
    guild = Guild.objects.create(name="首页帮会事件帮", founder=user, is_active=True)
    GuildMember.objects.create(guild=guild, user=user, position="leader", is_active=True)
    template = GuildMissionTemplate.objects.create(
        key="guild_home_event_task",
        name="首页巡防",
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
    GuildMissionRun.objects.create(
        guild=guild,
        template=template,
        status="active",
        selected_guest_count=2,
        ruby_reward=2,
        return_at=timezone.now() + timedelta(minutes=5),
    )
    assert client.login(username="guild_home_event_user", password="pass12345")

    response = client.get("/")

    body = response.content.decode("utf-8")
    assert "帮会出征：首页巡防" in body
    assert reverse("guilds:missions") in body


@pytest.mark.django_db
def test_home_page_hides_overdue_guild_mission_without_refresh_side_effect(client, django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="guild_home_event_refresh_user", password="pass12345")
    ensure_manor(user)
    guild = Guild.objects.create(name="首页帮会事件刷新帮", founder=user, is_active=True)
    GuildMember.objects.create(guild=guild, user=user, position="leader", is_active=True)
    template = GuildMissionTemplate.objects.create(
        key="guild_home_event_refresh_task",
        name="过期巡防",
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
        status="active",
        selected_guest_count=2,
        ruby_reward=2,
        return_at=timezone.now() - timedelta(seconds=1),
    )
    assert client.login(username="guild_home_event_refresh_user", password="pass12345")

    monkeypatch.setattr(
        "guilds.services.guild_missions.finalize_guild_mission_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("home page should not finalize guild missions")),
    )

    response = client.get("/")

    body = response.content.decode("utf-8")
    assert "帮会出征：过期巡防" not in body
    run.refresh_from_db()
    assert run.status == GuildMissionRun.Status.ACTIVE


@pytest.mark.django_db
def test_home_page_shows_guild_mission_retreat_for_manage_member(client, django_user_model):
    user = django_user_model.objects.create_user(username="guild_home_event_manage_user", password="pass12345")
    ensure_manor(user)
    guild = Guild.objects.create(name="首页帮会撤回帮", founder=user, is_active=True)
    GuildMember.objects.create(guild=guild, user=user, position="leader", is_active=True)
    template = GuildMissionTemplate.objects.create(
        key="guild_home_event_manage_task",
        name="首页撤回巡防",
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
        return_at=timezone.now() + timedelta(minutes=5),
    )
    assert client.login(username="guild_home_event_manage_user", password="pass12345")

    response = client.get("/")

    body = response.content.decode("utf-8")
    assert reverse("guilds:mission_retreat") in body
    assert f'value="{run.id}"' in body
    assert ">撤回<" in body


@pytest.mark.django_db
def test_home_page_keeps_guild_mission_view_for_regular_member(client, django_user_model):
    leader = django_user_model.objects.create_user(username="guild_home_event_member_leader", password="pass12345")
    user = django_user_model.objects.create_user(username="guild_home_event_member_user", password="pass12345")
    ensure_manor(user)
    guild = Guild.objects.create(name="首页帮会成员帮", founder=leader, is_active=True)
    GuildMember.objects.create(guild=guild, user=leader, position="leader", is_active=True)
    GuildMember.objects.create(guild=guild, user=user, position="member", is_active=True)
    template = GuildMissionTemplate.objects.create(
        key="guild_home_event_member_task",
        name="成员查看巡防",
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
    GuildMissionRun.objects.create(
        guild=guild,
        template=template,
        status=GuildMissionRun.Status.ACTIVE,
        selected_guest_count=2,
        ruby_reward=2,
        return_at=timezone.now() + timedelta(minutes=5),
    )
    assert client.login(username="guild_home_event_member_user", password="pass12345")

    response = client.get("/")

    body = response.content.decode("utf-8")
    assert reverse("guilds:missions") in body
    assert ">查看<" in body
    assert ">撤回<" not in body


@pytest.mark.django_db
def test_manage_member_can_retreat_guild_mission_from_home_event(client, django_user_model):
    user = django_user_model.objects.create_user(username="guild_home_event_retreat_user", password="pass12345")
    ensure_manor(user)
    guild = Guild.objects.create(name="首页帮会撤回执行帮", founder=user, is_active=True)
    GuildMember.objects.create(guild=guild, user=user, position="leader", is_active=True)
    template = GuildMissionTemplate.objects.create(
        key="guild_home_event_retreat_task",
        name="首页撤回执行巡防",
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
        return_at=timezone.now() + timedelta(minutes=5),
    )
    assert client.login(username="guild_home_event_retreat_user", password="pass12345")

    response = client.post(
        reverse("guilds:mission_retreat"),
        {"run_id": str(run.id)},
        follow=True,
    )

    run.refresh_from_db()
    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert response.redirect_chain
    assert run.status == GuildMissionRun.Status.RETREATED
    assert messages[-1] == "帮会任务已撤回"
