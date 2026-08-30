from __future__ import annotations

from types import SimpleNamespace

from guests.models import GuestRarity
from guests.templatetags import guest_extras


def test_rarity_filters_normalize_known_values():
    assert guest_extras.rarity_class(GuestRarity.GREEN) == "rarity-green"
    assert guest_extras.rarity_class("unknown") == "rarity-default"
    assert guest_extras.rarity_label(GuestRarity.ORANGE) == "橙"
    assert guest_extras.rarity_label("unknown") == "未知稀有度"


def test_gear_summary_combines_description_stats_and_set_bonus():
    template = SimpleNamespace(
        description="佩剑",
        attack_bonus=12,
        defense_bonus=5,
        extra_stats={"luck": 3},
        set_key="qinglong_set",
        set_description="青龙套装",
        set_bonus={"pieces": 2, "bonus": {"attack": 8}},
    )

    summary = guest_extras.gear_summary(template)

    assert summary == "佩剑；攻击+12、防御+5、运势+3；青龙套装（2件）：攻击+8"
    assert "qinglong_set" not in summary


def test_gear_summary_renders_multi_tier_set_bonus():
    template = SimpleNamespace(
        description="面具",
        attack_bonus=0,
        defense_bonus=0,
        extra_stats={"hp": 260},
        set_key="kasukabe_defense_set",
        set_description="春日部防卫队",
        set_bonus=[
            {"pieces": 2, "bonus": {"attack": 40, "defense": 30}},
            {"pieces": 4, "bonus": {"hp": 600, "agility": 25, "luck": 20}},
        ],
    )

    assert guest_extras.gear_summary(template) == (
        "面具；生命+260；春日部防卫队（2件）：攻击+40、防御+30；" "春日部防卫队（4件）：生命+600、敏捷+25、运势+20"
    )


def test_gear_tooltip_renders_lines_for_stats_and_set_members():
    template = SimpleNamespace(
        description="佩剑",
        attack_bonus=12,
        defense_bonus=0,
        extra_stats={"luck": 3},
        set_key="青龙",
    )
    set_map = {
        "青龙": {
            "description": "青龙套装",
            "members": [{"name": "青龙剑", "slot": "weapon"}],
            "bonus": {"attack": 8, "luck": 2},
        }
    }

    html = str(guest_extras.gear_tooltip(template, set_map))

    assert "佩剑" in html
    assert "攻击 +12" in html
    assert "运势 +3" in html
    assert "套装：青龙套装" in html
    assert "weapon·青龙剑" in html
    assert "套装属性：" in html
    assert "攻击+8" in html
    assert "运势+2" in html


def test_gear_tooltip_renders_multi_tier_set_bonus_lines():
    template = SimpleNamespace(
        description="面具",
        attack_bonus=0,
        defense_bonus=0,
        extra_stats={"hp": 260},
        set_key="春日部防卫队",
    )
    set_map = {
        "春日部防卫队": {
            "description": "春日部防卫队",
            "members": [{"name": "动感超人面具", "slot": "helmet"}],
            "bonus": [
                {"pieces": 2, "bonus": {"attack": 40, "defense": 30}},
                {"pieces": 4, "bonus": {"hp": 600, "agility": 25, "luck": 20}},
            ],
        }
    }

    html = str(guest_extras.gear_tooltip(template, set_map))

    assert "2件套属性：" in html
    assert "攻击+40" in html
    assert "防御+30" in html
    assert "4件套属性：" in html
    assert "生命+600" in html
    assert "敏捷+25" in html
    assert "运势+20" in html


def test_gear_summary_renders_troop_capacity_label_in_chinese():
    template = SimpleNamespace(
        description="军鼓",
        attack_bonus=0,
        defense_bonus=0,
        extra_stats={"troop_capacity": 12},
        set_key="",
        set_bonus={},
    )

    assert guest_extras.gear_summary(template) == "军鼓；可携带护院人数+12"


def test_gear_tooltip_renders_troop_capacity_label_in_chinese():
    template = SimpleNamespace(
        description="军鼓",
        attack_bonus=0,
        defense_bonus=0,
        extra_stats={"troop_capacity": 12},
        set_key="",
    )

    html = str(guest_extras.gear_tooltip(template))

    assert "可携带护院人数 +12" in html
    assert "troop_capacity" not in html


def test_gear_summary_and_tooltip_render_troop_device_bonus():
    template = SimpleNamespace(
        description="机械猫",
        attack_bonus=0,
        defense_bonus=0,
        extra_stats={"force": 25},
        set_key="",
        set_bonus={},
        troop_stat_bonus_summary="弓系生命+1%",
    )

    assert guest_extras.gear_summary(template) == "机械猫；武力+25；护院加成：弓系生命+1%"
    assert "护院加成：弓系生命+1%" in str(guest_extras.gear_tooltip(template))


def test_attribute_icons_renders_expected_icon_pack():
    html = str(guest_extras.attribute_icons(85))

    assert html.count("attr-crown") == 1
    assert html.count("attr-sun") == 1
    assert html.count("attr-moon") == 1
    assert html.count("attr-star") == 1
    assert guest_extras.attribute_icons(0) == ""
