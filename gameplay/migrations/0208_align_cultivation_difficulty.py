"""将三档修行任务难度对齐机关兽初现、瓦岗寨逆袭和洞天福地。"""

from copy import deepcopy

from django.db import migrations

ENEMY_STAT_UPDATES = {
    "task_cultivation_road_blade": {
        "base_attack": 70,
        "base_intellect": 40,
        "base_defense": 60,
        "base_agility": 45,
        "base_luck": 50,
        "base_hp": 2000,
    },
    "task_cultivation_bandit_leader": {
        "base_attack": 100,
        "base_intellect": 35,
        "base_defense": 55,
        "base_agility": 75,
        "base_luck": 65,
        "base_hp": 2400,
    },
    "task_cultivation_valley_swordsman": {
        "base_attack": 80,
        "base_intellect": 45,
        "base_defense": 70,
        "base_agility": 75,
        "base_luck": 65,
        "base_hp": 1600,
    },
    "task_cultivation_valley_healer": {
        "base_attack": 45,
        "base_intellect": 100,
        "base_defense": 55,
        "base_agility": 70,
        "base_luck": 75,
        "base_hp": 1600,
    },
    "task_cultivation_vajra_guard": {
        "base_attack": 150,
        "base_intellect": 65,
        "base_defense": 155,
        "base_agility": 75,
        "base_luck": 70,
        "base_hp": 100000,
    },
    "task_cultivation_mind_guard": {
        "base_attack": 65,
        "base_intellect": 175,
        "base_defense": 100,
        "base_agility": 100,
        "base_luck": 100,
        "base_hp": 100000,
    },
    "task_cultivation_demon_guard": {
        "base_attack": 170,
        "base_intellect": 60,
        "base_defense": 95,
        "base_agility": 155,
        "base_luck": 80,
        "base_hp": 90000,
    },
}

MISSION_COMBAT_UPDATES = {
    "yaolu_hujing": {
        "enemy_guests": [
            {"key": "task_cultivation_road_blade", "skills": ["iron_wall_heart"], "label": "拦路刀客"},
            {"key": "task_cultivation_road_blade", "skills": ["iron_wall_heart"], "label": "拦路刀客"},
            {"key": "task_cultivation_road_blade", "skills": ["iron_wall_heart"], "label": "拦路刀客"},
            {"key": "task_cultivation_road_blade", "skills": ["iron_wall_heart"], "label": "拦路刀客"},
            {"key": "task_cultivation_road_blade", "skills": ["iron_wall_heart"], "label": "拦路刀客"},
            {"key": "task_cultivation_road_blade", "skills": ["iron_wall_heart"], "label": "拦路刀客"},
            {"key": "task_cultivation_bandit_leader", "skills": [], "label": "劫经匪首"},
        ],
        "enemy_troops": {},
        "enemy_technology": {"level": 7, "guest_level": 35, "guest_bonus": 0.12},
        "description": "山中药师携经归庐，却被成群盗匪堵住山道。刀客结阵护住匪首，匪首伺机快攻。护送药师脱险，药庐愿以珍藏经卷相赠。",
    },
    "xisui_yougu": {
        "enemy_guests": [
            {"key": "task_cultivation_valley_swordsman", "skills": ["iron_wall_heart"], "label": "守谷剑客"},
            {"key": "task_cultivation_valley_swordsman", "skills": ["iron_wall_heart"], "label": "守谷剑客"},
            {
                "key": "task_cultivation_valley_swordsman",
                "skills": ["iron_wall_heart", "fatal_chain_sword"],
                "label": "守谷剑客",
            },
            {
                "key": "task_cultivation_valley_healer",
                "skills": ["flower_rain", "steadfast_planning"],
                "label": "幽谷药师",
            },
            {
                "key": "task_cultivation_valley_healer",
                "skills": ["flower_rain", "steadfast_planning"],
                "label": "幽谷药师",
            },
            {
                "key": "task_cultivation_valley_healer",
                "skills": ["flower_rain", "steadfast_planning"],
                "label": "幽谷药师",
            },
        ],
        "enemy_troops": {"arrow_god": 800, "qiang_wang": 800, "quan_sheng": 800},
        "enemy_technology": {"level": 10, "guest_level": 85, "guest_bonus": 0.3},
        "description": "幽谷药香不散，剑客结阵守谷，药师以言语扰敌。闯过守谷人的考验，便可带走谷中修行珍品。只重护甲，未必挡得住药师的攻势。",
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
        "enemy_troops": {"arrow_god": 300, "quan_sheng": 700},
        "enemy_technology": {"level": 9, "guest_level": 80, "guest_bonus": 0.26},
    },
}


def align_cultivation_difficulty(apps, schema_editor):
    database = schema_editor.connection.alias
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    MissionTemplate = apps.get_model("gameplay", "MissionTemplate")
    for key, changes in ENEMY_STAT_UPDATES.items():
        template = GuestTemplate.objects.using(database).select_for_update().get(key=key)
        for field, value in changes.items():
            setattr(template, field, value)
        template.save(using=database, update_fields=list(changes))
    for key, changes in MISSION_COMBAT_UPDATES.items():
        mission = MissionTemplate.objects.using(database).select_for_update().get(key=key)
        for field, value in deepcopy(changes).items():
            setattr(mission, field, value)
        mission.save(using=database, update_fields=list(changes))


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0207_cultivation_enemy_profiles")]
    operations = [migrations.RunPython(align_cultivation_difficulty, migrations.RunPython.noop, atomic=True)]
