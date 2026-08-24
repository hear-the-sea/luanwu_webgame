from __future__ import annotations

import textwrap

import pytest

from gameplay.services import luanwu_shop


def test_load_luanwu_shop_config_reads_products_and_currency(tmp_path, monkeypatch):
    config_path = tmp_path / "luanwu_shop.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            currency_item_key: event_coin
            items:
              - key: event_pack
                price: 2
                item_key: event_item
                reward_type: item
                reward_quantity: 5
              - key: random_blueprint
                name: 随机图纸
                description: 随机获得一张图纸。
                price: 7
                reward_type: random_device_blueprint
            """
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(luanwu_shop, "LUANWU_SHOP_CONFIG_PATH", config_path)
    try:
        luanwu_shop.clear_luanwu_shop_config_cache()
        config = luanwu_shop.load_luanwu_shop_config()

        assert config.currency_item_key == "event_coin"
        assert [product.key for product in config.products] == ["event_pack", "random_blueprint"]
        assert config.products[0].item_key == "event_item"
        assert config.products[0].reward_quantity == 5
        assert config.products[1].is_random_device_blueprint is True
    finally:
        monkeypatch.undo()
        luanwu_shop.clear_luanwu_shop_config_cache()


def test_default_luanwu_shop_includes_requested_coin_products():
    luanwu_shop.clear_luanwu_shop_config_cache()
    try:
        config = luanwu_shop.load_luanwu_shop_config()
    finally:
        luanwu_shop.clear_luanwu_shop_config_cache()

    products = {product.key: product for product in config.products}
    expected_prices = {
        "manor_rename_card": 1,
        "peace_shield_small": 1,
        "peace_shield_medium": 2,
        "peace_shield_large": 3,
        "xisuidan": 15,
        "edward_blue_guest_scroll": 15,
        "edward_purple_guest_scroll": 30,
    }

    assert config.currency_item_key == "chunqiu_coin"
    assert {key: products[key].price for key in expected_prices} == expected_prices
    assert {key: products[key].item_key for key in expected_prices} == {key: key for key in expected_prices}
    assert all(products[key].reward_quantity == 1 for key in expected_prices)


def test_load_luanwu_shop_config_rejects_invalid_reward_type(tmp_path, monkeypatch):
    config_path = tmp_path / "luanwu_shop.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            currency_item_key: chunqiu_coin
            items:
              - key: broken
                name: 错误商品
                description: 错误配置。
                price: 1
                item_key: broken_item
                reward_type: unsupported
                reward_quantity: 1
            """
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(luanwu_shop, "LUANWU_SHOP_CONFIG_PATH", config_path)
    try:
        with pytest.raises(AssertionError, match="reward_type"):
            luanwu_shop.clear_luanwu_shop_config_cache()
            luanwu_shop.load_luanwu_shop_config()
    finally:
        monkeypatch.undo()
        luanwu_shop.clear_luanwu_shop_config_cache()


def test_load_luanwu_shop_config_rejects_non_integer_price(tmp_path, monkeypatch):
    config_path = tmp_path / "luanwu_shop.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            currency_item_key: chunqiu_coin
            items:
              - key: broken_price
                price: 1.5
                item_key: fangdajing
                reward_type: item
                reward_quantity: 1
            """
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(luanwu_shop, "LUANWU_SHOP_CONFIG_PATH", config_path)
    try:
        with pytest.raises(AssertionError, match="price"):
            luanwu_shop.clear_luanwu_shop_config_cache()
            luanwu_shop.load_luanwu_shop_config()
    finally:
        monkeypatch.undo()
        luanwu_shop.clear_luanwu_shop_config_cache()
