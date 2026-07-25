from __future__ import annotations

import pytest

from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from guilds.models import Guild, GuildMember, GuildWarehouse


@pytest.fixture
def guild_member_with_warehouse_item(django_user_model):
    leader = django_user_model.objects.create_user(username="guild_wh_leader", password="pass123")
    ensure_manor(leader)
    guild = Guild.objects.create(name="仓库帮", founder=leader, is_active=True)
    member = GuildMember.objects.create(guild=guild, user=leader, position="leader", current_contribution=100)
    ItemTemplate.objects.create(
        key="guild_wh_item",
        name="帮会仓库道具",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
    )
    GuildWarehouse.objects.create(guild=guild, item_key="guild_wh_item", quantity=10, contribution_cost=5)
    return guild, member


@pytest.fixture
def guild_member_with_projected_resources(django_user_model):
    user = django_user_model.objects.create_user(username="guild_projected_user", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 0
    manor.grain = 0
    manor.save(update_fields=["silver", "grain"])
    guild = Guild.objects.create(
        name="投影资源帮",
        founder=user,
        is_active=True,
        silver=2_000,
        grain=4_000,
        gold_bar=3,
    )
    member = GuildMember.objects.create(guild=guild, user=user, position="member", current_contribution=5_000)
    ItemTemplate.objects.update_or_create(
        key="gold_bar",
        defaults={
            "name": "金条",
            "effect_type": ItemTemplate.EffectType.TOOL,
            "is_usable": True,
        },
    )
    return guild, member, manor


@pytest.fixture
def guild_with_mixed_warehouse_resources(django_user_model):
    leader = django_user_model.objects.create_user(username="guild_wh_projection_case", password="pass123")
    ensure_manor(leader)
    guild = Guild.objects.create(
        name="仓库投影帮",
        founder=leader,
        is_active=True,
        silver=120,
        grain=45,
        gold_bar=3,
    )
    GuildWarehouse.objects.create(guild=guild, item_key="grain", quantity=25, contribution_cost=2)
    GuildWarehouse.objects.create(guild=guild, item_key="gold_bar", quantity=4, contribution_cost=50)
    return guild


@pytest.fixture
def guild_member_ready_for_grain_donation(django_user_model):
    user = django_user_model.objects.create_user(username="guild_grain_donor", password="pass123")
    manor = ensure_manor(user)
    manor.grain = 5_000
    manor.save(update_fields=["grain"])
    guild = Guild.objects.create(name="粮仓帮", founder=user, is_active=True, grain=0, gold_bar=0, silver=0)
    member = GuildMember.objects.create(guild=guild, user=user, position="member", current_contribution=0)
    return member, manor


@pytest.fixture
def guild_member_with_real_warehouse_resources(django_user_model):
    user = django_user_model.objects.create_user(username="guild_real_wh_user", password="pass123")
    manor = ensure_manor(user)
    manor.silver = 0
    manor.grain = 0
    manor.save(update_fields=["silver", "grain"])
    guild = Guild.objects.create(
        name="真实仓库资源帮",
        founder=user,
        is_active=True,
        silver=120,
        grain=0,
        gold_bar=0,
    )
    member = GuildMember.objects.create(guild=guild, user=user, position="member", current_contribution=5_000)
    gold_bar_template, _created = ItemTemplate.objects.get_or_create(
        key="gold_bar",
        defaults={
            "name": "金条",
            "effect_type": ItemTemplate.EffectType.TOOL,
            "is_usable": True,
        },
    )
    InventoryItem.objects.update_or_create(
        manor=manor,
        template=gold_bar_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": 5},
    )
    GuildWarehouse.objects.create(guild=guild, item_key="grain", quantity=4_000, contribution_cost=2)
    GuildWarehouse.objects.create(guild=guild, item_key="gold_bar", quantity=4, contribution_cost=61)
    return guild, member, manor
