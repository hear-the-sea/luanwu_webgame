"""Validator for virtual player runtime configuration."""

from __future__ import annotations

from typing import Any

from .base import ValidationResult, _check_positive, _check_type


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
    if not isinstance(value, list):
        result.add(file, path, f"field '{field_name}' expected list, got {type(value).__name__}")
        return
    for idx, item in enumerate(value):
        if not isinstance(item, str):
            result.add(file, f"{path}.{field_name}[{idx}]", "expected string")


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
            for field_name in ("active_player_multiplier", "min_per_region", "min_attackable_per_band", "hard_cap"):
                value = population.get(field_name)
                if value is None:
                    continue
                _check_type(value, int, result=result, file=file, path="population", field_name=field_name)
                _check_positive(value, result=result, file=file, path="population", field_name=field_name)
            if "rolling_batch_size" in population:
                _validate_int_range(
                    population["rolling_batch_size"],
                    result=result,
                    file=file,
                    path="population.rolling_batch_size",
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
            if "high_tier_skill_chance" in projection:
                _validate_ratio(
                    projection["high_tier_skill_chance"],
                    result=result,
                    file=file,
                    path="projection.high_tier_skill_chance",
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
            for mapping_name, value_type in (
                ("gear_slots_by_archetype", int),
                ("inventory_quantity_multipliers", (int, float)),
            ):
                mapping = projection.get(mapping_name)
                if mapping is None:
                    continue
                if not isinstance(mapping, dict):
                    result.add(file, f"projection.{mapping_name}", "expected a mapping")
                    continue
                for key, value in mapping.items():
                    path = f"projection.{mapping_name}.{key}"
                    if not isinstance(value, value_type):
                        result.add(file, path, "expected a numeric value")
                        continue
                    if value < 0:
                        result.add(file, path, "must be >= 0")
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
            ):
                value = projection.get(field_name)
                if value is None:
                    continue
                _check_type(value, int, result=result, file=file, path="projection", field_name=field_name)
                _check_positive(value, result=result, file=file, path="projection", field_name=field_name)

    return result
