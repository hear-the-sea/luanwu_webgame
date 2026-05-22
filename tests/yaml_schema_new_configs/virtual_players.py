from core.utils.yaml_validators.virtual_players import validate_virtual_players


def _messages(result):
    return [error.message for error in result.errors]


def test_virtual_players_accepts_lifecycle_and_inventory_cap_fields():
    data = {
        "lifecycle": {
            "active_days": [30, 90],
            "abandoned_days": [14, 45],
            "next_growth_hours": [2, 18],
            "empty_hit_stale_threshold": 3,
            "empty_hit_window_hours": 24,
            "stale_no_interaction_days": 30,
        },
        "projection": {
            "item_template_keys": ["grain", "red_ruby"],
            "loot_item_template_keys": ["gold_bar"],
            "high_tier_skill_keys": ["stratagem_burst"],
            "high_tier_skill_chance": 0.05,
            "high_tier_skills_per_guest": [1, 1],
            "loot_item_quantity": [1, 3],
            "loot_limits": {"real_attacker_daily_resource_cap": 1000000},
            "rare_item_daily_global_cap": 20,
            "powerful_item_daily_global_cap": 5,
            "powerful_item_min_price": 100000,
        },
    }

    assert validate_virtual_players(data).is_valid


def test_virtual_players_rejects_negative_inventory_cap_fields():
    data = {
        "projection": {
            "rare_item_daily_global_cap": -1,
            "powerful_item_daily_global_cap": -1,
            "powerful_item_min_price": -1,
            "loot_limits": {"real_attacker_daily_resource_cap": -1},
        }
    }

    result = validate_virtual_players(data)

    assert not result.is_valid
    messages = _messages(result)
    assert any("rare_item_daily_global_cap" in message for message in messages)
    assert any("powerful_item_daily_global_cap" in message for message in messages)
    assert any("powerful_item_min_price" in message for message in messages)
    assert any("real_attacker_daily_resource_cap" in message for message in messages)


def test_virtual_players_rejects_invalid_inventory_projection_fields():
    data = {
        "projection": {
            "item_template_keys": ["grain", 1],
            "loot_item_template_keys": "gold_bar",
            "high_tier_skill_keys": ["stratagem_burst", 1],
            "high_tier_skill_chance": 1.5,
            "high_tier_skills_per_guest": [1],
            "loot_item_quantity": [3, "bad"],
            "rare_item_daily_global_cap": "20",
            "powerful_item_daily_global_cap": 1.5,
            "powerful_item_min_price": "100000",
            "loot_limits": {"real_attacker_daily_resource_cap": "1000000"},
        }
    }

    result = validate_virtual_players(data)

    assert not result.is_valid
    messages = [str(error) for error in result.errors]
    assert any("item_template_keys[1]" in message for message in messages)
    assert any("loot_item_template_keys" in message for message in messages)
    assert any("high_tier_skill_keys[1]" in message for message in messages)
    assert any("high_tier_skill_chance" in message for message in messages)
    assert any("high_tier_skills_per_guest" in message for message in messages)
    assert any("loot_item_quantity" in message for message in messages)
    assert any("rare_item_daily_global_cap" in message for message in messages)
    assert any("powerful_item_daily_global_cap" in message for message in messages)
    assert any("powerful_item_min_price" in message for message in messages)
    assert any("real_attacker_daily_resource_cap" in message for message in messages)
