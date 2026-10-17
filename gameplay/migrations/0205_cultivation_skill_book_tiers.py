"""修行任务按档次掉落绿色、蓝色、蓝色技能书。"""

from copy import deepcopy

from django.db import migrations

CHEST_BOOK_REWARDS = {
    "mission_chest_v3_yaolu_hujing": {
        "description": "打开必得易筋经×1，另有机会获得洗髓丹、千年朱果及一本初级技能书。",
        "group": {
            "label": "初级技能书",
            "chance": 0.05,
            "min_quantity": 1,
            "max_quantity": 1,
            "choices": [
                {"item_key": "book_ice_curse", "weight": 25},
                {"item_key": "book_thunder_control", "weight": 25},
                {"item_key": "book_dragon_break_curse", "weight": 25},
                {"item_key": "book_endless_chatter", "weight": 25},
                {"item_key": "book_taiyi_wind", "weight": 25},
                {"item_key": "book_steadfast_planning", "weight": 18},
                {"item_key": "book_iron_wall_heart", "weight": 18},
            ],
        },
    },
    "mission_chest_v3_xisui_yougu": {
        "description": "打开必得易筋经×2，另有机会获得洗髓丹、千年朱果及一本中级技能书。",
        "group": {
            "label": "中级技能书",
            "chance": 0.05,
            "min_quantity": 1,
            "max_quantity": 1,
            "choices": [
                {"item_key": "book_flower_rain", "weight": 18},
                {"item_key": "book_soul_erode", "weight": 18},
                {"item_key": "book_red_lotus_dance", "weight": 16},
                {"item_key": "book_fatal_chain_sword", "weight": 15},
                {"item_key": "book_last_chance_revival", "weight": 12},
                {"item_key": "book_draw_enemy_blades", "weight": 12},
            ],
        },
    },
    "mission_chest_v3_yijin_mijing": {
        "description": "打开必得易筋经×4，另有机会获得洗髓丹、千年朱果及一本中级技能书。",
        "group": {
            "label": "中级技能书",
            "chance": 0.05,
            "min_quantity": 1,
            "max_quantity": 1,
            "choices": [
                {"item_key": "book_flower_rain", "weight": 18},
                {"item_key": "book_soul_erode", "weight": 18},
                {"item_key": "book_red_lotus_dance", "weight": 16},
                {"item_key": "book_fatal_chain_sword", "weight": 15},
                {"item_key": "book_last_chance_revival", "weight": 12},
                {"item_key": "book_draw_enemy_blades", "weight": 12},
            ],
        },
    },
}


def update_book_rewards(key, original):
    payload = deepcopy(original)
    groups = payload.get("random_item_groups", [])
    payload["random_item_groups"] = [
        group for group in groups if group.get("label") not in {"初级技能书", "中级技能书"}
    ] + [deepcopy(CHEST_BOOK_REWARDS[key]["group"])]
    return payload


def sync_book_rewards(apps, schema_editor):
    database = schema_editor.connection.alias
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    for key, config in CHEST_BOOK_REWARDS.items():
        item = ItemTemplate.objects.using(database).select_for_update().get(key=key)
        item.description = config["description"]
        item.effect_payload = update_book_rewards(key, item.effect_payload)
        item.save(using=database, update_fields=["description", "effect_payload"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0204_cultivation_missions")]
    operations = [migrations.RunPython(sync_book_rewards, migrations.RunPython.noop, atomic=True)]
