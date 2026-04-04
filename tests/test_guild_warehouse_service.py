from importlib import import_module

import pytest
from django.db import transaction

from core.exceptions import GuildWarehouseError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from guilds.constants import CONTRIBUTION_RATES
from guilds.models import Guild, GuildExchangeLog, GuildMember, GuildWarehouse
from guilds.services import warehouse as warehouse_service
from guilds.services import warehouse_config
from guilds.services.warehouse import add_item_to_warehouse, exchange_item, produce_equipment


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
def test_add_item_to_warehouse_refreshes_existing_item_contribution_cost(guild_member_with_warehouse_item):
    guild, _member = guild_member_with_warehouse_item

    add_item_to_warehouse(guild, "guild_wh_item", 2, 9)

    warehouse_item = GuildWarehouse.objects.get(guild=guild, item_key="guild_wh_item")

    assert warehouse_item.quantity == 12
    assert warehouse_item.contribution_cost == 9


@pytest.mark.django_db
def test_produce_equipment_reads_latest_runtime_warehouse_production_config(django_user_model, monkeypatch):
    leader = django_user_model.objects.create_user(username="guild_wh_runtime_refresh", password="pass123")
    ensure_manor(leader)
    guild = Guild.objects.create(name="运行时仓库帮", founder=leader, is_active=True)

    payload = {
        "value": {
            "equipment": {
                "levels": {
                    1: [
                        {
                            "item_key": "runtime_old_item",
                            "quantity": 1,
                            "contribution_cost": 10,
                        }
                    ]
                }
            }
        }
    }
    monkeypatch.setattr(warehouse_config, "load_yaml_data", lambda *args, **kwargs: payload["value"])

    try:
        warehouse_config.reload_warehouse_production()
        produce_equipment(guild, 1)

        old_item = GuildWarehouse.objects.get(guild=guild, item_key="runtime_old_item")
        assert old_item.quantity == 1
        assert old_item.contribution_cost == 10

        payload["value"] = {
            "equipment": {
                "levels": {
                    1: [
                        {
                            "item_key": "runtime_new_item",
                            "quantity": 3,
                            "contribution_cost": 99,
                        }
                    ]
                }
            }
        }

        warehouse_config.reload_warehouse_production()
        produce_equipment(guild, 1)

        old_item.refresh_from_db()
        new_item = GuildWarehouse.objects.get(guild=guild, item_key="runtime_new_item")
        assert old_item.quantity == 1
        assert new_item.quantity == 3
        assert new_item.contribution_cost == 99
    finally:
        warehouse_config.reload_warehouse_production()


@pytest.mark.django_db
def test_exchange_item_rejects_non_positive_quantity(guild_member_with_warehouse_item):
    _guild, member = guild_member_with_warehouse_item

    with pytest.raises(GuildWarehouseError, match="兑换数量必须为正整数"):
        exchange_item(member, "guild_wh_item", 0)

    with pytest.raises(GuildWarehouseError, match="兑换数量必须为正整数"):
        exchange_item(member, "guild_wh_item", -3)


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

    member = type(
        "MemberArg",
        (),
        {
            "pk": 1,
            "user": object(),
            "guild": object(),
        },
    )()
    member_locked = type(
        "LockedMember",
        (),
        {
            "pk": 1,
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

        def get(self, pk):
            assert pk == member.pk
            return member_locked

        def filter(self, pk):
            assert pk == member_locked.pk
            return self

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
        "guilds.services.warehouse.GuildMember", type("GuildMemberModel", (), {"objects": _MemberObjects()})
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
    manor.grain = 500
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
    member = GuildMember.objects.create(guild=guild, user=user, position="member", current_contribution=500)
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
    GuildWarehouse.objects.create(guild=guild, item_key="grain", quantity=25, contribution_cost=2)
    GuildWarehouse.objects.create(guild=guild, item_key="gold_bar", quantity=4, contribution_cost=61)
    return guild, member, manor


@pytest.mark.django_db
def test_get_warehouse_items_only_projects_silver_after_resource_item_migration(guild_with_mixed_warehouse_resources):
    guild = guild_with_mixed_warehouse_resources

    payload = warehouse_service.get_warehouse_items(guild, page=1, per_page=20)

    projected_keys = {item.item_key for item in payload["items"] if getattr(item, "is_projected", False)}
    assert projected_keys == {"silver"}


@pytest.mark.django_db
def test_get_warehouse_items_reads_grain_and_gold_bar_from_real_rows(guild_with_mixed_warehouse_resources):
    guild = guild_with_mixed_warehouse_resources

    payload = warehouse_service.get_warehouse_items(guild, page=1, per_page=20)
    quantities = {item.item_key: item.display_quantity for item in payload["items"]}

    assert quantities["grain"] == 25
    assert quantities["gold_bar"] == 4


@pytest.mark.django_db
def test_get_warehouse_items_projects_legacy_grain_and_gold_bar_when_real_rows_missing(
    guild_member_with_projected_resources,
):
    guild, _member, _manor = guild_member_with_projected_resources

    payload = warehouse_service.get_warehouse_items(guild, page=1, per_page=20)
    projected_items = {item.item_key: item for item in payload["items"] if getattr(item, "is_projected", False)}

    assert projected_items["silver"].display_quantity == 120
    assert projected_items["grain"].display_quantity == 45
    assert projected_items["gold_bar"].display_quantity == 3


@pytest.mark.django_db
def test_donate_grain_adds_real_guild_warehouse_item(guild_member_ready_for_grain_donation):
    from guilds.services import contribution as contribution_service

    member, manor = guild_member_ready_for_grain_donation

    contribution_service.donate_resource(member, "grain", 200)

    warehouse_row = GuildWarehouse.objects.get(guild=member.guild, item_key="grain")
    member.refresh_from_db()
    member.guild.refresh_from_db()
    manor.refresh_from_db()

    assert warehouse_row.quantity == 200
    assert member.current_contribution == 400
    assert member.guild.grain == 0
    assert manor.grain == 300


@pytest.mark.django_db
def test_resource_promotion_migration_fails_fast_when_real_rows_already_exist(django_user_model):
    migration_module = import_module("guilds.migrations.0012_promote_grain_gold_bar_to_warehouse_items")

    leader = django_user_model.objects.create_user(username="guild_migration_existing_row", password="pass123")
    ensure_manor(leader)
    guild = Guild.objects.create(
        name="迁移冲突帮",
        founder=leader,
        is_active=True,
        grain=12,
        gold_bar=3,
    )
    GuildWarehouse.objects.create(guild=guild, item_key="grain", quantity=1, contribution_cost=2)

    class _Apps:
        @staticmethod
        def get_model(app_label, model_name):
            assert app_label == "guilds"
            if model_name == "Guild":
                return Guild
            if model_name == "GuildWarehouse":
                return GuildWarehouse
            raise LookupError(model_name)

    with pytest.raises(RuntimeError, match="grain"):
        migration_module.forward_promote_resources(_Apps(), None)


@pytest.mark.django_db
def test_resource_promotion_migration_moves_resources_into_warehouse_and_can_roll_back(django_user_model):
    migration_module = import_module("guilds.migrations.0012_promote_grain_gold_bar_to_warehouse_items")

    leader = django_user_model.objects.create_user(username="guild_migration_roundtrip", password="pass123")
    ensure_manor(leader)
    guild = Guild.objects.create(
        name="迁移往返帮",
        founder=leader,
        is_active=True,
        grain=12,
        gold_bar=3,
    )

    class _Apps:
        @staticmethod
        def get_model(app_label, model_name):
            assert app_label == "guilds"
            if model_name == "Guild":
                return Guild
            if model_name == "GuildWarehouse":
                return GuildWarehouse
            raise LookupError(model_name)

    migration_module.forward_promote_resources(_Apps(), None)

    guild.refresh_from_db()
    grain_row = GuildWarehouse.objects.get(guild=guild, item_key="grain")
    gold_bar_row = GuildWarehouse.objects.get(guild=guild, item_key="gold_bar")

    assert guild.grain == 0
    assert guild.gold_bar == 0
    assert grain_row.quantity == 12
    assert grain_row.total_produced == 12
    assert grain_row.contribution_cost == 2
    assert gold_bar_row.quantity == 3
    assert gold_bar_row.total_produced == 3
    assert gold_bar_row.contribution_cost == 50

    migration_module.backward_restore_resources(_Apps(), None)

    guild.refresh_from_db()

    assert guild.grain == 12
    assert guild.gold_bar == 3
    assert GuildWarehouse.objects.filter(guild=guild, item_key__in=["grain", "gold_bar"]).exists() is False


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
def test_exchange_item_uses_latest_runtime_projected_resource_cost(guild_member_with_projected_resources, monkeypatch):
    guild, member, manor = guild_member_with_projected_resources
    monkeypatch.setattr("guilds.constants.CONTRIBUTION_RATES", {"silver": 11, "grain": 2, "gold_bar": 50})

    exchange_item(member, "silver", 7)

    guild.refresh_from_db()
    member.refresh_from_db()
    manor.refresh_from_db()

    assert guild.silver == 113
    assert manor.silver == 7
    assert member.current_contribution == 500 - 77
    assert member.daily_exchange_count == 1


@pytest.mark.django_db
def test_exchange_item_uses_latest_runtime_daily_exchange_limit(guild_member_with_projected_resources, monkeypatch):
    _guild, member, _manor = guild_member_with_projected_resources
    monkeypatch.setattr("guilds.constants.DAILY_EXCHANGE_LIMIT", 0)

    with pytest.raises(GuildWarehouseError, match="今日兑换次数已达上限（0次）"):
        exchange_item(member, "silver", 1)


@pytest.mark.django_db
def test_exchange_item_grants_projected_legacy_grain_to_member_manor(guild_member_with_projected_resources):
    guild, member, manor = guild_member_with_projected_resources

    exchange_item(member, "grain", 5)

    guild.refresh_from_db()
    member.refresh_from_db()
    manor.refresh_from_db()

    assert guild.grain == 40
    assert manor.grain == 5
    assert member.current_contribution == 500 - (5 * CONTRIBUTION_RATES["grain"])
    assert member.daily_exchange_count == 1
    assert GuildWarehouse.objects.filter(guild=guild, item_key="grain").exists() is False
    assert GuildExchangeLog.objects.filter(member=member, item_key="grain", quantity=5).exists()


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
    assert member.current_contribution == 500 - (2 * CONTRIBUTION_RATES["gold_bar"])
    assert member.daily_exchange_count == 1
    assert GuildWarehouse.objects.filter(guild=guild, item_key="gold_bar").exists() is False
    assert GuildExchangeLog.objects.filter(member=member, item_key="gold_bar", quantity=2).exists()


@pytest.mark.django_db
def test_exchange_item_grants_real_warehouse_grain_to_member_manor(guild_member_with_real_warehouse_resources):
    guild, member, manor = guild_member_with_real_warehouse_resources

    exchange_item(member, "grain", 5)

    guild.refresh_from_db()
    member.refresh_from_db()
    manor.refresh_from_db()
    grain_row = GuildWarehouse.objects.get(guild=guild, item_key="grain")

    assert guild.grain == 0
    assert grain_row.quantity == 20
    assert grain_row.total_exchanged == 5
    assert manor.grain == 5
    assert member.current_contribution == 500 - 10
    assert member.daily_exchange_count == 1
    assert GuildExchangeLog.objects.filter(member=member, item_key="grain", quantity=5, contribution_cost=10).exists()


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
    assert member.current_contribution == 500 - 122
    assert member.daily_exchange_count == 1
    assert GuildExchangeLog.objects.filter(
        member=member, item_key="gold_bar", quantity=2, contribution_cost=122
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
    assert member.current_contribution == 500
    assert InventoryItem.objects.get(manor=manor, template__key="gold_bar").quantity == 5
