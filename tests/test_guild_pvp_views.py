from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from battle.models import TroopTemplate
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate
from guilds.models import Guild, GuildMember, GuildRaidRun, GuildTroopStorage
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
    user, _manor = _create_user_with_manor(django_user_model, "guild_pvp_view_leader")
    guild = Guild.objects.create(name="帮会PVP视图帮", founder=user, is_active=True, level=5, silver=50000)
    GuildMember.objects.create(guild=guild, user=user, position="leader", is_active=True)

    client = Client()
    assert client.login(username="guild_pvp_view_leader", password="pass12345")
    return client, user, guild


@pytest.mark.django_db
def test_guild_pvp_page_lists_attackable_targets(guild_member_client, django_user_model):
    client, _user, guild = guild_member_client
    other_user, _other_manor = _create_user_with_manor(django_user_model, "guild_pvp_view_target")
    target_guild = Guild.objects.create(name="可攻打目标帮", founder=other_user, is_active=True, level=guild.level + 1)
    GuildMember.objects.create(guild=target_guild, user=other_user, position="leader", is_active=True)

    response = client.get(reverse("guilds:pvp"))
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "帮会PVP" in body
    assert "可攻打目标帮" in body
    assert "选择目标帮会" in body
    assert "选择门客" in body


@pytest.mark.django_db
def test_guild_pvp_page_uses_list_detail_layout_and_hides_old_hint_blocks(guild_member_client, django_user_model):
    client, user, guild = guild_member_client
    leader_member = user.guild_membership

    other_user, _other_manor = _create_user_with_manor(django_user_model, "guild_pvp_view_layout_target")
    target_guild = Guild.objects.create(name="界面目标帮", founder=other_user, is_active=True, level=guild.level + 2)
    GuildMember.objects.create(guild=target_guild, user=other_user, position="leader", is_active=True)

    guest = _create_guest(manor=user.manor, template=_create_template("guild_pvp_layout_tpl"), name="界面门客")
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=user, pool_entry_id=entry.id)

    troop_template = TroopTemplate.objects.create(key="guild_pvp_layout_guard", name="界面护院")
    GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=9)

    response = client.get(reverse("guilds:pvp"))
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "搜索帮会名称" in body
    assert "全部区域" in body
    assert "北俱芦洲" in body
    assert ">全部<" in body
    assert ">可进攻<" in body
    assert ">不可进攻<" in body
    assert "发起帮会出征" in body
    assert "gpvp-submit-bar" not in body
    assert "gpvp-target-detail" not in body
    assert "data-target-detail" not in body
    assert "data-target-confirm" not in body
    assert "管理员/帮主可发起帮会进攻" not in body
    assert "今日主动进攻" not in body
    assert "当前出征" not in body
    assert "来袭预警" not in body
    assert 'type="radio"' in body
    assert 'name="defender_guild_id"' in body


@pytest.mark.django_db
def test_guild_pvp_page_resets_stale_daily_counters_before_disabling_targets(guild_member_client, django_user_model):
    client, _user, guild = guild_member_client
    yesterday = timezone.localdate() - timedelta(days=1)
    Guild.objects.filter(pk=guild.pk).update(
        pvp_attack_count_today=2,
        pvp_attack_count_reset_at=yesterday,
    )

    other_user, _other_manor = _create_user_with_manor(django_user_model, "guild_pvp_view_stale_target")
    target_guild = Guild.objects.create(
        name="跨天恢复目标帮",
        founder=other_user,
        is_active=True,
        level=guild.level,
        pvp_defense_count_today=3,
        pvp_defense_count_reset_at=yesterday,
    )
    GuildMember.objects.create(guild=target_guild, user=other_user, position="leader", is_active=True)

    response = client.get(reverse("guilds:pvp"))
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "跨天恢复目标帮" in body
    assert "搜索帮会名称" in body
    assert "满足出征条件，可作为本次进攻目标。" not in body
    assert "今日主动进攻次数已达上限" not in body
    assert "对方今日被攻击次数已达上限" not in body

    guild.refresh_from_db()
    target_guild.refresh_from_db()
    assert guild.pvp_attack_count_today == 2
    assert guild.pvp_attack_count_reset_at == yesterday
    assert target_guild.pvp_defense_count_today == 3
    assert target_guild.pvp_defense_count_reset_at == yesterday


@pytest.mark.django_db
def test_guild_pvp_page_does_not_process_due_runs_on_get(guild_member_client, monkeypatch):
    client, _user, _guild = guild_member_client

    def _raise_if_called(*_args, **_kwargs):
        raise AssertionError("prepare_guild_pvp_read_state should not run during GET render")

    monkeypatch.setattr("guilds.views.pvp.guild_raid_service.prepare_guild_pvp_read_state", _raise_if_called)

    response = client.get(reverse("guilds:pvp"))

    assert response.status_code == 200
    assert "帮会PVP" in response.content.decode("utf-8")


@pytest.mark.django_db
def test_non_manager_cannot_launch_guild_raid(django_user_model):
    leader, _leader_manor = _create_user_with_manor(django_user_model, "guild_pvp_launch_guard_leader")
    member_user, _member_manor = _create_user_with_manor(django_user_model, "guild_pvp_launch_guard_member")
    defender_user, _defender_manor = _create_user_with_manor(django_user_model, "guild_pvp_launch_guard_defender")
    guild = Guild.objects.create(name="帮会PVP权限帮", founder=leader, is_active=True, level=5, silver=50000)
    defender_guild = Guild.objects.create(
        name="帮会PVP权限目标",
        founder=defender_user,
        is_active=True,
        level=5,
        silver=50000,
    )
    GuildMember.objects.create(guild=guild, user=leader, position="leader", is_active=True)
    GuildMember.objects.create(guild=guild, user=member_user, position="member", is_active=True)
    GuildMember.objects.create(guild=defender_guild, user=defender_user, position="leader", is_active=True)

    client = Client()
    assert client.login(username="guild_pvp_launch_guard_member", password="pass12345")

    response = client.post(reverse("guilds:pvp_launch"), {"defender_guild_id": defender_guild.id}, follow=True)

    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert response.redirect_chain
    assert "只有管理员/帮主可以发起帮会攻击" in messages[-1]


@pytest.mark.django_db(transaction=True)
def test_manager_can_launch_guild_raid_and_redirect_back(guild_member_client, django_user_model, monkeypatch):
    client, user, guild = guild_member_client
    leader_member = user.guild_membership
    defender_user, _defender_manor = _create_user_with_manor(django_user_model, "guild_pvp_launch_defender")
    defender_guild = Guild.objects.create(
        name="帮会PVP发起目标",
        founder=defender_user,
        is_active=True,
        level=5,
        silver=50000,
    )
    GuildMember.objects.create(guild=defender_guild, user=defender_user, position="leader", is_active=True)
    guest_one = _create_guest(manor=user.manor, template=_create_template("guild_pvp_launch_tpl_1"), name="PVP门客一")
    guest_two = _create_guest(manor=user.manor, template=_create_template("guild_pvp_launch_tpl_2"), name="PVP门客二")
    entry_one = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest_one.id, slot_index=1).entry
    entry_two = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest_two.id, slot_index=2).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=user, pool_entry_id=entry_one.id)
    hero_pool_service.add_lineup_entry(guild=guild, operator=user, pool_entry_id=entry_two.id)
    troop_template = TroopTemplate.objects.create(key="guild_pvp_view_archer", name="页面弓手")
    GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=12)
    monkeypatch.setattr(
        "guilds.views.pvp.guild_raid_service.schedule_guild_raid_completion", lambda *_args, **_kwargs: None
    )

    response = client.post(
        reverse("guilds:pvp_launch"),
        {
            "defender_guild_id": defender_guild.id,
            "pool_entry_ids": [str(entry_one.id), str(entry_two.id)],
            "troop_guild_pvp_view_archer": "7",
        },
        follow=True,
    )

    messages = [str(message) for message in get_messages(response.wsgi_request)]
    assert response.redirect_chain
    assert messages[-1] == "帮会部队已出征"
    run = GuildRaidRun.objects.get(attacker_guild=guild, defender_guild=defender_guild, status="marching")
    assert run.selected_guest_count == 2
    assert run.guest_ids == [guest_one.id, guest_two.id]
    assert run.troop_loadout == {"guild_pvp_view_archer": 7}
