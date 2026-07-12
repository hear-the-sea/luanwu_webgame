from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gameplay.models import ItemTemplate

from .forge_blueprints import build_blueprint_recipe_index


@dataclass(frozen=True)
class BlueprintCatalogEntry:
    key: str
    rarity: str
    result_key: str
    result_rarity: str


def build_blueprint_catalog(config: dict[str, Any]) -> dict[str, BlueprintCatalogEntry]:
    """Build the validated, canonical mapping from blueprint items to equipment."""
    recipe_index = build_blueprint_recipe_index(config)
    recipe_item_keys = {
        item_key
        for blueprint_key, recipe in recipe_index.items()
        for item_key in (blueprint_key, recipe["result_item_key"])
    }
    templates = {
        template.key: template
        for template in ItemTemplate.objects.filter(key__in=recipe_item_keys).only(
            "key", "effect_type", "rarity", "tradeable"
        )
    }

    catalog: dict[str, BlueprintCatalogEntry] = {}
    for blueprint_key, recipe in recipe_index.items():
        if not blueprint_key.startswith("blueprint_"):
            raise AssertionError(f"blueprint key must start with 'blueprint_': {blueprint_key}")
        blueprint_template = templates.get(blueprint_key)
        if blueprint_template is None:
            raise AssertionError(f"blueprint catalog missing ItemTemplate: {blueprint_key}")
        if blueprint_template.effect_type != "tool":
            raise AssertionError(f"blueprint ItemTemplate must be tool: {blueprint_key}")
        if not blueprint_template.tradeable:
            raise AssertionError(f"blueprint ItemTemplate must be tradeable: {blueprint_key}")

        result_key = recipe["result_item_key"]
        result_template = templates.get(result_key)
        if result_template is None:
            raise AssertionError(f"blueprint catalog missing ItemTemplate: {result_key}")
        if not str(result_template.effect_type or "").startswith("equip_"):
            raise AssertionError(f"blueprint result must be equipment: {result_key}")
        if blueprint_template.rarity != result_template.rarity:
            raise AssertionError(f"blueprint rarity mismatch: {blueprint_key}")

        catalog[blueprint_key] = BlueprintCatalogEntry(
            key=blueprint_key,
            rarity=blueprint_template.rarity,
            result_key=result_key,
            result_rarity=result_template.rarity,
        )

    blueprint_keys = ItemTemplate.objects.filter(key__startswith="blueprint_").values_list("key", flat=True)
    for blueprint_key in blueprint_keys:
        if blueprint_key not in catalog:
            raise AssertionError(f"blueprint missing forge recipe: {blueprint_key}")

    return catalog
