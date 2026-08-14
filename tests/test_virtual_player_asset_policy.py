from __future__ import annotations

from gameplay.services.virtual_player_core.asset_policy import (
    VIRTUAL_PLAYER_EXCLUDED_TROOP_KEYS,
    VIRTUAL_PLAYER_RETAINER_COUNT,
    filter_virtual_building_focuses,
    is_virtual_player_building_useful,
    resolve_virtual_technology_focuses,
    useful_virtual_technology_keys,
)


def test_virtual_player_building_policy_removes_human_only_buildings() -> None:
    focuses = filter_virtual_building_focuses(
        (
            "forge",
            "farm",
            "jiadingfang",
            "tax_office",
            "treasury",
            "juxianzhuang",
        )
    )

    assert focuses == ("farm", "tax_office", "juxianzhuang")
    assert is_virtual_player_building_useful("bathhouse")
    assert not is_virtual_player_building_useful("jiadingfang")
    assert not is_virtual_player_building_useful("wall")


def test_virtual_player_technology_policy_tracks_active_troop_classes() -> None:
    useful = useful_virtual_technology_keys(("dao", "qiang"))

    assert {"architecture", "farming", "dao_attack", "qiang_recruit"} <= useful
    assert "gong_attack" not in useful
    assert "forging" not in useful
    assert "scout_art" not in useful


def test_virtual_player_technology_policy_ignores_excluded_zero_weight_classes() -> None:
    useful = useful_virtual_technology_keys(("dao", "scout"))

    assert "dao_attack" in useful
    assert "scout_art" not in useful


def test_virtual_player_technology_focuses_add_useful_active_class_tech_only() -> None:
    focuses = resolve_virtual_technology_focuses(
        ("forging", "gong_defense", "dao_attack"),
        troop_classes=("dao", "qiang"),
    )

    assert focuses[:3] == ("dao_attack", "architecture", "farming")
    assert "qiang_recruit" not in focuses
    assert "gong_defense" not in focuses
    assert "forging" not in focuses


def test_virtual_players_have_no_retainers() -> None:
    assert VIRTUAL_PLAYER_RETAINER_COUNT == 0


def test_virtual_players_do_not_generate_scouts() -> None:
    assert VIRTUAL_PLAYER_EXCLUDED_TROOP_KEYS == frozenset({"scout"})
