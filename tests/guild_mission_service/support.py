from __future__ import annotations

from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate
from guilds.models import Guild, GuildMember
from guilds.services import hero_pool as hero_pool_service


def create_guild_and_leader(django_user_model, suffix: str) -> tuple[Guild, GuildMember]:
    user = django_user_model.objects.create_user(username=f"guild_mission_{suffix}", password="pass12345")
    guild = Guild.objects.create(name=f"帮会任务{suffix}", founder=user)
    member = GuildMember.objects.create(guild=guild, user=user, position="leader")
    return guild, member


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


__all__ = [
    "Guild",
    "GuildMember",
    "create_guild_and_leader",
    "create_guest",
    "create_template",
    "create_user_with_manor",
    "hero_pool_service",
]
