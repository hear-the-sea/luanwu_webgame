from guilds.constants import DEFAULT_GUILD_RULES, clear_guild_rules_cache, load_guild_rules, normalize_guild_rules


def test_normalize_guild_rules_uses_defaults_for_invalid_root():
    assert normalize_guild_rules(["invalid-root"]) == DEFAULT_GUILD_RULES


def test_normalize_guild_rules_merges_and_clamps_values():
    loaded = normalize_guild_rules(
        {
            "pagination": {"guild_list_page_size": "30"},
            "creation": {"guild_creation_cost": {"gold_bar": "3"}, "guild_upgrade_base_cost": "8"},
            "contribution": {
                "units": {"silver": "1000", "grain": "2000", "gold_bar": "1"},
                "rates": {"silver": "2", "gold_bar": "50"},
                "daily_limits": {"grain": "60000", "gold_bar": "20"},
                "troop_rates": {0: "1", 1: "1", 3: "3", 5: "6", 7: "12"},
                "daily_troop_contribution_limit": "300",
                "min_donation_amount": "1",
            },
            "technology": {
                "upgrade_costs": {"equipment_forge": {"silver": "6000", "grain": 2500, "gold_bar": 2}},
                "upgrade_cost_curves": {"standard_10": {2: "3"}},
                "upgrade_cost_curve_by_tech": {"equipment_forge": "standard_10"},
                "upgrade_cost_overrides": {
                    "mysticism": {2: {"red_ruby": "333", "gold_bar": "155"}},
                },
                "names": {"equipment_forge": "新装备锻造"},
                "descriptions": {"equipment_forge": "新的科技简介"},
            },
            "warehouse": {
                "exchange_costs": {"gear_green": "60"},
                "daily_exchange_limit": "12",
            },
            "hero_pool": {
                "slot_limit": "3",
                "battle_lineup_limit": "25",
                "replace_cooldown_seconds": "1200",
            },
        }
    )

    assert loaded["pagination"]["guild_list_page_size"] == 30
    assert loaded["creation"]["guild_creation_cost"]["gold_bar"] == 3
    assert loaded["creation"]["guild_upgrade_base_cost"] == 8
    assert loaded["contribution"]["units"] == {"silver": 1000, "grain": 2000, "gold_bar": 1}
    assert loaded["contribution"]["rates"]["silver"] == 2
    assert loaded["contribution"]["rates"]["gold_bar"] == 50
    assert loaded["contribution"]["daily_limits"]["grain"] == 60000
    assert loaded["contribution"]["daily_limits"]["gold_bar"] == 20
    assert loaded["contribution"]["troop_rates"] == {"0": 1, "1": 1, "3": 3, "5": 6, "7": 12}
    assert loaded["contribution"]["daily_troop_contribution_limit"] == 300
    assert loaded["contribution"]["min_donation_amount"] == 1
    assert loaded["technology"]["upgrade_costs"]["equipment_forge"]["silver"] == 6000
    assert loaded["technology"]["upgrade_costs"]["equipment_forge"]["grain"] == 2500
    assert loaded["technology"]["upgrade_costs"]["equipment_forge"]["gold_bar"] == 2
    assert loaded["technology"]["names"]["equipment_forge"] == "新装备锻造"
    assert loaded["technology"]["names"]["guild_lineup_capacity"] == "出战位扩容"
    assert loaded["technology"]["descriptions"]["equipment_forge"] == "新的科技简介"
    assert loaded["technology"]["descriptions"]["guild_lineup_capacity"] == "提升帮会已上阵名单总容量"
    assert loaded["technology"]["upgrade_costs"]["guild_lineup_capacity"] == {"red_ruby": 5}
    assert loaded["technology"]["upgrade_costs"]["guild_dispatch_capacity"] == {"red_ruby": 5}
    assert loaded["technology"]["upgrade_costs"]["mysticism"] == {"red_ruby": 200}
    assert loaded["technology"]["upgrade_cost_curves"]["standard_10"]["2"] == 3
    assert loaded["technology"]["upgrade_cost_curves"]["standard_10"]["10"] == 350
    assert loaded["technology"]["upgrade_cost_curve_by_tech"]["equipment_forge"] == "standard_10"
    assert loaded["technology"]["upgrade_cost_overrides"]["mysticism"] == {
        "2": {"red_ruby": 333, "gold_bar": 155},
        "3": {"red_ruby": 300, "gold_bar": 200},
    }
    assert loaded["technology"]["names"]["mysticism"] == "神秘学"
    assert loaded["warehouse"]["exchange_costs"]["gear_green"] == 60
    assert loaded["warehouse"]["daily_exchange_limit"] == 12
    assert loaded["hero_pool"]["slot_limit"] == 2
    assert loaded["hero_pool"]["battle_lineup_limit"] == 20
    assert loaded["hero_pool"]["replace_cooldown_seconds"] == 1200


def test_load_guild_rules_reads_yaml_via_cache(monkeypatch):
    clear_guild_rules_cache()
    try:
        monkeypatch.setattr(
            "guilds.constants.load_yaml_data",
            lambda *args, **kwargs: {
                "pagination": {"guild_hall_display_limit": 9},
                "warehouse": {"daily_exchange_limit": 7},
            },
        )

        loaded = load_guild_rules()

        assert loaded["pagination"]["guild_hall_display_limit"] == 9
        assert loaded["warehouse"]["daily_exchange_limit"] == 7
    finally:
        clear_guild_rules_cache()


def test_load_guild_rules_normalizes_pvp_section():
    rules = normalize_guild_rules(
        {
            "pvp": {
                "base_travel_time_seconds": 28800,
                "newbie_protection_seconds": 172800,
                "defeat_protection_seconds": 43200,
                "max_daily_attack_count": 2,
                "max_daily_defense_count": 3,
                "max_target_level_gap": 3,
                "silver_floor": 20000,
                "silver_loot_percent": 10,
                "warehouse_loot_percent": 20,
                "fixed_attack_cost_silver": 10000,
                "warehouse_loot_whitelist": ["grain", "gold_bar", "red_ruby"],
            }
        }
    )

    assert rules["pvp"]["fixed_attack_cost_silver"] == 10000
    assert rules["pvp"]["base_travel_time_seconds"] == 28800
    assert rules["pvp"]["warehouse_loot_percent"] == 20
    assert rules["pvp"]["warehouse_loot_whitelist"] == ["grain", "gold_bar", "red_ruby"]
