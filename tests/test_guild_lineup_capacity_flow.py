from __future__ import annotations

import random

import pytest
from django.utils import timezone

from battle.execution import BattleOptions, _build_defender_units
from core.exceptions import GuildTechnologyError, GuildValidationError
from gameplay.services.manor.core import ensure_manor
from guilds.models import GuildBattleLineupEntry, GuildMember, GuildTechnology, GuildWarehouse
from guilds.services.guild_raid_support import load_defender_guests
from guilds.services.hero_pool import add_lineup_entry, get_hero_pool_page_context, submit_hero_pool_entry
from guilds.services.technology import get_guild_dispatch_capacity, upgrade_technology
from tests.guild_pvp_service.support import create_guest, create_guild_with_leader, create_template


@pytest.mark.django_db
@pytest.mark.parametrize("initial_level,upgrade_cost,capacity", [(0, 5, 21), (19, 100, 40)])
def test_lineup_upgrade_unlocks_slots_and_full_defense_without_expanding_dispatch(
    django_user_model, initial_level, upgrade_cost, capacity
):
    guild, leader, leader_manor = create_guild_with_leader(django_user_model, f"扩容{initial_level}")
    tech = GuildTechnology.objects.create(
        guild=guild, tech_key="guild_lineup_capacity", category="public", level=initial_level, max_level=20
    )
    rubies = GuildWarehouse.objects.create(guild=guild, item_key="red_ruby", quantity=upgrade_cost, contribution_cost=0)
    template = create_template(f"lineup_capacity_flow_{initial_level}")
    entries = []
    for member_index in range((capacity + 2) // 2):
        if member_index == 0:
            member, manor = leader, leader_manor
        else:
            user = django_user_model.objects.create_user(username=f"capacity_{initial_level}_{member_index}")
            manor = ensure_manor(user)
            member = GuildMember.objects.create(guild=guild, user=user, position="member")
        for slot in (1, 2):
            guest = create_guest(manor=manor, template=template, name=f"守卫{member_index}_{slot}")
            entries.append(submit_hero_pool_entry(member, guest_id=guest.pk, slot_index=slot).entry)

    for entry in entries[: capacity - 1]:
        add_lineup_entry(guild=guild, operator=leader.user, pool_entry_id=entry.pk)
    with pytest.raises(GuildValidationError, match="出战名单已满"):
        add_lineup_entry(guild=guild, operator=leader.user, pool_entry_id=entries[capacity - 1].pk)

    upgrade_technology(guild, "guild_lineup_capacity", leader.user)
    tech.refresh_from_db()
    assert tech.level == initial_level + 1
    assert not GuildWarehouse.objects.filter(pk=rubies.pk).exists()
    assert get_guild_dispatch_capacity(guild) == 5

    added = add_lineup_entry(guild=guild, operator=leader.user, pool_entry_id=entries[capacity - 1].pk)
    assert added.lineup_entry.slot_index == capacity
    assert GuildBattleLineupEntry.objects.filter(guild=guild).count() == capacity
    with pytest.raises(GuildValidationError, match="出战名单已满"):
        add_lineup_entry(guild=guild, operator=leader.user, pool_entry_id=entries[capacity].pk)
    context = get_hero_pool_page_context(leader)
    assert context["lineup_count"] == context["lineup_limit"] == capacity

    defender_guests = load_defender_guests(guild)
    assert {guest.pk for guest in defender_guests} == {entry.source_guest_id for entry in entries[:capacity]}
    options = BattleOptions(
        defender_guests=defender_guests,
        defender_limit=len(defender_guests),
        fill_default_troops=False,
        recover_live_guest_hp=False,
    )
    defender_units, *_ = _build_defender_units(options, random.Random(1), timezone.now())
    assert len(defender_units) == capacity
    if capacity == 40:
        with pytest.raises(GuildTechnologyError, match="科技已达最高等级"):
            upgrade_technology(guild, "guild_lineup_capacity", leader.user)
