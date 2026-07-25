from __future__ import annotations

from guilds.templatetags import guild_extras


def test_get_item_returns_dictionary_value_or_fallback_key():
    assert guild_extras.get_item({"gold": 12}, "gold") == 12
    assert guild_extras.get_item({"gold": 12}, "wood") == "未知项目"
    assert guild_extras.get_item(None, "wood") is None


def test_numeric_filters_handle_valid_and_invalid_inputs():
    assert guild_extras.mul("6", "7") == 42
    assert guild_extras.mul("bad", 7) == 0
    assert guild_extras.add_filter("6", "7") == 13
    assert guild_extras.add_filter("1.5", "2.5") == 4.0
    assert guild_extras.add_filter("bad", 7) == "bad"
