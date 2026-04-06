from __future__ import annotations

from datetime import timedelta
from pathlib import Path

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


def _extract_target_row(body: str, guild_id: int) -> str:
    marker = f'data-target-id="{guild_id}"'
    start = body.index(marker)
    start = body.rfind("<label", 0, start)
    end = body.index("</label>", start) + len("</label>")
    return body[start:end]


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
def test_guild_pvp_page_active_runs_use_explicit_refresh_api(guild_member_client, django_user_model):
    client, user, guild = guild_member_client
    member = user.guild_membership

    defender_user, _defender_manor = _create_user_with_manor(django_user_model, "guild_pvp_active_defender")
    defender_guild = Guild.objects.create(
        name="帮会PVP进行中目标", founder=defender_user, is_active=True, level=6, silver=50000
    )
    GuildMember.objects.create(guild=defender_guild, user=defender_user, position="leader", is_active=True)

    incoming_user, _incoming_manor = _create_user_with_manor(django_user_model, "guild_pvp_active_incoming")
    incoming_guild = Guild.objects.create(
        name="帮会PVP来袭目标", founder=incoming_user, is_active=True, level=6, silver=50000
    )
    incoming_member = GuildMember.objects.create(
        guild=incoming_guild, user=incoming_user, position="leader", is_active=True
    )

    now = timezone.now()
    GuildRaidRun.objects.create(
        attacker_guild=guild,
        defender_guild=defender_guild,
        started_by=member,
        status=GuildRaidRun.Status.MARCHING,
        selected_guest_count=1,
        battle_at=now + timedelta(minutes=5),
        return_at=now + timedelta(minutes=10),
    )
    GuildRaidRun.objects.create(
        attacker_guild=incoming_guild,
        defender_guild=guild,
        started_by=incoming_member,
        status=GuildRaidRun.Status.MARCHING,
        selected_guest_count=1,
        battle_at=now + timedelta(minutes=7),
        return_at=now + timedelta(minutes=14),
    )

    response = client.get(reverse("guilds:pvp"))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    refresh_url = reverse("guilds:refresh_pvp_activity_api")
    assert "js/dashboard.js" in body
    assert "当前战况" in body
    assert "帮会出征：帮会PVP进行中目标" in body
    assert "帮会来袭：帮会PVP来袭目标" in body
    assert body.count(f'data-refresh-url="{refresh_url}"') == 2
    assert body.count('data-refresh-method="post"') == 2


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
    assert "基础阵容预计" in body
    assert 'type="radio"' in body
    assert 'name="defender_guild_id"' in body


def test_guild_pvp_styles_give_guest_cards_more_name_space() -> None:
    css_path = Path(__file__).resolve().parents[1] / "static" / "css" / "guild-pvp.css"
    css = css_path.read_text(encoding="utf-8")

    assert "grid-template-columns: repeat(auto-fill, minmax(10.6rem, 10.6rem));" in css
    assert ".gpvp-page .gpvp-guest-option .tw-guest-name-sm {" in css
    assert "flex: 1 1 auto;" in css
    assert "min-width: 0;" in css


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
def test_guild_pvp_page_renders_projected_status_attributes_for_target_rows(guild_member_client, django_user_model):
    client, _user, guild = guild_member_client

    attackable_user, _attackable_manor = _create_user_with_manor(django_user_model, "guild_pvp_projected_attackable")
    attackable_guild = Guild.objects.create(
        name="可投影目标帮",
        founder=attackable_user,
        is_active=True,
        level=guild.level,
        silver=50000,
    )
    GuildMember.objects.create(guild=attackable_guild, user=attackable_user, position="leader", is_active=True)

    blocked_user, _blocked_manor = _create_user_with_manor(django_user_model, "guild_pvp_projected_blocked")
    blocked_guild = Guild.objects.create(
        name="受保护目标帮",
        founder=blocked_user,
        is_active=True,
        level=guild.level + 1,
        silver=50000,
        defeat_protection_until=timezone.now() + timedelta(hours=1),
    )
    GuildMember.objects.create(guild=blocked_guild, user=blocked_user, position="leader", is_active=True)

    response = client.get(reverse("guilds:pvp"))
    body = response.content.decode("utf-8")

    attackable_row = _extract_target_row(body, attackable_guild.id)
    blocked_row = _extract_target_row(body, blocked_guild.id)

    assert response.status_code == 200
    assert "基础阵容预计" in body
    assert attackable_row.count('data-display-status="attackable"') == 1
    assert 'data-target-status="attackable"' in attackable_row
    assert "基础阵容预计" in attackable_row
    assert 'value="%s"' % attackable_guild.id in attackable_row
    assert "checked" in attackable_row
    assert "disabled" not in attackable_row

    assert blocked_row.count('data-display-status="blocked"') == 1
    assert 'data-target-status="blocked"' in blocked_row
    assert "基础阵容预计" in blocked_row
    assert "is-blocked" in blocked_row
    assert "对方处于战败保护期" in blocked_row
    assert 'value="%s"' % blocked_guild.id in blocked_row
    assert "disabled" in blocked_row
    assert "checked" not in blocked_row


@pytest.mark.django_db
def test_guild_pvp_page_does_not_mark_blocked_default_target_as_selected(guild_member_client, django_user_model):
    client, _user, guild = guild_member_client

    blocked_user, _blocked_manor = _create_user_with_manor(django_user_model, "guild_pvp_blocked_default_target")
    blocked_guild = Guild.objects.create(
        name="唯一受保护目标帮",
        founder=blocked_user,
        is_active=True,
        level=guild.level,
        silver=50000,
        defeat_protection_until=timezone.now() + timedelta(hours=1),
    )
    GuildMember.objects.create(guild=blocked_guild, user=blocked_user, position="leader", is_active=True)

    response = client.get(reverse("guilds:pvp"))
    body = response.content.decode("utf-8")
    blocked_row = _extract_target_row(body, blocked_guild.id)

    assert response.status_code == 200
    assert 'data-display-status="blocked"' in blocked_row
    assert "is-blocked" in blocked_row
    assert "is-active" not in blocked_row
    assert "is-selected-target" not in blocked_row
    assert "disabled" in blocked_row
    assert "checked" not in blocked_row


@pytest.mark.django_db
def test_get_guild_pvp_page_context_returns_projected_target_card_and_runs(django_user_model):
    user, _manor = _create_user_with_manor(django_user_model, "guild_pvp_projected_context_leader")
    guild = Guild.objects.create(name="投影进攻帮", founder=user, is_active=True, level=5, silver=50000)
    member = GuildMember.objects.create(guild=guild, user=user, position="leader", is_active=True)

    target_user, _target_manor = _create_user_with_manor(django_user_model, "guild_pvp_projected_target")
    target_guild = Guild.objects.create(name="投影目标帮", founder=target_user, is_active=True, level=6, silver=50000)
    GuildMember.objects.create(guild=target_guild, user=target_user, position="leader", is_active=True)

    incoming_user, _incoming_manor = _create_user_with_manor(django_user_model, "guild_pvp_projected_incoming")
    incoming_guild = Guild.objects.create(
        name="投影来袭帮", founder=incoming_user, is_active=True, level=6, silver=50000
    )
    incoming_member = GuildMember.objects.create(
        guild=incoming_guild, user=incoming_user, position="leader", is_active=True
    )

    now = timezone.now()
    active_run = GuildRaidRun.objects.create(
        attacker_guild=guild,
        defender_guild=target_guild,
        started_by=member,
        status=GuildRaidRun.Status.MARCHING,
        selected_guest_count=1,
        battle_at=now + timedelta(seconds=90),
        return_at=now + timedelta(seconds=210),
    )
    incoming_run = GuildRaidRun.objects.create(
        attacker_guild=incoming_guild,
        defender_guild=guild,
        started_by=incoming_member,
        status=GuildRaidRun.Status.MARCHING,
        selected_guest_count=1,
        battle_at=now - timedelta(seconds=15),
        return_at=now + timedelta(seconds=120),
    )

    from guilds.services.guild_pvp_queries import get_guild_pvp_page_context

    context = get_guild_pvp_page_context(member, now=now)

    projected_target = next(target for target in context["targets"] if target.guild.id == target_guild.id)
    projected_active_run = context["active_run"]
    projected_incoming_run = next(run for run in context["incoming_runs"] if run.run.id == incoming_run.id)

    assert not isinstance(projected_target, dict)
    assert projected_target.status_key == "attackable"
    assert projected_target.travel_time_seconds > 0
    assert projected_target.travel_projection_label == f"基础阵容预计 {projected_target.travel_time_seconds} 秒"
    assert not hasattr(projected_target, "can_attack")
    assert context["default_target_id"] == target_guild.id
    assert context["target_filter_counts"] == {"all": 2, "attackable": 2, "blocked": 0}

    assert projected_active_run.run.id == active_run.id
    assert projected_active_run.display_status_key == "marching"
    assert projected_active_run.display_hint == f"正在向{target_guild.name}进军"
    assert projected_active_run.display_eta_at == active_run.battle_at
    assert not hasattr(projected_active_run, "id")
    assert not hasattr(projected_active_run, "next_state_at")

    assert projected_incoming_run.run.id == incoming_run.id
    assert projected_incoming_run.display_status_key == "arrived"
    assert projected_incoming_run.display_hint == "敌方帮会已抵达，正在交战"
    assert projected_incoming_run.display_eta_at == incoming_run.return_at
    assert not hasattr(projected_incoming_run, "id")
    assert not hasattr(projected_incoming_run, "next_state_at")


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
