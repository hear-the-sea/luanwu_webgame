from __future__ import annotations

from typing import Any

from django.db import transaction

from core.exceptions import ForgeOperationError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.models import Manor as ManorModel

from .. import technology as technology_service
from ..inventory.core import add_item_to_inventory_locked, consume_inventory_item_locked
from .forge_helpers import build_blueprint_synthesis_option, build_inventory_quantity_map, collect_recipe_related_keys


def _normalize_blueprint_non_empty_string(raw_value: object, *, contract_name: str) -> str:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    return raw_value.strip()


def _normalize_blueprint_positive_int(raw_value: object, *, contract_name: str) -> int:
    if raw_value is None or isinstance(raw_value, bool):
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    raw_for_int: Any = raw_value
    try:
        parsed_value = int(raw_for_int)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}") from exc
    if parsed_value <= 0:
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    return parsed_value


def _normalize_blueprint_cost_mapping(raw_value: object, *, contract_name: str) -> dict[str, int]:
    if not isinstance(raw_value, dict):
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    normalized: dict[str, int] = {}
    for item_key, amount in raw_value.items():
        normalized_key = _normalize_blueprint_non_empty_string(
            item_key,
            contract_name=f"{contract_name} key",
        )
        normalized[normalized_key] = _normalize_blueprint_positive_int(
            amount,
            contract_name=f"{contract_name} amount",
        )
    return normalized


def _normalize_blueprint_recipe(raw_recipe: object, *, contract_name: str) -> dict[str, Any]:
    if not isinstance(raw_recipe, dict):
        raise AssertionError(f"invalid {contract_name}: {raw_recipe!r}")
    return {
        **raw_recipe,
        "blueprint_key": _normalize_blueprint_non_empty_string(
            raw_recipe.get("blueprint_key"),
            contract_name=f"{contract_name} blueprint_key",
        ),
        "result_item_key": _normalize_blueprint_non_empty_string(
            raw_recipe.get("result_item_key"),
            contract_name=f"{contract_name} result_item_key",
        ),
        "required_forging": _normalize_blueprint_positive_int(
            raw_recipe.get("required_forging"),
            contract_name=f"{contract_name} required_forging",
        ),
        "quantity_out": _normalize_blueprint_positive_int(
            raw_recipe.get("quantity_out"),
            contract_name=f"{contract_name} quantity_out",
        ),
        "costs": _normalize_blueprint_cost_mapping(
            raw_recipe.get("costs"),
            contract_name=f"{contract_name} costs",
        ),
    }


def build_blueprint_recipe_index(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_recipes = config.get("recipes")
    if not isinstance(raw_recipes, list):
        raise AssertionError(f"invalid forge blueprint config recipes: {raw_recipes!r}")
    result: dict[str, dict[str, Any]] = {}
    for raw_recipe in raw_recipes:
        recipe = _normalize_blueprint_recipe(raw_recipe, contract_name="forge blueprint recipe")
        key = recipe["blueprint_key"]
        if key in result:
            raise AssertionError(f"duplicate forge blueprint key: {key}")
        result[key] = recipe
    return result


def get_blueprint_synthesis_options(
    manor: Any,
    *,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_recipes = config.get("recipes")
    if not isinstance(raw_recipes, list):
        raise AssertionError(f"invalid forge blueprint config recipes: {raw_recipes!r}")
    recipes = [
        _normalize_blueprint_recipe(raw_recipe, contract_name="forge blueprint recipe") for raw_recipe in raw_recipes
    ]
    if not recipes:
        return []

    all_keys = collect_recipe_related_keys(recipes)
    template_map = {tpl.key: tpl for tpl in ItemTemplate.objects.filter(key__in=all_keys)}
    inventory_items = (
        InventoryItem.objects.filter(
            manor=manor,
            template__key__in=all_keys,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .select_related("template")
        .order_by("id")
    )
    quantities = build_inventory_quantity_map(inventory_items)
    forging_level = technology_service.get_player_technology_level(manor, "forging")

    options: list[dict[str, Any]] = []
    for recipe in recipes:
        option = build_blueprint_synthesis_option(
            recipe,
            quantities=quantities,
            template_map=template_map,
            forging_level=forging_level,
        )
        if option is not None:
            options.append(option)

    options.sort(key=lambda row: (row["required_forging"], row["result_name"]), reverse=True)
    return options


def synthesize_equipment_with_blueprint(
    manor: Any,
    blueprint_key: str,
    quantity: int = 1,
    *,
    recipe_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if quantity < 1:
        raise ForgeOperationError("合成数量至少为1")

    raw_recipe = recipe_index.get(blueprint_key)
    if not raw_recipe:
        raise ForgeOperationError("无效的图纸")
    recipe = _normalize_blueprint_recipe(raw_recipe, contract_name="forge blueprint recipe")

    required_forging = recipe["required_forging"]
    forging_level = technology_service.get_player_technology_level(manor, "forging")
    if forging_level < required_forging:
        raise ForgeOperationError(f"需要锻造技{required_forging}级才能合成")

    result_item_key = recipe["result_item_key"]
    result_template = ItemTemplate.objects.filter(key=result_item_key).only("key", "name", "effect_type").first()
    if not result_template:
        raise ForgeOperationError("图纸配置错误：产物不存在")
    if not str(result_template.effect_type or "").startswith("equip_"):
        raise ForgeOperationError("图纸配置错误：产物必须是装备")

    consume_requirements: dict[str, int] = {blueprint_key: quantity}
    for cost_key, cost_amount in recipe["costs"].items():
        total = cost_amount * quantity
        consume_requirements[cost_key] = consume_requirements.get(cost_key, 0) + total

    template_names = {
        tpl.key: tpl.name
        for tpl in ItemTemplate.objects.filter(key__in=consume_requirements.keys()).only("key", "name")
    }

    with transaction.atomic():
        locked_manor = ManorModel.objects.select_for_update().get(pk=manor.pk)
        locked_items = {
            item.template.key: item
            for item in InventoryItem.objects.select_for_update()
            .select_related("template")
            .filter(
                manor=locked_manor,
                template__key__in=consume_requirements.keys(),
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            )
        }

        for need_key, need_amount in consume_requirements.items():
            item = locked_items.get(need_key)
            if not item or item.quantity < need_amount:
                need_name = template_names.get(need_key, need_key)
                raise ForgeOperationError(f"{need_name}不足")

        for need_key, need_amount in consume_requirements.items():
            consume_inventory_item_locked(locked_items[need_key], need_amount)

        output_quantity = recipe["quantity_out"] * quantity
        add_item_to_inventory_locked(locked_manor, result_item_key, output_quantity)

    return {
        "blueprint_key": blueprint_key,
        "result_key": result_item_key,
        "result_name": result_template.name,
        "quantity": output_quantity,
        "craft_times": quantity,
    }
