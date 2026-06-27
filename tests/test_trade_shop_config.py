from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from gameplay.models import ItemTemplate
from trade.services import shop_config


@pytest.mark.django_db
def test_load_shop_config_normalizes_invalid_entries(tmp_path, monkeypatch):
    cfg_path = tmp_path / "shop_items.yaml"
    cfg_path.write_text(
        textwrap.dedent(
            """
            items:
              - item_key: item_a
                price: -5
                buy_price: 30
                stock: -3
                daily_refresh: "true"
              - item_key: item_b
                price: foo
                buy_price: -10
                stock: bar
                daily_refresh: "false"
              - item_key: ""
                stock: 1
              - bad_entry
            """
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(shop_config, "SHOP_CONFIG_PATH", cfg_path)

    configs = shop_config.load_shop_config()
    assert [c.item_key for c in configs] == ["item_a", "item_b"]

    assert configs[0].price is None
    assert configs[0].buy_price == 30
    assert configs[0].stock == 0
    assert configs[0].daily_refresh is True

    assert configs[1].price is None
    assert configs[1].buy_price is None
    assert configs[1].stock == 0
    assert configs[1].daily_refresh is False


@pytest.mark.django_db
def test_shop_config_is_limited_to_grain_medicine_recruit_equipment_and_guest_scrolls():
    allowed_medicine_keys = {
        "zhixuesan",
        "jinchuangyao",
        "baijiwan",
        "baicaodan",
        "buxuedan",
        "dingxiangdan",
        "tianxiangyuluwan",
    }
    allowed_scroll_keys = {
        "zhuyingtai_guest_scroll",
        "liangshanbo_guest_scroll",
        "mawencai_guest_scroll",
    }

    troop_data = yaml.safe_load(Path("data/troop_templates.yaml").read_text(encoding="utf-8"))
    allowed_equipment_keys = {
        equipment_key
        for troop in troop_data["troops"]
        for equipment_key in (troop.get("recruit") or {}).get("equipment") or []
    }

    expected_keys = {"grain"} | allowed_medicine_keys | allowed_scroll_keys | allowed_equipment_keys
    actual_keys = {config.item_key for config in shop_config.load_shop_config()}

    assert actual_keys == expected_keys


@pytest.mark.django_db
def test_tracked_skill_books_are_not_sold_in_shop():
    tracked_skill_books = {
        "book_prison_break_blade",
        "book_city_felling_strike",
        "book_fatal_chain_sword",
        "book_meteor_pierce_moon",
        "book_hell_instant_formation",
        "book_steadfast_planning",
        "book_iron_wall_heart",
        "book_last_chance_revival",
        "book_draw_enemy_blades",
        "book_desperate_beast",
        "book_bloodthirsty_fury",
        "book_comrade_command",
    }
    shop_item_keys = {config.item_key for config in shop_config.load_shop_config()}

    assert tracked_skill_books.isdisjoint(shop_item_keys)


@pytest.mark.django_db
def test_recruit_equipment_shop_buy_prices_are_150_percent_of_forge_cost():
    item_data = yaml.safe_load(Path("data/item_templates.yaml").read_text(encoding="utf-8"))
    forge_data = yaml.safe_load(Path("data/forge_equipment.yaml").read_text(encoding="utf-8"))
    troop_data = yaml.safe_load(Path("data/troop_templates.yaml").read_text(encoding="utf-8"))

    item_prices = {item["key"]: item["price"] for item in item_data["items"] if isinstance(item, dict)}
    forge_equipment = forge_data["equipment"]
    recruit_equipment_keys = {
        equipment_key
        for troop in troop_data["troops"]
        for equipment_key in (troop.get("recruit") or {}).get("equipment") or []
    }
    shop_config_by_key = {config.item_key: config for config in shop_config.load_shop_config()}

    for equipment_key in recruit_equipment_keys:
        config = shop_config_by_key[equipment_key]
        assert config.stock == -1

        materials = forge_equipment.get(equipment_key, {}).get("materials")
        if not materials:
            continue

        forge_cost = sum(item_prices[material_key] * amount for material_key, amount in materials.items())
        assert config.buy_price == int(forge_cost * 1.5)


@pytest.mark.django_db
def test_recruit_mounts_use_default_shop_markup():
    for item_key, sell_price, expected_buy_price in [
        ("equip_zaohongma", 620, 930),
        ("equip_huangbiaoma", 975, 1463),
        ("equip_dawanma", 1500, 2250),
    ]:
        ItemTemplate.objects.create(
            key=item_key,
            name=item_key,
            effect_type="equip_mount",
            price=sell_price,
        )
        config = shop_config.get_shop_item_config(item_key)
        assert config is not None
        assert config.buy_price is None
        assert shop_config.get_sell_price(item_key) == sell_price
        assert shop_config.get_buy_price(item_key) == expected_buy_price
