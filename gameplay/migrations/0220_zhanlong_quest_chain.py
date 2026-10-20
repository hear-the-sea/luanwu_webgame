"""同步战龙在野四关、专用敌将、技能、凭证与任务宝箱。"""

from copy import deepcopy
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import migrations, transaction

# 固化本次配置；历史迁移不读取后续可能变化的 YAML。
MISSION_DEFAULTS = {
    "zhanlong_yanshan_shizi": {
        "name": "战龙在野第一关：北地燕山初识子龙",
        "display_order": 1004,
        "difficulty": "advanced",
        "description": "北地入秋，燕山古道霜寒马瘦。连年兵乱让沿途村庄十室九空，逃难百姓带着仅剩的粮食与家当，沿山道向南求生。溃兵出身的寨主铁背熊却盘踞险隘，劫粮掠财，将不肯交出财物的百姓扣在山口。商路由此断绝，附近村民也无人敢再上山。\n"
        "\n"
        "庄园主押运物资途经山下，忽有伤者拦路求援：一名白袍小将为救被困百姓，独骑闯入匪阵，已在山口苦战多时。此人自称常山赵云，枪法凌厉，却因顾念身后老幼，始终无法放手突围。铁背熊披甲持锤堵住正道，故意招锋引敌，坡上弓手正趁他吸引火力之际张弓搭箭。\n"
        "\n"
        "山道狭窄，伏兵将成。还请庄园主率门客抢攻坡口，击溃拦路匪众，助这位素未谋面的白袍小将解开眼前危局。\n",
        "battle_type": "task1",
        "enemy_guests": [{"key": "task_zhanlong_tiebeixiong", "label": "燕山寨主·铁背熊"}],
        "enemy_troops": {"dao_sheng": 900, "qiang_wang": 800, "arrow_god": 700},
        "enemy_technology": {"level": 10, "guest_level": 92, "guest_bonus": 0.58},
        "drop_table": {"silver": 90000, "mission_chest_v3_zhanlong_yanshan_shizi": 1},
        "available_weekdays": [],
        "base_travel_time": 2400,
        "daily_limit": 1,
        "mission_card_daily_limit": 0,
    },
    "zhanlong_changban_tuwei": {
        "name": "战龙在野第二关：龙战长坂七进七出",
        "display_order": 1005,
        "difficulty": "advanced",
        "description": "荆襄骤变，曹军铁骑昼夜南下。刘备携百姓撤往江陵，军民相杂，车仗连绵，行至当阳长坂时，被曹军前锋追及。仓促之间，队伍四散，粮车倾覆，家眷失落；漫山遍野尽是奔逃的人影，呼救声淹没在马蹄与战鼓之中。\n"
        "\n"
        "混乱之际，有人看见一员白袍将军逆着人潮杀回阵中，正是昔日燕山相识的赵子龙。刘备的家眷仍陷在重围深处，他不肯独自撤离，正在曹军阵间往返寻人。虎豹骑统领曹纯却已收拢精骑，以迅疾突击截杀外围接应之人，几条通往长坂桥的道路正被逐一封锁。\n"
        "\n"
        "旧日所赠的白马银枪令尚在手中，故人却已身陷生死之地。此行需消耗一枚「白马银枪令」，请庄园主率门客突入长坂乱军，击破曹纯与虎豹骑的拦截，替赵云打开接应之路。\n",
        "battle_type": "task1",
        "entry_cost": {"zhanlong_white_horse_token": 1},
        "enemy_guests": [{"key": "task_zhanlong_caochun", "label": "曹纯"}],
        "enemy_troops": {"dao_sheng": 1200, "qiang_wang": 1200, "arrow_god": 1300},
        "enemy_technology": {"level": 10, "guest_level": 96, "guest_bonus": 0.66},
        "drop_table": {"silver": 110000, "mission_chest_v3_zhanlong_changban_tuwei": 1},
        "available_weekdays": [],
        "base_travel_time": 2400,
        "daily_limit": 1,
        "mission_card_daily_limit": 0,
    },
    "zhanlong_changban_duanqiao": {
        "name": "战龙在野第三关：长坂桥张飞断水流",
        "display_order": 1006,
        "difficulty": "advanced",
        "description": "长坂桥前，张飞横矛立马，怒目巡视来路。桥后树林烟尘滚滚，旌旗时隐时现，战马奔踏之声连绵不绝。逃来的散卒惊呼林中伏有百万大军，连追至岸边的曹军也勒马驻足，不敢贸然向前。\n"
        "\n"
        "庄园主率门客赶到桥头，尚未来得及通报身份，便被张飞一声断喝拦住。如今败军之中真假难辨，曹军又有乔装混入之嫌，他奉命守桥，绝不肯放来历未明的武装队伍靠近。桥后尚有百姓等待撤离，要取得信任，还须当面试过本领与胆色。\n"
        "\n"
        "桥后尘土愈起愈高，林中究竟藏着多少兵马，无人看得分明。眼前那杆丈八蛇矛却已缓缓抬起。此行需消耗一枚「长坂护主令」，请庄园主整顿队伍，迎接张翼德的桥头考验。\n",
        "battle_type": "task1",
        "entry_cost": {"zhanlong_changban_guard_token": 1},
        "enemy_guests": [{"key": "task_zhanlong_zhangfei_bridge", "label": "张飞"}],
        "enemy_troops": {"qiang_wang": 120},
        "enemy_troops_display": {"qiang_wang": 1000000},
        "enemy_technology": {"level": 10, "guest_level": 100, "guest_bonus": 0.74},
        "drop_table": {"silver": 130000, "mission_chest_v3_zhanlong_changban_duanqiao": 1},
        "available_weekdays": [],
        "base_travel_time": 2400,
        "daily_limit": 1,
        "mission_card_daily_limit": 0,
    },
    "zhanlong_wuhu_shizhen": {
        "name": "战龙在野第四关：谁人德行服赵云",
        "display_order": 1007,
        "difficulty": "advanced",
        "description": "自长坂一别，岁月流转，蜀中五虎如今已齐聚军前。庄园主昔日在燕山护民、长坂接应的经历，经赵云举荐传入刘备耳中。一封演武邀书送到庄园，邀众门客入营会武，也请庄园主当面谈一谈乱世之中如何待人、如何用人。\n"
        "\n"
        "演武场上，关羽按刀镇住阵脚，张飞持矛蓄势，赵云银枪护身，马超策马游走，黄忠在后方搭箭观阵。五人各有所长，彼此照应：重刀可破甲，蛇矛能聚力，银枪善守反击，快马压迫全阵，强弓专寻伤处。赵云虽是旧识，今日仍将亲自入阵，检验庄园门客的武艺与主人的临阵取舍。\n"
        "\n"
        "仁义之名须见诸行事，举荐之情也须以本领相承。此行需消耗一封「当阳义勇书」，请庄园主率众赴约，在五虎合阵之前展现庄园的担当。\n",
        "battle_type": "task1",
        "entry_cost": {"zhanlong_dangyang_valor_letter": 1},
        "enemy_guests": [
            {"key": "task_zhanlong_guanyu", "label": "关羽"},
            {"key": "task_zhanlong_zhangfei", "label": "张飞"},
            {"key": "task_zhanlong_zhaoyun", "label": "赵云"},
            {"key": "task_zhanlong_machao", "label": "马超"},
            {"key": "task_zhanlong_huangzhong", "label": "黄忠"},
        ],
        "enemy_troops": {"dao_sheng": 900, "qiang_wang": 900, "jian_sheng": 900, "quan_sheng": 700, "arrow_god": 900},
        "enemy_technology": {"level": 10, "guest_level": 100, "guest_bonus": 0.78},
        "drop_table": {"silver": 180000, "mission_chest_v3_zhanlong_wuhu_shizhen": 1},
        "available_weekdays": [],
        "base_travel_time": 2700,
        "daily_limit": 1,
        "mission_card_daily_limit": 0,
    },
}

ITEM_DEFAULTS = {
    "zhanlong_white_horse_token": {
        "name": "白马银枪令",
        "description": "燕山相识的白袍小将所赠信物，凭此可赴长坂接应赵云，进入战龙在野第二关时消耗一枚。",
        "effect_type": "resource",
        "rarity": "blue",
        "tradeable": False,
        "price": 0,
        "storage_space": 10,
        "is_usable": False,
        "image": "items/zhanlong/sishui_battle_report.png",
    },
    "zhanlong_changban_guard_token": {
        "name": "长坂护主令",
        "description": "长坂接应队伍传递的护主军令，凭此可前往长坂桥，进入战龙在野第三关时消耗一枚。",
        "effect_type": "resource",
        "rarity": "purple",
        "tradeable": False,
        "price": 0,
        "storage_space": 10,
        "is_usable": False,
        "image": "items/zhanlong/hulao_war_banner.png",
    },
    "zhanlong_dangyang_valor_letter": {
        "name": "当阳义勇书",
        "description": "记录当阳义勇事迹的荐书，凭此可赴蜀营演武之约，进入战龙在野第四关时消耗一封。",
        "effect_type": "resource",
        "rarity": "purple",
        "tradeable": False,
        "price": 0,
        "storage_space": 10,
        "is_usable": False,
        "image": "items/zhanlong/sishui_battle_report.png",
    },
    "mission_chest_v3_zhanlong_yanshan_shizi": {
        "name": "燕山识龙宝箱",
        "description": "燕山护民之行的任务宝箱，内有继续前往长坂的信物与补给。",
        "effect_type": "loot_box",
        "rarity": "purple",
        "tradeable": False,
        "price": 0,
        "storage_space": 5,
        "is_usable": True,
        "image": "items/zhanlong/mission_chest_v1.png",
        "effect_payload": {
            "mission_reward_version": 1,
            "mission_key": "zhanlong_yanshan_shizi",
            "reward_theme": "燕山识英雄：必得白马银枪令与行军补给",
            "item_rewards": [
                {"item_key": "zhanlong_white_horse_token", "min_quantity": 1, "max_quantity": 1},
                {"item_key": "buxuedan", "min_quantity": 4, "max_quantity": 4},
                {"item_key": "tong", "min_quantity": 12, "max_quantity": 12},
                {"item_key": "xi", "min_quantity": 8, "max_quantity": 8},
                {"item_key": "tie", "min_quantity": 5, "max_quantity": 5},
            ],
            "random_item_groups": [
                {
                    "label": "experience_watermelon",
                    "chance": 0.85,
                    "min_quantity": 5,
                    "max_quantity": 5,
                    "choices": [{"item_key": "experience_watermelon", "weight": 1}],
                }
            ],
            "reward_balance_version": 3,
            "blueprint_material_support": {},
            "blueprint_forging_requirements": {},
            "experience_fruit_source": "battle_salvage",
        },
    },
    "mission_chest_v3_zhanlong_changban_tuwei": {
        "name": "长坂突围宝箱",
        "description": "长坂接应之行的任务宝箱，内有护主军令，并有机会获得照夜玉狮子。",
        "effect_type": "loot_box",
        "rarity": "purple",
        "tradeable": False,
        "price": 0,
        "storage_space": 5,
        "is_usable": True,
        "image": "items/zhanlong/mission_chest_v1.png",
        "effect_payload": {
            "mission_reward_version": 1,
            "mission_key": "zhanlong_changban_tuwei",
            "reward_theme": "长坂见忠勇：必得长坂护主令，有机会获得照夜玉狮子",
            "item_rewards": [
                {"item_key": "zhanlong_changban_guard_token", "min_quantity": 1, "max_quantity": 1},
                {"item_key": "buxuedan", "min_quantity": 4, "max_quantity": 4},
                {"item_key": "tong", "min_quantity": 12, "max_quantity": 12},
                {"item_key": "xi", "min_quantity": 8, "max_quantity": 8},
                {"item_key": "tie", "min_quantity": 5, "max_quantity": 5},
            ],
            "random_item_groups": [
                {
                    "label": "experience_watermelon",
                    "chance": 0.85,
                    "min_quantity": 5,
                    "max_quantity": 5,
                    "choices": [{"item_key": "experience_watermelon", "weight": 1}],
                },
                {
                    "label": "equip_zhaoyeyushizi",
                    "chance": 0.1,
                    "min_quantity": 1,
                    "max_quantity": 1,
                    "choices": [{"item_key": "equip_zhaoyeyushizi", "weight": 1}],
                },
            ],
            "reward_balance_version": 3,
            "blueprint_material_support": {},
            "blueprint_forging_requirements": {},
            "experience_fruit_source": "battle_salvage",
        },
    },
    "mission_chest_v3_zhanlong_changban_duanqiao": {
        "name": "当阳试胆宝箱",
        "description": "长坂桥头较量的任务宝箱，内有前往蜀营演武的当阳义勇书。",
        "effect_type": "loot_box",
        "rarity": "purple",
        "tradeable": False,
        "price": 0,
        "storage_space": 5,
        "is_usable": True,
        "image": "items/zhanlong/mission_chest_v1.png",
        "effect_payload": {
            "mission_reward_version": 1,
            "mission_key": "zhanlong_changban_duanqiao",
            "reward_theme": "桥头试胆：必得当阳义勇书与行军补给",
            "item_rewards": [
                {"item_key": "zhanlong_dangyang_valor_letter", "min_quantity": 1, "max_quantity": 1},
                {"item_key": "buxuedan", "min_quantity": 5, "max_quantity": 5},
                {"item_key": "tong", "min_quantity": 16, "max_quantity": 16},
                {"item_key": "xi", "min_quantity": 10, "max_quantity": 10},
                {"item_key": "tie", "min_quantity": 6, "max_quantity": 6},
            ],
            "random_item_groups": [
                {
                    "label": "experience_watermelon",
                    "chance": 0.9,
                    "min_quantity": 6,
                    "max_quantity": 6,
                    "choices": [{"item_key": "experience_watermelon", "weight": 1}],
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
        "description": "五虎会武的任务宝箱，内有演武补给，并有独立百分之一的机会获得赵云招募卷轴。",
        "effect_type": "loot_box",
        "rarity": "purple",
        "tradeable": False,
        "price": 0,
        "storage_space": 5,
        "is_usable": True,
        "image": "items/zhanlong/mission_chest_v1.png",
        "effect_payload": {
            "mission_reward_version": 1,
            "mission_key": "zhanlong_wuhu_shizhen",
            "reward_theme": "仁德得人心：独立1%机会获得赵云招募卷轴",
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
            ],
            "reward_balance_version": 3,
            "blueprint_material_support": {},
            "blueprint_forging_requirements": {},
            "experience_fruit_source": "battle_salvage",
        },
    },
}

SKILL_DEFAULTS = {
    "zhanlong_iron_hammer": {
        "name": "铁锤压顶",
        "rarity": "purple",
        "kind": "active",
        "description": "狼牙重锤砸向单个目标，并有35%概率使其下次行动伤害降低30%。",
        "targets": 1,
        "damage_formula": {"base": 700, "ally": {"force": 5.0, "defense": 1.0}, "enemy": {"defense": 0.35}},
        "status_effect": "weakened",
        "status_probability": 0.35,
        "status_duration": 1,
    },
    "zhanlong_cavalry_assault": {
        "name": "虎豹奔袭",
        "rarity": "purple",
        "kind": "active",
        "description": "率精骑疾冲，对两名目标造成武力与敏捷伤害。",
        "targets": 2,
        "damage_formula": {"base": 700, "ally": {"force": 3.8, "agility": 3.0}, "enemy": {"defense": 0.3}},
    },
    "zhanlong_cavalry_command": {
        "name": "精骑督战",
        "rarity": "purple",
        "kind": "passive",
        "description": "每轮令己方护院伤害提高12%；统领阵亡后，下一轮不再获得督战增益。",
        "passive_config": {
            "triggers": [
                {
                    "timing": "round_start",
                    "effects": [
                        {
                            "type": "modify_outgoing_damage",
                            "value": 1.12,
                            "target_scope": "allies",
                            "target_kind_is": "troop",
                        }
                    ],
                }
            ]
        },
    },
    "zhanlong_tiger_dragon_roar": {
        "name": "虎啸龙吟",
        "rarity": "orange",
        "kind": "active",
        "description": "25%固定概率怒喝出矛，将全部劲力集中在一次单体重击，造成极高伤害。",
        "targets": 1,
        "damage_formula": {"ally": {"force": 4.0}, "enemy": {"defense": 0.4}},
        "passive_config": {"active": {"trigger_chance": 0.25, "damage_multiplier": 2.3}},
    },
    "zhanlong_dust_ruse": {
        "name": "疑兵扬尘",
        "rarity": "purple",
        "kind": "passive",
        "description": "开场揭示桥后扬尘疑兵，百万声势不计入实际参战兵力。",
        "passive_config": {
            "triggers": [
                {
                    "timing": "round_start",
                    "conditions": {"state_absent": "zhanlong_dust_revealed"},
                    "effects": [
                        {
                            "type": "emit_log",
                            "log_name": "疑兵扬尘",
                            "message": "桥后烟尘渐散，所谓百万大军竟是少数骑兵往来扬尘！百万声势原为疑兵，桥前张飞的丈八蛇矛却是真威胁。",
                        },
                        {"type": "set_state", "key": "zhanlong_dust_revealed", "value": True},
                    ],
                }
            ]
        },
    },
    "zhanlong_azure_dragon_slash": {
        "name": "青龙偃月",
        "rarity": "orange",
        "kind": "active",
        "description": "偃月刀劈向单个目标，使其有效防御降低25%，持续两次行动机会。",
        "targets": 1,
        "damage_formula": {"base": 700, "ally": {"force": 6.0}, "enemy": {"defense": 0.2}},
        "status_effect": "armor_broken",
        "status_probability": 1.0,
        "status_duration": 2,
    },
    "zhanlong_dragon_guard": {
        "name": "龙胆护身",
        "rarity": "orange",
        "kind": "passive",
        "description": "全场受到伤害降低20%，存活受击时有30%概率以自身攻击力的80%反击。",
        "passive_config": {
            "triggers": [
                {
                    "timing": "battle_start",
                    "effects": [
                        {"type": "modify_incoming_damage", "value": 0.8},
                        {"type": "set_counter", "chance": 0.3, "damage_multiplier": 0.8},
                    ],
                }
            ]
        },
    },
    "zhanlong_xiliang_charge": {
        "name": "西凉驰突",
        "rarity": "orange",
        "kind": "active",
        "description": "纵马横贯敌阵，对三名目标造成武力与敏捷伤害。",
        "targets": 3,
        "damage_formula": {"base": 600, "ally": {"force": 2.5, "agility": 4.0}, "enemy": {"defense": 0.25}},
    },
    "zhanlong_hundred_pace_shot": {
        "name": "百步穿杨",
        "rarity": "orange",
        "kind": "active",
        "description": "优先瞄准生命比例最低的敌人；目标生命不高于35%时，本次伤害提高50%。",
        "targets": 1,
        "damage_formula": {"base": 600, "ally": {"force": 4.2, "agility": 1.8}, "enemy": {"defense": 0.25}},
        "passive_config": {
            "active": {"target_priority": "lowest_hp_ratio", "execute_hp_ratio": 0.35, "execute_multiplier": 1.5}
        },
    },
}

FALLBACK_SKILL_DEFAULTS = {
    "draw_enemy_blades": {
        "name": "招锋引敌",
        "rarity": "blue",
        "description": "主动露出破绽牵动敌阵，使对手更容易把攻势集中到自己身上。",
        "kind": "passive",
        "required_level": 32,
        "required_defense": 170,
        "passive_config": {
            "triggers": [
                {
                    "timing": "round_start",
                    "effects": [
                        {"type": "emit_log", "log_name": "招锋引敌", "message": "受击概率↑"},
                        {"type": "modify_target_weight", "value": 10.0, "target_scope": "self"},
                    ],
                }
            ]
        },
    }
}

ENEMY_DEFAULTS = {
    "task_zhanlong_tiebeixiong": {
        "name": "燕山寨主·铁背熊",
        "rarity": "purple",
        "archetype": "military",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "flavor": "溃兵出身的燕山寨主，披重甲、持铁锤堵住山口，以自身吸引火力，掩护坡上弓手围攻过往行人。",
        "growth_range": [],
        "attribute_weights": {},
        "avatar": "guests/zhanlong/task_zhanlong_tiebeixiong.png",
        "base_attack": 360,
        "base_intellect": 90,
        "base_defense": 470,
        "base_agility": 160,
        "base_luck": 90,
        "base_hp": 10000,
    },
    "task_zhanlong_caochun": {
        "name": "曹纯",
        "rarity": "purple",
        "archetype": "military",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "flavor": "虎豹骑统领曹纯在长坂外围收拢精骑，以迅疾突击截杀接应队伍，试图封死赵云通往长坂桥的归路。",
        "growth_range": [],
        "attribute_weights": {},
        "avatar": "guests/zhanlong/task_zhanlong_caochun.png",
        "base_attack": 430,
        "base_intellect": 140,
        "base_defense": 300,
        "base_agility": 360,
        "base_luck": 110,
        "base_hp": 9000,
    },
    "task_zhanlong_zhangfei_bridge": {
        "name": "张飞",
        "rarity": "orange",
        "archetype": "military",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "flavor": "张飞横矛守住长坂桥，令少数骑兵于林中扬尘造势。来人身份未明，他已提起丈八蛇矛，准备试其本领与胆色。",
        "growth_range": [],
        "attribute_weights": {},
        "avatar": "guests/zhanlong/task_zhanlong_zhangfei_bridge.png",
        "base_attack": 600,
        "base_intellect": 150,
        "base_defense": 440,
        "base_agility": 220,
        "base_luck": 120,
        "base_hp": 15000,
    },
    "task_zhanlong_guanyu": {
        "name": "关羽",
        "rarity": "orange",
        "archetype": "military",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "flavor": "演武场上，关羽持青龙偃月刀压住阵脚，刀势专攻甲胄薄弱之处，为同阵诸将打开防线。",
        "growth_range": [],
        "attribute_weights": {},
        "avatar": "guests/zhanlong/task_zhanlong_guanyu.png",
        "base_attack": 450,
        "base_intellect": 170,
        "base_defense": 380,
        "base_agility": 290,
        "base_luck": 130,
        "base_hp": 7200,
    },
    "task_zhanlong_zhangfei": {
        "name": "张飞",
        "rarity": "orange",
        "archetype": "military",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "flavor": "五虎演武，张飞仍以丈八蛇矛相试，平日蓄势不发，一声虎啸龙吟便将全身劲力凝于一击。",
        "growth_range": [],
        "attribute_weights": {},
        "avatar": "guests/zhanlong/task_zhanlong_zhangfei.png",
        "base_attack": 440,
        "base_intellect": 110,
        "base_defense": 355,
        "base_agility": 220,
        "base_luck": 120,
        "base_hp": 6800,
    },
    "task_zhanlong_zhaoyun": {
        "name": "赵云",
        "rarity": "orange",
        "archetype": "military",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "flavor": "昔日燕山与长坂并肩的故人，如今执枪列于演武阵中。赵云守势沉稳、遇攻能还，要亲自检验庄园门客的持久战力。",
        "growth_range": [],
        "attribute_weights": {},
        "avatar": "guests/zhanlong/task_zhanlong_zhaoyun.png",
        "base_attack": 410,
        "base_intellect": 220,
        "base_defense": 400,
        "base_agility": 350,
        "base_luck": 150,
        "base_hp": 8000,
    },
    "task_zhanlong_machao": {
        "name": "马超",
        "rarity": "orange",
        "archetype": "military",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "flavor": "马超在演武阵侧策马待发，枪快马疾，擅以连续突击压低多人血线，不让对手从容整顿。",
        "growth_range": [],
        "attribute_weights": {},
        "avatar": "guests/zhanlong/task_zhanlong_machao.png",
        "base_attack": 415,
        "base_intellect": 120,
        "base_defense": 310,
        "base_agility": 440,
        "base_luck": 125,
        "base_hp": 6600,
    },
    "task_zhanlong_huangzhong": {
        "name": "黄忠",
        "rarity": "orange",
        "archetype": "military",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "flavor": "黄忠立于演武阵后，弓弦半引，目光紧盯对手伤势，随时准备以百步穿杨补上决定胜负的一箭。",
        "growth_range": [],
        "attribute_weights": {},
        "avatar": "guests/zhanlong/task_zhanlong_huangzhong.png",
        "base_attack": 410,
        "base_intellect": 160,
        "base_defense": 300,
        "base_agility": 315,
        "base_luck": 140,
        "base_hp": 6200,
    },
}

ENEMY_SKILLS = {
    "task_zhanlong_tiebeixiong": ["draw_enemy_blades", "zhanlong_iron_hammer"],
    "task_zhanlong_caochun": ["zhanlong_cavalry_assault", "zhanlong_cavalry_command"],
    "task_zhanlong_zhangfei_bridge": ["zhanlong_tiger_dragon_roar", "zhanlong_dust_ruse"],
    "task_zhanlong_guanyu": ["zhanlong_azure_dragon_slash"],
    "task_zhanlong_zhangfei": ["zhanlong_tiger_dragon_roar"],
    "task_zhanlong_zhaoyun": ["zhanlong_dragon_guard"],
    "task_zhanlong_machao": ["zhanlong_xiliang_charge"],
    "task_zhanlong_huangzhong": ["zhanlong_hundred_pace_shot"],
}

IMAGE_SOURCES = {
    "guests/zhanlong/task_zhanlong_tiebeixiong.png": "guests/monkey/任务/华雄.png",
    "guests/zhanlong/task_zhanlong_caochun.png": "guests/pig/武046.png",
    "guests/zhanlong/task_zhanlong_zhangfei_bridge.png": "guests/zhangfei.png",
    "guests/zhanlong/task_zhanlong_guanyu.png": "guests/monkey/orange/关羽.png",
    "guests/zhanlong/task_zhanlong_zhangfei.png": "guests/zhangfei.png",
    "guests/zhanlong/task_zhanlong_zhaoyun.png": "guests/zhaoyun.png",
    "guests/zhanlong/task_zhanlong_machao.png": "guests/monkey/三国两晋与南北朝/马超.png",
    "guests/zhanlong/task_zhanlong_huangzhong.png": "guests/monkey/三国两晋与南北朝/黄忠.png",
    "items/zhanlong/sishui_battle_report.png": "items/sishui_battle_report.png",
    "items/zhanlong/hulao_war_banner.png": "items/hulao_war_banner.png",
    "items/zhanlong/mission_chest_v1.png": "items/mission_chest_v1.png",
}


def sync_zhanlong_missions(apps, schema_editor):
    database = schema_editor.connection.alias
    Skill = apps.get_model("guests", "Skill")
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    MissionTemplate = apps.get_model("gameplay", "MissionTemplate")

    stored_images = {}
    for target, relative_source in IMAGE_SOURCES.items():
        if default_storage.exists(target):
            stored_images[target] = target
        else:
            source = Path(settings.BASE_DIR) / "data" / "images" / relative_source
            stored_images[target] = default_storage.save(target, ContentFile(source.read_bytes()))

    # 招锋引敌已存在时保持原配置；只为尚未导入该技能的环境补齐引用。
    for key, defaults in FALLBACK_SKILL_DEFAULTS.items():
        Skill.objects.using(database).get_or_create(key=key, defaults=deepcopy(defaults))
    for key, defaults in SKILL_DEFAULTS.items():
        Skill.objects.using(database).update_or_create(key=key, defaults=deepcopy(defaults))
    skill_keys = {key for keys in ENEMY_SKILLS.values() for key in keys}
    skill_map = {skill.key: skill for skill in Skill.objects.using(database).filter(key__in=skill_keys)}
    for key, data in ENEMY_DEFAULTS.items():
        defaults = deepcopy(data)
        defaults["avatar"] = stored_images[defaults["avatar"]]
        enemy, _ = GuestTemplate.objects.using(database).update_or_create(key=key, defaults=defaults)
        enemy.initial_skills.set([skill_map[skill_key] for skill_key in ENEMY_SKILLS[key]])
    for key, data in ITEM_DEFAULTS.items():
        defaults = deepcopy(data)
        defaults["image"] = stored_images[defaults["image"]]
        ItemTemplate.objects.using(database).update_or_create(key=key, defaults=defaults)
    for key, defaults in MISSION_DEFAULTS.items():
        MissionTemplate.objects.using(database).update_or_create(key=key, defaults=deepcopy(defaults))
    transaction.on_commit(lambda: cache.delete("battle:guest_templates"), using=database, robust=True)


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0219_missiontemplate_enemy_troops_display"),
        ("guests", "0092_guest_rarity_pill_used"),
    ]

    operations = [migrations.RunPython(sync_zhanlong_missions, migrations.RunPython.noop, atomic=True)]
