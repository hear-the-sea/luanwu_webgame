"""为修行任务建立独立敌人和与身份匹配的头像。"""

from copy import deepcopy
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import migrations

ENEMY_DEFAULTS = {
    "task_cultivation_road_blade": {
        "name": "拦路刀客",
        "archetype": "military",
        "rarity": "blue",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "base_attack": 90,
        "base_intellect": 35,
        "base_defense": 85,
        "base_agility": 65,
        "base_luck": 55,
        "base_hp": 1200,
        "growth_range": [5, 9],
        "avatar": "guests/cultivation_road_blade.webp",
    },
    "task_cultivation_bandit_leader": {
        "name": "劫经匪首",
        "archetype": "military",
        "rarity": "blue",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "base_attack": 115,
        "base_intellect": 40,
        "base_defense": 65,
        "base_agility": 90,
        "base_luck": 65,
        "base_hp": 1200,
        "growth_range": [5, 9],
        "avatar": "guests/cultivation_bandit_leader.webp",
    },
    "task_cultivation_valley_swordsman": {
        "name": "守谷剑客",
        "archetype": "military",
        "rarity": "purple",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "base_attack": 120,
        "base_intellect": 55,
        "base_defense": 110,
        "base_agility": 100,
        "base_luck": 65,
        "base_hp": 1800,
        "growth_range": [6, 11],
        "avatar": "guests/cultivation_valley_swordsman.webp",
    },
    "task_cultivation_valley_healer": {
        "name": "幽谷药师",
        "archetype": "civil",
        "rarity": "purple",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "base_attack": 45,
        "base_intellect": 125,
        "base_defense": 65,
        "base_agility": 80,
        "base_luck": 100,
        "base_hp": 1600,
        "growth_range": [6, 11],
        "avatar": "guests/cultivation_valley_healer.webp",
    },
    "task_cultivation_vajra_guard": {
        "name": "金刚护法",
        "archetype": "military",
        "rarity": "purple",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "base_attack": 150,
        "base_intellect": 65,
        "base_defense": 155,
        "base_agility": 75,
        "base_luck": 70,
        "base_hp": 2400,
        "growth_range": [6, 11],
        "avatar": "guests/cultivation_vajra_guard.webp",
    },
    "task_cultivation_mind_guard": {
        "name": "明心护法",
        "archetype": "civil",
        "rarity": "purple",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "base_attack": 65,
        "base_intellect": 175,
        "base_defense": 100,
        "base_agility": 100,
        "base_luck": 120,
        "base_hp": 2400,
        "growth_range": [6, 11],
        "avatar": "guests/cultivation_mind_guard.webp",
    },
    "task_cultivation_demon_guard": {
        "name": "伏魔护法",
        "archetype": "military",
        "rarity": "purple",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "base_attack": 170,
        "base_intellect": 60,
        "base_defense": 95,
        "base_agility": 155,
        "base_luck": 80,
        "base_hp": 2200,
        "growth_range": [6, 11],
        "avatar": "guests/cultivation_demon_guard.webp",
    },
}

AVATAR_SOURCES = {
    "task_cultivation_road_blade": "cultivation/cultivation_road_blade.webp",
    "task_cultivation_bandit_leader": "cultivation/cultivation_bandit_leader.webp",
    "task_cultivation_valley_swordsman": "cultivation/cultivation_valley_swordsman.webp",
    "task_cultivation_valley_healer": "cultivation/cultivation_valley_healer.webp",
    "task_cultivation_vajra_guard": "cultivation/cultivation_vajra_guard.webp",
    "task_cultivation_mind_guard": "cultivation/cultivation_mind_guard.webp",
    "task_cultivation_demon_guard": "cultivation/cultivation_demon_guard.webp",
}

MISSION_UPDATES = {
    "yaolu_hujing": {
        "enemy_guests": [
            {"key": "task_cultivation_road_blade", "label": "拦路刀客", "skills": ["iron_wall_heart"]},
            {"key": "task_cultivation_bandit_leader", "label": "劫经匪首", "skills": []},
        ],
        "description": "山中药师携经归庐，却被盗匪堵住山道。护送药师脱险，药庐愿以珍藏经卷相赠。拦路刀客善守，劫经匪首出手更疾。",
    },
    "xisui_yougu": {
        "enemy_guests": [
            {
                "key": "task_cultivation_valley_swordsman",
                "label": "守谷剑客",
                "skills": ["iron_wall_heart", "fatal_chain_sword"],
            },
            {
                "key": "task_cultivation_valley_healer",
                "label": "幽谷药师",
                "skills": ["flower_rain", "steadfast_planning"],
            },
        ],
        "description": "幽谷药香不散，剑客守阵，药师以言语扰敌。闯过二人的考验，便可带走谷中修行珍品。只重护甲，未必挡得住药师的攻势。",
    },
    "yijin_mijing": {
        "enemy_guests": [
            {
                "key": "task_cultivation_vajra_guard",
                "label": "金刚护法",
                "skills": ["iron_wall_heart", "draw_enemy_blades"],
            },
            {
                "key": "task_cultivation_mind_guard",
                "label": "明心护法",
                "skills": ["tangseng_sermon", "steadfast_planning"],
            },
            {"key": "task_cultivation_demon_guard", "label": "伏魔护法", "skills": ["meteor_pierce_moon"]},
        ],
        "description": "古寺深处别有洞天。金刚护法引敌护阵，明心护法诵经伤敌，伏魔护法快攻破阵。胜过三位护法，可获经卷、灵丹与遗留武学。",
    },
}


def sync_cultivation_enemies(apps, schema_editor):
    database = schema_editor.connection.alias
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    MissionTemplate = apps.get_model("gameplay", "MissionTemplate")
    for key, data in ENEMY_DEFAULTS.items():
        defaults = deepcopy(data)
        target = defaults["avatar"]
        if not default_storage.exists(target):
            source = Path(settings.BASE_DIR) / "data" / "images" / "guests" / AVATAR_SOURCES[key]
            defaults["avatar"] = default_storage.save(target, ContentFile(source.read_bytes()))
        GuestTemplate.objects.using(database).update_or_create(key=key, defaults=defaults)
    for key, defaults in MISSION_UPDATES.items():
        MissionTemplate.objects.using(database).filter(key=key).update(**deepcopy(defaults))


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0206_fix_cultivation_chest_images"), ("guests", "0091_custom_guest_daily_feeding")]
    operations = [migrations.RunPython(sync_cultivation_enemies, migrations.RunPython.noop, atomic=True)]
