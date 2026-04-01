import pytest

from core.exceptions import GuildWarehouseError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from guilds.constants import CONTRIBUTION_RATES
from guilds.models import Guild, GuildExchangeLog, GuildMember, GuildWarehouse
from guilds.services.warehouse import add_item_to_warehouse, exchange_item


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


@pytest.mark.django_db
def test_add_item_to_warehouse_rejects_non_positive_quantity(guild_member_with_warehouse_item):
    guild, _member = guild_member_with_warehouse_item

    with pytest.raises(GuildWarehouseError, match="产出数量必须为正整数"):
        add_item_to_warehouse(guild, "guild_wh_item", 0, 5)

    with pytest.raises(GuildWarehouseError, match="产出数量必须为正整数"):
        add_item_to_warehouse(guild, "guild_wh_item", -1, 5)


@pytest.mark.django_db
def test_add_item_to_warehouse_rejects_negative_cost(guild_member_with_warehouse_item):
    guild, _member = guild_member_with_warehouse_item

    with pytest.raises(GuildWarehouseError, match="兑换成本不能为负数"):
        add_item_to_warehouse(guild, "guild_wh_item", 1, -1)


@pytest.mark.django_db
def test_exchange_item_rejects_non_positive_quantity(guild_member_with_warehouse_item):
    _guild, member = guild_member_with_warehouse_item

    with pytest.raises(GuildWarehouseError, match="兑换数量必须为正整数"):
        exchange_item(member, "guild_wh_item", 0)

    with pytest.raises(GuildWarehouseError, match="兑换数量必须为正整数"):
        exchange_item(member, "guild_wh_item", -3)


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
        silver=120,
        grain=45,
        gold_bar=3,
    )
    member = GuildMember.objects.create(guild=guild, user=user, position="member", current_contribution=500)
    ItemTemplate.objects.get_or_create(
        key="gold_bar",
        defaults={
            "name": "金条",
            "effect_type": ItemTemplate.EffectType.TOOL,
        },
    )
    return guild, member, manor


@pytest.mark.django_db
def test_exchange_item_grants_projected_silver_to_member_manor(guild_member_with_projected_resources):
    guild, member, manor = guild_member_with_projected_resources

    exchange_item(member, "silver", 7)

    guild.refresh_from_db()
    member.refresh_from_db()
    manor.refresh_from_db()

    assert guild.silver == 113
    assert manor.silver == 7
    assert member.current_contribution == 500 - (7 * CONTRIBUTION_RATES["silver"])
    assert member.daily_exchange_count == 1
    assert GuildExchangeLog.objects.filter(member=member, item_key="silver", quantity=7).exists()


@pytest.mark.django_db
def test_exchange_item_grants_projected_grain_to_member_manor(guild_member_with_projected_resources):
    guild, member, manor = guild_member_with_projected_resources

    exchange_item(member, "grain", 5)

    guild.refresh_from_db()
    member.refresh_from_db()
    manor.refresh_from_db()

    assert guild.grain == 40
    assert manor.grain == 5
    assert member.current_contribution == 500 - (5 * CONTRIBUTION_RATES["grain"])
    assert member.daily_exchange_count == 1
    assert GuildExchangeLog.objects.filter(member=member, item_key="grain", quantity=5).exists()


@pytest.mark.django_db
def test_exchange_item_grants_projected_gold_bar_to_member_inventory(guild_member_with_projected_resources):
    guild, member, manor = guild_member_with_projected_resources

    exchange_item(member, "gold_bar", 2)

    guild.refresh_from_db()
    member.refresh_from_db()
    gold_bar_item = InventoryItem.objects.get(
        manor=manor,
        template__key="gold_bar",
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    assert guild.gold_bar == 1
    assert gold_bar_item.quantity == 2
    assert member.current_contribution == 500 - (2 * CONTRIBUTION_RATES["gold_bar"])
    assert member.daily_exchange_count == 1
    assert GuildExchangeLog.objects.filter(member=member, item_key="gold_bar", quantity=2).exists()


@pytest.mark.django_db
def test_exchange_item_rejects_when_projected_resource_stock_is_insufficient(guild_member_with_projected_resources):
    guild, member, manor = guild_member_with_projected_resources

    with pytest.raises(GuildWarehouseError, match="库存不足，剩余3件"):
        exchange_item(member, "gold_bar", 4)

    guild.refresh_from_db()
    member.refresh_from_db()
    manor.refresh_from_db()

    assert guild.gold_bar == 3
    assert member.current_contribution == 500
    assert InventoryItem.objects.filter(manor=manor, template__key="gold_bar").exists() is False
