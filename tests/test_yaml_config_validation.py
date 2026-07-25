from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import battle.troops as troop_loader
import gameplay.services.buildings.base as building_base
import gameplay.services.buildings.forge as forge_service
import gameplay.services.recruitment.recruitment as troop_recruitment
import guilds.services.warehouse_config as warehouse_config
import trade.services.auction_config as auction_config
import trade.services.shop_config as shop_config


def test_warehouse_production_item_keys_exist_in_item_templates():
    base_dir = Path(__file__).resolve().parents[1]
    production = yaml.safe_load((base_dir / "data" / "warehouse_production.yaml").read_text(encoding="utf-8"))
    item_templates = yaml.safe_load((base_dir / "data" / "item_templates.yaml").read_text(encoding="utf-8"))
    item_keys = {entry["key"] for entry in item_templates["items"]}

    missing = []
    for section, section_data in production.items():
        for level, entries in (section_data.get("levels") or {}).items():
            for entry in entries:
                item_key = entry.get("item_key")
                if item_key not in item_keys:
                    missing.append((section, level, item_key))

    assert missing == []


def test_shop_recruit_equipment_matches_troop_requirements():
    base_dir = Path(__file__).resolve().parents[1]
    shop_items = yaml.safe_load((base_dir / "data" / "shop_items.yaml").read_text(encoding="utf-8"))
    troop_templates = yaml.safe_load((base_dir / "data" / "troop_templates.yaml").read_text(encoding="utf-8"))

    recruit_equipment_keys = {
        equipment_key
        for troop in troop_templates["troops"]
        for equipment_key in (troop.get("recruit") or {}).get("equipment") or []
    }
    shop_equipment_keys = {
        entry["item_key"] for entry in shop_items["items"] if str(entry["item_key"]).startswith("equip_")
    }

    assert shop_equipment_keys == recruit_equipment_keys


def test_shop_items_match_expected_public_sale_catalog():
    base_dir = Path(__file__).resolve().parents[1]
    shop_items = yaml.safe_load((base_dir / "data" / "shop_items.yaml").read_text(encoding="utf-8"))
    troop_templates = yaml.safe_load((base_dir / "data" / "troop_templates.yaml").read_text(encoding="utf-8"))

    recruit_equipment_keys = {
        equipment_key
        for troop in troop_templates["troops"]
        for equipment_key in (troop.get("recruit") or {}).get("equipment") or []
    }

    expected_keys = [
        "grain",
        "zhixuesan",
        "jinchuangyao",
        "baijiwan",
        "baicaodan",
        "buxuedan",
        "dingxiangdan",
        "tianxiangyuluwan",
        "zhuyingtai_guest_scroll",
        "liangshanbo_guest_scroll",
        "mawencai_guest_scroll",
        *sorted(recruit_equipment_keys),
    ]

    assert [entry["item_key"] for entry in shop_items["items"]] == expected_keys


def test_recruitment_troop_loader_sanitizes_invalid_yaml(tmp_path):
    config_path = tmp_path / "troop_templates.yaml"
    config_path.write_text(
        """
troops:
  - key: spearman
    base_attack: bad
    recruit:
      tech_key: 123
      tech_level: bad
      equipment: [equip_spear, '', null, 7]
      retainer_cost: 0
      base_duration: bad
  - key: broken_recruit
    recruit: not_a_mapping
  - name: missing_key
  - not_a_mapping
""",
        encoding="utf-8",
    )

    troop_recruitment.clear_troop_cache()
    loaded = troop_recruitment.load_troop_templates(str(config_path))
    troop_recruitment.clear_troop_cache()

    assert isinstance(loaded, dict)
    assert "troops" in loaded
    assert [troop["key"] for troop in loaded["troops"]] == ["spearman", "broken_recruit"]

    spearman = loaded["troops"][0]
    assert spearman["name"] == "未知兵种"
    assert spearman["base_attack"] == 0
    assert spearman["recruit"]["tech_key"] == "123"
    assert spearman["recruit"]["tech_level"] == 0
    assert spearman["recruit"]["equipment"] == ["equip_spear", "7"]
    assert spearman["recruit"]["retainer_cost"] == 1
    assert spearman["recruit"]["base_duration"] == 120

    broken_recruit = loaded["troops"][1]
    assert broken_recruit["recruit"] is None


def test_battle_troop_yaml_loader_tolerates_invalid_fields(tmp_path):
    config_path = tmp_path / "troop_templates.yaml"
    config_path.write_text(
        """
troops:
  - key: archer
    priority: bad
    default_count: bad
  - key: swordsman
    priority: 2
    default_count: 150
  - not_a_mapping
  - name: missing_key
""",
        encoding="utf-8",
    )

    troop_loader.load_troop_templates_from_yaml.cache_clear()
    loaded = troop_loader.load_troop_templates_from_yaml(str(config_path))
    troop_loader.load_troop_templates_from_yaml.cache_clear()

    assert list(loaded.keys()) == ["archer", "swordsman"]
    assert loaded["archer"]["priority"] == 0
    assert loaded["archer"]["default_count"] == 120
    assert loaded["swordsman"]["priority"] == 2
    assert loaded["swordsman"]["default_count"] == 150


def test_invalidate_troop_templates_cache_bubbles_cache_clear_errors(monkeypatch):
    monkeypatch.setattr(troop_loader.cache, "delete", lambda _key: None)
    monkeypatch.setattr(
        troop_loader.load_troop_templates_from_yaml,
        "cache_clear",
        lambda: (_ for _ in ()).throw(RuntimeError("cache clear bug")),
    )

    with pytest.raises(RuntimeError, match="cache clear bug"):
        troop_loader.invalidate_troop_templates_cache()


def test_auction_config_loader_coerces_invalid_types(tmp_path, monkeypatch):
    config_path = tmp_path / "auction_items.yaml"
    config_path.write_text(
        """
settings:
  cycle_days: bad
  min_increment_ratio: bad
  default_min_increment: 0
items:
  - item_key: auction_sword
    slots: -5
    quantity_per_slot: bad
    starting_price: 0
    min_increment: bad
    enabled: "off"
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(auction_config, "AUCTION_CONFIG_PATH", config_path)
    auction_config.reload_auction_config()
    loaded = auction_config.load_auction_config()
    auction_config.reload_auction_config()

    assert loaded.settings.cycle_days == 3
    assert loaded.settings.min_increment_ratio == 0.1
    assert loaded.settings.default_min_increment == 1

    assert len(loaded.items) == 1
    item = loaded.items[0]
    assert item.item_key == "auction_sword"
    assert item.slots == 1
    assert item.quantity_per_slot == 1
    assert item.starting_price == 1
    assert item.min_increment == 1
    assert item.enabled is False


def test_warehouse_production_loader_sanitizes_invalid_values(tmp_path, monkeypatch):
    config_path = tmp_path / "warehouse_production.yaml"
    config_path.write_text(
        """
equipment:
  levels:
    "1":
      - item_key: equip_spear
        quantity: -2
        contribution_cost: bad
      - item_key: ""
    bad_level:
      - item_key: equip_invalid
    "2": not_a_list
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(warehouse_config, "WAREHOUSE_PRODUCTION_PATH", config_path)
    warehouse_config.reload_warehouse_production()
    loaded = warehouse_config.load_warehouse_production()
    warehouse_config.reload_warehouse_production()

    assert "equipment" in loaded
    equipment = loaded["equipment"]
    assert sorted(equipment.levels.keys()) == [1, 2]
    assert len(equipment.levels[1]) == 1
    assert equipment.levels[1][0].item_key == "equip_spear"
    assert equipment.levels[1][0].quantity == 1
    assert equipment.levels[1][0].contribution_cost == 0
    assert equipment.levels[2] == []


def test_shop_config_loader_handles_non_mapping_root(monkeypatch):
    shop_config.reload_shop_config()
    monkeypatch.setattr(shop_config, "load_yaml_data", lambda *args, **kwargs: ["invalid-root"])

    loaded = shop_config.load_shop_config()

    assert loaded == []
    shop_config.reload_shop_config()


def test_building_templates_loader_sanitizes_invalid_structure(monkeypatch):
    building_base.clear_building_cache()
    monkeypatch.setattr(
        building_base,
        "load_yaml_data",
        lambda *args, **kwargs: {
            "categories": [
                {"key": "military", "name": "Military"},
                {"name": "WithoutKey"},
                "bad-category-row",
            ],
            "buildings": [
                {"key": "forge", "category": "military", "description": 123},
                {"name": "missing_key"},
                "bad-building-row",
            ],
        },
    )

    payload = building_base.load_building_templates()
    assert [row["key"] for row in payload["buildings"]] == ["forge"]

    assert building_base.get_building_description("forge") == "123"
    assert building_base.get_building_description("missing") == ""
    assert [row["key"] for row in building_base.get_buildings_by_category("military")] == ["forge"]

    categories = building_base.get_building_categories()
    assert len(categories) == 2
    building_base.clear_building_cache()


def test_forge_blueprint_config_loader_rejects_non_mapping_root(monkeypatch):
    forge_service.clear_forge_blueprint_cache()
    monkeypatch.setattr(forge_service, "load_yaml_data", lambda *args, **kwargs: ["invalid-root"])

    try:
        with pytest.raises(AssertionError, match="invalid forge config blueprint root"):
            forge_service.load_forge_blueprint_config()
    finally:
        monkeypatch.undo()
        forge_service.clear_forge_blueprint_cache()


def test_forge_blueprint_config_loader_rejects_invalid_recipe(monkeypatch):
    forge_service.clear_forge_blueprint_cache()
    monkeypatch.setattr(
        forge_service,
        "load_yaml_data",
        lambda *args, **kwargs: {
            "recipes": [
                {
                    "blueprint_key": "bp_bad",
                    "result_item_key": "equip_bad",
                    "required_forging": 1,
                    "quantity_out": 1,
                    "costs": {"tong": True},
                }
            ]
        },
    )

    try:
        with pytest.raises(AssertionError, match="invalid forge config blueprint.costs.tong"):
            forge_service.load_forge_blueprint_config()
    finally:
        monkeypatch.undo()
        forge_service.clear_forge_blueprint_cache()
