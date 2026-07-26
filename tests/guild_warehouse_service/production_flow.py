from __future__ import annotations

import pytest

from core.exceptions import GuildWarehouseError
from gameplay.models import InventoryItem
from gameplay.services.manor.core import ensure_manor
from guilds.models import Guild, GuildMember, GuildWarehouse
from guilds.services import warehouse_config
from guilds.services.warehouse import add_item_to_warehouse, exchange_item, produce_equipment, produce_soul_containers

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


def test_get_warehouse_production_item_keys_collects_unique_items_across_all_tech_levels(monkeypatch):
    monkeypatch.setattr(
        warehouse_config,
        "get_warehouse_production",
        lambda: {
            "equipment": warehouse_config.TechProduction(
                tech_key="equipment",
                levels={
                    1: [warehouse_config.ProductionItem("guild_box_green", 1, 70)],
                    2: [
                        warehouse_config.ProductionItem("guild_box_green", 2, 70),
                        warehouse_config.ProductionItem("guild_box_blue", 1, 180),
                    ],
                },
            ),
            "mysticism": warehouse_config.TechProduction(
                tech_key="mysticism",
                levels={1: [warehouse_config.ProductionItem("soul_container", 1, 1000)]},
            ),
        },
    )

    assert warehouse_config.get_warehouse_production_item_keys() == frozenset(
        {"guild_box_green", "guild_box_blue", "soul_container"}
    )


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
@pytest.mark.parametrize(
    ("tech_level", "expected_outputs"),
    [
        (
            3,
            {
                "guild_gear_box_green": (2, 70),
                "guild_gear_box_blue": (1, 180),
            },
        ),
        (
            10,
            {
                "guild_gear_box_green": (2, 70),
                "guild_gear_box_blue": (2, 180),
                "guild_gear_box_purple": (2, 500),
                "guild_gear_box_refined_purple": (3, 650),
                "guild_gear_box_master": (2, 800),
            },
        ),
    ],
)
def test_produce_equipment_keeps_earlier_tier_outputs(django_user_model, tech_level, expected_outputs):
    leader = django_user_model.objects.create_user(username=f"guild_wh_level_{tech_level}", password="pass123")
    ensure_manor(leader)
    guild = Guild.objects.create(name=f"{tech_level}级仓库帮", founder=leader, is_active=True)

    produce_equipment(guild, tech_level)

    outputs = {
        item.item_key: (item.quantity, item.contribution_cost) for item in GuildWarehouse.objects.filter(guild=guild)
    }
    assert outputs == expected_outputs


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("tech_level", "expected_outputs"),
    [
        (
            3,
            {
                "guild_skill_book_box_green": (2, 80),
                "guild_skill_book_box_blue": (1, 180),
            },
        ),
        (
            10,
            {
                "guild_skill_book_box_green": (2, 80),
                "guild_skill_book_box_blue": (2, 180),
                "guild_skill_book_box_purple": (2, 400),
                "guild_skill_book_box_refined": (2, 500),
                "guild_skill_book_box_master": (2, 650),
            },
        ),
    ],
)
def test_produce_experience_keeps_earlier_tier_outputs(django_user_model, tech_level, expected_outputs):
    from guilds.services.warehouse import produce_experience_items

    leader = django_user_model.objects.create_user(username=f"guild_skill_level_{tech_level}", password="pass123")
    ensure_manor(leader)
    guild = Guild.objects.create(name=f"{tech_level}级藏书帮", founder=leader, is_active=True)

    produce_experience_items(guild, tech_level)

    outputs = {
        item.item_key: (item.quantity, item.contribution_cost) for item in GuildWarehouse.objects.filter(guild=guild)
    }
    assert outputs == expected_outputs


@pytest.mark.django_db
def test_produce_guard_items_level_ten_uses_master_box(django_user_model):
    from guilds.services.warehouse import produce_guard_items

    leader = django_user_model.objects.create_user(username="guild_guard_level_ten", password="pass123")
    ensure_manor(leader)
    guild = Guild.objects.create(name="十级护院帮", founder=leader, is_active=True)

    produce_guard_items(guild, 10)

    master_box = GuildWarehouse.objects.get(guild=guild, item_key="guild_guard_box_master")
    assert master_box.quantity == 2
    assert master_box.contribution_cost == 360


@pytest.mark.django_db
def test_mysticism_produces_and_exchanges_one_soul_container_for_1000_contribution(django_user_model):
    leader = django_user_model.objects.create_user(username="guild_mysticism_leader", password="pass123")
    manor = ensure_manor(leader)
    guild = Guild.objects.create(name="神秘学帮", founder=leader, is_active=True)
    member = GuildMember.objects.create(
        guild=guild,
        user=leader,
        position="leader",
        current_contribution=1000,
    )

    produce_soul_containers(guild, 1)

    warehouse_item = GuildWarehouse.objects.get(guild=guild, item_key="soul_container")
    assert warehouse_item.quantity == 1
    assert warehouse_item.contribution_cost == 1000

    exchange_item(member, "soul_container", 1)

    member.refresh_from_db()
    assert member.current_contribution == 0
    assert not GuildWarehouse.objects.filter(guild=guild, item_key="soul_container").exists()
    inventory_item = InventoryItem.objects.get(manor=manor, template__key="soul_container")
    assert inventory_item.quantity == 1


@pytest.mark.django_db
def test_mysticism_level_three_produces_each_unlocked_item_once(django_user_model):
    leader = django_user_model.objects.create_user(username="guild_mysticism_level_three", password="pass123")
    guild = Guild.objects.create(name="三级神秘学帮", founder=leader, is_active=True)

    produce_soul_containers(guild, 3)

    produced = {
        item_key: (quantity, contribution_cost)
        for item_key, quantity, contribution_cost in GuildWarehouse.objects.filter(guild=guild).values_list(
            "item_key",
            "quantity",
            "contribution_cost",
        )
    }
    assert produced == {
        "soul_container": (1, 1000),
        "guest_rebirth_card": (1, 1000),
        "xidianka": (1, 1000),
        "xisuidan": (1, 1000),
    }
