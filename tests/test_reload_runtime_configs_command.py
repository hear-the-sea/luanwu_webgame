from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from gameplay.services.runtime_configs import format_runtime_config_summary, reload_runtime_configs


def test_format_runtime_config_summary_orders_known_keys():
    summary = {
        "shop_items": 3,
        "auction_items": 2,
        "warehouse_techs": 1,
        "forge_equipment": 4,
    }

    rendered = format_runtime_config_summary(summary)

    assert rendered == "shop_items=3, auction_items=2, warehouse_techs=1, forge_equipment=4"


def test_reload_runtime_configs_command_renders_summary(monkeypatch):
    out = StringIO()
    monkeypatch.setattr(
        "gameplay.management.commands.reload_runtime_configs.reload_runtime_configs",
        lambda: {
            "shop_items": 3,
            "auction_items": 2,
            "warehouse_techs": 1,
            "forge_equipment": 4,
            "guest_growth_rarities": 7,
        },
    )

    call_command("reload_runtime_configs", stdout=out, verbosity=0)
    rendered = out.getvalue()

    assert "[OK] 运行期配置已刷新:" in rendered
    assert "shop_items=3" in rendered
    assert "forge_equipment=4" in rendered
    assert "guest_growth_rarities=7" in rendered


def test_reload_runtime_configs_updates_arena_module_constants(monkeypatch):
    """reload_runtime_configs() must propagate fresh values into arena/core.py module globals."""
    import gameplay.services.arena.core as arena_core
    import gameplay.services.arena.rules as arena_rules_module
    from gameplay.services.arena.rules import clear_arena_rules_cache

    try:
        clear_arena_rules_cache()
        with monkeypatch.context() as patcher:
            patcher.setattr(
                arena_rules_module,
                "load_yaml_data",
                lambda *args, **kwargs: {
                    "registration": {
                        "max_guests_per_entry": 7,
                        "registration_silver_cost": 1234,
                        "daily_participation_limit": 5,
                        "tournament_player_limit": 4,
                    },
                    "runtime": {
                        "round_interval_seconds": 300,
                        "completed_retention_seconds": 120,
                        "round_retry_seconds": 10,
                        "recruiting_lock_key": "arena:test:refresh",
                        "recruiting_lock_timeout": 3,
                    },
                    "rewards": {
                        "base_participation_coins": 99,
                        "rank_bonus_coins": {1: 500, 2: 250},
                    },
                },
            )

            reload_runtime_configs()

            assert arena_core.ARENA_MAX_GUESTS_PER_ENTRY == 7
            assert arena_core.ARENA_REGISTRATION_SILVER_COST == 1234
            assert arena_core.ARENA_ROUND_INTERVAL_SECONDS == 300
            assert arena_core.ARENA_BASE_PARTICIPATION_COINS == 99
            assert arena_core.ARENA_RECRUITING_LOCK_KEY == "arena:test:refresh"
    finally:
        clear_arena_rules_cache()
        reload_runtime_configs()


def test_reload_runtime_configs_rejects_invalid_arena_override_setting(monkeypatch, settings):
    import gameplay.services.arena.rules as arena_rules_module
    from gameplay.services.arena.rules import clear_arena_rules_cache

    settings.ARENA_DAILY_PARTICIPATION_LIMIT = "bad-limit"

    try:
        clear_arena_rules_cache()
        with monkeypatch.context() as patcher:
            patcher.setattr(
                arena_rules_module,
                "load_yaml_data",
                lambda *args, **kwargs: {
                    "registration": {
                        "max_guests_per_entry": 7,
                        "registration_silver_cost": 1234,
                        "daily_participation_limit": 5,
                        "tournament_player_limit": 4,
                    },
                    "runtime": {
                        "round_interval_seconds": 300,
                        "completed_retention_seconds": 120,
                        "round_retry_seconds": 10,
                        "recruiting_lock_key": "arena:test:refresh",
                        "recruiting_lock_timeout": 3,
                    },
                    "rewards": {
                        "base_participation_coins": 99,
                        "rank_bonus_coins": {1: 500, 2: 250},
                    },
                },
            )

            with pytest.raises(AssertionError, match="invalid arena setting ARENA_DAILY_PARTICIPATION_LIMIT"):
                reload_runtime_configs()
    finally:
        clear_arena_rules_cache()
        del settings.ARENA_DAILY_PARTICIPATION_LIMIT
        reload_runtime_configs()


def test_reload_runtime_configs_rejects_invalid_stable_production_config(monkeypatch):
    import gameplay.services.buildings.stable as stable_module

    try:
        stable_module.clear_stable_production_cache()
        with monkeypatch.context() as patcher:
            patcher.setattr(
                stable_module,
                "load_yaml_data",
                lambda *args, **kwargs: {
                    "production": {
                        "equip_bad_horse": {
                            "grain_cost": True,
                            "base_duration": 180,
                            "required_horsemanship": 2,
                        }
                    }
                },
            )

            with pytest.raises(AssertionError, match="invalid stable production grain_cost"):
                reload_runtime_configs()
    finally:
        stable_module.clear_stable_production_cache()
        reload_runtime_configs()


def test_reload_runtime_configs_rejects_invalid_forge_equipment_config(monkeypatch):
    import gameplay.services.buildings.forge as forge_module

    try:
        forge_module.clear_forge_equipment_cache()
        with monkeypatch.context() as patcher:
            patcher.setattr(
                forge_module,
                "load_yaml_data",
                lambda *args, **kwargs: {
                    "equipment": {
                        "equip_bad": {
                            "category": "helmet",
                            "materials": {"tong": True},
                            "base_duration": 120,
                            "required_forging": 2,
                        }
                    }
                },
            )

            with pytest.raises(AssertionError, match="invalid forge config equipment.equip_bad.materials.tong"):
                reload_runtime_configs()
    finally:
        forge_module.clear_forge_equipment_cache()
        reload_runtime_configs()


def test_reload_runtime_configs_updates_guild_module_constants(monkeypatch):
    """reload_runtime_configs() must propagate fresh values into guilds/constants.py module globals."""
    import guilds.constants as guild_constants
    from guilds.constants import clear_guild_rules_cache

    try:
        clear_guild_rules_cache()
        with monkeypatch.context() as patcher:
            patcher.setattr(
                "guilds.constants.load_yaml_data",
                lambda *args, **kwargs: {
                    "pagination": {"guild_list_page_size": 55, "guild_hall_display_limit": 33},
                    "creation": {"guild_creation_cost": {"gold_bar": 9}, "guild_upgrade_base_cost": 12},
                    "contribution": {
                        "rates": {"silver": 3, "grain": 4},
                        "daily_limits": {"silver": 200000, "grain": 80000},
                        "min_donation_amount": 500,
                    },
                    "technology": {
                        "upgrade_costs": {
                            "equipment_forge": {"silver": 7000, "grain": 3000, "gold_bar": 2},
                        },
                        "names": {"equipment_forge": "刷新锻造"},
                    },
                    "warehouse": {"exchange_costs": {"gear_green": 77}, "daily_exchange_limit": 15},
                    "hero_pool": {
                        "slot_limit": 4,
                        "battle_lineup_limit": 30,
                        "dispatch_guest_base_limit": 8,
                        "replace_cooldown_seconds": 900,
                    },
                },
            )

            reload_runtime_configs()

            assert guild_constants.GUILD_LIST_PAGE_SIZE == 55
            assert guild_constants.GUILD_HALL_DISPLAY_LIMIT == 33
            assert guild_constants.GUILD_CREATION_COST == {"gold_bar": 9}
            assert guild_constants.GUILD_UPGRADE_BASE_COST == 12
            assert guild_constants.MIN_DONATION_AMOUNT == 500
            assert guild_constants.DAILY_EXCHANGE_LIMIT == 15
            assert guild_constants.GUILD_HERO_POOL_SLOT_LIMIT == 2
            assert guild_constants.GUILD_BATTLE_LINEUP_LIMIT == 20
            assert guild_constants.GUILD_DISPATCH_GUEST_BASE_LIMIT == 5
            assert guild_constants.GUILD_HERO_POOL_REPLACE_COOLDOWN_SECONDS == 900
    finally:
        clear_guild_rules_cache()
        reload_runtime_configs()


def test_reload_runtime_configs_updates_guild_service_runtime_values(monkeypatch):
    import guilds.services.hero_pool as hero_pool_service
    import guilds.services.technology as technology_service
    from guilds.constants import clear_guild_rules_cache

    try:
        clear_guild_rules_cache()
        with monkeypatch.context() as patcher:
            patcher.setattr(
                "guilds.constants.load_yaml_data",
                lambda *args, **kwargs: {
                    "technology": {
                        "upgrade_costs": {"equipment_forge": {"silver": 7000, "grain": 3000, "gold_bar": 2}},
                    },
                    "hero_pool": {
                        "replace_cooldown_seconds": 120,
                    },
                },
            )

            reload_runtime_configs()

            cost = technology_service.calculate_tech_upgrade_cost("equipment_forge", 0)
            ruby_cost = technology_service.calculate_tech_upgrade_cost("guild_lineup_capacity", 0)
            submitted_at = timezone.now()
            cooldown_until = hero_pool_service._replace_cooldown_until(
                type("Entry", (), {"last_submitted_at": submitted_at})()
            )

            assert cost == {"silver": 7000, "grain": 3000, "gold_bar": 2}
            assert ruby_cost == {"red_ruby": 1}
            assert int((cooldown_until - submitted_at).total_seconds()) == 120
    finally:
        clear_guild_rules_cache()
        reload_runtime_configs()
