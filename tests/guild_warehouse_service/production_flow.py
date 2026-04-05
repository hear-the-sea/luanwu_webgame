from __future__ import annotations

import pytest

from core.exceptions import GuildWarehouseError
from gameplay.services.manor.core import ensure_manor
from guilds.models import Guild, GuildWarehouse
from guilds.services import warehouse_config
from guilds.services.warehouse import add_item_to_warehouse, produce_equipment

pytest_plugins = ("tests.guild_warehouse_service.support",)


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
