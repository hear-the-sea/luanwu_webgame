"""Validators for ranch, stable, guest skills/growth, and technology YAML configs."""

from __future__ import annotations

from .base import ValidationResult, _check_in, _check_positive, _check_required_fields, _check_type, _check_unique_keys

# ---------------------------------------------------------------------------
# Schema: ranch_production.yaml
# ---------------------------------------------------------------------------


def validate_ranch_production(data: dict, *, file: str = "ranch_production.yaml") -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    production = data.get("production")
    if production is None:
        result.add(file, "<root>", "missing required key 'production'")
        return result

    if not isinstance(production, dict):
        result.add(file, "production", "expected a mapping")
        return result

    for item_key, item_data in production.items():
        path = f"production.{item_key}"
        if not isinstance(item_data, dict):
            result.add(file, path, "expected a mapping")
            continue

        _check_required_fields(item_data, ["grain_cost", "base_duration"], result=result, file=file, path=path)

        grain_cost = item_data.get("grain_cost")
        if grain_cost is not None:
            _check_type(grain_cost, (int, float), result=result, file=file, path=path, field_name="grain_cost")
            _check_positive(grain_cost, result=result, file=file, path=path, field_name="grain_cost", allow_zero=False)

        base_duration = item_data.get("base_duration")
        if base_duration is not None:
            _check_type(base_duration, int, result=result, file=file, path=path, field_name="base_duration")
            _check_positive(
                base_duration, result=result, file=file, path=path, field_name="base_duration", allow_zero=False
            )

    return result


# ---------------------------------------------------------------------------
# Schema: stable_production.yaml
# ---------------------------------------------------------------------------


def validate_stable_production(
    data: dict,
    *,
    file: str = "stable_production.yaml",
    item_keys: set[str] | None = None,
) -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    production = data.get("production")
    if production is None:
        result.add(file, "<root>", "missing required key 'production'")
        return result

    if not isinstance(production, dict):
        result.add(file, "production", "expected a mapping")
        return result

    for equip_key, item_data in production.items():
        path = f"production.{equip_key}"
        if not isinstance(item_data, dict):
            result.add(file, path, "expected a mapping")
            continue

        if item_keys is not None and equip_key not in item_keys:
            result.add(file, path, f"production key '{equip_key}' not found in item_templates.yaml")

        _check_required_fields(item_data, ["grain_cost", "base_duration"], result=result, file=file, path=path)

        grain_cost = item_data.get("grain_cost")
        if grain_cost is not None:
            _check_type(grain_cost, (int, float), result=result, file=file, path=path, field_name="grain_cost")
            _check_positive(grain_cost, result=result, file=file, path=path, field_name="grain_cost", allow_zero=False)

        base_duration = item_data.get("base_duration")
        if base_duration is not None:
            _check_type(base_duration, int, result=result, file=file, path=path, field_name="base_duration")
            _check_positive(
                base_duration, result=result, file=file, path=path, field_name="base_duration", allow_zero=False
            )

    return result


# ---------------------------------------------------------------------------
# Schema: guest_skills.yaml
# ---------------------------------------------------------------------------

VALID_SKILL_RARITIES = {"black", "gray", "green", "red", "blue", "purple", "orange"}
VALID_SKILL_KINDS = {"active", "passive"}
VALID_PASSIVE_EFFECT_TYPES = {
    "heal_ratio",
    "modify_outgoing_damage",
    "modify_incoming_damage",
    "set_softcap",
    "set_reflect",
    "set_state",
    "emit_log",
}
VALID_PASSIVE_TARGET_SCOPES = {"self", "allies"}


def _validate_passive_effects(
    effects: list[dict],
    *,
    result: ValidationResult,
    file: str,
    path: str,
) -> None:
    for effect_idx, effect in enumerate(effects):
        effect_path = f"{path}.effects[{effect_idx}]"
        if not isinstance(effect, dict):
            result.add(file, effect_path, "expected a mapping")
            continue

        effect_type = effect.get("type")
        if effect_type is None:
            result.add(file, effect_path, "missing required field 'type'")
        else:
            _check_in(
                effect_type,
                VALID_PASSIVE_EFFECT_TYPES,
                result=result,
                file=file,
                path=effect_path,
                field_name="type",
            )

        target_scope = effect.get("target_scope")
        if target_scope is not None:
            _check_in(
                target_scope,
                VALID_PASSIVE_TARGET_SCOPES,
                result=result,
                file=file,
                path=effect_path,
                field_name="target_scope",
            )

        target_template_in = effect.get("target_template_in")
        if target_template_in is not None:
            if not isinstance(target_template_in, list):
                result.add(file, effect_path, "field 'target_template_in' expected list")
            else:
                for item_idx, item in enumerate(target_template_in):
                    if not isinstance(item, str) or not item.strip():
                        result.add(
                            file,
                            f"{effect_path}.target_template_in[{item_idx}]",
                            "expected a non-empty string",
                        )

        target_kind_is = effect.get("target_kind_is")
        if target_kind_is is not None and (not isinstance(target_kind_is, str) or not target_kind_is.strip()):
            result.add(file, effect_path, "field 'target_kind_is' expected non-empty string")


def _validate_passive_config(passive_config: dict, *, result: ValidationResult, file: str, path: str) -> None:
    triggers = passive_config.get("triggers")
    if triggers is None:
        return
    if not isinstance(triggers, list):
        result.add(file, f"{path}.triggers", "expected a list")
        return

    for trigger_idx, trigger in enumerate(triggers):
        trigger_path = f"{path}.triggers[{trigger_idx}]"
        if not isinstance(trigger, dict):
            result.add(file, trigger_path, "expected a mapping")
            continue

        timing = trigger.get("timing")
        if timing is not None and (not isinstance(timing, str) or not timing.strip()):
            result.add(file, trigger_path, "field 'timing' expected non-empty string")

        conditions = trigger.get("conditions")
        if conditions is not None and not isinstance(conditions, dict):
            result.add(file, trigger_path, "field 'conditions' expected mapping")

        effects = trigger.get("effects")
        if effects is None:
            result.add(file, trigger_path, "missing required field 'effects'")
            continue
        if not isinstance(effects, list):
            result.add(file, f"{trigger_path}.effects", "expected a list")
            continue
        _validate_passive_effects(effects, result=result, file=file, path=trigger_path)


def validate_guest_skills(data: dict, *, file: str = "guest_skills.yaml") -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    skills = data.get("skills")
    if skills is None:
        result.add(file, "<root>", "missing required key 'skills'")
        return result

    if not isinstance(skills, list):
        result.add(file, "skills", "expected a list")
        return result

    _check_unique_keys(skills, "key", result=result, file=file, context="skills")

    for idx, skill in enumerate(skills):
        path = f"skills[{idx}]"
        if not isinstance(skill, dict):
            result.add(file, path, "expected a mapping")
            continue

        _check_required_fields(skill, ["key", "name", "rarity"], result=result, file=file, path=path)

        rarity = skill.get("rarity")
        if rarity is not None:
            _check_in(rarity, VALID_SKILL_RARITIES, result=result, file=file, path=path, field_name="rarity")

        kind = skill.get("kind")
        if kind is not None:
            _check_in(kind, VALID_SKILL_KINDS, result=result, file=file, path=path, field_name="kind")

        base_power = skill.get("base_power")
        if base_power is not None:
            _check_type(base_power, (int, float), result=result, file=file, path=path, field_name="base_power")
            _check_positive(base_power, result=result, file=file, path=path, field_name="base_power")

        base_probability = skill.get("base_probability")
        if base_probability is not None:
            _check_type(
                base_probability, (int, float), result=result, file=file, path=path, field_name="base_probability"
            )
            if isinstance(base_probability, (int, float)) and not (0.0 <= base_probability <= 1.0):
                result.add(file, path, f"field 'base_probability' must be between 0 and 1, got {base_probability}")

        targets = skill.get("targets")
        if targets is not None:
            _check_type(targets, int, result=result, file=file, path=path, field_name="targets")
            _check_positive(targets, result=result, file=file, path=path, field_name="targets", allow_zero=False)

        required_level = skill.get("required_level")
        if required_level is not None:
            _check_type(required_level, int, result=result, file=file, path=path, field_name="required_level")
            _check_positive(
                required_level, result=result, file=file, path=path, field_name="required_level", allow_zero=False
            )

        for field_name in ("required_force", "required_intellect", "required_defense", "required_agility"):
            required_value = skill.get(field_name)
            if required_value is None:
                continue
            _check_type(required_value, int, result=result, file=file, path=path, field_name=field_name)
            _check_positive(
                required_value, result=result, file=file, path=path, field_name=field_name, allow_zero=False
            )

        passive_config = skill.get("passive_config")
        if passive_config is not None:
            if isinstance(passive_config, dict):
                _validate_passive_config(passive_config, result=result, file=file, path=f"{path}.passive_config")
            else:
                result.add(file, f"{path}.passive_config", "expected a mapping")

    return result


# ---------------------------------------------------------------------------
# Schema: guest_growth_rules.yaml
# ---------------------------------------------------------------------------

VALID_GROWTH_RARITIES = {"black", "gray", "green", "red", "blue", "purple", "orange"}
VALID_GROWTH_ARCHETYPES = {"military", "civil"}


def validate_guest_growth_rules(data: dict, *, file: str = "guest_growth_rules.yaml") -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    # Validate rarity_hp_profiles
    hp_profiles = data.get("rarity_hp_profiles")
    if hp_profiles is not None:
        if not isinstance(hp_profiles, dict):
            result.add(file, "rarity_hp_profiles", "expected a mapping")
        else:
            for rarity, profile in hp_profiles.items():
                path = f"rarity_hp_profiles.{rarity}"
                if rarity not in VALID_GROWTH_RARITIES:
                    result.add(file, path, f"unknown rarity '{rarity}'")
                if not isinstance(profile, dict):
                    result.add(file, path, "expected a mapping")
                    continue
                base = profile.get("base")
                if base is not None:
                    _check_type(base, (int, float), result=result, file=file, path=path, field_name="base")
                    _check_positive(base, result=result, file=file, path=path, field_name="base", allow_zero=False)

    # Validate rarity_skill_point_gains
    skill_point_gains = data.get("rarity_skill_point_gains")
    if skill_point_gains is not None:
        if not isinstance(skill_point_gains, dict):
            result.add(file, "rarity_skill_point_gains", "expected a mapping")
        else:
            for rarity, gain in skill_point_gains.items():
                path = f"rarity_skill_point_gains.{rarity}"
                if rarity not in VALID_GROWTH_RARITIES:
                    result.add(file, path, f"unknown rarity '{rarity}'")
                if not isinstance(gain, (int, float)) or gain < 0:
                    result.add(file, path, f"expected a non-negative number, got {gain!r}")

    # Validate rarity_attribute_growth_range
    growth_ranges = data.get("rarity_attribute_growth_range")
    if growth_ranges is not None:
        if not isinstance(growth_ranges, dict):
            result.add(file, "rarity_attribute_growth_range", "expected a mapping")
        else:
            for rarity, growth_range in growth_ranges.items():
                path = f"rarity_attribute_growth_range.{rarity}"
                if rarity not in VALID_GROWTH_RARITIES:
                    result.add(file, path, f"unknown rarity '{rarity}'")
                if not isinstance(growth_range, list) or len(growth_range) != 2:
                    result.add(file, path, "expected a list of [min, max]")

    # Validate archetype_attribute_weights
    archetype_weights = data.get("archetype_attribute_weights")
    if archetype_weights is not None:
        if not isinstance(archetype_weights, dict):
            result.add(file, "archetype_attribute_weights", "expected a mapping")
        else:
            for archetype, weights in archetype_weights.items():
                path = f"archetype_attribute_weights.{archetype}"
                if archetype not in VALID_GROWTH_ARCHETYPES:
                    result.add(file, path, f"unknown archetype '{archetype}'")
                if not isinstance(weights, dict):
                    result.add(file, path, "expected a mapping of attribute weights")

    return result


# ---------------------------------------------------------------------------
# Schema: technology_templates.yaml
# ---------------------------------------------------------------------------

VALID_TECH_CATEGORIES = {"basic", "martial", "production"}


def validate_technology_templates(data: dict, *, file: str = "technology_templates.yaml") -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    # Validate categories section
    categories = data.get("categories")
    if categories is not None:
        if not isinstance(categories, list):
            result.add(file, "categories", "expected a list")
        else:
            _check_unique_keys(categories, "key", result=result, file=file, context="categories")
            for idx, cat in enumerate(categories):
                cat_path = f"categories[{idx}]"
                if not isinstance(cat, dict):
                    result.add(file, cat_path, "expected a mapping")
                    continue
                _check_required_fields(cat, ["key", "name"], result=result, file=file, path=cat_path)

    # Validate technologies list
    technologies = data.get("technologies")
    if technologies is None:
        result.add(file, "<root>", "missing required key 'technologies'")
        return result

    if not isinstance(technologies, list):
        result.add(file, "technologies", "expected a list")
        return result

    _check_unique_keys(technologies, "key", result=result, file=file, context="technologies")

    for idx, tech in enumerate(technologies):
        path = f"technologies[{idx}]"
        if not isinstance(tech, dict):
            result.add(file, path, "expected a mapping")
            continue

        _check_required_fields(
            tech,
            ["key", "name", "category", "effect_type", "max_level", "base_cost"],
            result=result,
            file=file,
            path=path,
        )

        category = tech.get("category")
        if category is not None:
            _check_in(category, VALID_TECH_CATEGORIES, result=result, file=file, path=path, field_name="category")

        max_level = tech.get("max_level")
        if max_level is not None:
            _check_type(max_level, int, result=result, file=file, path=path, field_name="max_level")
            _check_positive(max_level, result=result, file=file, path=path, field_name="max_level", allow_zero=False)

        base_cost = tech.get("base_cost")
        if base_cost is not None:
            _check_type(base_cost, (int, float), result=result, file=file, path=path, field_name="base_cost")
            _check_positive(base_cost, result=result, file=file, path=path, field_name="base_cost", allow_zero=False)

        effect_per_level = tech.get("effect_per_level")
        if effect_per_level is not None:
            _check_type(
                effect_per_level, (int, float), result=result, file=file, path=path, field_name="effect_per_level"
            )

    return result
