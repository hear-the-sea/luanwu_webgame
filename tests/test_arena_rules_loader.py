from gameplay.services.arena.rules import (
    DEFAULT_ARENA_RULES,
    clear_arena_rules_cache,
    load_arena_rules,
    normalize_arena_rules,
)


def test_normalize_arena_rules_uses_defaults_for_invalid_root():
    assert normalize_arena_rules(["invalid-root"]) == DEFAULT_ARENA_RULES


def test_normalize_arena_rules_merges_and_clamps_values():
    loaded = normalize_arena_rules(
        {
            "registration": {
                "max_guests_per_entry": "12",
                "registration_silver_cost": "6000",
                "daily_participation_limit": 0,
                "tournament_player_limit": 1,
            },
            "runtime": {
                "round_interval_seconds": "900",
                "completed_retention_seconds": "0",
                "round_retry_seconds": "15",
                "recruiting_lock_key": "arena:test:lock",
                "recruiting_lock_timeout": "7",
            },
            "rewards": {
                "base_participation_coins": "40",
                "rank_bonus_coins": {"1": 300, "2": 200, "0": 999, "bad": 1},
            },
        }
    )

    assert loaded == {
        "registration": {
            "max_guests_per_entry": 12,
            "registration_silver_cost": 6000,
            "daily_participation_limit": 1,
            "tournament_player_limit": 2,
        },
        "runtime": {
            "round_interval_seconds": 900,
            "virtual_fill_wait_seconds": 18000,
            "completed_retention_seconds": 0,
            "round_retry_seconds": 15,
            "recruiting_lock_key": "arena:test:lock",
            "recruiting_lock_timeout": 7,
        },
        "rewards": {
            "base_participation_coins": 40,
            "rank_bonus_coins": {1: 300, 2: 200},
        },
    }


def test_load_arena_rules_reads_yaml_via_cache(monkeypatch):
    clear_arena_rules_cache()
    monkeypatch.setattr(
        "gameplay.services.arena.rules.load_yaml_data",
        lambda *args, **kwargs: {
            "registration": {"max_guests_per_entry": 11},
            "runtime": {"recruiting_lock_key": "arena:cached:lock"},
            "rewards": {"base_participation_coins": 35},
        },
    )

    loaded = load_arena_rules()

    assert loaded["registration"]["max_guests_per_entry"] == 11
    assert loaded["runtime"]["recruiting_lock_key"] == "arena:cached:lock"
    assert loaded["rewards"]["base_participation_coins"] == 35
