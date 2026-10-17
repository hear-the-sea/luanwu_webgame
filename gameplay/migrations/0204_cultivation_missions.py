"""新增三档修行任务及奖励宝箱。"""

from copy import deepcopy

from django.db import migrations

MISSION_DEFAULTS = {
    "yaolu_hujing": {
        "name": "药庐护经",
        "difficulty": "junior",
        "description": "山中药师携经归庐，却被盗匪堵住山道。护送药师脱险，药庐愿以珍藏经卷相赠。",
        "battle_type": "task1",
        "is_defense": False,
        "guest_only": False,
        "enemy_guests": [
            {"key": "task_nine_peaks_bandit", "label": "拦路刀客", "skills": ["iron_wall_heart"]},
            {"key": "task_wulong_bandit_chief_zuanshanbao", "label": "劫经匪首", "skills": []},
        ],
        "enemy_troops": {"qiang_ke": 120},
        "enemy_technology": {"level": 3, "guest_level": 45, "guest_bonus": 0},
        "entry_cost": {},
        "drop_table": {"mission_chest_v3_yaolu_hujing": 1},
        "probability_drop_table": {},
        "available_weekdays": [],
        "display_order": 9,
        "base_travel_time": 1800,
        "daily_limit": 2,
        "mission_card_daily_limit": 0,
    },
    "xisui_yougu": {
        "name": "洗髓幽谷",
        "difficulty": "intermediate",
        "description": "幽谷药香不散，旧日剑客仍守着谷中药藏。闯过剑阵与药师的考验，便可带走谷中修行珍品。",
        "battle_type": "task1",
        "is_defense": False,
        "guest_only": False,
        "enemy_guests": [
            {"key": "task_cellar_guard", "label": "守谷剑客", "skills": ["iron_wall_heart"]},
            {
                "key": "task_shiren_xuanyuansanguang",
                "label": "幽谷药师",
                "skills": ["flower_rain", "steadfast_planning"],
            },
        ],
        "enemy_troops": {"jian_hao": 180, "archer": 100},
        "enemy_technology": {"level": 5, "guest_level": 70, "guest_bonus": 0.08},
        "entry_cost": {},
        "drop_table": {"mission_chest_v3_xisui_yougu": 1},
        "probability_drop_table": {},
        "available_weekdays": [],
        "display_order": 9,
        "base_travel_time": 1800,
        "daily_limit": 2,
        "mission_card_daily_limit": 0,
    },
    "yijin_mijing": {
        "name": "易筋秘境",
        "difficulty": "advanced",
        "description": "古寺深处别有洞天，三位护法各守一重修行关。胜过护法，可获秘藏经卷；若有机缘，亦能寻得灵丹、朱果与遗留武学。",
        "battle_type": "task1",
        "is_defense": False,
        "guest_only": False,
        "enemy_guests": [
            {"key": "task_cellar_guard", "label": "金刚护法", "skills": ["iron_wall_heart"]},
            {"key": "task_fox_spirit_keeper", "label": "明心护法", "skills": ["flower_rain", "steadfast_planning"]},
            {"key": "task_cellar_guard", "label": "伏魔护法", "skills": ["meteor_pierce_moon"]},
        ],
        "enemy_troops": {"jian_sheng": 250, "quan_sheng": 200, "arrow_god": 100},
        "enemy_technology": {"level": 7, "guest_level": 95, "guest_bonus": 0.15},
        "entry_cost": {},
        "drop_table": {"mission_chest_v3_yijin_mijing": 1},
        "probability_drop_table": {},
        "available_weekdays": [],
        "display_order": 9,
        "base_travel_time": 1800,
        "daily_limit": 1,
        "mission_card_daily_limit": 0,
    },
}

CHEST_DEFAULTS = {
    "mission_chest_v3_yaolu_hujing": {
        "name": "药庐护经宝箱",
        "description": "打开必得易筋经×1，另有机会获得洗髓丹、千年朱果。",
        "effect_type": "loot_box",
        "rarity": "green",
        "tradeable": False,
        "default_bound": False,
        "price": 0,
        "storage_space": 5,
        "is_usable": True,
        "image": "items/mission_chest_v1.png",
        "effect_payload": {
            "mission_reward_version": 1,
            "reward_balance_version": 3,
            "mission_key": "yaolu_hujing",
            "reward_theme": "易筋修行",
            "item_rewards": [{"item_key": "yijinjing", "min_quantity": 1, "max_quantity": 1}],
            "random_item_groups": [
                {
                    "label": "xisuidan",
                    "chance": 0.01,
                    "min_quantity": 1,
                    "max_quantity": 1,
                    "choices": [{"item_key": "xisuidan", "weight": 1}],
                },
                {
                    "label": "millennium_zhuguo",
                    "chance": 0.005,
                    "min_quantity": 1,
                    "max_quantity": 1,
                    "choices": [{"item_key": "millennium_zhuguo", "weight": 1}],
                },
            ],
            "blueprint_material_support": {},
            "blueprint_forging_requirements": {},
            "experience_fruit_source": "battle_salvage",
        },
    },
    "mission_chest_v3_xisui_yougu": {
        "name": "洗髓幽谷宝箱",
        "description": "打开必得易筋经×2，另有机会获得洗髓丹、千年朱果。",
        "effect_type": "loot_box",
        "rarity": "blue",
        "tradeable": False,
        "default_bound": False,
        "price": 0,
        "storage_space": 5,
        "is_usable": True,
        "image": "items/mission_chest_v1.png",
        "effect_payload": {
            "mission_reward_version": 1,
            "reward_balance_version": 3,
            "mission_key": "xisui_yougu",
            "reward_theme": "易筋修行",
            "item_rewards": [{"item_key": "yijinjing", "min_quantity": 2, "max_quantity": 2}],
            "random_item_groups": [
                {
                    "label": "xisuidan",
                    "chance": 0.03,
                    "min_quantity": 1,
                    "max_quantity": 1,
                    "choices": [{"item_key": "xisuidan", "weight": 1}],
                },
                {
                    "label": "millennium_zhuguo",
                    "chance": 0.01,
                    "min_quantity": 1,
                    "max_quantity": 1,
                    "choices": [{"item_key": "millennium_zhuguo", "weight": 1}],
                },
            ],
            "blueprint_material_support": {},
            "blueprint_forging_requirements": {},
            "experience_fruit_source": "battle_salvage",
        },
    },
    "mission_chest_v3_yijin_mijing": {
        "name": "易筋秘境宝箱",
        "description": "打开必得易筋经×4，另有机会获得洗髓丹、千年朱果。亦有机会获得一本初级技能书。",
        "effect_type": "loot_box",
        "rarity": "purple",
        "tradeable": False,
        "default_bound": False,
        "price": 0,
        "storage_space": 5,
        "is_usable": True,
        "image": "items/mission_chest_v1.png",
        "effect_payload": {
            "mission_reward_version": 1,
            "reward_balance_version": 3,
            "mission_key": "yijin_mijing",
            "reward_theme": "易筋修行",
            "item_rewards": [{"item_key": "yijinjing", "min_quantity": 4, "max_quantity": 4}],
            "random_item_groups": [
                {
                    "label": "xisuidan",
                    "chance": 0.05,
                    "min_quantity": 1,
                    "max_quantity": 1,
                    "choices": [{"item_key": "xisuidan", "weight": 1}],
                },
                {
                    "label": "millennium_zhuguo",
                    "chance": 0.02,
                    "min_quantity": 1,
                    "max_quantity": 1,
                    "choices": [{"item_key": "millennium_zhuguo", "weight": 1}],
                },
                {
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
            ],
            "blueprint_material_support": {},
            "blueprint_forging_requirements": {},
            "experience_fruit_source": "battle_salvage",
        },
    },
}


def create_cultivation_missions(apps, schema_editor):
    database = schema_editor.connection.alias
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    MissionTemplate = apps.get_model("gameplay", "MissionTemplate")
    for key, defaults in CHEST_DEFAULTS.items():
        ItemTemplate.objects.using(database).update_or_create(key=key, defaults=deepcopy(defaults))
    for key, defaults in MISSION_DEFAULTS.items():
        MissionTemplate.objects.using(database).update_or_create(key=key, defaults=deepcopy(defaults))


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0203_custom_guest_orange_civil_base")]
    operations = [migrations.RunPython(create_cultivation_missions, migrations.RunPython.noop, atomic=True)]
