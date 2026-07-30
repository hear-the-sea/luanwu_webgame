from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from gameplay.services.virtual_player_core.random_context import RandomContext
from gameplay.services.virtual_player_core.strategy import (
    BotDevelopmentPlan,
    DevelopmentPlanCatalog,
    InvalidDevelopmentPlanError,
    UnsupportedPlanSchemaError,
    canonical_development_plan_bytes,
    development_plan_checksum,
    generate_development_plan,
    load_development_plan_json,
    parse_development_plan,
    upgrade_development_plan,
)


def _context(**overrides) -> RandomContext:
    values = {
        "rng_version": 1,
        "growth_seed": 314159,
        "engine_version": 2,
        "plan_schema_version": 1,
        "policy_version": 1,
        "maintenance_sequence": 0,
    }
    values.update(overrides)
    return RandomContext(**values)


def _catalog(*, reverse: bool = False) -> DevelopmentPlanCatalog:
    values = {
        "guest_archetypes": ("martial", "scholar", "support", "defender"),
        "troop_classes": ("dao", "qiang", "jian", "quan", "gong"),
        "gear_stats": ("attack", "defense", "health", "speed"),
        "skill_kinds": ("active", "passive", "support"),
        "building_keys": ("farm", "granary", "juxianzhuang", "barracks"),
        "technology_keys": ("agriculture", "commerce", "dao_attack", "city_defense"),
    }
    if reverse:
        values = {key: tuple(reversed(items)) for key, items in values.items()}
    return DevelopmentPlanCatalog(**values)


def _plan(**overrides) -> BotDevelopmentPlan:
    values = {
        "schema_version": 1,
        "optimization_bias": 0.75,
        "inertia_bias": 0.4,
        "roster_focus": 0.8,
        "preferred_guest_archetypes": ("martial", "support"),
        "primary_troop_class": "dao",
        "secondary_troop_class": "qiang",
        "troop_mix": (("dao", 0.7), ("qiang", 0.3)),
        "preferred_gear_stats": ("attack",),
        "preferred_skill_kinds": ("active",),
        "building_focuses": ("farm",),
        "technology_focuses": ("agriculture",),
    }
    values.update(overrides)
    return BotDevelopmentPlan(**values)


def test_generation_is_deterministic_and_catalog_order_independent() -> None:
    first = generate_development_plan(context=_context(), archetype="dojo", catalog=_catalog())
    second = generate_development_plan(context=_context(), archetype="dojo", catalog=_catalog(reverse=True))

    assert first == second
    assert parse_development_plan(first.to_payload(), catalog=_catalog()) == first
    assert canonical_development_plan_bytes(first) == canonical_development_plan_bytes(second)


def test_generation_has_a_frozen_checksum_vector() -> None:
    plan = generate_development_plan(context=_context(), archetype="dojo", catalog=_catalog())

    assert development_plan_checksum(plan) == "bb7ab9ceea4bccb5accf300b1e57797305c3731f5e73716d79e6ef61dda3336f"


def test_generation_uses_persisted_context_and_archetype() -> None:
    baseline = generate_development_plan(context=_context(), archetype="balanced", catalog=_catalog())
    changed_seed = generate_development_plan(
        context=_context(growth_seed=314160),
        archetype="balanced",
        catalog=_catalog(),
    )
    changed_archetype = generate_development_plan(context=_context(), archetype="guard", catalog=_catalog())

    assert baseline != changed_seed
    assert baseline != changed_archetype


def test_plan_is_frozen_and_serializes_only_json_types() -> None:
    plan = _plan()

    with pytest.raises(FrozenInstanceError):
        plan.roster_focus = 0.1  # type: ignore[misc]
    assert plan.to_payload()["troop_mix"] == [["dao", 0.7], ["qiang", 0.3]]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("optimization_bias", True, "optimization_bias"),
        ("inertia_bias", float("inf"), "inertia_bias"),
        ("roster_focus", -0.01, "roster_focus"),
        ("primary_troop_class", "", "primary_troop_class"),
        ("secondary_troop_class", "dao", "must differ"),
        ("troop_mix", (("dao", 0.8), ("qiang", 0.3)), "sum to 1"),
        ("preferred_gear_stats", ("attack", "attack"), "must be unique"),
    ],
)
def test_plan_rejects_invalid_values(field: str, value, message: str) -> None:
    with pytest.raises(InvalidDevelopmentPlanError, match=message):
        _plan(**{field: value})


def test_parser_rejects_missing_and_unknown_fields() -> None:
    payload = _plan().to_payload()
    del payload["building_focuses"]
    payload["building_focusses"] = ["farm"]

    with pytest.raises(InvalidDevelopmentPlanError, match="missing fields: building_focuses"):
        parse_development_plan(payload)

    payload["building_focuses"] = ["farm"]
    with pytest.raises(InvalidDevelopmentPlanError, match="unknown fields: building_focusses"):
        parse_development_plan(payload)


def test_parser_rejects_invalid_json_and_unknown_catalog_references() -> None:
    with pytest.raises(InvalidDevelopmentPlanError, match="not valid JSON"):
        load_development_plan_json("{")

    payload = _plan(building_focuses=("unknown",)).to_payload()
    with pytest.raises(InvalidDevelopmentPlanError, match="building_focuses has unknown references"):
        parse_development_plan(payload, catalog=_catalog())


def test_schema_versions_fail_closed_and_upgrade_is_explicit() -> None:
    payload = _plan().to_payload()
    assert upgrade_development_plan(payload, target_schema_version=1) == _plan()

    payload["schema_version"] = 2
    with pytest.raises(UnsupportedPlanSchemaError, match="schema: 2"):
        parse_development_plan(payload)
    with pytest.raises(UnsupportedPlanSchemaError, match="No BotDevelopmentPlan upgrade path"):
        upgrade_development_plan(_plan().to_payload(), target_schema_version=2)


def test_catalog_requires_two_troop_classes_and_non_empty_taxonomies() -> None:
    with pytest.raises(InvalidDevelopmentPlanError, match="troop_classes requires at least 2"):
        DevelopmentPlanCatalog(
            guest_archetypes=("martial",),
            troop_classes=("dao",),
            gear_stats=("attack",),
            skill_kinds=("active",),
            building_keys=("farm",),
            technology_keys=("agriculture",),
        )
