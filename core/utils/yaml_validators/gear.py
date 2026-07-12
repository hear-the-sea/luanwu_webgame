"""Validators for forge equipment, shop items, blueprints, decompose, and smithy YAML configs."""

from __future__ import annotations

from .base import ValidationResult, _check_in, _check_positive, _check_required_fields, _check_type, _check_unique_keys

# ---------------------------------------------------------------------------
# Schema: forge_equipment.yaml
# ---------------------------------------------------------------------------

VALID_FORGE_CATEGORIES = {"helmet", "armor", "shoes", "sword", "dao", "spear", "bow", "whip"}


def validate_forge_equipment(
    data: dict,
    *,
    file: str = "forge_equipment.yaml",
    item_keys: set[str] | None = None,
) -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    equipment = data.get("equipment")
    if equipment is None:
        result.add(file, "<root>", "missing required key 'equipment'")
        return result

    if not isinstance(equipment, dict):
        result.add(file, "equipment", "expected a mapping")
        return result

    for equip_key, config in equipment.items():
        path = f"equipment.{equip_key}"
        if not isinstance(config, dict):
            result.add(file, path, "expected a mapping")
            continue

        # Check referential integrity with item_templates
        if item_keys is not None and equip_key not in item_keys:
            result.add(file, path, f"equipment key '{equip_key}' not found in item_templates.yaml")

        category = config.get("category")
        if category is not None:
            _check_in(category, VALID_FORGE_CATEGORIES, result=result, file=file, path=path, field_name="category")

        materials = config.get("materials")
        if materials is not None:
            if not isinstance(materials, dict):
                result.add(file, path, "field 'materials' expected a mapping")

        base_duration = config.get("base_duration")
        if base_duration is not None:
            _check_type(base_duration, int, result=result, file=file, path=path, field_name="base_duration")
            _check_positive(
                base_duration, result=result, file=file, path=path, field_name="base_duration", allow_zero=False
            )

        required_forging = config.get("required_forging")
        if required_forging is not None:
            _check_type(required_forging, int, result=result, file=file, path=path, field_name="required_forging")
            _check_positive(
                required_forging, result=result, file=file, path=path, field_name="required_forging", allow_zero=False
            )

    return result


# ---------------------------------------------------------------------------
# Schema: shop_items.yaml
# ---------------------------------------------------------------------------


def validate_shop_items(
    data: dict,
    *,
    file: str = "shop_items.yaml",
    item_keys: set[str] | None = None,
) -> ValidationResult:
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

    _check_unique_keys(items, "item_key", result=result, file=file, context="items")

    for idx, entry in enumerate(items):
        path = f"items[{idx}]"
        if not isinstance(entry, dict):
            result.add(file, path, "expected a mapping")
            continue

        _check_required_fields(entry, ["item_key"], result=result, file=file, path=path)

        item_key = entry.get("item_key")
        if item_key is not None and item_keys is not None:
            if item_key not in item_keys:
                result.add(file, path, f"item_key '{item_key}' not found in item_templates.yaml")

        stock = entry.get("stock")
        if stock is not None:
            _check_type(stock, int, result=result, file=file, path=path, field_name="stock")

        price = entry.get("price")
        if price is not None:
            _check_type(price, int, result=result, file=file, path=path, field_name="price")

        buy_price = entry.get("buy_price")
        if buy_price is not None:
            _check_type(buy_price, int, result=result, file=file, path=path, field_name="buy_price")

        daily_refresh = entry.get("daily_refresh")
        if daily_refresh is not None:
            _check_type(daily_refresh, bool, result=result, file=file, path=path, field_name="daily_refresh")

    return result


# ---------------------------------------------------------------------------
# Schema: forge_blueprints.yaml
# ---------------------------------------------------------------------------


def validate_forge_blueprints(
    data: dict,
    *,
    file: str = "forge_blueprints.yaml",
    item_keys: set[str] | None = None,
    item_metadata: dict[str, tuple[str, str, bool]] | None = None,
) -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    recipes = data.get("recipes")
    if recipes is None:
        result.add(file, "<root>", "missing required key 'recipes'")
        return result

    if not isinstance(recipes, list):
        result.add(file, "recipes", "expected a list")
        return result

    _check_unique_keys(recipes, "blueprint_key", result=result, file=file, context="recipes")

    recipe_blueprint_keys: set[str] = set()
    for idx, recipe in enumerate(recipes):
        path = f"recipes[{idx}]"
        if not isinstance(recipe, dict):
            result.add(file, path, "expected a mapping")
            continue

        _check_required_fields(
            recipe,
            ["blueprint_key", "result_item_key", "required_forging", "quantity_out"],
            result=result,
            file=file,
            path=path,
        )

        blueprint_key = recipe.get("blueprint_key")
        if isinstance(blueprint_key, str):
            recipe_blueprint_keys.add(blueprint_key)
            if not blueprint_key.startswith("blueprint_"):
                result.add(file, path, "blueprint_key must start with 'blueprint_'")
        if blueprint_key is not None and item_keys is not None:
            if blueprint_key not in item_keys:
                result.add(file, path, f"blueprint_key '{blueprint_key}' not found in item_templates.yaml")

        result_item_key = recipe.get("result_item_key")
        if isinstance(result_item_key, str) and not result_item_key.startswith("equip_"):
            result.add(file, path, "result_item_key must start with 'equip_'")
        if result_item_key is not None and item_keys is not None:
            if result_item_key not in item_keys:
                result.add(file, path, f"result_item_key '{result_item_key}' not found in item_templates.yaml")

        blueprint_metadata = (
            item_metadata.get(blueprint_key) if item_metadata is not None and isinstance(blueprint_key, str) else None
        )
        result_metadata = (
            item_metadata.get(result_item_key)
            if item_metadata is not None and isinstance(result_item_key, str)
            else None
        )
        if blueprint_metadata is not None and blueprint_metadata[0] != "tool":
            result.add(file, path, "blueprint_key must reference a tool ItemTemplate")
        if blueprint_metadata is not None and not blueprint_metadata[2]:
            result.add(file, path, "blueprint_key must reference a tradeable ItemTemplate")
        if result_metadata is not None and not result_metadata[0].startswith("equip_"):
            result.add(file, path, "result_item_key must reference an equip_ ItemTemplate")
        if blueprint_metadata is not None and result_metadata is not None:
            if blueprint_metadata[1] != result_metadata[1]:
                result.add(file, path, "blueprint rarity must match result_item_key rarity")

        required_forging = recipe.get("required_forging")
        if required_forging is not None:
            _check_type(required_forging, int, result=result, file=file, path=path, field_name="required_forging")
            _check_positive(
                required_forging, result=result, file=file, path=path, field_name="required_forging", allow_zero=False
            )

        quantity_out = recipe.get("quantity_out")
        if quantity_out is not None:
            _check_type(quantity_out, int, result=result, file=file, path=path, field_name="quantity_out")
            _check_positive(
                quantity_out, result=result, file=file, path=path, field_name="quantity_out", allow_zero=False
            )

        costs = recipe.get("costs")
        if costs is not None and not isinstance(costs, dict):
            result.add(file, path, "field 'costs' expected a mapping")

    if item_metadata is not None:
        for blueprint_key in sorted(key for key in item_metadata if key.startswith("blueprint_")):
            if blueprint_key not in recipe_blueprint_keys:
                result.add(file, "recipes", f"blueprint ItemTemplate '{blueprint_key}' missing forge recipe")

    return result


# ---------------------------------------------------------------------------
# Schema: forge_decompose.yaml
# ---------------------------------------------------------------------------

VALID_DECOMPOSE_RARITIES = {"green", "blue", "purple", "orange"}


def validate_forge_decompose(data: dict, *, file: str = "forge_decompose.yaml") -> ValidationResult:
    result = ValidationResult()

    if not isinstance(data, dict):
        result.add(file, "<root>", "expected a mapping at root level")
        return result

    supported_rarities = data.get("supported_rarities")
    if supported_rarities is None:
        result.add(file, "<root>", "missing required key 'supported_rarities'")
    elif not isinstance(supported_rarities, list):
        result.add(file, "supported_rarities", "expected a list")
    else:
        for rarity in supported_rarities:
            if rarity not in VALID_DECOMPOSE_RARITIES:
                result.add(file, "supported_rarities", f"unknown rarity '{rarity}'")

    # Validate base_materials per rarity
    base_materials = data.get("base_materials")
    if base_materials is not None:
        if not isinstance(base_materials, dict):
            result.add(file, "base_materials", "expected a mapping")
        else:
            for rarity, materials in base_materials.items():
                rarity_path = f"base_materials.{rarity}"
                if rarity not in VALID_DECOMPOSE_RARITIES:
                    result.add(file, rarity_path, f"unknown rarity '{rarity}'")
                if not isinstance(materials, dict):
                    result.add(file, rarity_path, "expected a mapping of material ranges")
                    continue
                for mat_key, mat_range in materials.items():
                    mat_path = f"{rarity_path}.{mat_key}"
                    if not isinstance(mat_range, list) or len(mat_range) != 2:
                        result.add(file, mat_path, "expected a list of [min, max]")
                        continue
                    for i, bound in enumerate(mat_range):
                        if not isinstance(bound, (int, float)) or bound < 0:
                            result.add(file, mat_path, f"bound[{i}] must be a non-negative number, got {bound!r}")

    # Validate chance_rewards per rarity
    chance_rewards = data.get("chance_rewards")
    if chance_rewards is not None:
        if not isinstance(chance_rewards, dict):
            result.add(file, "chance_rewards", "expected a mapping")
        else:
            for rarity, rewards in chance_rewards.items():
                rarity_path = f"chance_rewards.{rarity}"
                if rarity not in VALID_DECOMPOSE_RARITIES:
                    result.add(file, rarity_path, f"unknown rarity '{rarity}'")
                if not isinstance(rewards, dict):
                    result.add(file, rarity_path, "expected a mapping of chance values")
                    continue
                for reward_key, prob in rewards.items():
                    reward_path = f"{rarity_path}.{reward_key}"
                    if not isinstance(prob, (int, float)):
                        result.add(file, reward_path, f"expected a number, got {type(prob).__name__}")
                    elif not (0.0 <= prob <= 1.0):
                        result.add(file, reward_path, f"probability must be between 0 and 1, got {prob}")

    return result


# ---------------------------------------------------------------------------
# Schema: smithy_production.yaml
# ---------------------------------------------------------------------------

VALID_SMITHY_CATEGORIES = {"metal", "medicine"}


def validate_smithy_production(data: dict, *, file: str = "smithy_production.yaml") -> ValidationResult:
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

        _check_required_fields(
            item_data, ["cost_type", "cost_amount", "base_duration"], result=result, file=file, path=path
        )

        cost_amount = item_data.get("cost_amount")
        if cost_amount is not None:
            _check_type(cost_amount, (int, float), result=result, file=file, path=path, field_name="cost_amount")
            _check_positive(
                cost_amount, result=result, file=file, path=path, field_name="cost_amount", allow_zero=False
            )

        base_duration = item_data.get("base_duration")
        if base_duration is not None:
            _check_type(base_duration, int, result=result, file=file, path=path, field_name="base_duration")
            _check_positive(
                base_duration, result=result, file=file, path=path, field_name="base_duration", allow_zero=False
            )

        category = item_data.get("category")
        if category is not None:
            _check_in(category, VALID_SMITHY_CATEGORIES, result=result, file=file, path=path, field_name="category")

    return result
