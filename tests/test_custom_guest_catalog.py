from importlib import import_module
from pathlib import Path

import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
rules = import_module("gameplay.migrations.0202_custom_guest_items_and_templates")
base_rules = import_module("gameplay.migrations.0203_custom_guest_orange_civil_base")
copy_rules = import_module("gameplay.migrations.0242_player_facing_mission_item_copy")


def test_custom_guest_catalog_matches_migration_and_generated_images():
    items = {row["key"]: row for row in yaml.safe_load((ROOT / "data/item_templates.yaml").read_text())["items"]}
    for key, defaults in rules.ITEM_DEFAULTS.items():
        expected = {
            "key": key,
            **defaults,
            "effect_type": "tool",
            "image": f"{key}.png",
            "price": 0,
            "storage_space": 1,
            "is_usable": key == "custom_guest_card",
            **copy_rules.ITEM_UPDATES.get(key, {}),
        }
        assert items[key] == expected
        with Image.open(ROOT / "data/images/items" / expected["image"]) as image:
            image.verify()
    approved_sources = {
        "mission_chest_v3_yaolu_hujing",
        "mission_chest_v3_xisui_yougu",
        "mission_chest_v3_yijin_mijing",
    }
    for material in ("yijinjing", "millennium_zhuguo"):
        sources = {
            item["key"]
            for item in items.values()
            if item["key"] not in rules.ITEM_DEFAULTS and material in repr(item.get("effect_payload", {}))
        }
        assert sources == approved_sources


def test_custom_guest_template_catalog_matches_migration_and_standard_hp():
    heroes = yaml.safe_load((ROOT / "data/guests/custom.yaml").read_text())["heroes"]
    for rarity, entries in heroes.items():
        assert len(entries) == 1
        row = entries[0]
        defaults = {
            **rules.TEMPLATE_DEFAULTS[row["key"]],
            **{field: base_rules.BASE_STATS[stat] for stat, field in base_rules.TEMPLATE_FIELDS.items()},
        }
        assert not row["recruitable"]
        assert defaults["rarity"] == rarity
        assert row["attribute_weights"] == defaults["attribute_weights"]
        assert row["growth_range"] == defaults["growth_range"]
        mapping = {
            "force": "base_attack",
            "intellect": "base_intellect",
            "defense": "base_defense",
            "agility": "base_agility",
            "luck": "base_luck",
            "base_hp": "base_hp",
        }
        for key, field in mapping.items():
            assert row["custom_stats"][key] == defaults[field]
        assert defaults["base_hp"] == {"green": 400, "blue": 600, "purple": 800, "orange": 1000}[rarity]
