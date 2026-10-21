"""新增姓氏传说之李、恶来专属技能、李氏宝箱及集卡兑换门客。"""

from copy import deepcopy
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import migrations, transaction

MISSION_DEFAULTS = {
    "surname_legend_li": {
        "name": "姓氏傳說之“李”",
        "display_order": 1008,
        "difficulty": "advanced",
        "description": "话说商纣王当政时，理氏一族因直言进谏获罪，利贞随母亲四处逃难。母子断粮多日，幸得荒野中一株李树结满甘果，这才保住性命。为报救命之恩，利贞决定改“理”为“李”，谁知新姓氏刚取好，老仇家却追上了门。\n"
        "\n"
        "助纣为虐的恶来奉命赶尽杀绝，率领精兵封住去路，连那株救命的李树也不肯放过。附近百姓有心相助，却敌不过他的凶猛重戟，只得向各庄园求援。还请庄园主率领门客击退恶来，护住母子二人，让这段“木子成李”的传说得以流传。\n",
        "battle_type": "task1",
        "is_defense": False,
        "guest_only": False,
        "enemy_guests": [{"key": "task_surname_li_elai", "label": "恶来"}],
        "enemy_troops": {"dao_sheng": 600, "qiang_wang": 400},
        "enemy_technology": {"level": 9, "guest_level": 80, "guest_bonus": 0.25},
        "entry_cost": {},
        "drop_table": {"mission_chest_v3_surname_legend_li": 1},
        "probability_drop_table": {},
        "available_weekdays": [],
        "base_travel_time": 1800,
        "daily_limit": 3,
        "mission_card_daily_limit": 5,
    }
}

ITEM_DEFAULTS = {
    "li_plum": {
        "name": "李子",
        "description": "李树结下的紫色甘果。每次使用一颗，为本庄园全体门客各回复5000点生命值，不超过各自生命上限；无人需要治疗时不消耗。",
        "effect_type": "tool",
        "rarity": "purple",
        "tradeable": False,
        "price": 0,
        "storage_space": 25,
        "is_usable": True,
        "image": "items/surname_li/plum.webp",
        "effect_payload": {"action": "heal_all_guests", "hp": 5000},
    },
    "li_clan_card": {
        "name": "李氏卡",
        "description": "记录李氏传承的信物。集齐50张后使用，可兑换紫色门客「李利贞」；每个庄园限拥有一位，已拥有时不消耗卡片。",
        "effect_type": "tool",
        "rarity": "purple",
        "tradeable": False,
        "price": 0,
        "storage_space": 25,
        "is_usable": True,
        "image": "items/surname_li/li_clan_card.webp",
        "effect_payload": {
            "action": "summon_guest",
            "required_items": {"li_clan_card": 49},
            "exclusive_template_keys": ["hero_li_lizhen"],
            "choices": [{"template_key": "hero_li_lizhen", "weight": 1}],
        },
    },
    "villain_card": {
        "name": "恶人卡",
        "description": "凝聚恶煞气息的信物。集齐300张后使用，可兑换紫色门客「黑山老妖」；每个庄园限拥有一位，已拥有时不消耗卡片。",
        "effect_type": "tool",
        "rarity": "purple",
        "tradeable": False,
        "price": 0,
        "storage_space": 25,
        "is_usable": True,
        "image": "items/surname_li/villain_card.webp",
        "effect_payload": {
            "action": "summon_guest",
            "required_items": {"villain_card": 299},
            "exclusive_template_keys": ["hero_heishan_laoyao"],
            "choices": [{"template_key": "hero_heishan_laoyao", "weight": 1}],
        },
    },
    "mission_chest_v3_surname_legend_li": {
        "name": "李氏宝箱",
        "description": "守护李氏传承所得的宝箱。开启后获得李子×2，并有机会获得李氏卡、恶人卡。",
        "effect_type": "loot_box",
        "rarity": "purple",
        "tradeable": False,
        "price": 0,
        "storage_space": 5,
        "is_usable": True,
        "image": "items/mission_chest_v1.webp",
        "effect_payload": {
            "mission_reward_version": 1,
            "mission_key": "surname_legend_li",
            "reward_theme": "木子传承：李子、李氏卡与恶人卡",
            "reward_balance_version": 3,
            "show_reward_probabilities": True,
            "item_rewards": [{"item_key": "li_plum", "min_quantity": 2, "max_quantity": 2}],
            "random_item_groups": [
                {
                    "label": "li_clan_card",
                    "chance": 0.1,
                    "min_quantity": 1,
                    "max_quantity": 1,
                    "choices": [{"item_key": "li_clan_card", "weight": 1}],
                },
                {
                    "label": "villain_card",
                    "chance": 0.5,
                    "min_quantity": 1,
                    "max_quantity": 1,
                    "choices": [{"item_key": "villain_card", "weight": 1}],
                },
            ],
            "blueprint_material_support": {},
            "blueprint_forging_requirements": {},
            "experience_fruit_source": "battle_salvage",
        },
    },
}

SKILL_DEFAULTS = {
    "surname_li_elai_crushing_blow": {
        "name": "崩山裂甲",
        "rarity": "orange",
        "kind": "active",
        "description": "恶来以35%固定概率挥动重戟，攻击两名敌人并施加破甲，使其有效防御降低25%，持续两次行动机会。恶煞死战触发后改为攻击三名敌人。",
        "targets": 2,
        "damage_formula": {"base": 700, "ally": {"force": 4.0, "defense": 1.0}, "enemy": {"defense": 0.3}},
        "status_effect": "armor_broken",
        "status_probability": 1.0,
        "status_duration": 2,
        "passive_config": {"active": {"trigger_chance": 0.35}},
    },
    "surname_li_elai_last_stand": {
        "name": "恶煞死战",
        "rarity": "orange",
        "kind": "passive",
        "description": "生命低于30%时进入死战状态，崩山裂甲改为攻击三名敌人，每场战斗仅触发一次，持续至战斗结束。",
        "passive_config": {
            "triggers": [
                {
                    "timing": "hit_taken",
                    "conditions": {"hp_ratio_lt": 0.3},
                    "effects": [
                        {
                            "type": "enrage",
                            "luck_bonus": 0,
                            "skill_key": "surname_li_elai_crushing_blow",
                            "targets": 3,
                            "log_name": "恶煞死战",
                            "message": "恶来怒吼死战，崩山裂甲转为同时攻击三人！",
                        }
                    ],
                },
                {
                    "timing": "action_before",
                    "conditions": {"hp_ratio_lt": 0.3},
                    "effects": [
                        {
                            "type": "enrage",
                            "luck_bonus": 0,
                            "skill_key": "surname_li_elai_crushing_blow",
                            "targets": 3,
                            "log_name": "恶煞死战",
                            "message": "恶来怒吼死战，崩山裂甲转为同时攻击三人！",
                        }
                    ],
                },
            ]
        },
    },
}

FALLBACK_SKILL_DEFAULTS = {
    "stratagem_burst": {
        "name": "奇策突袭",
        "rarity": "purple",
        "description": "随机对敌人造成高额伤害并可能眩晕。",
        "base_power": 220,
        "base_probability": 0.8,
        "kind": "active",
        "status_effect": "stunned",
        "status_probability": 0.3,
        "status_duration": 1,
        "damage_formula": {
            "base": 800,
            "ally": {"force": 0.5, "intellect": 5, "agility": 0.2},
            "enemy": {"defense": 0.35, "intellect": 0.5},
        },
        "required_level": 50,
        "required_intellect": 255,
        "required_agility": 125,
        "targets": 1,
    },
    "hell_instant_formation": {
        "name": "瞬狱杀阵",
        "rarity": "purple",
        "description": "身法化影布下杀阵，刹那间连袭多名敌人，招招夺命。",
        "base_probability": 0.72,
        "kind": "active",
        "damage_formula": {
            "base": 1000,
            "ally": {"force": 0.6, "agility": 3.2, "luck": 0.6},
            "enemy": {"defense": 0.5, "agility": 0.2},
        },
        "required_level": 65,
        "required_force": 170,
        "required_agility": 260,
        "targets": 3,
    },
    "steadfast_planning": {
        "name": "静水筹谋",
        "rarity": "green",
        "description": "以静制动，沉着观察局势后再稳步放大己方攻势。",
        "kind": "passive",
        "required_level": 18,
        "required_intellect": 110,
        "passive_config": {
            "triggers": [
                {
                    "timing": "round_start",
                    "chance": 0.5,
                    "conditions": {"hp_ratio_gte": 0.5},
                    "effects": [
                        {"type": "emit_log", "log_name": "静水筹谋", "message": "伤害增加"},
                        {"type": "modify_outgoing_damage", "value": 1.2, "target_scope": "self"},
                    ],
                }
            ]
        },
    },
    "desperate_beast": {
        "name": "困兽犹斗",
        "rarity": "purple",
        "description": "陷入绝境时强行压榨战意，攻势更狠，但防线也会露出破绽。",
        "kind": "passive",
        "required_level": 45,
        "required_force": 255,
        "required_defense": 145,
        "passive_config": {
            "triggers": [
                {
                    "timing": "round_start",
                    "conditions": {"hp_ratio_lte": 0.35},
                    "effects": [
                        {"type": "emit_log", "log_name": "困兽犹斗", "message": "进入狂暴"},
                        {"type": "modify_outgoing_damage", "value": 1.16, "target_scope": "self"},
                        {"type": "modify_incoming_damage", "value": 1.05, "target_scope": "self"},
                    ],
                }
            ]
        },
    },
}

GUEST_DEFAULTS = {
    "hero_li_lizhen": {
        "name": "李利贞",
        "archetype": "civil",
        "rarity": "purple",
        "base_attack": 32,
        "base_intellect": 56,
        "base_defense": 28,
        "base_agility": 42,
        "base_luck": 141,
        "base_hp": 800,
        "flavor": "抱木简而守族谱，拾甘李以济饥寒。李利贞肩负延续宗族的重任，在追索与流离中护着族人辗转求生。纵使衣食难继、前路未明，他仍将记载先人名讳的木简珍藏怀中，不肯让一家一族的来处湮没于乱世。逃亡途中的甘李，既是果腹救命之物，也寄托着后人铭记患难、不忘根本的心愿。他性情温厚而有坚守，遇事善察人心、审度进退，常在众人慌乱之际理清头绪，为老弱安排退路，为同行者稳住心神。历经风霜后，他比旁人更珍惜安居的炊烟与灯火。入庄之后，愿以清明谋略辅佐庄主，护住族谱，也护住眼前百姓的生计，使木子传承在一代代人的守望中生生不息。",
        "default_gender": "male",
        "default_morality": 90,
        "recruitable": False,
        "is_hermit": False,
        "is_world_unique": False,
        "growth_range": [6, 11],
        "attribute_weights": {"force": 15, "intellect": 45, "defense": 20, "agility": 20},
        "avatar": "guests/surname_li/li_lizhen.webp",
    },
    "hero_heishan_laoyao": {
        "name": "黑山老妖",
        "archetype": "military",
        "rarity": "purple",
        "base_attack": 62,
        "base_intellect": 17,
        "base_defense": 42,
        "base_agility": 48,
        "base_luck": 98,
        "base_hp": 800,
        "flavor": "黑山幽谷积怨成形的妖王，长居阴雾不散、群鬼出没的荒山深处。黑袍之下煞气翻涌，所过之处寒意侵骨，连山间虫鸣也会骤然沉寂。他性情阴鸷，惯以威压慑服群邪，将幽谷视作自己的疆土，不容旁人轻易踏足。临阵时身法如影，往往借暗雾掩去行踪，待敌人惊觉寒意逼近，已陷入无声展开的杀阵；即使遭逢重创，也会困兽般暴起反扑，凶性愈发难制。三百恶人卡凝成缚妖契后，这位桀骜妖王才肯暂收锋芒，为持契之人驱使。契约能够约束他的行动，却难以磨平那份睥睨众生的傲慢。庄园灯火之外，他常独立夜色之中，黑袍无风而动，仿佛仍在倾听远处幽谷传来的低语。",
        "default_gender": "male",
        "default_morality": 10,
        "recruitable": False,
        "is_hermit": False,
        "is_world_unique": False,
        "growth_range": [6, 11],
        "attribute_weights": {"force": 40, "intellect": 10, "defense": 20, "agility": 30},
        "avatar": "guests/surname_li/heishan_laoyao.webp",
    },
    "task_surname_li_elai": {
        "name": "恶来",
        "archetype": "military",
        "rarity": "orange",
        "base_attack": 360,
        "base_intellect": 80,
        "base_defense": 310,
        "base_agility": 180,
        "base_luck": 100,
        "base_hp": 500000,
        "flavor": "恶来挟重戟镇守李氏旧地，力能裂甲崩山。身陷绝境时愈战愈凶，唯有破其凶阵，方能取回李氏传承。",
        "default_gender": "male",
        "default_morality": 70,
        "recruitable": False,
        "is_hermit": False,
        "is_world_unique": False,
        "growth_range": [],
        "attribute_weights": {},
        "avatar": "guests/surname_li/elai.webp",
    },
}

GUEST_SKILLS = {
    "hero_li_lizhen": ["stratagem_burst", "steadfast_planning"],
    "hero_heishan_laoyao": ["hell_instant_formation", "desperate_beast"],
    "task_surname_li_elai": ["surname_li_elai_crushing_blow", "surname_li_elai_last_stand"],
}

IMAGE_SOURCES = {
    "items/surname_li/plum.webp": "items/surname_li/plum.webp",
    "items/surname_li/li_clan_card.webp": "items/surname_li/li_clan_card.webp",
    "items/surname_li/villain_card.webp": "items/surname_li/villain_card.webp",
    "items/mission_chest_v1.webp": "items/mission_chest_v1.webp",
    "guests/surname_li/li_lizhen.webp": "guests/surname_li/li_lizhen.webp",
    "guests/surname_li/heishan_laoyao.webp": "guests/surname_li/heishan_laoyao.webp",
    "guests/surname_li/elai.webp": "guests/surname_li/elai.webp",
}


def sync_surname_li_mission(apps, schema_editor):
    database = schema_editor.connection.alias
    Skill = apps.get_model("guests", "Skill")
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    MissionTemplate = apps.get_model("gameplay", "MissionTemplate")

    # 先验证全部源文件，再写入存储；迁移只维护本任务的内容。
    for target, relative_source in IMAGE_SOURCES.items():
        if not default_storage.exists(target):
            source = Path(settings.BASE_DIR) / "data" / "images" / relative_source
            if not source.is_file():
                raise FileNotFoundError(f"姓氏传说图片不存在：{source}")
    stored_images = {}
    for target, relative_source in IMAGE_SOURCES.items():
        if default_storage.exists(target):
            stored_images[target] = target
        else:
            source = Path(settings.BASE_DIR) / "data" / "images" / relative_source
            stored_images[target] = default_storage.save(target, ContentFile(source.read_bytes()))

    for key, defaults in FALLBACK_SKILL_DEFAULTS.items():
        Skill.objects.using(database).get_or_create(key=key, defaults=deepcopy(defaults))
    for key, defaults in SKILL_DEFAULTS.items():
        Skill.objects.using(database).update_or_create(key=key, defaults=deepcopy(defaults))
    skill_keys = {key for keys in GUEST_SKILLS.values() for key in keys}
    skill_map = {skill.key: skill for skill in Skill.objects.using(database).filter(key__in=skill_keys)}
    for key, snapshot in GUEST_DEFAULTS.items():
        defaults = deepcopy(snapshot)
        defaults["avatar"] = stored_images[defaults["avatar"]]
        guest, _ = GuestTemplate.objects.using(database).update_or_create(key=key, defaults=defaults)
        guest.initial_skills.set([skill_map[skill_key] for skill_key in GUEST_SKILLS[key]])
    for key, snapshot in ITEM_DEFAULTS.items():
        defaults = deepcopy(snapshot)
        defaults["image"] = stored_images[defaults["image"]]
        ItemTemplate.objects.using(database).update_or_create(key=key, defaults=defaults)
    for key, defaults in MISSION_DEFAULTS.items():
        MissionTemplate.objects.using(database).update_or_create(key=key, defaults=deepcopy(defaults))
    transaction.on_commit(lambda: cache.delete("battle:guest_templates"), using=database, robust=True)


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0236_zhaoyun_scroll_untradeable")]
    operations = [migrations.RunPython(sync_surname_li_mission, migrations.RunPython.noop, atomic=True)]
