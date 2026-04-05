from __future__ import annotations

from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate
from guilds.models import Guild, GuildMember
from guilds.services import hero_pool as hero_pool_service


def create_user_with_manor(django_user_model, username: str):
    user = django_user_model.objects.create_user(username=username, password="pass12345")
    manor = ensure_manor(user)
    return user, manor


def create_guild_with_leader(django_user_model, suffix: str) -> tuple[Guild, GuildMember, object]:
    leader, manor = create_user_with_manor(django_user_model, f"guild_pvp_{suffix}")
    guild = Guild.objects.create(name=f"帮{suffix}"[:12], founder=leader, is_active=True)
    member = GuildMember.objects.create(guild=guild, user=leader, position="leader")
    return guild, member, manor


def create_template(key: str) -> GuestTemplate:
    return GuestTemplate.objects.create(
        key=key,
        name=f"模板{key}",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
    )


def create_guest(*, manor, template: GuestTemplate, name: str) -> Guest:
    return Guest.objects.create(
        manor=manor,
        template=template,
        custom_name=name,
        level=20,
        force=120,
        intellect=80,
        defense_stat=100,
        agility=90,
        luck=60,
    )


def seed_attacker_lineup(*, guild: Guild, leader: GuildMember, guest: Guest) -> int:
    entry = hero_pool_service.submit_hero_pool_entry(leader, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=guild, operator=leader.user, pool_entry_id=entry.id)
    return entry.id
