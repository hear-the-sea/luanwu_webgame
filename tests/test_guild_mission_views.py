from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.test import Client, override_settings
from django.urls import reverse

from battle.models import TroopTemplate
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate
from guilds.models import Guild, GuildMember, GuildMissionRun, GuildMissionTemplate, GuildTroopStorage
from guilds.services import hero_pool as hero_pool_service


def _create_user_with_manor(django_user_model, username: str):
    user = django_user_model.objects.create_user(username=username, password="pass12345")
    manor = ensure_manor(user)
    return user, manor


def _create_template(key: str) -> GuestTemplate:
    return GuestTemplate.objects.create(
        key=key,
        name=f"模板{key}",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
    )


def _create_guest(*, manor, template: GuestTemplate, name: str) -> Guest:
    return Guest.objects.create(
        manor=manor,
        template=template,
        custom_name=name,
        level=20,
        force=120,
        intellect=85,
        defense_stat=100,
        agility=90,
        luck=60,
    )


@pytest.fixture
def guild_member_client(django_user_model):
    user, _manor = _create_user_with_manor(django_user_model, "guild_mission_view_leader")
    guild = Guild.objects.create(name="帮会任务视图帮", founder=user, is_active=True)
    GuildMember.objects.create(guild=guild, user=user, position="leader", is_active=True)

    client = Client()
    assert client.login(username="guild_mission_view_leader", password="pass12345")
    return client, user, guild


@pytest.mark.django_db
def test_guild_mission_page_renders_tabbed_task_list_without_troop_pool(guild_member_client):
    client, _user, _guild = guild_member_client
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

    response = client.get(reverse("guilds:missions"))
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "视图任务" in body
    assert "tw-mission-tabs" in body
    assert "帮会护院池" not in body
    assert "当前帮会出征" not in body
    assert "当前上阵门客" not in body
    assert "门客池" not in body
    assert "详情" in body
    assert "门客" in body


@pytest.mark.django_db
def test_guild_mission_page_renders_selected_task_detail_modal(guild_member_client):
    client, user, guild = guild_member_client
    leader_member = user.guild_membership
    template = GuildMissionTemplate.objects.create(
        key="guild_modal_task",
        name="详情任务",
        description="显示详情",
        difficulty="intermediate",
        task_type="troop",
        base_duration_seconds=900,
        ruby_reward=20,
        recommended_guest_count=1,
        allow_troops=True,
        is_active=True,
        sort_weight=2,
    )
    troop_template = TroopTemplate.objects.create(key="guild_modal_archer", name="详情弓手")
    GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=12)
    guest_template = _create_template("guild_modal_tpl")
    lineup_guest = _create_guest(manor=user.manor, template=guest_template, name="详情门客")
    extra_guest = _create_guest(manor=user.manor, template=guest_template, name="非上阵门客")
    lineup_entry = hero_pool_service.submit_hero_pool_entry(
        leader_member,
        guest_id=lineup_guest.id,
        slot_index=1,
    ).entry
    hero_pool_service.submit_hero_pool_entry(
        leader_member,
        guest_id=extra_guest.id,
        slot_index=2,
    )
    hero_pool_service.add_lineup_entry(guild=guild, operator=user, pool_entry_id=lineup_entry.id)

    response = client.get(f"{reverse('guilds:missions')}?mission={template.key}")
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "tw-modal-overlay" in body
    assert "帮会任务情报" in body
    assert "选择门客" in body
    assert "配置护院" in body
    assert "详情门客" in body
    assert "非上阵门客" not in body
    assert "护院" in body


@pytest.mark.django_db
def test_guild_mission_task_type_display_matches_personal_mission_style():
    guest_template = GuildMissionTemplate(
        key="guild_guest_type_task",
        name="门客任务",
        difficulty="junior",
        task_type="guest",
        allow_troops=False,
    )
    troop_template = GuildMissionTemplate(
        key="guild_troop_type_task",
        name="护院任务",
        difficulty="intermediate",
        task_type="troop",
        allow_troops=True,
    )
    defense_template = GuildMissionTemplate(
        key="guild_defense_type_task",
        name="防守任务",
        difficulty="advanced",
        task_type="defense",
        allow_troops=True,
    )

    assert guest_template.get_task_type_display() == "门客"
    assert troop_template.get_task_type_display() == "护院"
    assert defense_template.get_task_type_display() == "防守"


@pytest.mark.django_db
def test_guild_mission_page_shows_scaled_duration_for_selected_task(guild_member_client):
    client, _user, _guild = guild_member_client
    template = GuildMissionTemplate.objects.create(
        key="guild_modal_scaled_duration_task",
        name="倍率详情任务",
        description="显示倍率耗时",
        difficulty="intermediate",
        task_type="guest",
        base_duration_seconds=900,
        ruby_reward=20,
        recommended_guest_count=1,
        allow_troops=False,
        is_active=True,
        sort_weight=5,
    )

    with override_settings(GAME_TIME_MULTIPLIER=5):
        response = client.get(f"{reverse('guilds:missions')}?mission={template.key}")

    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "预计耗时 180 秒" in body


@pytest.mark.django_db
def test_guild_mission_page_selects_matching_difficulty_tab_for_selected_mission(guild_member_client):
    client, _user, _guild = guild_member_client
    template = GuildMissionTemplate.objects.create(
        key="guild_selected_intermediate_task",
        name="指定中级帮会任务",
        description="",
        difficulty="intermediate",
        task_type="troop",
        base_duration_seconds=900,
        ruby_reward=12,
        recommended_guest_count=1,
        allow_troops=True,
        is_active=True,
        sort_weight=3,
    )

    response = client.get(f"{reverse('guilds:missions')}?mission={template.key}")

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert '<button class="tw-trade-tab active" data-tab="intermediate">中级任务</button>' in body
    assert '<div id="tab-intermediate" class="mission-tab-content active">' in body


@pytest.mark.django_db
def test_non_manager_cannot_launch_guild_mission(django_user_model):
    leader, _leader_manor = _create_user_with_manor(django_user_model, "guild_mission_launch_guard_leader")
    member_user, _member_manor = _create_user_with_manor(django_user_model, "guild_mission_launch_guard_member")
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
    guest = _create_guest(
        manor=user.manor,
        template=_create_template("guild_launch_view_tpl"),
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
def test_guild_mission_page_uses_manager_only_retreat_button(client, django_user_model):
    leader, _leader_manor = _create_user_with_manor(django_user_model, "guild_mission_retreat_button_leader")
    member_user, _member_manor = _create_user_with_manor(django_user_model, "guild_mission_retreat_button_member")
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
    guest = _create_guest(
        manor=user.manor,
        template=_create_template("guild_retreat_view_tpl"),
        name="撤回门客",
    )
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=user, pool_entry_id=entry.id)
    run = hero_pool_service  # keep import usage local for lint-neutral structure
    del run

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
