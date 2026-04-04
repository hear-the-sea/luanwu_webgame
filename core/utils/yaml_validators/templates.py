"""Validators for item, building, guest, troop, mission, and guild mission template YAML configs."""

from __future__ import annotations

from .base import ValidationResult, _check_in, _check_positive, _check_required_fields, _check_type, _check_unique_keys

# ---------------------------------------------------------------------------
# Schema: item_templates.yaml
# ---------------------------------------------------------------------------

VALID_ITEM_EFFECT_TYPES = {
    "resource",
    "resource_pack",
    "skill_book",
    "experience_items",
    "medicine",
    "tool",
    "loot_box",
    "equip_helmet",
    "equip_armor",
    "equip_shoes",
    "equip_weapon",
    "equip_mount",
    "equip_ornament",
    "equip_device",
}

VALID_ITEM_RARITIES = {"black", "gray", "green", "red", "blue", "purple", "orange"}


def validate_item_templates(data: dict, *, file: str = "item_templates.yaml") -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    items = data.get("items")
    if items is None:
        result.add(file, "<root>", "missing required key 'items'")
        return result

    if not isinstance(items, list):
        result.add(file, "items", "expected a list")
        return result

    _check_unique_keys(items, "key", result=result, file=file, context="items")

    for idx, item in enumerate(items):
        path = f"items[{idx}]"
        if not isinstance(item, dict):
            result.add(file, path, "expected a mapping")
            continue

        _check_required_fields(item, ["key", "name", "effect_type"], result=result, file=file, path=path)

        key = item.get("key")
        if key is not None:
            _check_type(key, str, result=result, file=file, path=path, field_name="key")

        effect_type = item.get("effect_type")
        if effect_type is not None:
            _check_in(
                effect_type, VALID_ITEM_EFFECT_TYPES, result=result, file=file, path=path, field_name="effect_type"
            )

        rarity = item.get("rarity")
        if rarity is not None:
            _check_in(rarity, VALID_ITEM_RARITIES, result=result, file=file, path=path, field_name="rarity")

        price = item.get("price")
        if price is not None:
            _check_type(price, (int, float), result=result, file=file, path=path, field_name="price")
            _check_positive(price, result=result, file=file, path=path, field_name="price")

        storage_space = item.get("storage_space")
        if storage_space is not None:
            _check_type(storage_space, int, result=result, file=file, path=path, field_name="storage_space")
            _check_positive(
                storage_space, result=result, file=file, path=path, field_name="storage_space", allow_zero=False
            )

        for bool_field in ("tradeable", "is_usable"):
            val = item.get(bool_field)
            if val is not None:
                _check_type(val, bool, result=result, file=file, path=path, field_name=bool_field)

    return result


# ---------------------------------------------------------------------------
# Schema: building_templates.yaml
# ---------------------------------------------------------------------------

VALID_BUILDING_CATEGORIES = {"resource", "storage", "production", "personnel", "special"}
VALID_BUILDING_RESOURCE_TYPES = {"grain", "silver"}


def validate_building_templates(data: dict, *, file: str = "building_templates.yaml") -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    buildings = data.get("buildings")
    if buildings is None:
        result.add(file, "<root>", "missing required key 'buildings'")
        return result

    if not isinstance(buildings, list):
        result.add(file, "buildings", "expected a list")
        return result

    _check_unique_keys(buildings, "key", result=result, file=file, context="buildings")

    for idx, bld in enumerate(buildings):
        path = f"buildings[{idx}]"
        if not isinstance(bld, dict):
            result.add(file, path, "expected a mapping")
            continue

        _check_required_fields(
            bld,
            ["key", "name", "category", "resource_type"],
            result=result,
            file=file,
            path=path,
        )

        category = bld.get("category")
        if category is not None:
            _check_in(category, VALID_BUILDING_CATEGORIES, result=result, file=file, path=path, field_name="category")

        resource_type = bld.get("resource_type")
        if resource_type is not None:
            _check_in(
                resource_type,
                VALID_BUILDING_RESOURCE_TYPES,
                result=result,
                file=file,
                path=path,
                field_name="resource_type",
            )

        for num_field in ("base_rate_per_hour", "base_upgrade_time"):
            val = bld.get(num_field)
            if val is not None:
                _check_type(val, (int, float), result=result, file=file, path=path, field_name=num_field)
                _check_positive(val, result=result, file=file, path=path, field_name=num_field)

        for float_field in ("rate_growth", "time_growth", "cost_growth"):
            val = bld.get(float_field)
            if val is not None:
                _check_type(val, (int, float), result=result, file=file, path=path, field_name=float_field)
                _check_positive(val, result=result, file=file, path=path, field_name=float_field)

        base_cost = bld.get("base_cost")
        if base_cost is not None:
            if not isinstance(base_cost, dict):
                result.add(file, path, "field 'base_cost' expected a mapping")
            else:
                for cost_key, cost_val in base_cost.items():
                    if not isinstance(cost_val, (int, float)):
                        result.add(
                            file, f"{path}.base_cost.{cost_key}", f"expected number, got {type(cost_val).__name__}"
                        )

    # Validate categories section if present
    categories = data.get("categories")
    if categories is not None:
        if not isinstance(categories, list):
            result.add(file, "categories", "expected a list")
        else:
            for idx, cat in enumerate(categories):
                cat_path = f"categories[{idx}]"
                if not isinstance(cat, dict):
                    result.add(file, cat_path, "expected a mapping")
                    continue
                _check_required_fields(cat, ["key", "name"], result=result, file=file, path=cat_path)

    return result


# ---------------------------------------------------------------------------
# Schema: guest_templates.yaml
# ---------------------------------------------------------------------------

VALID_GUEST_RARITIES = {"black", "gray", "green", "red", "blue", "purple", "orange"}
VALID_GUEST_ARCHETYPES = {"military", "civil"}


def validate_guest_templates(data: dict, *, file: str = "guest_templates.yaml") -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    # Validate pools
    pools = data.get("pools")
    if pools is not None:
        if not isinstance(pools, list):
            result.add(file, "pools", "expected a list")
        else:
            _check_unique_keys(pools, "key", result=result, file=file, context="pools")
            for idx, pool in enumerate(pools):
                path = f"pools[{idx}]"
                if not isinstance(pool, dict):
                    result.add(file, path, "expected a mapping")
                    continue
                _check_required_fields(pool, ["key", "name"], result=result, file=file, path=path)

                cost = pool.get("cost")
                if cost is not None and not isinstance(cost, dict):
                    result.add(file, path, "field 'cost' expected a mapping")

                cooldown = pool.get("cooldown_seconds")
                if cooldown is not None:
                    _check_type(cooldown, int, result=result, file=file, path=path, field_name="cooldown_seconds")
                    _check_positive(cooldown, result=result, file=file, path=path, field_name="cooldown_seconds")

                draw_count = pool.get("draw_count")
                if draw_count is not None:
                    _check_type(draw_count, int, result=result, file=file, path=path, field_name="draw_count")
                    _check_positive(
                        draw_count, result=result, file=file, path=path, field_name="draw_count", allow_zero=False
                    )

    # Validate attribute_profiles
    profiles = data.get("attribute_profiles")
    if profiles is not None:
        if not isinstance(profiles, dict):
            result.add(file, "attribute_profiles", "expected a mapping")
        else:
            for rarity, archetypes in profiles.items():
                profile_path = f"attribute_profiles.{rarity}"
                if rarity not in VALID_GUEST_RARITIES:
                    result.add(file, profile_path, f"unknown rarity '{rarity}'")
                if not isinstance(archetypes, dict):
                    result.add(file, profile_path, "expected a mapping of archetypes")
                    continue
                for archetype, stats in archetypes.items():
                    arch_path = f"{profile_path}.{archetype}"
                    if archetype not in VALID_GUEST_ARCHETYPES:
                        result.add(file, arch_path, f"unknown archetype '{archetype}'")
                    if not isinstance(stats, dict):
                        result.add(file, arch_path, "expected a mapping of stat values")

    return result


# ---------------------------------------------------------------------------
# Schema: troop_templates.yaml
# ---------------------------------------------------------------------------


def validate_troop_templates(data: dict, *, file: str = "troop_templates.yaml") -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    troops = data.get("troops")
    if troops is None:
        result.add(file, "<root>", "missing required key 'troops'")
        return result

    if not isinstance(troops, list):
        result.add(file, "troops", "expected a list")
        return result

    _check_unique_keys(troops, "key", result=result, file=file, context="troops")

    for idx, troop in enumerate(troops):
        path = f"troops[{idx}]"
        if not isinstance(troop, dict):
            result.add(file, path, "expected a mapping")
            continue

        _check_required_fields(troop, ["key", "name"], result=result, file=file, path=path)

        for int_field in ("base_attack", "base_defense", "base_hp", "speed_bonus", "priority", "default_count"):
            val = troop.get(int_field)
            if val is not None:
                _check_type(val, (int, float), result=result, file=file, path=path, field_name=int_field)

        recruit = troop.get("recruit")
        if recruit is not None:
            if not isinstance(recruit, dict):
                result.add(file, path, "field 'recruit' expected a mapping")
            else:
                equipment = recruit.get("equipment")
                if equipment is not None and not isinstance(equipment, list):
                    result.add(file, f"{path}.recruit", "field 'equipment' expected a list")

                for recruit_int in ("tech_level", "retainer_cost", "base_duration"):
                    val = recruit.get(recruit_int)
                    if val is not None:
                        _check_type(val, int, result=result, file=file, path=f"{path}.recruit", field_name=recruit_int)

    return result


# ---------------------------------------------------------------------------
# Schema: mission_templates.yaml
# ---------------------------------------------------------------------------


def validate_mission_templates(
    data: dict,
    *,
    file: str = "mission_templates.yaml",
    item_keys: set[str] | None = None,
    troop_keys: set[str] | None = None,
) -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    missions = data.get("missions")
    if missions is None:
        result.add(file, "<root>", "missing required key 'missions'")
        return result

    if not isinstance(missions, list):
        result.add(file, "missions", "expected a list")
        return result

    _check_unique_keys(missions, "key", result=result, file=file, context="missions")

    for idx, mission in enumerate(missions):
        path = f"missions[{idx}]"
        if not isinstance(mission, dict):
            result.add(file, path, "expected a mapping")
            continue

        _check_required_fields(mission, ["key", "name"], result=result, file=file, path=path)

        daily_limit = mission.get("daily_limit")
        if daily_limit is not None:
            _check_type(daily_limit, int, result=result, file=file, path=path, field_name="daily_limit")
            _check_positive(
                daily_limit, result=result, file=file, path=path, field_name="daily_limit", allow_zero=False
            )

        base_travel_time = mission.get("base_travel_time")
        if base_travel_time is not None:
            _check_type(
                base_travel_time, (int, float), result=result, file=file, path=path, field_name="base_travel_time"
            )
            _check_positive(base_travel_time, result=result, file=file, path=path, field_name="base_travel_time")

        # Validate enemy_guests
        enemy_guests = mission.get("enemy_guests")
        if enemy_guests is not None:
            if not isinstance(enemy_guests, list):
                result.add(file, path, "field 'enemy_guests' expected a list")
            else:
                for gi, guest in enumerate(enemy_guests):
                    guest_path = f"{path}.enemy_guests[{gi}]"
                    if isinstance(guest, str):
                        pass  # simple string reference
                    elif isinstance(guest, dict):
                        if "key" not in guest:
                            result.add(file, guest_path, "missing required field 'key'")
                    else:
                        result.add(file, guest_path, f"expected string or mapping, got {type(guest).__name__}")

        # Validate enemy_troops with referential integrity
        enemy_troops = mission.get("enemy_troops")
        if enemy_troops is not None:
            if not isinstance(enemy_troops, dict):
                result.add(file, path, "field 'enemy_troops' expected a mapping")
            elif troop_keys is not None:
                for troop_key, count in enemy_troops.items():
                    if troop_key not in troop_keys:
                        result.add(
                            file,
                            f"{path}.enemy_troops",
                            f"troop key '{troop_key}' not found in troop_templates.yaml",
                        )
                    if isinstance(count, bool) or not isinstance(count, (int, float)) or count < 0:
                        result.add(
                            file,
                            f"{path}.enemy_troops.{troop_key}",
                            f"expected a non-negative number, got {count!r}",
                        )

        # Validate enemy_technology
        enemy_tech = mission.get("enemy_technology")
        if enemy_tech is not None:
            if not isinstance(enemy_tech, dict):
                result.add(file, path, "field 'enemy_technology' expected a mapping")
            else:
                level = enemy_tech.get("level")
                if level is not None:
                    _check_type(
                        level, int, result=result, file=file, path=f"{path}.enemy_technology", field_name="level"
                    )

                guest_level = enemy_tech.get("guest_level")
                if guest_level is not None:
                    _check_type(
                        guest_level,
                        int,
                        result=result,
                        file=file,
                        path=f"{path}.enemy_technology",
                        field_name="guest_level",
                    )

                guest_bonus = enemy_tech.get("guest_bonus")
                if guest_bonus is not None:
                    _check_type(
                        guest_bonus,
                        (int, float),
                        result=result,
                        file=file,
                        path=f"{path}.enemy_technology",
                        field_name="guest_bonus",
                    )

        # Validate drop_table referential integrity
        drop_table = mission.get("drop_table")
        if drop_table is not None:
            if not isinstance(drop_table, dict):
                result.add(file, path, "field 'drop_table' expected a mapping")
            elif item_keys is not None:
                # 'silver' is a special case (resource), not always in item_templates.
                # Entries whose value is a dict with 'choices' are virtual random-pick
                # entries -- the key is a label, not an item key.
                for drop_key, drop_val in drop_table.items():
                    if drop_key == "silver":
                        continue
                    is_random_choice = isinstance(drop_val, dict) and "choices" in drop_val
                    if is_random_choice:
                        # Validate the individual choices instead
                        for choice in drop_val["choices"]:
                            if isinstance(choice, str) and choice not in item_keys:
                                result.add(
                                    file,
                                    f"{path}.drop_table.{drop_key}.choices",
                                    f"choice item key '{choice}' not found in item_templates.yaml",
                                )
                        continue
                    if drop_key not in item_keys:
                        result.add(
                            file,
                            f"{path}.drop_table",
                            f"item key '{drop_key}' not found in item_templates.yaml",
                        )

    return result


def validate_guild_mission_templates(data: dict, *, file: str = "guild_mission_templates.yaml") -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    missions = data.get("missions")
    if missions is None:
        result.add(file, "<root>", "missing required key 'missions'")
        return result

    if not isinstance(missions, list):
        result.add(file, "missions", "expected a list")
        return result

    _check_unique_keys(missions, "key", result=result, file=file, context="missions")

    for idx, mission in enumerate(missions):
        path = f"missions[{idx}]"
        if not isinstance(mission, dict):
            result.add(file, path, "expected a mapping")
            continue

        _check_required_fields(mission, ["key", "name"], result=result, file=file, path=path)

        for field_name in ("description", "difficulty", "task_type"):
            value = mission.get(field_name)
            if value is None:
                continue
            _check_type(value, str, result=result, file=file, path=path, field_name=field_name)

        for field_name in ("base_duration_seconds", "recommended_guest_count"):
            value = mission.get(field_name)
            if value is None:
                continue
            _check_type(value, int, result=result, file=file, path=path, field_name=field_name)
            _check_positive(value, result=result, file=file, path=path, field_name=field_name, allow_zero=False)

        ruby_reward = mission.get("ruby_reward")
        if ruby_reward is not None:
            _check_type(ruby_reward, int, result=result, file=file, path=path, field_name="ruby_reward")
            _check_positive(ruby_reward, result=result, file=file, path=path, field_name="ruby_reward")

        sort_weight = mission.get("sort_weight")
        if sort_weight is not None:
            _check_type(sort_weight, int, result=result, file=file, path=path, field_name="sort_weight")

        for bool_field in ("allow_troops", "is_active"):
            value = mission.get(bool_field)
            if value is None:
                continue
            _check_type(value, bool, result=result, file=file, path=path, field_name=bool_field)

        enemy_guests = mission.get("enemy_guests")
        if enemy_guests is not None and not isinstance(enemy_guests, list):
            result.add(file, f"{path}.enemy_guests", "expected a list")

        enemy_troops = mission.get("enemy_troops")
        if enemy_troops is not None and not isinstance(enemy_troops, dict):
            result.add(file, f"{path}.enemy_troops", "expected a mapping")

        enemy_technology = mission.get("enemy_technology")
        if enemy_technology is not None and not isinstance(enemy_technology, dict):
            result.add(file, f"{path}.enemy_technology", "expected a mapping")

    return result
