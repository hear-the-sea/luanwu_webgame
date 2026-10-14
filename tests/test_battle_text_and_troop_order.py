from copy import deepcopy

import pytest
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from battle.view_helpers import build_rounds_display, serialize_casualty_rows, serialize_troops
from core.game_data.technology import troop_display_sort_key

ORDER = [
    "archer",
    "fast_archer",
    "divine_archer",
    "arrow_god",
    "quan_shi",
    "quan_ba",
    "quan_wang",
    "quan_sheng",
    "qiang_ke",
    "qiang_hao",
    "qiang_ba",
    "qiang_wang",
    "jian_shi",
    "jian_xia",
    "jian_hao",
    "jian_sheng",
    "dao_ke",
    "dao_jie",
    "dao_ba",
    "dao_sheng",
    "scout",
]


def test_troops_use_class_then_level_order_in_overview_and_rounds():
    stock = {key: index + 1 for index, key in enumerate(reversed(ORDER))}
    assert sorted(stock, key=troop_display_sort_key) == ORDER
    assert [row["key"] for row in serialize_troops(stock, {})] == ORDER
    raw = {
        "lineups": {
            "attacker": {
                "troops": [{"template_key": key, "name": key, "count": count} for key, count in stock.items()]
            },
            "defender": {},
        }
    }
    original = deepcopy(raw)
    rows = build_rounds_display([raw], {})[0]["lineups"]["attacker"]["troops"]
    assert [row["template_key"] for row in rows] == ORDER
    assert all(row["count"] == stock[row["template_key"]] for row in rows)
    assert raw == original
    assert troop_display_sort_key("", "箭神")[:2] == troop_display_sort_key("arrow_god")[:2]
    assert troop_display_sort_key("unknown")[0] > troop_display_sort_key("scout")[0]


def test_casualty_list_uses_the_same_troop_order():
    raw = {"casualties": [{"label": "探子", "lost": 2}, {"label": "剑圣", "lost": 3}, {"label": "弓箭手", "lost": 4}]}
    assert [row["label"] for row in serialize_casualty_rows(raw)] == ["弓箭手", "剑圣", "探子"]


def test_troop_display_order_refreshes_with_technology_cache(monkeypatch):
    from core.game_data import technology

    with monkeypatch.context() as patch:
        classes = {"gong": {"troops": ["archer", "arrow_god"]}}
        patch.setattr(technology, "load_technology_templates", lambda: {"troop_classes": classes})
        technology.clear_technology_cache()
        assert troop_display_sort_key("archer") < troop_display_sort_key("arrow_god")
        classes["gong"]["troops"].reverse()
        technology.clear_technology_cache()
        assert troop_display_sort_key("arrow_god") < troop_display_sort_key("archer")
    technology.clear_technology_cache()


@pytest.mark.parametrize("viewer", ["attacker", "defender", "spectator"])
def test_primary_dodge_preserves_skill_name_and_secondary_status(viewer):
    event = {
        "actor": "浪翻云",
        "kind": "guest",
        "side": "attacker",
        "target": "刀霸",
        "skills": ["覆雨剑法"],
        "is_dodge": True,
        "additional_targets": [
            {
                "actor": "浪翻云",
                "kind": "guest",
                "side": "attacker",
                "target": "剑圣",
                "skills": ["覆雨剑法"],
                "damage": 123,
                "status_inflicted": ["士气低落"],
                "reflect_damage": 45,
                "reflect_defeated": True,
            }
        ],
    }
    html = render_to_string(
        "battle/partials/round_event_list.html",
        {
            "events": [event],
            "my_side": viewer,
            "is_spectator": viewer == "spectator",
        },
    )
    text = "".join(strip_tags(html).split())
    assert text.count("覆雨剑法") == 1
    assert "刀霸闪避了攻击" in text
    assert "对剑圣造成状态：士气低落" in text
    assert "爆发护身剑罡，造成伤害45" in text


def test_target_statuses_follow_their_own_hit_instead_of_the_last_target():
    event = {
        "actor": "浪翻云",
        "target": "刀霸",
        "damage": 100,
        "status_inflicted": ["主目标标记"],
        "additional_targets": [{"actor": "浪翻云", "target": "剑圣", "damage": 50, "status_inflicted": ["次目标标记"]}],
    }
    html = render_to_string("battle/partials/round_event_list.html", {"events": [event]})
    assert html.index("主目标标记") < html.index("波及") < html.index("次目标标记")
