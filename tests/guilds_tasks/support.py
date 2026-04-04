from __future__ import annotations

from datetime import timedelta

from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate
from guilds.services import hero_pool as hero_pool_service


def dispatch_immediately(task, *, args=None, kwargs=None, countdown=None, logger=None, log_message="", **_kwargs):
    del kwargs, countdown, logger, log_message, _kwargs
    task.run(*(args or []))
    return True


def create_user_with_manor(django_user_model, username: str):
    user = django_user_model.objects.create_user(username=username, password="pass12345")
    manor = ensure_manor(user)
    return user, manor


def create_template(key: str) -> GuestTemplate:
    return GuestTemplate.objects.create(
        key=key,
        name=f"模板{key}",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
    )


def create_guest(*, manor, template: GuestTemplate, name: str, level: int = 20) -> Guest:
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
    )


def create_active_guild_run(django_user_model, *, username: str, key_suffix: str, return_at):
    from battle.models import TroopTemplate
    from guilds.models import Guild, GuildMember, GuildMissionRun, GuildMissionTemplate, GuildTroopStorage

    user, manor = create_user_with_manor(django_user_model, username)
    guild = Guild.objects.create(name=f"任务帮会{key_suffix}", founder=user, is_active=True)
    member = GuildMember.objects.create(guild=guild, user=user, position="leader", is_active=True)
    template = GuildMissionTemplate.objects.create(
        key=f"guild_task_{key_suffix}",
        name="任务测试",
        description="",
        difficulty="junior",
        task_type="dispatch",
        base_duration_seconds=60,
        ruby_reward=3,
        recommended_guest_count=1,
        allow_troops=True,
        is_active=True,
    )
    troop_template = TroopTemplate.objects.create(key=f"guild_task_archer_{key_suffix}", name="任务弓手")
    GuildTroopStorage.objects.create(guild=guild, troop_template=troop_template, count=50)
    guest = create_guest(manor=manor, template=create_template(f"guild_task_tpl_{key_suffix}"), name="队长")
    entry = hero_pool_service.submit_hero_pool_entry(member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=user, pool_entry_id=entry.id)

    return GuildMissionRun.objects.create(
        guild=guild,
        template=template,
        started_by=member,
        status="active",
        selected_guest_count=1,
        ruby_reward=template.ruby_reward,
        guest_ids=[guest.id],
        guest_snapshots=[],
        troop_loadout={troop_template.key: 20},
        battle_at=return_at - timedelta(seconds=30),
        return_at=return_at,
    )
