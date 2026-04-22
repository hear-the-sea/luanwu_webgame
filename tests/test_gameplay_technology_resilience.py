from __future__ import annotations

import pytest

import gameplay.services.technology as tech_service
import gameplay.services.technology_refresh_state as tech_refresh_state


def test_get_tech_bonus_from_levels_tolerates_invalid_level_and_template_rows(monkeypatch):
    monkeypatch.setattr(
        tech_service,
        "load_technology_templates",
        lambda: {
            "technologies": [
                "bad-row",
                {"effect_type": "troop_attack"},  # missing key
                {"key": "atk_1", "effect_type": "troop_attack", "effect_per_level": "bad"},
            ]
        },
    )

    bonus = tech_service.get_tech_bonus_from_levels({"atk_1": "bad"}, "troop_attack")
    assert bonus == 0.0


def test_build_uniform_tech_levels_skips_invalid_rows(monkeypatch):
    monkeypatch.setattr(
        tech_service,
        "load_technology_templates",
        lambda: {
            "technologies": [
                {"key": "t1", "max_level": "3"},
                {"max_level": 5},
                {"key": "t2", "max_level": "bad"},
                "not-a-dict",
            ]
        },
    )

    resolved = tech_service.build_uniform_tech_levels(2)
    assert resolved == {"t1": 2, "t2": 2}


def test_resolve_enemy_tech_levels_tolerates_invalid_values(monkeypatch):
    monkeypatch.setattr(
        tech_service,
        "load_technology_templates",
        lambda: {
            "technologies": [
                {"key": "t1", "max_level": 5},
                {"key": "t2", "max_level": 5},
            ]
        },
    )

    resolved = tech_service.resolve_enemy_tech_levels(
        {
            "level": "bad",
            "levels": {"t1": "7", "t2": "oops", "": "3", "t3": -2},
        }
    )
    assert resolved["t1"] == 7
    assert resolved["t2"] == 0
    assert resolved["t3"] == 0
    assert "" not in resolved


def test_resource_production_bonus_ignores_bad_effect_value(monkeypatch):
    monkeypatch.setattr(
        tech_service,
        "load_technology_templates",
        lambda: {
            "technologies": [
                {
                    "key": "grain_1",
                    "effect_type": "resource_production",
                    "resource_type": "grain",
                    "effect_per_level": "bad",
                }
            ]
        },
    )

    bonus = tech_service.get_resource_production_bonus_from_levels({"grain_1": 2}, "grain")
    assert bonus == 0.1


def test_get_categories_and_troop_classes_return_safe_defaults(monkeypatch):
    monkeypatch.setattr(
        tech_service,
        "load_technology_templates",
        lambda: {
            "categories": "bad-type",
            "troop_classes": "bad-type",
        },
    )

    assert tech_service.get_categories() == []
    assert tech_service.get_troop_classes() == {}


def test_load_technology_templates_returns_empty_when_yaml_root_not_mapping(monkeypatch):
    tech_service.clear_technology_cache()
    monkeypatch.setattr(tech_service, "load_yaml_data", lambda *args, **kwargs: ["invalid-root"])

    data = tech_service.load_technology_templates()
    assert data == {}

    tech_service.clear_technology_cache()


def test_get_guest_stat_bonuses_tolerates_invalid_guest_bonus():
    bonuses = tech_service.get_guest_stat_bonuses({"guest_bonus": "bad-value"})
    assert bonuses == {"attack": 0.0, "defense": 0.0, "hp": 0.0, "agility": 0.0}


def test_build_player_battle_technology_payload_wraps_levels(monkeypatch):
    manor = object()
    monkeypatch.setattr(
        tech_service,
        "get_player_technologies",
        lambda current_manor: {"gong_attack": 7} if current_manor is manor else {},
    )

    payload = tech_service.build_player_battle_technology_payload(manor)

    assert payload == {"levels": {"gong_attack": 7}}


def test_refresh_technology_upgrades_local_fallback_throttles_when_cache_unavailable(monkeypatch, settings):
    settings.MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS = 5
    tech_refresh_state.clear_local_tech_refresh_fallback()

    monkeypatch.setattr(
        tech_service.cache,
        "add",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("cache down")),
    )

    calls = {"finalize": 0}

    def _finalize(_tech, send_notification=True):
        calls["finalize"] += 1
        return True

    monkeypatch.setattr(tech_service, "finalize_technology_upgrade", _finalize)

    class _TechManager:
        def __init__(self, items):
            self._items = items

        def filter(self, **kwargs):
            return list(self._items)

    class _Manor:
        def __init__(self, pk, items):
            self.pk = pk
            self.technologies = _TechManager(items)

    manor = _Manor(1, [object(), object()])
    first = tech_service.refresh_technology_upgrades(manor)
    second = tech_service.refresh_technology_upgrades(manor)

    assert first == 2
    assert second == 0
    assert calls["finalize"] == 2
    tech_refresh_state.clear_local_tech_refresh_fallback()


def test_refresh_technology_upgrades_local_fallback_allows_after_interval(monkeypatch, settings):
    settings.MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS = 5
    tech_refresh_state.clear_local_tech_refresh_fallback()

    monkeypatch.setattr(
        tech_service.cache,
        "add",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("cache down")),
    )
    monotonic_values = iter([100.0, 102.0, 106.1])
    monkeypatch.setattr(tech_service.time, "monotonic", lambda: next(monotonic_values))

    calls = {"finalize": 0}

    def _finalize(_tech, send_notification=True):
        calls["finalize"] += 1
        return True

    monkeypatch.setattr(tech_service, "finalize_technology_upgrade", _finalize)

    class _TechManager:
        def __init__(self, items):
            self._items = items

        def filter(self, **kwargs):
            return list(self._items)

    class _Manor:
        def __init__(self, pk, items):
            self.pk = pk
            self.technologies = _TechManager(items)

    manor = _Manor(2, [object()])
    first = tech_service.refresh_technology_upgrades(manor)
    second = tech_service.refresh_technology_upgrades(manor)
    third = tech_service.refresh_technology_upgrades(manor)

    assert first == 1
    assert second == 0
    assert third == 1
    assert calls["finalize"] == 2
    tech_refresh_state.clear_local_tech_refresh_fallback()


def test_refresh_technology_upgrades_runtime_cache_error_bubbles_up(monkeypatch, settings):
    settings.MANOR_STATE_REFRESH_MIN_INTERVAL_SECONDS = 5
    tech_refresh_state.clear_local_tech_refresh_fallback()

    monkeypatch.setattr(
        tech_service.cache,
        "add",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("cache down")),
    )

    class _TechManager:
        def filter(self, **kwargs):
            raise AssertionError("should not query technologies when cache contract is broken")

    class _Manor:
        pk = 3
        technologies = _TechManager()

    with pytest.raises(RuntimeError, match="cache down"):
        tech_service.refresh_technology_upgrades(_Manor())

    tech_refresh_state.clear_local_tech_refresh_fallback()
