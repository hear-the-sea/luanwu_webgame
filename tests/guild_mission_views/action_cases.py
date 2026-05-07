from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from guilds.models import GuildMissionRun, GuildMissionTemplate
from guilds.services import hero_pool as hero_pool_service
from tests.guild_mission_views.support import create_guest, create_template


@pytest.mark.django_db(transaction=True)
def test_manager_can_launch_guild_mission_and_redirect_back(guild_member_client):
    client, user, guild = guild_member_client
    leader_member = user.guild_membership
    GuildMissionTemplate.objects.create(
        key="guild_launch_view_task",
        name="发起视图任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=600,
        ruby_reward=2,
        recommended_guest_count=1,
        allow_troops=False,
        is_active=True,
        sort_weight=1,
    )
    guest = create_guest(
        manor=user.manor,
        template=create_template("guild_launch_view_tpl"),
        name="出征门客",
    )
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=user, pool_entry_id=entry.id)

    response = client.post(
        reverse("guilds:mission_launch"),
        {"template_key": "guild_launch_view_task", "pool_entry_ids": [str(entry.id)]},
        follow=True,
    )

    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert response.redirect_chain
    assert messages[-1] == "帮会任务已出征"
    assert GuildMissionRun.objects.filter(guild=guild, status="active").exists()


@pytest.mark.django_db
def test_manager_can_retreat_guild_mission(guild_member_client):
    client, user, guild = guild_member_client
    leader_member = user.guild_membership
    GuildMissionTemplate.objects.create(
        key="guild_retreat_view_task",
        name="撤回视图任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=600,
        ruby_reward=2,
        recommended_guest_count=1,
        allow_troops=False,
        is_active=True,
        sort_weight=2,
    )
    guest = create_guest(
        manor=user.manor,
        template=create_template("guild_retreat_view_tpl"),
        name="撤回门客",
    )
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=user, pool_entry_id=entry.id)

    client.post(
        reverse("guilds:mission_launch"),
        {"template_key": "guild_retreat_view_task", "pool_entry_ids": [str(entry.id)]},
        follow=True,
    )
    active_run = GuildMissionRun.objects.get(guild=guild, status="active")

    response = client.post(reverse("guilds:mission_retreat"), {"run_id": str(active_run.id)}, follow=True)

    messages = [str(message) for message in get_messages(response.wsgi_request)]
    active_run.refresh_from_db()
    assert response.redirect_chain
    assert messages[-1] == "帮会任务已撤回"
    assert active_run.status == "retreated"
