from __future__ import annotations

from typing import Any

from django.db import transaction

from core.exceptions import ForgeOperationError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.models import Manor as ManorModel

from .. import technology as technology_service
from ..equipment_affixes import (
    AFFIX_FIELD,
    AFFIX_LABELS,
    BASE_KEY_FIELD,
    equipment_affix_key,
    roll_equipment_reward_items_locked,
)
from ..inventory.core import add_items_to_inventory_locked, consume_inventory_item_locked
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


def _equipment_material_aliases(
    template_map: dict[str, ItemTemplate], material_keys: set[str]
) -> dict[str, tuple[str, str]]:
    """只将配方引用的装备基模板扩展为固定词缀身份。"""
    aliases = {}
    for key in sorted(material_keys):
        template = template_map.get(key)
        if not template or not str(template.effect_type or "").startswith("equip_"):
            continue
        payload = template.effect_payload or {}
        if not isinstance(payload, dict) or payload.get(BASE_KEY_FIELD) or payload.get(AFFIX_FIELD):
            continue
        for affix in AFFIX_LABELS:
            aliases[equipment_affix_key(key, affix)] = (key, affix)
    return aliases


def _group_blueprint_material_inventory(
    inventory_items: list[InventoryItem],
    template_map: dict[str, ItemTemplate],
    aliases: dict[str, tuple[str, str]],
) -> dict[str, list[InventoryItem]]:
    grouped: dict[str, list[InventoryItem]] = {}
    for item in inventory_items:
        template = item.template
        grouped.setdefault(template.key, []).append(item)
        identity = aliases.get(template.key)
        if identity is None:
            continue
        base_key, affix = identity
        base = template_map[base_key]
        payload = template.effect_payload or {}
        if (
            isinstance(payload, dict)
            and payload.get(BASE_KEY_FIELD) == base_key
            and payload.get(AFFIX_FIELD) == affix
            and template.effect_type == base.effect_type
            and template.rarity == base.rarity
        ):
            grouped.setdefault(base_key, []).append(item)
    for key, items in grouped.items():
        # 先用原版装备，再按库存主键消耗变体，保证扣除顺序可复现。
        items.sort(key=lambda item: (item.template.key != key, item.pk))
    return grouped


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
    material_keys = {key for recipe in recipes for key in recipe["costs"]}
    aliases = _equipment_material_aliases(template_map, material_keys)
    inventory_items = list(
        InventoryItem.objects.filter(
            manor=manor,
            template__key__in=all_keys | aliases.keys(),
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .select_related("template")
        .order_by("id")
    )
    quantities = build_inventory_quantity_map(inventory_items)
    material_inventory = _group_blueprint_material_inventory(inventory_items, template_map, aliases)
    for key in material_keys:
        quantities[key] = sum(item.quantity for item in material_inventory.get(key, []))
    bound_quantities = {item.template.key: item.bound_quantity for item in inventory_items}
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
            result_template = template_map.get(recipe["result_item_key"])
            option["result_image_url"] = result_template.image.url if result_template and result_template.image else ""
            option["blueprint_bound_count"] = bound_quantities.get(recipe["blueprint_key"], 0)
            option["blueprint_unbound_count"] = option["blueprint_count"] - option["blueprint_bound_count"]
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

    template_map = {tpl.key: tpl for tpl in ItemTemplate.objects.filter(key__in=consume_requirements)}
    aliases = _equipment_material_aliases(template_map, set(recipe["costs"]))

    with transaction.atomic():
        locked_manor = ManorModel.objects.select_for_update().get(pk=manor.pk)
        locked_items = list(
            InventoryItem.objects.select_for_update()
            .select_related("template")
            .filter(
                manor=locked_manor,
                template__key__in=consume_requirements.keys() | aliases.keys(),
                storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            )
            .order_by("id")
        )
        material_inventory = _group_blueprint_material_inventory(locked_items, template_map, aliases)
        remaining = {item.pk: item.quantity for item in locked_items}
        consumption_plan: dict[str, list[tuple[InventoryItem, int]]] = {}

        # 精确指定的材料先预留，避免同一词缀库存被两个材料要求重复计算。
        base_material_keys = {base for base, _ in aliases.values()}
        for need_key in sorted(consume_requirements, key=lambda key: key in base_material_keys):
            needed = consume_requirements[need_key]
            plan = []
            for item in material_inventory.get(need_key, []):
                used = min(needed, remaining[item.pk])
                if used:
                    plan.append((item, used))
                    remaining[item.pk] -= used
                    needed -= used
                if not needed:
                    break
            if needed:
                need_name = getattr(template_map.get(need_key), "name", "所需材料")
                raise ForgeOperationError(f"{need_name}不足")
            consumption_plan[need_key] = plan

        # 同一图纸可能同时出现在主图纸和 costs 中，先汇总后各扣一次，
        # 再按每次合成的图纸用量分配绑定产物，避免绑定图纸被重复分配。
        bound_craft_count = 0
        for need_key, need_amount in consume_requirements.items():
            bound_used = sum(consume_inventory_item_locked(item, amount) for item, amount in consumption_plan[need_key])
            need_template = template_map.get(need_key)
            is_equipment = need_template and str(need_template.effect_type or "").startswith("equip_")
            if (need_key == blueprint_key) or (need_key.startswith("blueprint_")) or is_equipment:
                per_craft = need_amount // quantity
                bound_craft_count = max(bound_craft_count, (bound_used + per_craft - 1) // per_craft)

        output_quantity = recipe["quantity_out"] * quantity
        bound_output_quantity = bound_craft_count * recipe["quantity_out"]
        actual_items: dict[str, int] = {}
        actual_templates: dict[str, ItemTemplate] = {}
        bound_items: dict[str, int] = {}
        for amount, is_bound in (
            (bound_output_quantity, True),
            (output_quantity - bound_output_quantity, False),
        ):
            if not amount:
                continue
            rolled_items, rolled_templates = roll_equipment_reward_items_locked({result_item_key: amount})
            actual_templates.update(rolled_templates)
            for actual_key, actual_amount in rolled_items.items():
                actual_items[actual_key] = actual_items.get(actual_key, 0) + actual_amount
                bound_items.setdefault(actual_key, 0)
                if is_bound:
                    bound_items[actual_key] += actual_amount
        add_items_to_inventory_locked(
            locked_manor,
            actual_items,
            templates=actual_templates,
            bound_quantities=bound_items,
        )

    return {
        "blueprint_key": blueprint_key,
        "items": actual_items,
        "result_key": result_item_key,
        "result_name": result_template.name,
        "quantity": output_quantity,
        "bound_quantity": bound_craft_count * recipe["quantity_out"],
        "unbound_quantity": output_quantity - bound_craft_count * recipe["quantity_out"],
        "craft_times": quantity,
    }
