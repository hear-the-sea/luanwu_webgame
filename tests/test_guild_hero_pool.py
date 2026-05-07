from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from core.exceptions import GuildValidationError
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestArchetype, GuestRarity, GuestStatus, GuestTemplate
from guilds.models import Guild, GuildBattleLineupEntry, GuildHeroPoolEntry, GuildMember
from guilds.services import hero_pool as hero_pool_service
from guilds.services import member as member_service


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


def _create_guest(
    *,
    manor,
    template: GuestTemplate,
    name: str,
    level: int = 20,
    status: str = GuestStatus.IDLE,
) -> Guest:
    return Guest.objects.create(
        manor=manor,
        template=template,
        custom_name=name,
        level=level,
        force=120,
        intellect=85,
        defense_stat=100,
        agility=90,
        luck=60,
        status=status,
    )


@pytest.mark.django_db
def test_submit_real_mapping_does_not_change_guest_status(django_user_model):
    leader, leader_manor = _create_user_with_manor(django_user_model, "ghp_leader_status")
    guild = Guild.objects.create(name="门客池状态帮", founder=leader)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")

    template = _create_template("ghp_tpl_status")
    guest = _create_guest(
        manor=leader_manor,
        template=template,
        name="打工门客",
        status=GuestStatus.WORKING,
    )

    result = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1)

    guest.refresh_from_db()
    assert guest.status == GuestStatus.WORKING
    assert result.entry.source_guest_id == guest.id


@pytest.mark.django_db
def test_replace_has_30_minute_cooldown(django_user_model):
    leader, leader_manor = _create_user_with_manor(django_user_model, "ghp_leader_cd")
    guild = Guild.objects.create(name="门客池冷却帮", founder=leader)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")

    template = _create_template("ghp_tpl_cd")
    guest_a = _create_guest(manor=leader_manor, template=template, name="门客甲")
    guest_b = _create_guest(manor=leader_manor, template=template, name="门客乙")

    base_time = timezone.now()
    hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest_a.id, slot_index=1, now=base_time)

    with pytest.raises(GuildValidationError, match="替换冷却中"):
        hero_pool_service.submit_hero_pool_entry(
            leader_member,
            guest_id=guest_b.id,
            slot_index=1,
            now=base_time + timedelta(minutes=20),
        )

    result = hero_pool_service.submit_hero_pool_entry(
        leader_member,
        guest_id=guest_b.id,
        slot_index=1,
        now=base_time + timedelta(minutes=31),
    )
    assert result.replaced is True
    assert result.entry.source_guest_id == guest_b.id


@pytest.mark.django_db
def test_remove_slot_also_respects_replace_cooldown(django_user_model):
    leader, leader_manor = _create_user_with_manor(django_user_model, "ghp_leader_remove_cd")
    guild = Guild.objects.create(name="门客池清空冷却帮", founder=leader)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")

    template = _create_template("ghp_tpl_remove_cd")
    guest = _create_guest(manor=leader_manor, template=template, name="门客甲")
    hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1)

    with pytest.raises(GuildValidationError, match="替换冷却中"):
        hero_pool_service.remove_hero_pool_entry(leader_member, slot_index=1)


@pytest.mark.django_db
def test_same_player_two_guests_can_both_enter_lineup(django_user_model):
    leader, leader_manor = _create_user_with_manor(django_user_model, "ghp_leader_twice")
    guild = Guild.objects.create(name="门客池双人帮", founder=leader)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")

    template = _create_template("ghp_tpl_twice")
    guest_a = _create_guest(manor=leader_manor, template=template, name="门客甲")
    guest_b = _create_guest(manor=leader_manor, template=template, name="门客乙")

    entry_a = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest_a.id, slot_index=1).entry
    entry_b = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest_b.id, slot_index=2).entry

    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry_a.id)
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry_b.id)

    lineup_pool_ids = list(
        GuildBattleLineupEntry.objects.filter(guild=guild)
        .order_by("slot_index")
        .values_list("pool_entry_id", flat=True)
    )
    assert lineup_pool_ids == [entry_a.id, entry_b.id]


@pytest.mark.django_db
def test_lineup_has_capacity_limit(django_user_model, monkeypatch):
    monkeypatch.setattr("guilds.constants.GUILD_BATTLE_LINEUP_LIMIT", 2)

    leader, leader_manor = _create_user_with_manor(django_user_model, "ghp_leader_limit")
    guild = Guild.objects.create(name="门客池上限帮", founder=leader)
    GuildMember.objects.create(guild=guild, user=leader, position="leader")

    template = _create_template("ghp_tpl_limit")
    entry_ids: list[int] = []

    for idx in range(3):
        user, manor = _create_user_with_manor(django_user_model, f"ghp_limit_{idx}")
        member = GuildMember.objects.create(guild=guild, user=user, position="member")
        guest = _create_guest(manor=manor, template=template, name=f"门客{idx}")
        entry = hero_pool_service.submit_hero_pool_entry(member, guest_id=guest.id, slot_index=1).entry
        entry_ids.append(entry.id)

    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry_ids[0])
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry_ids[1])

    with pytest.raises(GuildValidationError, match="出战名单已满"):
        hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry_ids[2])


@pytest.mark.django_db
def test_lineup_limit_uses_guild_lineup_capacity_tech(django_user_model, monkeypatch):
    from guilds.models import GuildTechnology

    monkeypatch.setattr("guilds.constants.GUILD_BATTLE_LINEUP_LIMIT", 20)

    leader, _ = _create_user_with_manor(django_user_model, "ghp_leader_dynamic_limit")
    guild = Guild.objects.create(name="门客池动态上限帮", founder=leader)
    GuildMember.objects.create(guild=guild, user=leader, position="leader")
    GuildTechnology.objects.create(guild=guild, tech_key="guild_lineup_capacity", level=1, max_level=20)

    template = _create_template("ghp_tpl_dynamic_limit")
    entry_ids: list[int] = []
    for idx in range(21):
        user, manor = _create_user_with_manor(django_user_model, f"ghp_dynamic_limit_{idx}")
        member = GuildMember.objects.create(guild=guild, user=user, position="member")
        guest = _create_guest(manor=manor, template=template, name=f"门客{idx}")
        entry = hero_pool_service.submit_hero_pool_entry(member, guest_id=guest.id, slot_index=1).entry
        entry_ids.append(entry.id)

    for entry_id in entry_ids:
        hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry_id)

    assert GuildBattleLineupEntry.objects.filter(guild=guild).count() == 21


@pytest.mark.django_db
def test_lock_guild_lineup_uses_dispatch_capacity_tech(django_user_model, monkeypatch):
    from guilds.models import GuildTechnology

    monkeypatch.setattr("guilds.constants.GUILD_BATTLE_LINEUP_LIMIT", 10)
    monkeypatch.setattr("guilds.constants.GUILD_DISPATCH_GUEST_BASE_LIMIT", 2)

    leader, _ = _create_user_with_manor(django_user_model, "ghp_leader_dispatch_limit")
    guild = Guild.objects.create(name="门客池动态出征帮", founder=leader)
    GuildMember.objects.create(guild=guild, user=leader, position="leader")
    GuildTechnology.objects.create(guild=guild, tech_key="guild_dispatch_capacity", level=1, max_level=20)

    template = _create_template("ghp_tpl_dispatch_limit")
    expected_guest_ids: list[int] = []
    for idx in range(4):
        user, manor = _create_user_with_manor(django_user_model, f"ghp_dispatch_limit_{idx}")
        member = GuildMember.objects.create(guild=guild, user=user, position="member")
        guest = _create_guest(manor=manor, template=template, name=f"出征门客{idx}")
        expected_guest_ids.append(guest.id)
        entry = hero_pool_service.submit_hero_pool_entry(member, guest_id=guest.id, slot_index=1).entry
        hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    result = hero_pool_service.lock_guild_lineup_for_dispatch(guild)

    assert result.guest_ids == expected_guest_ids[:3]


@pytest.mark.django_db
def test_invalid_entry_cleanup_auto_removes_lineup(django_user_model):
    leader, leader_manor = _create_user_with_manor(django_user_model, "ghp_leader_invalid_cleanup")
    guild = Guild.objects.create(name="门客池无效清理帮", founder=leader)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")

    template = _create_template("ghp_tpl_invalid_cleanup")
    guest = _create_guest(manor=leader_manor, template=template, name="失效门客")
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    GuildBattleLineupEntry.objects.create(guild=guild, pool_entry=entry, slot_index=1, selected_by=leader)

    GuildMember.objects.filter(pk=leader_member.pk).update(is_active=False)

    cleaned = hero_pool_service.cleanup_invalid_hero_pool_entries(limit=100)
    assert cleaned >= 1
    assert not GuildHeroPoolEntry.objects.filter(pk=entry.pk).exists()
    assert GuildBattleLineupEntry.objects.filter(guild=guild).count() == 0


@pytest.mark.django_db
def test_leave_or_kick_invalidates_member_hero_pool(django_user_model):
    leader, _ = _create_user_with_manor(django_user_model, "ghp_leader_invalidate")
    guild = Guild.objects.create(name="门客池失效帮", founder=leader)
    GuildMember.objects.create(guild=guild, user=leader, position="leader")

    member_user, member_manor = _create_user_with_manor(django_user_model, "ghp_member_leave")
    member = GuildMember.objects.create(guild=guild, user=member_user, position="member")

    template = _create_template("ghp_tpl_invalidate")
    guest = _create_guest(manor=member_manor, template=template, name="失效门客")
    hero_pool_service.submit_hero_pool_entry(member, guest_id=guest.id, slot_index=1)
    assert GuildHeroPoolEntry.objects.filter(owner_member=member).exists()

    member_service.leave_guild(member)
    assert not GuildHeroPoolEntry.objects.filter(owner_member=member).exists()

    kicked_user, kicked_manor = _create_user_with_manor(django_user_model, "ghp_member_kick")
    kicked_member = GuildMember.objects.create(guild=guild, user=kicked_user, position="member")
    kicked_guest = _create_guest(manor=kicked_manor, template=template, name="被踢门客")
    hero_pool_service.submit_hero_pool_entry(kicked_member, guest_id=kicked_guest.id, slot_index=1)

    target = GuildMember.objects.get(pk=kicked_member.pk)
    member_service.kick_member(target, leader)
    assert not GuildHeroPoolEntry.objects.filter(owner_member=target).exists()


@pytest.mark.django_db
def test_page_context_cleans_cross_guild_inconsistent_entries(django_user_model):
    leader_a, _ = _create_user_with_manor(django_user_model, "ghp_leader_a")
    leader_b, _ = _create_user_with_manor(django_user_model, "ghp_leader_b")
    guild_a = Guild.objects.create(name="门客池A帮", founder=leader_a)
    guild_b = Guild.objects.create(name="门客池B帮", founder=leader_b)
    GuildMember.objects.create(guild=guild_a, user=leader_a, position="leader")
    GuildMember.objects.create(guild=guild_b, user=leader_b, position="leader")

    user, manor = _create_user_with_manor(django_user_model, "ghp_inconsistent_member")
    member = GuildMember.objects.create(guild=guild_a, user=user, position="member", is_active=True)

    template = _create_template("ghp_tpl_inconsistent")
    guest = _create_guest(manor=manor, template=template, name="异常门客")
    entry = hero_pool_service.submit_hero_pool_entry(member, guest_id=guest.id, slot_index=1).entry
    assert GuildHeroPoolEntry.objects.filter(pk=entry.pk).exists()

    # 模拟异常数据：成员被迁到其他帮会，但旧帮会门客池条目未清理
    member.guild = guild_b
    member.save(update_fields=["guild"])

    leader_member_a = GuildMember.objects.get(guild=guild_a, user=leader_a, is_active=True)
    context = hero_pool_service.get_hero_pool_page_context(leader_member_a)

    assert context["pool_rows"] == []
    assert not GuildHeroPoolEntry.objects.filter(pk=entry.pk).exists()


@pytest.mark.django_db
def test_hero_pool_page_context_builds_status_counts_for_roster_filters(django_user_model):
    leader, leader_manor = _create_user_with_manor(django_user_model, "ghp_plan_status_leader")
    guild = Guild.objects.create(name="计划状态帮", founder=leader)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")

    template = _create_template("ghp_plan_status_tpl")
    my_guest = _create_guest(manor=leader_manor, template=template, name="我的门客", level=26)
    hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=my_guest.id, slot_index=1)

    other_user, other_manor = _create_user_with_manor(django_user_model, "ghp_plan_status_member")
    other_member = GuildMember.objects.create(guild=guild, user=other_user, position="member")
    lineup_guest = _create_guest(manor=other_manor, template=template, name="已上阵门客", level=24)
    lineup_entry = hero_pool_service.submit_hero_pool_entry(other_member, guest_id=lineup_guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=lineup_entry.id)

    idle_user, idle_manor = _create_user_with_manor(django_user_model, "ghp_plan_status_idle")
    idle_member = GuildMember.objects.create(guild=guild, user=idle_user, position="member")
    idle_guest = _create_guest(manor=idle_manor, template=template, name="可加入门客", level=18)
    hero_pool_service.submit_hero_pool_entry(idle_member, guest_id=idle_guest.id, slot_index=1)

    context = hero_pool_service.get_hero_pool_page_context(leader_member)

    assert context["hero_pool_filter_counts"] == {
        "joinable": 1,
        "lineup": 1,
        "mine": 1,
        "all": 3,
    }
    status_keys = {row["status_key"] for row in context["pool_rows"]}
    assert status_keys >= {"mine", "lineup", "joinable"}


@pytest.mark.django_db
def test_hero_pool_page_context_builds_scrollable_lineup_summary_rows(django_user_model):
    leader, leader_manor = _create_user_with_manor(django_user_model, "ghp_plan_lineup_leader")
    guild = Guild.objects.create(name="计划出战帮", founder=leader)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")

    template = _create_template("ghp_plan_lineup_tpl")
    guest = _create_guest(manor=leader_manor, template=template, name="赵云", level=30)
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader, pool_entry_id=entry.id)

    context = hero_pool_service.get_hero_pool_page_context(leader_member)

    assert context["lineup_summary_rows"][0]["guest_name"] == "赵云"
    assert context["lineup_summary_rows"][0]["owner_name"] == leader.manor.display_name
    assert context["lineup_summary_rows"][0]["avatar_url"] == (
        guest.template.avatar.url if guest.template.avatar else None
    )


@pytest.mark.django_db
def test_lock_guild_lineup_for_dispatch_locks_guest_order(django_user_model):
    leader, leader_manor = _create_user_with_manor(django_user_model, "ghp_lock_leader")
    guild = Guild.objects.create(name="门客池锁定帮", founder=leader)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")

    template = _create_template("ghp_tpl_lock")
    guest_a = _create_guest(manor=leader_manor, template=template, name="门客甲")
    guest_b = _create_guest(manor=leader_manor, template=template, name="门客乙")
    entry_a = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest_a.id, slot_index=1).entry
    entry_b = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest_b.id, slot_index=2).entry

    GuildBattleLineupEntry.objects.create(guild=guild, pool_entry=entry_a, slot_index=2, selected_by=leader)
    GuildBattleLineupEntry.objects.create(guild=guild, pool_entry=entry_b, slot_index=1, selected_by=leader)

    lock_result = hero_pool_service.lock_guild_lineup_for_dispatch(guild)
    assert lock_result.guest_ids == [guest_b.id, guest_a.id]
    assert lock_result.removed_invalid_count == 0


@pytest.mark.django_db
def test_lock_guild_lineup_for_dispatch_drops_invalid_lineup_rows(django_user_model):
    leader, leader_manor = _create_user_with_manor(django_user_model, "ghp_lock_invalid_leader")
    guild = Guild.objects.create(name="门客池锁定清理帮", founder=leader)
    leader_member = GuildMember.objects.create(guild=guild, user=leader, position="leader")

    template = _create_template("ghp_tpl_lock_invalid")
    guest = _create_guest(manor=leader_manor, template=template, name="门客甲")
    entry = hero_pool_service.submit_hero_pool_entry(leader_member, guest_id=guest.id, slot_index=1).entry
    lineup = GuildBattleLineupEntry.objects.create(guild=guild, pool_entry=entry, slot_index=1, selected_by=leader)

    GuildMember.objects.filter(pk=leader_member.pk).update(is_active=False)

    lock_result = hero_pool_service.lock_guild_lineup_for_dispatch(guild)
    assert lock_result.guest_ids == []
    assert lock_result.removed_invalid_count == 1
    assert not GuildBattleLineupEntry.objects.filter(pk=lineup.pk).exists()


def test_hero_pool_write_paths_use_guild_first_lock_helper() -> None:
    source = Path(__file__).resolve().parents[1] / "guilds" / "services" / "hero_pool.py"
    text = source.read_text(encoding="utf-8")

    assert "def _lock_guild_and_active_member(" in text
    assert "_lock_active_member(member)" not in text
    assert 'GuildMember.objects.select_for_update()\n        .select_related("guild")' not in text
