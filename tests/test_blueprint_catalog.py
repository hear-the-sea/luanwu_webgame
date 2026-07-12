from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from gameplay.models import ItemTemplate
from gameplay.services.buildings import forge as forge_service
from gameplay.services.buildings.forge_blueprints import build_blueprint_recipe_index


def _create_item_template(key: str, *, effect_type: str, rarity: str, tradeable: bool = True) -> ItemTemplate:
    return ItemTemplate.objects.create(
        key=key,
        name=key,
        effect_type=effect_type,
        rarity=rarity,
        tradeable=tradeable,
        is_usable=False,
    )


def _recipe(blueprint_key: str, result_item_key: str) -> dict[str, object]:
    return {
        "blueprint_key": blueprint_key,
        "result_item_key": result_item_key,
        "required_forging": 1,
        "quantity_out": 1,
        "costs": {"tong": 1},
    }


def _use_blueprint_config(monkeypatch, config: dict[str, object]) -> None:
    monkeypatch.setattr(forge_service, "load_yaml_data", lambda *args, **kwargs: config)


@pytest.mark.django_db
def test_load_blueprint_catalog_returns_immutable_blueprint_entries(monkeypatch):
    blueprint = _create_item_template("blueprint_catalog_valid", effect_type="tool", rarity="blue")
    result = _create_item_template("equip_catalog_valid", effect_type="equip_weapon", rarity="blue")
    _use_blueprint_config(monkeypatch, {"recipes": [_recipe(blueprint.key, result.key)]})

    try:
        forge_service.clear_forge_blueprint_cache()
        catalog = forge_service.load_blueprint_catalog()
    finally:
        forge_service.clear_forge_blueprint_cache()

    entry = catalog[blueprint.key]
    assert entry.key == blueprint.key
    assert entry.rarity == "blue"
    assert entry.result_key == result.key
    assert entry.result_rarity == "blue"
    with pytest.raises(FrozenInstanceError):
        entry.rarity = "purple"


@pytest.mark.django_db
def test_load_blueprint_catalog_rejects_recipe_with_missing_item_template(monkeypatch):
    _use_blueprint_config(monkeypatch, {"recipes": [_recipe("blueprint_catalog_missing", "equip_catalog_missing")]})

    try:
        forge_service.clear_forge_blueprint_cache()
        with pytest.raises(AssertionError, match="missing ItemTemplate: blueprint_catalog_missing"):
            forge_service.load_blueprint_catalog()
    finally:
        forge_service.clear_forge_blueprint_cache()


@pytest.mark.django_db
def test_load_blueprint_catalog_rejects_non_blueprint_recipe_key(monkeypatch):
    blueprint = _create_item_template("catalog_wrong_prefix", effect_type="tool", rarity="blue")
    result = _create_item_template("equip_catalog_prefix", effect_type="equip_weapon", rarity="blue")
    _use_blueprint_config(monkeypatch, {"recipes": [_recipe(blueprint.key, result.key)]})

    try:
        forge_service.clear_forge_blueprint_cache()
        with pytest.raises(AssertionError, match=f"blueprint key must start with 'blueprint_': {blueprint.key}"):
            forge_service.load_blueprint_catalog()
    finally:
        forge_service.clear_forge_blueprint_cache()


@pytest.mark.django_db
def test_load_blueprint_catalog_rejects_non_tool_blueprint_template(monkeypatch):
    blueprint = _create_item_template("blueprint_catalog_non_tool", effect_type="resource", rarity="blue")
    result = _create_item_template("equip_catalog_tool", effect_type="equip_weapon", rarity="blue")
    _use_blueprint_config(monkeypatch, {"recipes": [_recipe(blueprint.key, result.key)]})

    try:
        forge_service.clear_forge_blueprint_cache()
        with pytest.raises(AssertionError, match=f"blueprint ItemTemplate must be tool: {blueprint.key}"):
            forge_service.load_blueprint_catalog()
    finally:
        forge_service.clear_forge_blueprint_cache()


@pytest.mark.django_db
def test_load_blueprint_catalog_rejects_untradeable_template(monkeypatch):
    blueprint = _create_item_template(
        "blueprint_catalog_untradeable",
        effect_type="tool",
        rarity="blue",
        tradeable=False,
    )
    result = _create_item_template("equip_catalog_untradeable", effect_type="equip_weapon", rarity="blue")
    _use_blueprint_config(monkeypatch, {"recipes": [_recipe(blueprint.key, result.key)]})

    try:
        forge_service.clear_forge_blueprint_cache()
        with pytest.raises(AssertionError, match=f"blueprint ItemTemplate must be tradeable: {blueprint.key}"):
            forge_service.load_blueprint_catalog()
    finally:
        forge_service.clear_forge_blueprint_cache()


def test_build_blueprint_recipe_index_rejects_duplicate_blueprint_key():
    recipe = _recipe("blueprint_catalog_duplicate", "equip_catalog_duplicate")

    with pytest.raises(AssertionError, match="duplicate forge blueprint key: blueprint_catalog_duplicate"):
        build_blueprint_recipe_index({"recipes": [recipe, recipe]})


@pytest.mark.django_db
def test_load_blueprint_catalog_rejects_blueprint_without_forge_recipe(monkeypatch):
    blueprint = _create_item_template("blueprint_catalog_orphan", effect_type="tool", rarity="blue")
    _use_blueprint_config(monkeypatch, {"recipes": []})

    try:
        forge_service.clear_forge_blueprint_cache()
        with pytest.raises(AssertionError, match=f"blueprint missing forge recipe: {blueprint.key}"):
            forge_service.load_blueprint_catalog()
    finally:
        forge_service.clear_forge_blueprint_cache()


@pytest.mark.django_db
def test_load_blueprint_catalog_rejects_non_equipment_recipe_result(monkeypatch):
    blueprint = _create_item_template("blueprint_catalog_non_equipment", effect_type="tool", rarity="blue")
    result = _create_item_template("tool_catalog_result", effect_type="tool", rarity="blue")
    _use_blueprint_config(monkeypatch, {"recipes": [_recipe(blueprint.key, result.key)]})

    try:
        forge_service.clear_forge_blueprint_cache()
        with pytest.raises(AssertionError, match=f"blueprint result must be equipment: {result.key}"):
            forge_service.load_blueprint_catalog()
    finally:
        forge_service.clear_forge_blueprint_cache()


@pytest.mark.django_db
def test_load_blueprint_catalog_rejects_blueprint_result_rarity_mismatch(monkeypatch):
    blueprint = _create_item_template("blueprint_catalog_mismatch", effect_type="tool", rarity="blue")
    result = _create_item_template("equip_catalog_mismatch", effect_type="equip_weapon", rarity="purple")
    _use_blueprint_config(monkeypatch, {"recipes": [_recipe(blueprint.key, result.key)]})

    try:
        forge_service.clear_forge_blueprint_cache()
        with pytest.raises(AssertionError, match=f"blueprint rarity mismatch: {blueprint.key}"):
            forge_service.load_blueprint_catalog()
    finally:
        forge_service.clear_forge_blueprint_cache()
