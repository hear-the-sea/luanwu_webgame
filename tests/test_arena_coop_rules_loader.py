from gameplay.services.arena.coop_rules import (
    DEFAULT_ARENA_COOP_RULES,
    clear_arena_coop_rules_cache,
    load_arena_coop_rules,
    normalize_arena_coop_rules,
)


def test_normalize_arena_coop_rules_uses_defaults_for_invalid_root():
    assert normalize_arena_coop_rules(["invalid-root"]) == DEFAULT_ARENA_COOP_RULES


def test_normalize_arena_coop_rules_merges_rewards_and_enemy_keys():
    loaded = normalize_arena_coop_rules(
        {
            "registration": {"player_limit": "5", "guest_limit_per_entry": "3", "daily_participation_limit": "2"},
            "rewards": {"participation_coins": "40", "rank_rewards": {"1": 90, "2": 60, "3": 35}},
            "rare_drop": {"item_key": "equip_tulongdao", "chance_bps": "12"},
            "enemy": {"boss": {"template_key": "arena_gl_top_zhang_wuji_boss"}},
        }
    )

    assert loaded["registration"]["player_limit"] == 5
    assert loaded["registration"]["guest_limit_per_entry"] == 3
    assert loaded["rewards"]["participation_coins"] == 40
    assert loaded["rewards"]["rank_rewards"] == {1: 90, 2: 60, 3: 35}
    assert loaded["rare_drop"]["item_key"] == "equip_tulongdao"
    assert loaded["enemy"]["boss"]["template_key"] == "arena_gl_top_zhang_wuji_boss"


def test_load_arena_coop_rules_reads_yaml_via_cache(monkeypatch):
    clear_arena_coop_rules_cache()
    monkeypatch.setattr(
        "gameplay.services.arena.coop_rules.load_yaml_data",
        lambda *args, **kwargs: {"registration": {"prepare_duration_seconds": 180}},
    )

    loaded = load_arena_coop_rules()

    assert loaded["registration"]["prepare_duration_seconds"] == 180
