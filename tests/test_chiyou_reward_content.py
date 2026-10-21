from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parents[1] / "data"
CHAIN_KEYS = (
    "zhanlong_yanshan_shizi",
    "zhanlong_changban_tuwei",
    "zhanlong_changban_duanqiao",
    "zhanlong_wuhu_shizhen",
)
ARMOR_KEYS = (
    "equip_tianshukaijia",
    "equip_tianxuankaijia",
    "equip_tianjikaijia",
    "equip_yaoguangkaijia",
    "equip_tianquankaijia",
    "equip_yuhengkaijia",
    "equip_kaiyangkaijia",
)


def _items():
    payload = yaml.safe_load((DATA / "item_templates.yaml").read_text(encoding="utf-8"))
    return {entry["key"]: entry for entry in payload["items"]}


def _payload(items, key):
    return items["mission_chest_v3_" + key]["effect_payload"]


def test_chiyou_chest_guarantees_card_and_exactly_one_weighted_extra_reward():
    items = _items()
    chest = items["mission_chest_v3_zhuolu_zhongyuan"]
    assert chest["name"] == "蚩尤宝箱"
    payload = chest["effect_payload"]
    assert payload["show_reward_probabilities"] is True
    assert payload["item_rewards"] == [
        {"item_key": "chiyou_card", "min_quantity": 1, "max_quantity": 1},
        {"item_key": "xuanyuan_sword_intent", "min_quantity": 1, "max_quantity": 1},
    ]
    assert len(payload["random_item_groups"]) == 1
    group = payload["random_item_groups"][0]
    assert group["chance"] == 1
    assert group["min_quantity"] == group["max_quantity"] == 1
    choices = {choice["item_key"]: choice for choice in group["choices"]}
    assert len(choices) == len(group["choices"]) == 10
    assert sum(choice["weight"] for choice in choices.values()) == 100
    expected = {key: (10, 1) for key in ARMOR_KEYS}
    expected.update({"blueprint_chiyoujia": (5, 1), "equip_chiyouzhanxue": (5, 1), "chunqiu_coin": (20, 2)})
    assert {key: (choice["weight"], choice["quantity"]) for key, choice in choices.items()} == expected
    assert set(choices) <= items.keys()
    assert "equip_chiyoujia" not in choices
    assert not payload["blueprint_material_support"]


def test_old_zhuolu_rewards_move_once_across_zhanlong_chain_without_changing_odds():
    items = _items()
    historical = items["mission_chest_zhuolu_zhongyuan"]["effect_payload"]
    chain = [_payload(items, key) for key in CHAIN_KEYS]
    destinations = {
        "experience_watermelon": 0,
        "blueprint_top_blue": 0,
        "blueprint_top_purple": 1,
        "book_bloodthirsty_fury": 1,
        "book_desperate_beast": 1,
        "book_prison_break_blade": 2,
        "book_city_felling_strike": 2,
        "blueprint_top_orange": 3,
        "上古王权": 3,
    }
    for old_group in historical["random_item_groups"]:
        assert sum(group == old_group for payload in chain for group in payload["random_item_groups"]) == 1
        assert old_group in chain[destinations[old_group["label"]]]["random_item_groups"]
    intent = {"item_key": "xuanyuan_sword_intent", "min_quantity": 1, "max_quantity": 1}
    assert not any(intent in payload["item_rewards"] for payload in chain)
    assert intent in _payload(items, "zhuolu_zhongyuan")["item_rewards"]
    assert chain[-1]["blueprint_material_support"] == historical["blueprint_material_support"]
    assert chain[-1]["blueprint_forging_requirements"] == historical["blueprint_forging_requirements"]
    assert all(not payload["blueprint_material_support"] for payload in chain[:-1])
    assert not any(row["item_key"] == "chiyou_card" for row in historical["item_rewards"])
    assert items["mission_chest_zhuolu_zhongyuan"]["name"] == "逐鹿中原宝箱"


def test_chiyou_cards_require_one_hundred_and_offer_an_explicit_choice():
    item = _items()["chiyou_card"]
    assert item["effect_type"] == "tool" and item["is_usable"] is True
    assert item["tradeable"] is False
    assert (DATA / "images/items" / item["image"]).is_file()
    payload = item["effect_payload"]
    assert payload["action"] == "summon_guest"
    assert payload["player_choice"] is True
    assert payload["required_items"] == {"chiyou_card": 99}
    assert not payload.get("exclusive_template_keys")
    assert payload["choices"] == [
        {"template_key": "hero_yang_zheng_purple", "label": "杨铮", "weight": 1},
        {"template_key": "hero_di_qinglin", "label": "狄青麟", "weight": 1},
    ]


def test_chiyou_exchange_guests_are_individually_world_unique_with_configured_purple_stats():
    payload = yaml.safe_load((DATA / "guests/chiyou_exchange.yaml").read_text(encoding="utf-8"))
    heroes = {hero["key"]: hero for hero in payload["heroes"]["purple"]}
    assert set(heroes) == {"hero_yang_zheng_purple", "hero_di_qinglin"}
    base_profiles = yaml.safe_load((DATA / "guest_templates.yaml").read_text(encoding="utf-8"))
    purple_military = base_profiles["attribute_profiles"]["purple"]["military"]
    stats = ("force", "intellect", "defense", "agility")
    for hero in heroes.values():
        assert hero["is_world_unique"] is True
        assert hero["recruitable"] is False
        assert hero["default_gender"] == "male"
        assert hero["archetype"] == "military"
        assert hero["growth_range"] == [6, 14]
        assert sum(hero["attribute_weights"].values()) == 100
        assert sum(hero["custom_stats"][stat] for stat in stats) == sum(purple_military[stat] for stat in stats)
        assert hero["custom_stats"]["luck"] == 98
        assert hero["custom_stats"]["base_hp"] == 800
        assert (DATA / "images/guests" / hero["avatar"]).is_file()
    assert heroes["hero_yang_zheng_purple"]["default_morality"] > heroes["hero_di_qinglin"]["default_morality"]
    assert (
        heroes["hero_yang_zheng_purple"]["custom_stats"]["defense"]
        > heroes["hero_di_qinglin"]["custom_stats"]["defense"]
    )
    assert (
        heroes["hero_di_qinglin"]["custom_stats"]["agility"]
        > heroes["hero_yang_zheng_purple"]["custom_stats"]["agility"]
    )


def test_zhanlong_keeps_entry_tokens_mount_and_independent_zhaoyun_scroll():
    items = _items()
    chain = [_payload(items, key) for key in CHAIN_KEYS]
    tokens = ("zhanlong_white_horse_token", "zhanlong_changban_guard_token", "zhanlong_dangyang_valor_letter")
    for payload, token in zip(chain, tokens):
        assert {"item_key": token, "min_quantity": 1, "max_quantity": 1} in payload["item_rewards"]
    mount_group = next(group for group in chain[1]["random_item_groups"] if group["label"] == "equip_zhaoyeyushizi")
    assert mount_group["chance"] == 0.1
    scroll_group = next(group for group in chain[3]["random_item_groups"] if group["label"] == "zhaoyun_guest_scroll")
    assert scroll_group["chance"] == 0.01
    assert scroll_group["choices"] == [{"item_key": "zhaoyun_guest_scroll", "weight": 1}]
    for payload in chain:
        for key, quantity in (("buxuedan", 6), ("tong", 20), ("xi", 12), ("tie", 8)):
            rows = [row for row in payload["item_rewards"] if row["item_key"] == key]
            assert len(rows) == 1
            assert rows[0]["max_quantity"] <= quantity


def test_seven_armors_forge_one_chiyou_armor_without_extra_materials():
    items = _items()
    recipes = yaml.safe_load((DATA / "forge_blueprints.yaml").read_text(encoding="utf-8"))["recipes"]
    recipe = next(recipe for recipe in recipes if recipe["blueprint_key"] == "blueprint_chiyoujia")
    assert recipe["result_item_key"] == "equip_chiyoujia"
    assert recipe["required_forging"] == 10
    assert recipe["quantity_out"] == 1
    assert recipe["costs"] == dict.fromkeys(ARMOR_KEYS, 1)
    blueprint = items["blueprint_chiyoujia"]
    assert blueprint["effect_type"] == "tool" and blueprint["is_usable"] is False
    assert blueprint["rarity"] == "orange" and blueprint["tradeable"] is True
    signatures = set()
    for key in ARMOR_KEYS:
        armor = items[key]
        assert armor["rarity"] == "purple" and armor["effect_type"] == "equip_armor"
        assert armor["tradeable"] is True
        assert armor["effect_payload"]["required_level"] == 55
        signatures.add(tuple(sorted(armor["effect_payload"].items())))
    assert len(signatures) == 7
    for key in (*ARMOR_KEYS, "equip_chiyoujia", "equip_chiyouzhanxue", "blueprint_chiyoujia"):
        assert (DATA / "images/items" / items[key]["image"]).is_file()


def test_chiyou_armor_is_top_tier_and_boots_match_normal_orange_budget():
    items = _items()
    armor = items["equip_chiyoujia"]
    assert armor["rarity"] == "orange" and armor["effect_type"] == "equip_armor"
    payload = armor["effect_payload"]
    assert payload["required_level"] == 80
    assert payload["luck"] == 15
    assert payload["hp"] > items["equip_longlinzhanjia"]["effect_payload"]["hp"]
    assert payload["defense"] >= items["equip_ruanweijia"]["effect_payload"]["defense"]
    assert payload["force"] >= items["equip_longlinzhanjia"]["effect_payload"]["force"]
    boots = items["equip_chiyouzhanxue"]
    assert boots["rarity"] == "orange" and boots["effect_type"] == "equip_shoes"
    budget_stats = ("defense", "force", "intellect", "agility", "luck")
    ordinary_keys = ("equip_tatianxue", "equip_shenxingxue", "equip_wujixingxue")
    average_budget = sum(
        sum(items[key]["effect_payload"].get(stat, 0) for stat in budget_stats) for key in ordinary_keys
    ) / len(ordinary_keys)
    boots_budget = sum(boots["effect_payload"].get(stat, 0) for stat in budget_stats)
    assert 0.95 * average_budget <= boots_budget <= 1.05 * average_budget
    assert boots["effect_payload"]["hp"] == 220
