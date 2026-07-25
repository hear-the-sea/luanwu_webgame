from __future__ import annotations

from typing import Any, Dict, Iterable

WEAPON_KEYWORD_TO_CATEGORY: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sword", ("jian",)),
    ("dao", ("dao",)),
    ("spear", ("qiang", "mao")),
    ("bow", ("gong",)),
    ("whip", ("bian",)),
)

DECOMPOSE_WEAPON_CATEGORIES: set[str] = {"sword", "dao", "spear", "bow", "whip"}
DECOMPOSE_CATEGORIES: Dict[str, str] = {
    "helmet": "头盔",
    "armor": "衣服",
    "shoes": "鞋子",
    "weapon": "武器",
    "device": "器械",
}


def infer_equipment_category(
    item_key: str,
    effect_type: str | None = None,
    *,
    equipment_config: dict[str, dict[str, Any]] | None = None,
) -> str | None:
    """推断装备分类，优先使用锻造配置，缺失时按 effect_type/key 兜底。"""
    config_map = equipment_config or {}
    config = config_map.get(item_key)
    if config:
        category = config.get("category")
        if isinstance(category, str) and category:
            return category

    normalized_effect_type = (effect_type or "").strip()
    if normalized_effect_type == "equip_helmet":
        return "helmet"
    if normalized_effect_type == "equip_armor":
        return "armor"
    if normalized_effect_type == "equip_shoes":
        return "shoes"
    if normalized_effect_type == "equip_weapon":
        key_lower = (item_key or "").lower()
        for category, keywords in WEAPON_KEYWORD_TO_CATEGORY:
            if any(keyword in key_lower for keyword in keywords):
                return category
    if normalized_effect_type == "equip_device":
        return "device"
    return None


def to_decompose_category(equipment_category: str | None) -> str | None:
    """分解分类映射：剑/刀/枪/弓/鞭统一归类为武器。"""
    if not equipment_category:
        return None
    if equipment_category in DECOMPOSE_WEAPON_CATEGORIES:
        return "weapon"
    return equipment_category


def collect_recipe_related_keys(recipes: Iterable[dict[str, Any]]) -> set[str]:
    all_keys: set[str] = set()
    for recipe in recipes:
        all_keys.add(str(recipe.get("blueprint_key") or ""))
        all_keys.add(str(recipe.get("result_item_key") or ""))
        for cost_key in (recipe.get("costs", {}) or {}).keys():
            all_keys.add(str(cost_key or ""))
    all_keys.discard("")
    return all_keys


def build_inventory_quantity_map(inventory_items: Iterable[Any]) -> dict[str, int]:
    quantities: dict[str, int] = {}
    for item in inventory_items:
        key = str(getattr(getattr(item, "template", None), "key", "") or "")
        if not key:
            continue
        quantities[key] = quantities.get(key, 0) + int(getattr(item, "quantity", 0) or 0)
    return quantities


def build_blueprint_synthesis_option(
    recipe: dict[str, Any],
    *,
    quantities: dict[str, int],
    template_map: dict[str, Any],
    forging_level: int,
) -> dict[str, Any] | None:
    blueprint_key = str(recipe["blueprint_key"])
    blueprint_count = quantities.get(blueprint_key, 0)
    if blueprint_count <= 0:
        return None

    result_key = str(recipe["result_item_key"])
    quantity_out = int(recipe.get("quantity_out", 1) or 1)
    required_forging = int(recipe.get("required_forging", 1) or 1)
    costs = recipe.get("costs", {}) or {}

    costs_info: list[dict[str, Any]] = []
    max_by_cost = blueprint_count
    can_afford = True
    for cost_key, cost_amount_raw in costs.items():
        cost_key_str = str(cost_key)
        cost_amount = int(cost_amount_raw or 0)
        current_amount = quantities.get(cost_key_str, 0)
        cost_template = template_map.get(cost_key_str)
        costs_info.append(
            {
                "key": cost_key_str,
                "name": getattr(cost_template, "name", "未知材料"),
                "required": cost_amount,
                "current": current_amount,
            }
        )
        if cost_amount > 0:
            max_by_cost = min(max_by_cost, current_amount // cost_amount)
        if current_amount < cost_amount:
            can_afford = False

    is_unlocked = forging_level >= required_forging
    max_synthesis_quantity = max(0, max_by_cost)
    blueprint_template = template_map.get(blueprint_key)
    result_template = template_map.get(result_key)
    return {
        "blueprint_key": blueprint_key,
        "blueprint_name": getattr(blueprint_template, "name", "未知图纸"),
        "blueprint_count": blueprint_count,
        "result_key": result_key,
        "result_name": getattr(result_template, "name", "未知装备"),
        "result_effect_type": str(getattr(result_template, "effect_type", "") or ""),
        "result_quantity": quantity_out,
        "required_forging": required_forging,
        "description": str(recipe.get("description", "") or ""),
        "costs": costs_info,
        "max_synthesis_quantity": max_synthesis_quantity,
        "is_unlocked": is_unlocked,
        "can_afford": can_afford,
        "can_synthesize": is_unlocked and can_afford and max_synthesis_quantity > 0,
    }


def collect_material_keys(filtered_configs: Iterable[tuple[str, dict[str, Any]]]) -> set[str]:
    material_keys: set[str] = set()
    for _equip_key, config in filtered_configs:
        material_keys.update((config.get("materials") or {}).keys())
    return material_keys


def build_equipment_option(
    equip_key: str,
    config: dict[str, Any],
    *,
    item_name_map: dict[str, str],
    material_quantities: dict[str, int],
    material_name_fallback_map: dict[str, str],
    equipment_categories: dict[str, str],
    actual_duration: int,
    required_level: int,
    forging_level: int,
    max_quantity: int,
    is_forging: bool,
) -> dict[str, Any]:
    equipment_name = item_name_map.get(equip_key, "未知装备")
    materials = config.get("materials", {}) or {}
    material_info: list[dict[str, Any]] = []
    can_afford = True
    for mat_key, mat_amount in materials.items():
        current_amount = material_quantities.get(mat_key, 0)
        mat_name = item_name_map.get(mat_key, material_name_fallback_map.get(mat_key, "未知材料"))
        material_info.append(
            {
                "key": mat_key,
                "name": mat_name,
                "required": mat_amount,
                "current": current_amount,
            }
        )
        if current_amount < mat_amount:
            can_afford = False

    return {
        "key": equip_key,
        "name": equipment_name,
        "category": config["category"],
        "category_name": equipment_categories.get(config["category"], "其他装备"),
        "materials": material_info,
        "base_duration": config["base_duration"],
        "actual_duration": actual_duration,
        "can_afford": can_afford,
        "required_forging": required_level,
        "is_unlocked": forging_level >= required_level,
        "max_quantity": max_quantity,
        "is_forging": is_forging,
    }
