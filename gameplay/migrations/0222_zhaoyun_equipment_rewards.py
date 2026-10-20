"""战龙四关名器、桥头张飞头像与轩辕剑意奖励调整。"""

from copy import deepcopy
from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import migrations

from core.utils.image_utils import compress_and_resize_image

ITEM_DEFAULTS = {
    "equip_shuduanbaijinpao": {
        "name": "蜀缎白锦袍",
        "description": "以蜀中白锦裁成的轻战袍，银线暗绣云龙，护身而不碍腾挪，尽显白袍银枪的英姿。",
        "effect_type": "equip_armor",
        "rarity": "orange",
        "tradeable": True,
        "price": 60000,
        "storage_space": 200,
        "image": "items/zhaoyun/equip_shuduanbaijinpao.webp",
        "effect_payload": {"hp": 550, "defense": 26, "force": 10, "agility": 30, "required_level": 75},
    },
    "equip_longdanqiang": {
        "name": "龙胆枪",
        "description": "亮银枪锋如龙牙，青杆白缨映寒光，出枪凌厉而回转轻捷，是子龙破阵护身的名器。",
        "effect_type": "equip_weapon",
        "rarity": "orange",
        "tradeable": True,
        "price": 65000,
        "storage_space": 200,
        "image": "items/zhaoyun/equip_longdanqiang.webp",
        "effect_payload": {"hp": 220, "defense": 6, "force": 74, "agility": 18, "required_level": 75},
    },
    "mission_chest_v3_zhuolu_zhongyuan": {
        "name": "蚩尤宝箱",
        "description": "逐鹿之战的兵主宝箱，必得一张蚩尤卡与一缕轩辕剑意，再从七星铠甲、蚩尤甲图纸、蚩尤战靴与春秋币中额外抽取一项奖励。",
        "effect_type": "loot_box",
        "rarity": "purple",
        "tradeable": False,
        "price": 0,
        "storage_space": 5,
        "is_usable": True,
        "image": "items/zhaoyun/mission_chest_v1.webp",
        "effect_payload": {
            "mission_reward_version": 1,
            "mission_key": "zhuolu_zhongyuan",
            "reward_theme": "兵主逐鹿：必得蚩尤卡，再额外抽取一项奖励",
            "show_reward_probabilities": True,
            "item_rewards": [
                {"item_key": "chiyou_card", "min_quantity": 1, "max_quantity": 1},
                {"item_key": "xuanyuan_sword_intent", "min_quantity": 1, "max_quantity": 1},
            ],
            "random_item_groups": [
                {
                    "label": "蚩尤战利品",
                    "chance": 1,
                    "min_quantity": 1,
                    "max_quantity": 1,
                    "choices": [
                        {"item_key": "equip_tianshukaijia", "weight": 10, "quantity": 1},
                        {"item_key": "equip_tianxuankaijia", "weight": 10, "quantity": 1},
                        {"item_key": "equip_tianjikaijia", "weight": 10, "quantity": 1},
                        {"item_key": "equip_yaoguangkaijia", "weight": 10, "quantity": 1},
                        {"item_key": "equip_tianquankaijia", "weight": 10, "quantity": 1},
                        {"item_key": "equip_yuhengkaijia", "weight": 10, "quantity": 1},
                        {"item_key": "equip_kaiyangkaijia", "weight": 10, "quantity": 1},
                        {"item_key": "blueprint_chiyoujia", "weight": 5, "quantity": 1},
                        {"item_key": "equip_chiyouzhanxue", "weight": 5, "quantity": 1},
                        {"item_key": "chunqiu_coin", "weight": 20, "quantity": 2},
                    ],
                }
            ],
            "reward_balance_version": 3,
            "blueprint_material_support": {},
            "blueprint_forging_requirements": {},
            "experience_fruit_source": "battle_salvage",
        },
    },
    "mission_chest_v3_zhanlong_wuhu_shizhen": {
        "name": "五虎演武宝箱",
        "description": "五虎会武的任务宝箱，内有演武补给；独立1%机会获得赵云招募卷轴，另有10%机会获得蜀缎白锦袍或龙胆枪，两件等权抽取。",
        "effect_type": "loot_box",
        "rarity": "purple",
        "tradeable": False,
        "price": 0,
        "storage_space": 5,
        "is_usable": True,
        "image": "items/zhaoyun/mission_chest_v1.webp",
        "effect_payload": {
            "mission_reward_version": 1,
            "mission_key": "zhanlong_wuhu_shizhen",
            "reward_theme": "仁德得人心：赵云招募卷轴、白锦袍与龙胆枪",
            "item_rewards": [
                {"item_key": "buxuedan", "min_quantity": 6, "max_quantity": 6},
                {"item_key": "tong", "min_quantity": 20, "max_quantity": 20},
                {"item_key": "xi", "min_quantity": 12, "max_quantity": 12},
                {"item_key": "tie", "min_quantity": 8, "max_quantity": 8},
            ],
            "random_item_groups": [
                {
                    "label": "experience_watermelon",
                    "chance": 0.9,
                    "min_quantity": 8,
                    "max_quantity": 8,
                    "choices": [{"item_key": "experience_watermelon", "weight": 1}],
                },
                {
                    "label": "zhaoyun_guest_scroll",
                    "chance": 0.01,
                    "min_quantity": 1,
                    "max_quantity": 1,
                    "choices": [{"item_key": "zhaoyun_guest_scroll", "weight": 1}],
                },
                {
                    "label": "blueprint_top_orange",
                    "chance": 0.06,
                    "min_quantity": 1,
                    "max_quantity": 1,
                    "choices": [
                        {"item_key": "blueprint_feiyuan", "weight": 1},
                        {"item_key": "blueprint_chixiaojifeng", "weight": 1},
                        {"item_key": "blueprint_mojiajiguanren", "weight": 1},
                    ],
                },
                {
                    "label": "上古王权",
                    "chance": 0.15,
                    "min_quantity": 1,
                    "max_quantity": 1,
                    "choices": [
                        {"item_key": "equip_shanheshejitu", "weight": 1},
                        {"item_key": "equip_zhoutianxingpan", "weight": 1},
                        {"item_key": "equip_xuanyuanjian", "weight": 1},
                        {"item_key": "equip_longhuangguan", "weight": 1},
                        {"item_key": "equip_chuanguoyuxi", "weight": 1},
                        {"item_key": "equip_qilin", "weight": 1},
                        {"item_key": "equip_qingluan", "weight": 1},
                    ],
                },
                {
                    "label": "赵云名器",
                    "chance": 0.1,
                    "min_quantity": 1,
                    "max_quantity": 1,
                    "choices": [
                        {"item_key": "equip_shuduanbaijinpao", "weight": 1},
                        {"item_key": "equip_longdanqiang", "weight": 1},
                    ],
                },
            ],
            "reward_balance_version": 3,
            "blueprint_material_support": {
                "blueprint_chixiaojifeng": [
                    {"item_key": "tong", "min_quantity": 53, "max_quantity": 53},
                    {"item_key": "xi", "min_quantity": 35, "max_quantity": 35},
                    {"item_key": "tie", "min_quantity": 28, "max_quantity": 28},
                    {"item_key": "xuan_tie_essence", "min_quantity": 2, "max_quantity": 2},
                    {"item_key": "zitanmu", "min_quantity": 7, "max_quantity": 7},
                    {"item_key": "shuiqumu", "min_quantity": 5, "max_quantity": 5},
                    {"item_key": "tiemu", "min_quantity": 10, "max_quantity": 10},
                    {"item_key": "heiyuanshi", "min_quantity": 11, "max_quantity": 11},
                    {"item_key": "jingangmei", "min_quantity": 11, "max_quantity": 11},
                    {"item_key": "paozi", "min_quantity": 4, "max_quantity": 4},
                    {"item_key": "zaozi", "min_quantity": 7, "max_quantity": 7},
                    {"item_key": "gaolu", "min_quantity": 4, "max_quantity": 4},
                    {"item_key": "fire_stone", "min_quantity": 2, "max_quantity": 2},
                    {"item_key": "air_stone", "min_quantity": 1, "max_quantity": 1},
                ],
                "blueprint_feiyuan": [
                    {"item_key": "tong", "min_quantity": 50, "max_quantity": 50},
                    {"item_key": "xi", "min_quantity": 34, "max_quantity": 34},
                    {"item_key": "tie", "min_quantity": 26, "max_quantity": 26},
                    {"item_key": "xuan_tie_essence", "min_quantity": 2, "max_quantity": 2},
                    {"item_key": "zitanmu", "min_quantity": 8, "max_quantity": 8},
                    {"item_key": "shuiqumu", "min_quantity": 11, "max_quantity": 11},
                    {"item_key": "tiemu", "min_quantity": 7, "max_quantity": 7},
                    {"item_key": "heiyuanshi", "min_quantity": 6, "max_quantity": 6},
                    {"item_key": "jingangmei", "min_quantity": 6, "max_quantity": 6},
                    {"item_key": "paozi", "min_quantity": 7, "max_quantity": 7},
                    {"item_key": "zaozi", "min_quantity": 4, "max_quantity": 4},
                    {"item_key": "gaolu", "min_quantity": 3, "max_quantity": 3},
                    {"item_key": "air_stone", "min_quantity": 2, "max_quantity": 2},
                    {"item_key": "fire_stone", "min_quantity": 1, "max_quantity": 1},
                ],
                "blueprint_mojiajiguanren": [
                    {"item_key": "tong", "min_quantity": 58, "max_quantity": 58},
                    {"item_key": "xi", "min_quantity": 38, "max_quantity": 38},
                    {"item_key": "tie", "min_quantity": 31, "max_quantity": 31},
                    {"item_key": "xuan_tie_essence", "min_quantity": 2, "max_quantity": 2},
                    {"item_key": "zitanmu", "min_quantity": 7, "max_quantity": 7},
                    {"item_key": "shuiqumu", "min_quantity": 5, "max_quantity": 5},
                    {"item_key": "tiemu", "min_quantity": 13, "max_quantity": 13},
                    {"item_key": "heiyuanshi", "min_quantity": 14, "max_quantity": 14},
                    {"item_key": "jingangmei", "min_quantity": 13, "max_quantity": 13},
                    {"item_key": "paozi", "min_quantity": 6, "max_quantity": 6},
                    {"item_key": "zaozi", "min_quantity": 10, "max_quantity": 10},
                    {"item_key": "gaolu", "min_quantity": 5, "max_quantity": 5},
                    {"item_key": "earth_stone", "min_quantity": 1, "max_quantity": 1},
                    {"item_key": "fire_stone", "min_quantity": 1, "max_quantity": 1},
                    {"item_key": "water_stone", "min_quantity": 1, "max_quantity": 1},
                ],
            },
            "blueprint_forging_requirements": {
                "blueprint_chixiaojifeng": 10,
                "blueprint_feiyuan": 10,
                "blueprint_mojiajiguanren": 10,
            },
            "experience_fruit_source": "battle_salvage",
        },
    },
}

BRIDGE_AVATAR = "guests/zhanlong/task_zhanlong_zhangfei_bridge_wu047.webp"

IMAGE_SOURCES = {
    "items/zhaoyun/equip_shuduanbaijinpao.webp": "items/equip_shuduanbaijinpao.png",
    "items/zhaoyun/equip_longdanqiang.webp": "items/equip_longdanqiang.png",
    "items/zhaoyun/mission_chest_v1.webp": "items/mission_chest_v1.png",
    "guests/zhanlong/task_zhanlong_zhangfei_bridge_wu047.webp": "guests/monkey/任务/武047_男_张飞.png",
}


def sync_zhaoyun_equipment_rewards(apps, schema_editor):
    database = schema_editor.connection.alias
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    stored_images = {}
    for target, relative_source in IMAGE_SOURCES.items():
        if default_storage.exists(target):
            stored_images[target] = target
        else:
            source = Path(settings.BASE_DIR) / "data" / "images" / relative_source
            size = (200, 200) if target.startswith("items/") else (400, 400)
            content, _ = compress_and_resize_image(source, max_size=size, quality=85, convert_to_webp=True)
            stored_images[target] = default_storage.save(target, content)
    for key, data in ITEM_DEFAULTS.items():
        defaults = deepcopy(data)
        defaults["image"] = stored_images[defaults["image"]]
        ItemTemplate.objects.using(database).update_or_create(key=key, defaults=defaults)
    GuestTemplate.objects.using(database).filter(key="task_zhanlong_zhangfei_bridge").update(
        avatar=stored_images[BRIDGE_AVATAR]
    )


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0221_chiyou_rewards_and_unique_guests")]
    operations = [migrations.RunPython(sync_zhaoyun_equipment_rewards, migrations.RunPython.noop, atomic=True)]
