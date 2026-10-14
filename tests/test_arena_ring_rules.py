import pytest

from core.utils.yaml_schema import get_supported_yaml_configs, validate_arena_ring_rules
from gameplay.services.arena import ring_rules


@pytest.fixture(autouse=True)
def reset_rules_cache():
    ring_rules.clear_ring_rules_cache()
    yield
    ring_rules.clear_ring_rules_cache()


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        "bad",
        {"time_interval_seconds": 0, "daily_challenges": True, "tenure_seconds": -1},
    ],
)
def test_malformed_rules_use_safe_defaults(monkeypatch, raw):
    monkeypatch.setattr(ring_rules, "load_yaml_data", lambda *a, **k: raw)
    rules = ring_rules.load_ring_rules()
    assert rules["time_interval_seconds"] == 600
    assert rules["daily_challenges"] == 10
    assert rules["tenure_seconds"] == 7200
    assert rules["time_interval_coins"] == 4
    assert rules["daily_time_coins"] == 120
    assert rules["defense_coins"] == 20
    assert rules["participation_coins"] == 10


def test_reward_budget_and_yaml_match_defaults():
    rules = ring_rules.load_ring_rules()
    assert all(rules[key] == value for key, value in ring_rules.DEFAULT_RULES.items())
    assert (
        rules["daily_time_coins"]
        + rules["defense_coins"] * rules["daily_defense_rewards"]
        + rules["participation_coins"] * rules["daily_participation_rewards"]
        + rules["daily_streak_coins"]
        + rules["daily_break_streak_coins"]
    ) == 700


@pytest.mark.parametrize(
    "raw",
    [
        {
            "inspection_cost": 200000,
            "inspection_income": 190000,
            "inspection_fee": 10000,
        },
        {
            "inspection_cost": 200000,
            "inspection_income": 180000,
            "inspection_fee": 10000,
        },
        {"inspection_fee": True},
    ],
)
def test_fee_requires_exact_ten_percent_and_balanced_transfer(monkeypatch, raw):
    monkeypatch.setattr(ring_rules, "load_yaml_data", lambda *a, **k: raw)
    rules = ring_rules.load_ring_rules()
    assert (
        rules["inspection_cost"],
        rules["inspection_income"],
        rules["inspection_fee"],
    ) == (200000, 180000, 20000)


def test_zero_cooldown_cache_clear_and_return_copy(monkeypatch):
    raw = {"cooldown_seconds": 0}
    monkeypatch.setattr(ring_rules, "load_yaml_data", lambda *a, **k: raw)
    rules = ring_rules.load_ring_rules()
    assert rules["cooldown_seconds"] == 0
    rules["time_interval_seconds"] = 0
    assert ring_rules.load_ring_rules()["time_interval_seconds"] == 600
    raw["cooldown_seconds"] = 30
    assert ring_rules.load_ring_rules()["cooldown_seconds"] == 0
    ring_rules.clear_ring_rules_cache()
    assert ring_rules.load_ring_rules()["cooldown_seconds"] == 30


def test_ring_yaml_schema_uses_runtime_defaults_and_supports_partial_overrides():
    assert "arena_ring_rules.yaml" in get_supported_yaml_configs()
    assert validate_arena_ring_rules(ring_rules.DEFAULT_RULES).is_valid
    assert validate_arena_ring_rules({}).is_valid
    assert validate_arena_ring_rules({"cooldown_seconds": 0}).is_valid
    assert validate_arena_ring_rules({"inspection_cost": 100, "inspection_income": 90, "inspection_fee": 10}).is_valid


@pytest.mark.parametrize(
    "rules",
    [
        None,
        [],
        {"daily_challenges": True},
        {"max_guests": 1.5},
        {"time_interval_seconds": 0},
        {"cooldown_seconds": -1},
        {"daily_challanges": 10},
        {"inspection_income": 190000},
        {"inspection_cost": 190000, "inspection_income": 180000, "inspection_fee": 10000},
    ],
)
def test_ring_yaml_schema_rejects_invalid_values_and_unbalanced_inspection(rules):
    assert not validate_arena_ring_rules(rules).is_valid
