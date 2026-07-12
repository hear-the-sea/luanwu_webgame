from __future__ import annotations

from core.utils.yaml_schema import validate_arena_coop_rules, validate_arena_rules, validate_trade_market_rules
from tests.yaml_schema.support import assert_has_error, assert_valid


class TestArenaRulesValidation:
    def test_valid_minimal(self):
        data = {
            "registration": {"max_guests_per_entry": 10, "registration_silver_cost": 5000},
            "runtime": {"round_interval_seconds": 600},
            "rewards": {"base_participation_coins": 30, "rank_bonus_coins": {1: 100}},
        }
        assert_valid(validate_arena_rules(data))

    def test_missing_sections(self):
        result = validate_arena_rules({})
        assert_has_error(result, substring="missing required section 'registration'")
        assert_has_error(result, substring="missing required section 'runtime'")
        assert_has_error(result, substring="missing required section 'rewards'")

    def test_zero_registration_cost(self):
        data = {
            "registration": {"registration_silver_cost": 0},
            "runtime": {},
            "rewards": {},
        }
        result = validate_arena_rules(data)
        assert_has_error(result, substring="must be >= 1")

    def test_zero_virtual_fill_wait_is_invalid(self):
        data = {
            "registration": {},
            "runtime": {"virtual_fill_wait_seconds": 0},
            "rewards": {},
        }
        result = validate_arena_rules(data)
        assert_has_error(result, substring="field 'virtual_fill_wait_seconds' must be >= 1")


class TestArenaCoopRulesValidation:
    def test_zero_virtual_fill_wait_is_invalid(self):
        data = {
            "registration": {},
            "runtime": {"virtual_fill_wait_seconds": 0},
            "contribution": {},
            "rewards": {},
            "rare_drop": {},
            "enemy": {},
        }
        result = validate_arena_coop_rules(data)
        assert_has_error(result, substring="field 'virtual_fill_wait_seconds' must be >= 1")


class TestTradeMarketRulesValidation:
    def test_valid(self):
        data = {"listing_fees": {7200: 5000, 28800: 10000}}
        assert_valid(validate_trade_market_rules(data))

    def test_missing_listing_fees(self):
        result = validate_trade_market_rules({})
        assert_has_error(result, substring="missing required key 'listing_fees'")

    def test_negative_fee(self):
        data = {"listing_fees": {3600: -100}}
        result = validate_trade_market_rules(data)
        assert_has_error(result, substring="fee must be >= 0")

    def test_non_number_fee(self):
        data = {"listing_fees": {3600: "free"}}
        result = validate_trade_market_rules(data)
        assert_has_error(result, substring="expected a number")
