from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse

from guilds.models import Guild, GuildMember, GuildMissionRun, GuildMissionTemplate
from tests.guild_mission_views.support import create_user_with_manor


@pytest.mark.django_db
def test_non_manager_cannot_launch_guild_mission(django_user_model):
    leader, _leader_manor = create_user_with_manor(django_user_model, "guild_mission_launch_guard_leader")
    member_user, _member_manor = create_user_with_manor(django_user_model, "guild_mission_launch_guard_member")
    guild = Guild.objects.create(name="帮会任务权限帮", founder=leader, is_active=True)
    GuildMember.objects.create(guild=guild, user=leader, position="leader", is_active=True)
    GuildMember.objects.create(guild=guild, user=member_user, position="member", is_active=True)
    GuildMissionTemplate.objects.create(
        key="guild_view_task",
        name="视图任务",
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

    client = Client()
    assert client.login(username="guild_mission_launch_guard_member", password="pass12345")

    response = client.post(reverse("guilds:mission_launch"), {"template_key": "guild_view_task"}, follow=True)

    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert response.redirect_chain
    assert "只有管理员/帮主可以发起帮会任务" in messages[-1]


@pytest.mark.django_db
def test_guild_mission_page_uses_manager_only_retreat_button(client, django_user_model):
    leader, _leader_manor = create_user_with_manor(django_user_model, "guild_mission_retreat_button_leader")
    member_user, _member_manor = create_user_with_manor(django_user_model, "guild_mission_retreat_button_member")
    guild = Guild.objects.create(name="撤回按钮帮", founder=leader, is_active=True)
    GuildMember.objects.create(guild=guild, user=leader, position="leader", is_active=True)
    GuildMember.objects.create(guild=guild, user=member_user, position="member", is_active=True)
    template = GuildMissionTemplate.objects.create(
        key="guild_retreat_button_task",
        name="按钮任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=600,
        ruby_reward=2,
        recommended_guest_count=1,
        allow_troops=False,
        is_active=True,
        sort_weight=4,
    )
    GuildMissionRun.objects.create(
        guild=guild,
        template=template,
        status="active",
        selected_guest_count=1,
        ruby_reward=2,
    )

    assert client.login(username="guild_mission_retreat_button_member", password="pass12345")
    response = client.get(reverse("guilds:missions"))

    body = response.content.decode("utf-8")
    assert response.status_code == 200
    assert "撤回" not in body
