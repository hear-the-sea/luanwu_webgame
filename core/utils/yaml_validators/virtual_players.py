"""Validator for virtual player runtime configuration."""

from __future__ import annotations

from typing import Any

from common.constants.virtual_players import (
    DEFAULT_VIRTUAL_PLAYER_PRESTIGE_BANDS,
    VIRTUAL_PLAYER_ARCHETYPES,
    VIRTUAL_PLAYER_INVENTORY_EFFECT_TYPES,
)

from .base import ValidationResult, _check_positive, _check_type

_SELECTION_SENTINELS = {"__all__", "__all_tradeable__"}
_GEAR_RARITIES = {"black", "gray", "green", "red", "blue", "purple", "orange"}
_COMBAT_PERSONAS = VIRTUAL_PLAYER_ARCHETYPES
_LIFECYCLE_PERSONAS = {"tourist", "casual", "committed", "veteran"}
_STRENGTH_QUANTILES = {"p25", "p50", "p75"}
_PERSONA_MULTIPLIERS = {"guest_level_multiplier", "guest_count_multiplier", "troop_multiplier"}


def _validate_int(
    value: Any,
    *,
    result: ValidationResult,
    file: str,
    path: str,
    minimum: int,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        result.add(file, path, "expected an integer")
        return
    if value < minimum:
        result.add(file, path, f"must be >= {minimum}")


def _validate_int_range(
    value: Any,
    *,
    result: ValidationResult,
    file: str,
    path: str,
    allow_open_high: bool = False,
    min_value: int | None = None,
) -> None:
    if not isinstance(value, list) or len(value) != 2:
        result.add(file, path, "expected a two-item list range")
        return

    low, high = value
    if not isinstance(low, int):
        result.add(file, path, "range lower bound must be an integer")
    if high is not None or not allow_open_high:
        if not isinstance(high, int):
            result.add(file, path, "range upper bound must be an integer")
            return
    if isinstance(low, int) and isinstance(high, int) and high < low:
        result.add(file, path, "range upper bound must be >= lower bound")
    if min_value is not None:
        if isinstance(low, int) and low < min_value:
            result.add(file, path, f"range lower bound must be >= {min_value}")
        if isinstance(high, int) and high < min_value:
            result.add(file, path, f"range upper bound must be >= {min_value}")


def _validate_ratio_range(value: Any, *, result: ValidationResult, file: str, path: str) -> None:
    if not isinstance(value, list) or len(value) != 2:
        result.add(file, path, "expected a two-item ratio range")
        return
    low, high = value
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        result.add(file, path, "ratio bounds must be numbers")
        return
    if low < 0 or high > 1:
        result.add(file, path, "ratio bounds must be between 0 and 1")
    if high < low:
        result.add(file, path, "ratio upper bound must be >= lower bound")


def _validate_ratio(value: Any, *, result: ValidationResult, file: str, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        result.add(file, path, "expected a number between 0 and 1")
        return
    if value < 0 or value > 1:
        result.add(file, path, "must be between 0 and 1")


def _validate_string_list(value: Any, *, result: ValidationResult, file: str, path: str, field_name: str) -> None:
    if isinstance(value, str):
        allowed_sentinels = {"__all__"}
        if field_name in {"item_template_keys", "loot_item_template_keys"}:
            allowed_sentinels = _SELECTION_SENTINELS
        if value not in allowed_sentinels:
            result.add(file, path, f"field '{field_name}' expected list or supported selector")
        return
    if not isinstance(value, list):
        result.add(file, path, f"field '{field_name}' expected list, got {type(value).__name__}")
        return
    for idx, item in enumerate(value):
        if not isinstance(item, str):
            result.add(file, f"{path}.{field_name}[{idx}]", "expected string")


def _validate_prestige_chance_table(value: Any, *, result: ValidationResult, file: str, path: str) -> None:
    if not isinstance(value, list):
        result.add(file, path, "powerful_item_prestige_chance expected a list of prestige chance entries")
        return
    for idx, row in enumerate(value):
        row_path = f"{path}[{idx}]"
        if not isinstance(row, dict):
            result.add(file, row_path, "expected a mapping")
            continue
        min_prestige = row.get("min_prestige")
        if not isinstance(min_prestige, int) or min_prestige < 0:
            result.add(file, f"{row_path}.min_prestige", "expected a non-negative integer")
        _validate_ratio(row.get("chance"), result=result, file=file, path=f"{row_path}.chance")


def validate_virtual_players(data: dict, *, file: str = "virtual_players.yaml") -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    enabled = data.get("enabled")
    if enabled is not None:
        _check_type(enabled, bool, result=result, file=file, path="<root>", field_name="enabled")

    population = data.get("population")
    if population is not None:
        if not isinstance(population, dict):
            result.add(file, "population", "expected a mapping")
        else:
            for field_name in (
                "active_player_multiplier",
                "min_per_region",
                "min_attackable_per_band",
                "hard_cap",
            ):
                value = population.get(field_name)
                if value is None:
                    continue
                _check_type(value, int, result=result, file=file, path="population", field_name=field_name)
                _check_positive(value, result=result, file=file, path="population", field_name=field_name)
            for field_name, minimum in (
                ("active_window_days", 1),
                ("cell_floor", 0),
                ("cell_active_multiplier", 0),
                ("region_floor", 0),
                ("region_active_multiplier", 0),
                ("global_floor", 0),
                ("global_active_multiplier", 0),
                ("exploration_supply", 0),
            ):
                if field_name in population:
                    _validate_int(
                        population[field_name],
                        result=result,
                        file=file,
                        path=f"population.{field_name}",
                        minimum=minimum,
                    )
            if "rolling_batch_size" in population:
                _validate_int_range(
                    population["rolling_batch_size"],
                    result=result,
                    file=file,
                    path="population.rolling_batch_size",
                )
            if "retired_reactivation_chance" in population:
                _validate_ratio(
                    population["retired_reactivation_chance"],
                    result=result,
                    file=file,
                    path="population.retired_reactivation_chance",
                )

    prestige_bands = data.get("prestige_bands")
    if prestige_bands is not None:
        if not isinstance(prestige_bands, dict):
            result.add(file, "prestige_bands", "expected a mapping")
        else:
            for band, value in prestige_bands.items():
                _validate_int_range(
                    value,
                    result=result,
                    file=file,
                    path=f"prestige_bands.{band}",
                    allow_open_high=True,
                )

    lifecycle = data.get("lifecycle")
    if lifecycle is not None:
        if not isinstance(lifecycle, dict):
            result.add(file, "lifecycle", "expected a mapping")
        else:
            for field_name in ("active_days", "abandoned_days", "next_growth_hours"):
                if field_name in lifecycle:
                    _validate_int_range(
                        lifecycle[field_name],
                        result=result,
                        file=file,
                        path=f"lifecycle.{field_name}",
                    )
            for field_name in ("empty_hit_stale_threshold", "empty_hit_window_hours", "stale_no_interaction_days"):
                value = lifecycle.get(field_name)
                if value is None:
                    continue
                _check_type(value, int, result=result, file=file, path="lifecycle", field_name=field_name)
                _check_positive(value, result=result, file=file, path="lifecycle", field_name=field_name)

    growth = data.get("growth")
    if growth is not None:
        if not isinstance(growth, dict):
            result.add(file, "growth", "expected a mapping")
        else:
            stage_caps = growth.get("stage_caps")
            if stage_caps is not None:
                if not isinstance(stage_caps, dict):
                    result.add(file, "growth.stage_caps", "expected a mapping")
                else:
                    supported_bands = (
                        set(prestige_bands)
                        if isinstance(prestige_bands, dict) and prestige_bands
                        else DEFAULT_VIRTUAL_PLAYER_PRESTIGE_BANDS
                    )
                    for band, value in stage_caps.items():
                        if band not in supported_bands:
                            result.add(file, f"growth.stage_caps.{band}", "expected a configured prestige band")
                        _check_type(
                            value, int, result=result, file=file, path="growth.stage_caps", field_name=str(band)
                        )
                        _check_positive(value, result=result, file=file, path="growth.stage_caps", field_name=str(band))
            for field_name in ("catch_up_ratio", "slowing_ratio_multiplier"):
                if field_name in growth:
                    _validate_ratio(
                        growth[field_name],
                        result=result,
                        file=file,
                        path=f"growth.{field_name}",
                    )
            for field_name in ("max_building_step", "max_guest_level_step", "max_prestige_step"):
                if field_name in growth:
                    _validate_int(
                        growth[field_name],
                        result=result,
                        file=file,
                        path=f"growth.{field_name}",
                        minimum=1,
                    )

    resources = data.get("resources")
    if resources is not None:
        if not isinstance(resources, dict):
            result.add(file, "resources", "expected a mapping")
        else:
            for archetype, value in resources.items():
                _validate_ratio_range(value, result=result, file=file, path=f"resources.{archetype}")

    projection = data.get("projection")
    if projection is not None:
        if not isinstance(projection, dict):
            result.add(file, "projection", "expected a mapping")
        else:
            for field_name in (
                "guest_template_keys",
                "gear_template_keys",
                "troop_template_keys",
                "technology_keys",
                "extra_skill_keys",
                "high_tier_skill_keys",
                "item_template_keys",
                "loot_item_template_keys",
            ):
                if field_name in projection:
                    _validate_string_list(
                        projection[field_name],
                        result=result,
                        file=file,
                        path="projection",
                        field_name=field_name,
                    )
            if "extra_skills_per_guest" in projection:
                _validate_int_range(
                    projection["extra_skills_per_guest"],
                    result=result,
                    file=file,
                    path="projection.extra_skills_per_guest",
                )
            if "early_stage_skill_count" in projection:
                _validate_int_range(
                    projection["early_stage_skill_count"],
                    result=result,
                    file=file,
                    path="projection.early_stage_skill_count",
                    min_value=0,
                )
                values = projection["early_stage_skill_count"]
                if isinstance(values, list) and len(values) == 2:
                    for index, value in enumerate(values):
                        if isinstance(value, int) and value > 1:
                            result.add(file, f"projection.early_stage_skill_count[{index}]", "must be <= 1")
            if "high_tier_skill_chance" in projection:
                _validate_ratio(
                    projection["high_tier_skill_chance"],
                    result=result,
                    file=file,
                    path="projection.high_tier_skill_chance",
                )
            if "multi_skill_passive_focus_chance" in projection:
                _validate_ratio(
                    projection["multi_skill_passive_focus_chance"],
                    result=result,
                    file=file,
                    path="projection.multi_skill_passive_focus_chance",
                )
            if "low_stage_powerful_item_chance" in projection:
                _validate_ratio(
                    projection["low_stage_powerful_item_chance"],
                    result=result,
                    file=file,
                    path="projection.low_stage_powerful_item_chance",
                )
            if "high_tier_skills_per_guest" in projection:
                _validate_int_range(
                    projection["high_tier_skills_per_guest"],
                    result=result,
                    file=file,
                    path="projection.high_tier_skills_per_guest",
                    min_value=0,
                )
            if "loot_item_quantity" in projection:
                _validate_int_range(
                    projection["loot_item_quantity"],
                    result=result,
                    file=file,
                    path="projection.loot_item_quantity",
                    min_value=0,
                )
            if "powerful_item_prestige_chance" in projection:
                _validate_prestige_chance_table(
                    projection["powerful_item_prestige_chance"],
                    result=result,
                    file=file,
                    path="projection.powerful_item_prestige_chance",
                )
            for field_name in ("real_projection_sample_size", "real_projection_jitter_bps"):
                value = projection.get(field_name)
                if value is None:
                    continue
                _check_type(value, int, result=result, file=file, path="projection", field_name=field_name)
                _check_positive(value, result=result, file=file, path="projection", field_name=field_name)
            for field_name in ("active_sample_days", "regional_min_sample_size"):
                if field_name in projection:
                    _validate_int(
                        projection[field_name],
                        result=result,
                        file=file,
                        path=f"projection.{field_name}",
                        minimum=1,
                    )
            quantile_weights = projection.get("strength_quantile_weights")
            if quantile_weights is not None:
                if not isinstance(quantile_weights, dict):
                    result.add(file, "projection.strength_quantile_weights", "expected a mapping")
                else:
                    for key, value in quantile_weights.items():
                        path = f"projection.strength_quantile_weights.{key}"
                        if key not in _STRENGTH_QUANTILES:
                            result.add(file, path, "expected one of p25, p50, p75")
                        _validate_int(value, result=result, file=file, path=path, minimum=0)
                    if not any(
                        isinstance(value, int) and not isinstance(value, bool) and value > 0
                        for value in quantile_weights.values()
                    ):
                        result.add(
                            file,
                            "projection.strength_quantile_weights",
                            "requires at least one positive weight",
                        )
            if "early_stage_skill_max" in projection:
                value = projection["early_stage_skill_max"]
                _check_type(value, int, result=result, file=file, path="projection", field_name="early_stage_skill_max")
                if isinstance(value, int) and value < 0:
                    result.add(file, "projection.early_stage_skill_max", "must be >= 0")
            gear_max_rarity = projection.get("gear_max_rarity_by_stage")
            if gear_max_rarity is not None:
                if not isinstance(gear_max_rarity, dict):
                    result.add(file, "projection.gear_max_rarity_by_stage", "expected a mapping")
                else:
                    for raw_stage, rarity in gear_max_rarity.items():
                        path = f"projection.gear_max_rarity_by_stage.{raw_stage}"
                        if not isinstance(raw_stage, int) or raw_stage <= 0:
                            result.add(file, path, "stage must be a positive integer")
                        if not isinstance(rarity, str) or rarity not in _GEAR_RARITIES:
                            result.add(file, path, "expected a supported gear rarity")
            for mapping_name, value_type in (
                ("gear_slots_by_archetype", int),
                ("inventory_quantity_multipliers", (int, float)),
                ("inventory_template_slots_by_archetype", int),
            ):
                mapping = projection.get(mapping_name)
                if mapping is None:
                    continue
                if not isinstance(mapping, dict):
                    result.add(file, f"projection.{mapping_name}", "expected a mapping")
                    continue
                for key, value in mapping.items():
                    path = f"projection.{mapping_name}.{key}"
                    if mapping_name == "inventory_template_slots_by_archetype" and key not in _COMBAT_PERSONAS:
                        result.add(file, path, "expected a supported combat archetype")
                    if not isinstance(value, value_type):
                        result.add(file, path, "expected a numeric value")
                        continue
                    if mapping_name == "inventory_template_slots_by_archetype" and value <= 0:
                        result.add(file, path, "must be > 0")
                    elif value < 0:
                        result.add(file, path, "must be >= 0")
            effect_weights = projection.get("inventory_effect_type_weights")
            if effect_weights is not None:
                if not isinstance(effect_weights, dict):
                    result.add(file, "projection.inventory_effect_type_weights", "expected a mapping")
                else:
                    for archetype, weights in effect_weights.items():
                        weights_path = f"projection.inventory_effect_type_weights.{archetype}"
                        if archetype not in _COMBAT_PERSONAS:
                            result.add(file, weights_path, "expected a supported combat archetype")
                        if not isinstance(weights, dict):
                            result.add(file, weights_path, "expected a mapping")
                            continue
                        for effect_type, weight in weights.items():
                            weight_path = f"{weights_path}.{effect_type}"
                            if effect_type not in VIRTUAL_PLAYER_INVENTORY_EFFECT_TYPES:
                                result.add(file, weight_path, "expected a supported inventory effect type")
                            if not isinstance(weight, int) or weight <= 0:
                                result.add(file, weight_path, "expected a positive integer")
            loot_budget = projection.get("loot_budget_daily")
            if loot_budget is not None:
                _check_type(
                    loot_budget, int, result=result, file=file, path="projection", field_name="loot_budget_daily"
                )
                _check_positive(
                    loot_budget, result=result, file=file, path="projection", field_name="loot_budget_daily"
                )
            loot_limits = projection.get("loot_limits")
            if loot_limits is not None:
                if not isinstance(loot_limits, dict):
                    result.add(file, "projection.loot_limits", "expected a mapping")
                else:
                    real_attacker_cap = loot_limits.get("real_attacker_daily_resource_cap")
                    if real_attacker_cap is not None:
                        _check_type(
                            real_attacker_cap,
                            int,
                            result=result,
                            file=file,
                            path="projection.loot_limits",
                            field_name="real_attacker_daily_resource_cap",
                        )
                        _check_positive(
                            real_attacker_cap,
                            result=result,
                            file=file,
                            path="projection.loot_limits",
                            field_name="real_attacker_daily_resource_cap",
                        )
            for field_name in (
                "rare_item_daily_global_cap",
                "powerful_item_daily_global_cap",
                "powerful_item_min_price",
                "powerful_item_min_growth_stage",
            ):
                value = projection.get(field_name)
                if value is None:
                    continue
                _check_type(value, int, result=result, file=file, path="projection", field_name=field_name)
                if field_name == "powerful_item_min_growth_stage":
                    if isinstance(value, int) and value < 0:
                        result.add(file, "projection", f"field '{field_name}' must be >= 0")
                else:
                    _check_positive(value, result=result, file=file, path="projection", field_name=field_name)

    combat_personas = data.get("combat_personas")
    if combat_personas is not None:
        if not isinstance(combat_personas, dict):
            result.add(file, "combat_personas", "expected a mapping")
        else:
            for persona, values in combat_personas.items():
                persona_path = f"combat_personas.{persona}"
                if persona not in _COMBAT_PERSONAS:
                    result.add(file, persona_path, "expected a supported combat persona")
                if not isinstance(values, dict):
                    result.add(file, persona_path, "expected a mapping")
                    continue
                for field_name, value in values.items():
                    field_path = f"{persona_path}.{field_name}"
                    if field_name not in _PERSONA_MULTIPLIERS:
                        result.add(file, field_path, "expected a supported persona multiplier")
                        continue
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        result.add(file, field_path, "expected a positive number")
                    elif value <= 0:
                        result.add(file, field_path, "must be > 0")

    lifecycle_personas = data.get("lifecycle_personas")
    if lifecycle_personas is not None:
        if not isinstance(lifecycle_personas, dict):
            result.add(file, "lifecycle_personas", "expected a mapping")
        else:
            positive_weight = False
            for persona, values in lifecycle_personas.items():
                persona_path = f"lifecycle_personas.{persona}"
                if persona not in _LIFECYCLE_PERSONAS:
                    result.add(file, persona_path, "expected a supported lifecycle persona")
                if not isinstance(values, dict):
                    result.add(file, persona_path, "expected a mapping")
                    continue
                weight = values.get("weight")
                _validate_int(weight, result=result, file=file, path=f"{persona_path}.weight", minimum=0)
                positive_weight = positive_weight or (
                    isinstance(weight, int) and not isinstance(weight, bool) and weight > 0
                )
                for field_name in ("active_days", "abandoned_days"):
                    _validate_int_range(
                        values.get(field_name),
                        result=result,
                        file=file,
                        path=f"{persona_path}.{field_name}",
                        min_value=0,
                    )
            if not positive_weight:
                result.add(
                    file,
                    "lifecycle_personas",
                    "requires at least one positive lifecycle weight",
                )

    return result
