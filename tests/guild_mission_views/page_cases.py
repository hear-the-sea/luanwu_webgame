from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from battle.models import TroopTemplate
from guilds.models import GuildMissionRun, GuildMissionTemplate, GuildTroopStorage
from guilds.services import hero_pool as hero_pool_service
from tests.guild_mission_views.support import (
    build_uploaded_gif,
    create_guest,
    create_template,
    create_template_with_avatar,
)


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
    assert 'class="ghp-layout"' not in body
    assert "详情" in body
    assert "门客" in body
    assert "本周 0 / 3" in body


@pytest.mark.django_db
def test_guild_mission_page_list_hides_task_description(guild_member_client):
    client, _user, _guild = guild_member_client
    GuildMissionTemplate.objects.create(
        key="guild_list_without_description",
        name="只看名称任务",
        description="这段简介不该出现在帮会任务列表中",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=600,
        ruby_reward=3,
        recommended_guest_count=1,
        allow_troops=False,
        is_active=True,
        sort_weight=1,
    )

    response = client.get(reverse("guilds:missions"))
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "只看名称任务" in body
    assert "这段简介不该出现在帮会任务列表中" not in body


@pytest.mark.django_db
def test_guild_mission_page_task_name_links_to_selected_detail(guild_member_client):
    client, _user, _guild = guild_member_client
    template = GuildMissionTemplate.objects.create(
        key="guild_name_link_task",
        name="名称可点击任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=600,
        ruby_reward=3,
        recommended_guest_count=1,
        allow_troops=False,
        is_active=True,
        sort_weight=1,
    )

    response = client.get(reverse("guilds:missions"))
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert re.search(
        rf'class="[^"]*tw-mission-name-link[^"]*"[^>]*href="\?mission={re.escape(template.key)}"[^>]*>{re.escape(template.name)}</a>',
        body,
    )


@pytest.mark.django_db
def test_guild_mission_page_uses_responsive_mission_table_column_classes(guild_member_client):
    client, _user, _guild = guild_member_client
    GuildMissionTemplate.objects.create(
        key="guild_responsive_columns_task",
        name="移动端帮会列宽任务",
        description="",
        difficulty="junior",
        task_type="guest",
        base_duration_seconds=600,
        ruby_reward=3,
        recommended_guest_count=1,
        allow_troops=False,
        is_active=True,
        sort_weight=1,
    )

    response = client.get(reverse("guilds:missions"))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert 'class="tw-mission-name-col"' in body
    assert 'class="tw-mission-meta-col text-center"' in body
    assert 'class="tw-mission-action-col text-center"' in body


@pytest.mark.django_db
def test_guild_mission_page_active_run_uses_explicit_refresh_api(guild_member_client):
    client, user, guild = guild_member_client
    template = GuildMissionTemplate.objects.create(
        key="guild_active_run_task",
        name="帮会进行中任务",
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
    GuildMissionRun.objects.create(
        guild=guild,
        template=template,
        started_by=user.guild_membership,
        status=GuildMissionRun.Status.ACTIVE,
        selected_guest_count=1,
        ruby_reward=2,
        return_at=timezone.now() + timedelta(minutes=8),
    )

    response = client.get(reverse("guilds:missions"))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    refresh_url = reverse("guilds:refresh_mission_runs_api")
    assert "js/dashboard.js" in body
    assert "当前帮会出征" in body
    assert "帮会进行中任务" in body
    assert body.count(f'data-refresh-url="{refresh_url}"') == 1
    assert body.count('data-refresh-method="post"') == 1


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
    guest_template = create_template("guild_modal_tpl")
    lineup_guest = create_guest(manor=user.manor, template=guest_template, name="详情门客")
    extra_guest = create_guest(manor=user.manor, template=guest_template, name="非上阵门客")
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
    assert "任务简介" in body
    assert body.count("tw-task-detail-section") >= 3


@pytest.mark.django_db
def test_guild_mission_page_selected_detail_shows_enemy_intel_like_personal_mission(guild_member_client):
    client, _user, _guild = guild_member_client
    enemy_guest = create_template_with_avatar("guild_enemy_guest_tpl", name="敌方先锋")
    enemy_troop = TroopTemplate.objects.create(
        key="guild_enemy_archer",
        name="敌方弓手",
        avatar=build_uploaded_gif("guild_enemy_archer.gif"),
    )
    template = GuildMissionTemplate.objects.create(
        key="guild_enemy_intel_task",
        name="敌情任务",
        description="展示敌方配置",
        difficulty="junior",
        task_type="troop",
        ruby_reward=5,
        recommended_guest_count=2,
        allow_troops=True,
        enemy_guests=[{"key": enemy_guest.key, "label": enemy_guest.name}],
        enemy_troops={enemy_troop.key: 18},
        is_active=True,
    )

    response = client.get(f"{reverse('guilds:missions')}?mission={template.key}")

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "敌人情报" in body
    assert "tw-enemy-grid" in body
    assert enemy_guest.avatar.url in body
    assert "敌方先锋" in body
    assert enemy_troop.avatar.url in body
    assert "×18" in body


@pytest.mark.django_db
def test_guild_mission_page_selected_detail_supports_template_key_enemy_guest_entries(guild_member_client):
    client, _user, _guild = guild_member_client
    enemy_guest = create_template_with_avatar("guild_enemy_template_key_tpl", name="模板键敌将")
    template = GuildMissionTemplate.objects.create(
        key="guild_enemy_template_key_task",
        name="模板键敌情任务",
        description="展示 template_key 敌方门客",
        difficulty="junior",
        task_type="guest",
        ruby_reward=3,
        recommended_guest_count=1,
        allow_troops=False,
        enemy_guests=[{"template_key": enemy_guest.key}],
        is_active=True,
    )

    response = client.get(f"{reverse('guilds:missions')}?mission={template.key}")

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert enemy_guest.avatar.url in body


@pytest.mark.django_db
def test_guild_mission_page_selected_detail_reuses_avatar_cards_for_dispatch_loadout(guild_member_client):
    client, user, guild = guild_member_client
    leader_member = user.guild_membership
    guest_template = create_template_with_avatar("guild_dispatch_guest_tpl", name="门面门客")
    lineup_guest = create_guest(manor=user.manor, template=guest_template, name="门面门客")
    lineup_entry = hero_pool_service.submit_hero_pool_entry(
        leader_member,
        guest_id=lineup_guest.id,
        slot_index=1,
    ).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=user, pool_entry_id=lineup_entry.id)
    troop_template = TroopTemplate.objects.create(
        key="guild_dispatch_archer",
        name="门面弓手",
        avatar=build_uploaded_gif("guild_dispatch_archer.gif"),
    )
    GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=24)
    template = GuildMissionTemplate.objects.create(
        key="guild_dispatch_card_task",
        name="出征样式任务",
        description="展示头像卡片",
        difficulty="intermediate",
        task_type="troop",
        ruby_reward=6,
        recommended_guest_count=1,
        allow_troops=True,
        is_active=True,
    )

    response = client.get(f"{reverse('guilds:missions')}?mission={template.key}")

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "tw-guest-avatar" in body
    assert guest_template.avatar.url in body
    assert "tw-troop-avatar" in body
    assert troop_template.avatar.url in body


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
