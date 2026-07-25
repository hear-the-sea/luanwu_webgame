from __future__ import annotations

import pytest
from django.db import transaction

from core.exceptions import GuildWarehouseError
from gameplay.models import InventoryItem
from guilds.constants import CONTRIBUTION_RATES
from guilds.models import Guild, GuildExchangeLog, GuildMember, GuildWarehouse
from guilds.services import warehouse_config
from guilds.services.warehouse import exchange_item, get_exchange_logs, get_member_weekly_exchange_quantity

pytest_plugins = ("tests.guild_warehouse_service.support",)


@pytest.mark.django_db
def test_exchange_logs_use_chinese_item_names_and_hide_unknown_keys(guild_member_with_warehouse_item):
    guild, member = guild_member_with_warehouse_item
    GuildExchangeLog.objects.create(
        guild=guild,
        member=member,
        item_key="guild_wh_item",
        quantity=2,
        contribution_cost=10,
    )
    GuildExchangeLog.objects.create(
        guild=guild,
        member=member,
        item_key="future_internal_item",
        quantity=1,
        contribution_cost=5,
    )

    logs = get_exchange_logs(guild)

    assert [log.item_display_name for log in logs] == ["未知物品", "帮会仓库道具"]


@pytest.mark.django_db
def test_exchange_item_rejects_non_positive_quantity(guild_member_with_warehouse_item):
    _guild, member = guild_member_with_warehouse_item

    with pytest.raises(GuildWarehouseError, match="兑换数量必须为正整数"):
        exchange_item(member, "guild_wh_item", 0)

    with pytest.raises(GuildWarehouseError, match="兑换数量必须为正整数"):
        exchange_item(member, "guild_wh_item", -3)


@pytest.mark.django_db
def test_exchange_item_rejects_partial_projected_resource_bundle(guild_member_with_projected_resources):
    guild, member, manor = guild_member_with_projected_resources

    with pytest.raises(GuildWarehouseError, match="银两兑换数量必须是1000的整数倍"):
        exchange_item(member, "silver", 999)

    guild.refresh_from_db()
    member.refresh_from_db()
    manor.refresh_from_db()
    assert guild.silver == 2_000
    assert member.current_contribution == 5_000
    assert manor.silver == 0
    assert not GuildExchangeLog.objects.filter(member=member).exists()


@pytest.mark.django_db
def test_exchange_item_rejects_partial_real_resource_bundle(guild_member_with_real_warehouse_resources):
    guild, member, manor = guild_member_with_real_warehouse_resources

    with pytest.raises(GuildWarehouseError, match="粮食兑换数量必须是2000的整数倍"):
        exchange_item(member, "grain", 1_999)

    member.refresh_from_db()
    manor.refresh_from_db()
    grain_row = GuildWarehouse.objects.get(guild=guild, item_key="grain")
    assert grain_row.quantity == 4_000
    assert member.current_contribution == 5_000
    assert manor.grain == 0
    assert not GuildExchangeLog.objects.filter(member=member).exists()


def test_exchange_item_locks_manor_before_member_for_non_projected_items(monkeypatch):
    events: list[str] = []

    class _Atomic:
        def __enter__(self):
            events.append("atomic_enter")
            return self

        def __exit__(self, exc_type, exc, tb):
            events.append("atomic_exit")
            return False

    monkeypatch.setattr(transaction, "atomic", lambda: _Atomic())

    guild = type("GuildArg", (), {"pk": 9})()
    member = type(
        "MemberArg",
        (),
        {
            "pk": 1,
            "guild_id": 9,
            "user": object(),
            "guild": guild,
        },
    )()
    member_locked = type(
        "LockedMember",
        (),
        {
            "pk": 1,
            "guild_id": 9,
            "user": member.user,
            "guild": member.guild,
            "current_contribution": 100,
            "daily_exchange_count": 0,
            "reset_daily_limits": lambda self: events.append("reset_daily_limits"),
        },
    )()
    warehouse_item = type(
        "WarehouseItem",
        (),
        {
            "pk": 2,
            "quantity": 3,
            "contribution_cost": 5,
        },
    )()
    manor_locked = object()
    template = type("Template", (), {"is_usable": True})()

    class _MemberObjects:
        def select_for_update(self):
            events.append("member_lock")
            return self

        def select_related(self, *args):
            assert args == ("guild", "user")
            return self

        def get(self, pk):
            assert pk == member.pk
            return member_locked

        def filter(self, **kwargs):
            if "guild_id" in kwargs:
                assert kwargs == {"pk": member.pk, "guild_id": 9, "is_active": True}
            else:
                assert kwargs == {"pk": member_locked.pk}
            return self

        def first(self):
            return member_locked

        def update(self, **kwargs):
            events.append(f"member_update:{sorted(kwargs)}")
            return 1

    class _WarehouseSelectQuery:
        def filter(self, guild, item_key):
            assert guild is member_locked.guild
            assert item_key == "guild_wh_item"
            events.append("warehouse_lock")
            return self

        def first(self):
            return warehouse_item

    class _WarehouseFilterQuery:
        def update(self, **kwargs):
            events.append(f"warehouse_update:{sorted(kwargs)}")
            return 1

        def delete(self):
            events.append("warehouse_delete_zero")
            return (0, {})

    class _WarehouseObjects:
        def select_for_update(self):
            return _WarehouseSelectQuery()

        def filter(self, pk, quantity=0, quantity__gte=None):
            assert pk == warehouse_item.pk
            assert quantity == 0 or quantity__gte == 2
            return _WarehouseFilterQuery()

    class _ManorObjects:
        def select_for_update(self):
            events.append("manor_lock")
            return self

        def get(self, user):
            assert user is member_locked.user
            return manor_locked

    class _ItemTemplateObjects:
        def get(self, key):
            assert key == "guild_wh_item"
            return template

    monkeypatch.setattr(
        "guilds.services.warehouse.GuildMember",
        type("GuildMemberModel", (), {"objects": _MemberObjects()}),
    )
    monkeypatch.setattr(
        "guilds.services.warehouse.GuildWarehouse",
        type("GuildWarehouseModel", (), {"objects": _WarehouseObjects()}),
    )
    monkeypatch.setattr("guilds.services.warehouse.Manor", type("ManorModel", (), {"objects": _ManorObjects()}))
    monkeypatch.setattr(
        "gameplay.models.ItemTemplate",
        type("ItemTemplateModel", (), {"objects": _ItemTemplateObjects(), "DoesNotExist": LookupError}),
    )
    monkeypatch.setattr(
        "guilds.services.warehouse._grant_inventory_item_to_manor_locked",
        lambda manor, got_template, quantity: events.append(f"grant:{quantity}") or None,
    )
    monkeypatch.setattr(
        "guilds.services.warehouse.GuildExchangeLog",
        type(
            "GuildExchangeLogModel",
            (),
            {"objects": type("Objects", (), {"create": lambda self, **kwargs: events.append("log_create")})()},
        ),
    )

    exchange_item(member, "guild_wh_item", 2)

    assert events.index("manor_lock") < events.index("member_lock")
    assert events.index("member_lock") < events.index("warehouse_lock")


@pytest.mark.django_db
def test_exchange_item_grants_projected_silver_to_member_manor(guild_member_with_projected_resources):
    guild, member, manor = guild_member_with_projected_resources

    exchange_item(member, "silver", 1_000)

    guild.refresh_from_db()
    member.refresh_from_db()
    manor.refresh_from_db()

    assert guild.silver == 1_000
    assert manor.silver == 1_000
    assert member.current_contribution == 5_000 - CONTRIBUTION_RATES["silver"]
    assert member.daily_exchange_count == 1
    assert GuildExchangeLog.objects.filter(
        member=member,
        item_key="silver",
        quantity=1_000,
        contribution_cost=1,
    ).exists()


@pytest.mark.django_db
def test_exchange_item_rejects_member_moved_to_another_guild(guild_member_with_projected_resources):
    old_guild, stale_member, manor = guild_member_with_projected_resources
    new_guild = Guild.objects.create(name="新帮会", founder=stale_member.user, is_active=True, silver=999)
    GuildMember.objects.filter(pk=stale_member.pk).update(guild=new_guild, current_contribution=5_000)

    with pytest.raises(GuildWarehouseError, match="您不在帮会中"):
        exchange_item(stale_member, "silver", 1_000)

    old_guild.refresh_from_db()
    new_guild.refresh_from_db()
    manor.refresh_from_db()
    current_member = GuildMember.objects.get(pk=stale_member.pk)

    assert old_guild.silver == 2_000
    assert new_guild.silver == 999
    assert manor.silver == 0
    assert current_member.current_contribution == 5_000
    assert GuildExchangeLog.objects.filter(member=current_member).exists() is False


@pytest.mark.django_db
def test_exchange_item_uses_latest_runtime_projected_resource_cost(guild_member_with_projected_resources, monkeypatch):
    guild, member, manor = guild_member_with_projected_resources
    monkeypatch.setattr("guilds.constants.CONTRIBUTION_RATES", {"silver": 11, "grain": 2, "gold_bar": 50})

    exchange_item(member, "silver", 1_000)

    guild.refresh_from_db()
    member.refresh_from_db()
    manor.refresh_from_db()

    assert guild.silver == 1_000
    assert manor.silver == 1_000
    assert member.current_contribution == 5_000 - 11
    assert member.daily_exchange_count == 1


@pytest.mark.django_db
def test_exchange_item_uses_latest_runtime_daily_exchange_limit(guild_member_with_projected_resources, monkeypatch):
    _guild, member, _manor = guild_member_with_projected_resources
    monkeypatch.setattr("guilds.constants.DAILY_EXCHANGE_LIMIT", 0)

    with pytest.raises(GuildWarehouseError, match="今日兑换次数已达上限（0次）"):
        exchange_item(member, "silver", 1_000)


@pytest.mark.django_db
def test_exchange_item_rejects_when_weekly_item_limit_is_exhausted(
    guild_member_with_warehouse_item,
    monkeypatch,
):
    guild, member = guild_member_with_warehouse_item
    payload = {
        "equipment": {
            "levels": {
                1: [
                    {
                        "item_key": "guild_wh_item",
                        "quantity": 1,
                        "contribution_cost": 5,
                        "weekly_personal_limit": 2,
                    }
                ]
            }
        }
    }
    monkeypatch.setattr(warehouse_config, "load_yaml_data", lambda *args, **kwargs: payload)

    try:
        warehouse_config.reload_warehouse_production()
        GuildExchangeLog.objects.create(
            guild=guild,
            member=member,
            item_key="guild_wh_item",
            quantity=2,
            contribution_cost=10,
        )
        assert warehouse_config.get_weekly_personal_limit("guild_wh_item") == 2
        assert get_member_weekly_exchange_quantity(member, "guild_wh_item") == 2

        with pytest.raises(GuildWarehouseError, match="本周该物品兑换次数已达上限（2件）"):
            exchange_item(member, "guild_wh_item", 1)

        member.refresh_from_db()
        warehouse_item = GuildWarehouse.objects.get(guild=guild, item_key="guild_wh_item")
        assert member.current_contribution == 100
        assert warehouse_item.quantity == 10
        assert GuildExchangeLog.objects.filter(member=member, item_key="guild_wh_item").count() == 1
    finally:
        warehouse_config.reload_warehouse_production()


@pytest.mark.django_db
def test_exchange_item_grants_projected_legacy_grain_to_member_manor(guild_member_with_projected_resources):
    guild, member, manor = guild_member_with_projected_resources

    exchange_item(member, "grain", 2_000)

    guild.refresh_from_db()
    member.refresh_from_db()
    manor.refresh_from_db()

    assert guild.grain == 2_000
    assert manor.grain == 2_000
    assert member.current_contribution == 5_000 - CONTRIBUTION_RATES["grain"]
    assert member.daily_exchange_count == 1
    assert GuildWarehouse.objects.filter(guild=guild, item_key="grain").exists() is False
    assert GuildExchangeLog.objects.filter(
        member=member,
        item_key="grain",
        quantity=2_000,
        contribution_cost=1,
    ).exists()


@pytest.mark.django_db
def test_exchange_item_grants_projected_legacy_gold_bar_to_member_inventory(guild_member_with_projected_resources):
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
    assert member.current_contribution == 5_000 - (2 * CONTRIBUTION_RATES["gold_bar"])
    assert member.daily_exchange_count == 1
    assert GuildWarehouse.objects.filter(guild=guild, item_key="gold_bar").exists() is False
    assert GuildExchangeLog.objects.filter(member=member, item_key="gold_bar", quantity=2).exists()


@pytest.mark.django_db
def test_exchange_item_grants_real_warehouse_grain_to_member_manor(guild_member_with_real_warehouse_resources):
    guild, member, manor = guild_member_with_real_warehouse_resources

    exchange_item(member, "grain", 2_000)

    guild.refresh_from_db()
    member.refresh_from_db()
    manor.refresh_from_db()
    grain_row = GuildWarehouse.objects.get(guild=guild, item_key="grain")

    assert guild.grain == 0
    assert grain_row.quantity == 2_000
    assert grain_row.total_exchanged == 2_000
    assert manor.grain == 2_000
    assert member.current_contribution == 4_999
    assert member.daily_exchange_count == 1
    assert GuildExchangeLog.objects.filter(
        member=member,
        item_key="grain",
        quantity=2_000,
        contribution_cost=1,
    ).exists()


@pytest.mark.django_db
def test_exchange_item_grants_real_warehouse_gold_bar_to_member_inventory(guild_member_with_real_warehouse_resources):
    guild, member, manor = guild_member_with_real_warehouse_resources

    exchange_item(member, "gold_bar", 2)

    guild.refresh_from_db()
    member.refresh_from_db()
    gold_bar_row = GuildWarehouse.objects.get(guild=guild, item_key="gold_bar")
    gold_bar_item = InventoryItem.objects.get(
        manor=manor,
        template__key="gold_bar",
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    assert guild.gold_bar == 0
    assert gold_bar_row.quantity == 2
    assert gold_bar_row.total_exchanged == 2
    assert gold_bar_item.quantity == 7
    assert member.current_contribution == 2_600
    assert member.daily_exchange_count == 1
    assert GuildExchangeLog.objects.filter(
        member=member,
        item_key="gold_bar",
        quantity=2,
        contribution_cost=2_400,
    ).exists()


@pytest.mark.django_db
def test_exchange_item_rejects_when_real_warehouse_resource_stock_is_insufficient(
    guild_member_with_real_warehouse_resources,
):
    guild, member, manor = guild_member_with_real_warehouse_resources

    with pytest.raises(GuildWarehouseError, match="库存不足，剩余4件"):
        exchange_item(member, "gold_bar", 5)

    guild.refresh_from_db()
    member.refresh_from_db()
    manor.refresh_from_db()

    assert guild.gold_bar == 0
    assert GuildWarehouse.objects.get(guild=guild, item_key="gold_bar").quantity == 4
    assert member.current_contribution == 5_000
    assert InventoryItem.objects.get(manor=manor, template__key="gold_bar").quantity == 5
