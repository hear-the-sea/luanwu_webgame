from __future__ import annotations

from core.utils.yaml_schema import validate_guest_growth_rules, validate_guild_rules, validate_technology_templates
from tests.yaml_schema_new_configs.support import assert_invalid


def test_guild_rules_rejects_non_dict_root():
    result = validate_guild_rules("oops")
    assert_invalid(result)


def test_guild_rules_rejects_missing_required_sections():
    result = validate_guild_rules({})
    assert_invalid(result)
    assert len([error for error in result.errors if "missing required section" in error.message]) >= 3


def test_guild_rules_rejects_zero_page_size():
    data = {
        "pagination": {"guild_list_page_size": 0, "guild_hall_display_limit": 20},
        "creation": {},
        "contribution": {},
    }
    result = validate_guild_rules(data)
    assert_invalid(result, substring="guild_list_page_size")


def test_guild_rules_rejects_negative_daily_limit():
    data = {
        "pagination": {"guild_list_page_size": 20, "guild_hall_display_limit": 20},
        "creation": {},
        "contribution": {"daily_limits": {"silver": -1}},
    }
    result = validate_guild_rules(data)
    assert_invalid(result, substring="non-negative")


def test_guild_rules_rejects_zero_contribution_unit():
    data = {
        "pagination": {"guild_list_page_size": 20, "guild_hall_display_limit": 20},
        "creation": {},
        "contribution": {"units": {"silver": 0}},
    }
    result = validate_guild_rules(data)
    assert_invalid(result, substring="positive integer")
    assert result.errors[0].path == "contribution.units.silver"


def test_guild_rules_rejects_negative_troop_rate():
    data = {
        "pagination": {"guild_list_page_size": 20, "guild_hall_display_limit": 20},
        "creation": {},
        "contribution": {"troop_rates": {7: -1}},
    }
    result = validate_guild_rules(data)
    assert_invalid(result, substring="non-negative integer")
    assert result.errors[0].path == "contribution.troop_rates.7"


def test_guild_rules_rejects_negative_daily_troop_contribution_limit():
    data = {
        "pagination": {"guild_list_page_size": 20, "guild_hall_display_limit": 20},
        "creation": {},
        "contribution": {"daily_troop_contribution_limit": -1},
    }
    result = validate_guild_rules(data)
    assert_invalid(result, substring="non-negative integer")
    assert result.errors[0].path == "contribution.daily_troop_contribution_limit"


def test_guest_growth_rules_rejects_non_dict_root():
    result = validate_guest_growth_rules([])
    assert_invalid(result)


def test_guest_growth_rules_rejects_unknown_rarity_in_hp_profiles():
    data = {"rarity_hp_profiles": {"legendary": {"base": 999}}}
    result = validate_guest_growth_rules(data)
    assert_invalid(result, substring="unknown rarity 'legendary'")


def test_guest_growth_rules_rejects_non_positive_hp_base():
    data = {"rarity_hp_profiles": {"green": {"base": 0}}}
    result = validate_guest_growth_rules(data)
    assert_invalid(result, substring="base")


def test_guest_growth_rules_rejects_malformed_growth_range():
    data = {"rarity_attribute_growth_range": {"green": [3]}}
    result = validate_guest_growth_rules(data)
    assert_invalid(result, substring="list of [min, max]")


def test_guest_growth_rules_rejects_unknown_archetype():
    data = {"archetype_attribute_weights": {"soldier": {"force": 40}}}
    result = validate_guest_growth_rules(data)
    assert_invalid(result, substring="unknown archetype 'soldier'")


def test_technology_templates_rejects_non_dict_root():
    result = validate_technology_templates("nope")
    assert_invalid(result)


def test_technology_templates_rejects_missing_technologies():
    result = validate_technology_templates({"categories": []})
    assert_invalid(result, substring="missing required key 'technologies'")


def test_technology_templates_rejects_unknown_category():
    data = {
        "technologies": [
            {
                "key": "fire_art",
                "name": "Fire Art",
                "category": "magic",
                "effect_type": "bonus",
                "max_level": 10,
                "base_cost": 1000,
            }
        ]
    }
    result = validate_technology_templates(data)
    assert_invalid(result, substring="category")


def test_technology_templates_rejects_zero_max_level():
    data = {
        "technologies": [
            {
                "key": "scout_art",
                "name": "Scout Art",
                "category": "basic",
                "effect_type": "scout_bonus",
                "max_level": 0,
                "base_cost": 8000,
            }
        ]
    }
    result = validate_technology_templates(data)
    assert_invalid(result, substring="max_level")


def test_technology_templates_rejects_duplicate_tech_keys():
    data = {
        "technologies": [
            {
                "key": "scout_art",
                "name": "Scout Art",
                "category": "basic",
                "effect_type": "scout_bonus",
                "max_level": 10,
                "base_cost": 8000,
            },
            {
                "key": "scout_art",
                "name": "Scout Art Copy",
                "category": "martial",
                "effect_type": "scout_bonus",
                "max_level": 5,
                "base_cost": 5000,
            },
        ]
    }
    result = validate_technology_templates(data)
    assert_invalid(result, substring="duplicate")


def test_technology_templates_rejects_negative_base_cost():
    data = {
        "technologies": [
            {
                "key": "scout_art",
                "name": "Scout Art",
                "category": "basic",
                "effect_type": "scout_bonus",
                "max_level": 10,
                "base_cost": -1,
            }
        ]
    }
    result = validate_technology_templates(data)
    assert_invalid(result, substring="base_cost")
