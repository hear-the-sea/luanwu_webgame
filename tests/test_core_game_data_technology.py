from __future__ import annotations

from core.game_data import technology as tech


def test_get_tech_bonus_from_levels_tolerates_invalid_level_value(monkeypatch):
    monkeypatch.setattr(
        tech,
        "load_technology_templates",
        lambda: {
            "technologies": [
                {"key": "atk_1", "effect_type": "troop_attack", "troop_class": "dao", "effect_per_level": 0.1},
            ]
        },
    )

    bonus = tech.get_tech_bonus_from_levels({"atk_1": "bad"}, "troop_attack", "dao")
    assert bonus == 0.0


def test_get_tech_bonus_from_levels_skips_invalid_template_rows(monkeypatch):
    monkeypatch.setattr(
        tech,
        "load_technology_templates",
        lambda: {
            "technologies": [
                "not-a-dict",
                {"effect_type": "troop_attack", "effect_per_level": 0.1},  # missing key
                {"key": "atk_2", "effect_type": "troop_attack", "effect_per_level": "bad"},
            ]
        },
    )

    bonus = tech.get_tech_bonus_from_levels({"atk_2": 2}, "troop_attack", None)
    assert bonus == 0.2


def test_build_uniform_tech_levels_skips_bad_entries(monkeypatch):
    monkeypatch.setattr(
        tech,
        "load_technology_templates",
        lambda: {
            "technologies": [
                {"key": "t1", "max_level": "3"},
                {"max_level": 5},  # missing key
                {"key": "t2", "max_level": "bad"},
                "not-a-dict",
            ]
        },
    )

    resolved = tech.build_uniform_tech_levels(2)
    assert resolved == {"t1": 2, "t2": 2}


def test_resolve_enemy_tech_levels_clamps_invalid_values(monkeypatch):
    monkeypatch.setattr(
        tech,
        "load_technology_templates",
        lambda: {
            "technologies": [
                {"key": "t1", "max_level": 5},
                {"key": "t2", "max_level": 5},
            ]
        },
    )

    resolved = tech.resolve_enemy_tech_levels(
        {
            "level": "bad",
            "levels": {
                "t1": "7",
                "t2": "oops",
                "": "3",
                "t3": -2,
            },
        }
    )
    assert resolved["t1"] == 7
    assert resolved["t2"] == 0
    assert "t3" in resolved and resolved["t3"] == 0
    assert "" not in resolved


def test_load_technology_templates_returns_empty_when_yaml_root_not_mapping(monkeypatch):
    tech.clear_technology_cache()
    monkeypatch.setattr(tech, "load_yaml_data", lambda *args, **kwargs: ["invalid-root"])

    data = tech.load_technology_templates()
    assert data == {}

    tech.clear_technology_cache()


def test_get_troop_class_for_key_ignores_invalid_troop_classes_shape(monkeypatch):
    monkeypatch.setattr(
        tech,
        "load_technology_templates",
        lambda: {
            "troop_classes": {
                "dao": {"troops": ["dao_ke"]},
                "bad": "invalid-row",
            }
        },
    )
    tech.clear_technology_cache()
    assert tech.get_troop_class_for_key("dao_ke") == "dao"
    assert tech.get_troop_class_for_key("unknown") is None
    tech.clear_technology_cache()


def test_get_guest_stat_bonuses_tolerates_invalid_guest_bonus():
    bonuses = tech.get_guest_stat_bonuses({"guest_bonus": "bad-value"})
    assert bonuses == {"attack": 0.0, "defense": 0.0, "hp": 0.0}


def test_guest_bonus_does_not_grant_combat_agility():
    bonuses = tech.get_guest_stat_bonuses({"guest_bonus": 0.2})
    assert bonuses == {"attack": 0.2, "defense": 0.2, "hp": 0.2}


def test_troop_agility_technology_bonus_is_retained():
    bonuses = tech.get_troop_stat_bonuses_from_levels({"dao_agility": 2}, "dao_ke")
    assert bonuses["agility"] == 0.2
