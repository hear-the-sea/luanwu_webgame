"""个人帮会任务改编自白蛇传、八仙过海与宝莲灯，保留原任务键及参与记录。"""

from copy import deepcopy

from django.db import migrations

# 固化数据快照，迁移不读取会随版本变化的运行期 YAML。
ENEMY_DEFAULTS = {
    "task_legend_crane_attendant": {
        "name": "鹤童",
        "flavor": "白蛇传盗仙草一折中的仙山守童，以轻灵身法阻拦求药之人。",
        "rarity": "purple",
        "archetype": "civil",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "growth_range": [6, 11],
        "base_attack": 75,
        "base_intellect": 175,
        "base_defense": 95,
        "base_agility": 150,
        "base_luck": 85,
        "base_hp": 18000,
    },
    "task_legend_deer_attendant": {
        "name": "鹿童",
        "flavor": "白蛇传盗仙草一折中的仙山守童，守住灵芝药圃，不让来人轻易取草。",
        "rarity": "purple",
        "archetype": "military",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "growth_range": [6, 11],
        "base_attack": 155,
        "base_intellect": 90,
        "base_defense": 175,
        "base_agility": 95,
        "base_luck": 70,
        "base_hp": 22000,
    },
    "task_legend_crab_general": {
        "name": "蟹将",
        "flavor": "八仙过海故事中的龙宫水族，披甲横行，以坚壳为同伴挡下攻势。",
        "rarity": "purple",
        "archetype": "military",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "growth_range": [6, 11],
        "base_attack": 180,
        "base_intellect": 80,
        "base_defense": 220,
        "base_agility": 100,
        "base_luck": 70,
        "base_hp": 28000,
    },
    "task_legend_shrimp_vanguard": {
        "name": "虾兵先锋",
        "flavor": "八仙过海故事中的龙宫水族，持刃逐浪，以迅疾攻势封锁海路。",
        "rarity": "purple",
        "archetype": "military",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "growth_range": [6, 11],
        "base_attack": 210,
        "base_intellect": 90,
        "base_defense": 140,
        "base_agility": 230,
        "base_luck": 85,
        "base_hp": 22000,
    },
    "task_legend_east_sea_dragon": {
        "name": "东海龙王",
        "flavor": "八仙过海故事中的东海龙王，统领水族，龙吟声中海浪翻涌。",
        "rarity": "orange",
        "archetype": "military",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "growth_range": [8, 13],
        "base_attack": 250,
        "base_intellect": 200,
        "base_defense": 190,
        "base_agility": 160,
        "base_luck": 100,
        "base_hp": 30000,
    },
    "task_legend_meishan_guard": {
        "name": "梅山守将",
        "flavor": "宝莲灯劈山救母关卡中的梅山守军，奉二郎神之命守住华山要道。",
        "rarity": "orange",
        "archetype": "military",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "growth_range": [8, 13],
        "base_attack": 220,
        "base_intellect": 130,
        "base_defense": 260,
        "base_agility": 140,
        "base_luck": 85,
        "base_hp": 36000,
    },
    "task_legend_celestial_hound": {
        "name": "哮天犬",
        "flavor": "二郎神身边的神犬，迅疾凶猛，在华山守阵中追击来人。",
        "rarity": "orange",
        "archetype": "military",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "growth_range": [8, 13],
        "base_attack": 265,
        "base_intellect": 100,
        "base_defense": 170,
        "base_agility": 280,
        "base_luck": 100,
        "base_hp": 28000,
    },
    "task_legend_erlang_shen": {
        "name": "二郎神",
        "flavor": "宝莲灯传说中的二郎神，三圣母之兄，持三尖两刃刀镇守华山，与前来救母的沉香相争。",
        "rarity": "orange",
        "archetype": "civil",
        "recruitable": False,
        "default_gender": "male",
        "default_morality": 70,
        "growth_range": [8, 13],
        "base_attack": 260,
        "base_intellect": 280,
        "base_defense": 230,
        "base_agility": 230,
        "base_luck": 115,
        "base_hp": 40000,
    },
}

MISSION_DEFAULTS = {
    "guild_personal_zhenlong_qipu": {
        "name": "白蛇传·盗仙草",
        "display_order": 1301,
        "difficulty": "junior",
        "description": "端午一杯雄黄酒，白素贞现出原形，许仙惊惧倒地，命悬一线。为救丈夫，白素贞远赴昆仑仙山，求取能起死回生的灵芝仙草。帮会收到小青的求援，请你带门客入山相助。\n"
        "\n"
        "仙草近在眼前，鹤童与鹿童却拦住去路：鹤童身法轻灵，以奇策突袭扰乱来人；鹿童守住药圃，以铁壁心法抵挡攻势。击退二童，助白素贞带草归杭，让断魂之人重回人间。\n",
        "battle_type": "task1",
        "guest_only": True,
        "enemy_guests": [
            {"key": "task_legend_crane_attendant", "label": "鹤童", "skills": ["stratagem_burst"]},
            {"key": "task_legend_deer_attendant", "label": "鹿童", "skills": ["iron_wall_heart"]},
        ],
        "enemy_technology": {"level": 7, "guest_level": 80, "guest_bonus": 0.28},
        "drop_table": {
            "silver": 40000,
            "grain": 50000,
            "guild_contribution": 100,
            "red_ruby": {"chance": 0.1, "count": {"min": 1, "max": 2}},
        },
        "available_weekdays": [],
        "base_travel_time": 1200,
        "daily_limit": 10,
        "mission_card_daily_limit": 0,
    },
    "guild_personal_shaolin_night_shadow": {
        "name": "八仙过海·各显神通",
        "display_order": 1302,
        "difficulty": "intermediate",
        "description": "八仙行至东海，不乘云驾雾，各执法宝渡海：铁拐李抛下葫芦，汉钟离展开芭蕉扇，吕洞宾踏剑逐浪，何仙姑立于荷花之上。正当众仙各显神通，海面忽然翻涌，龙宫水族拦住去路。\n"
        "\n"
        "蟹将横甲在前，引敌护阵；虾兵持刃突进，专打立足未稳之人；东海龙王居中发出龙吟，震得浪头齐起。帮会应邀助阵，请你带门客破开水族封锁，助八仙平安渡海。\n",
        "battle_type": "task1",
        "guest_only": True,
        "enemy_guests": [
            {"key": "task_legend_crab_general", "label": "蟹将", "skills": ["iron_wall_heart", "draw_enemy_blades"]},
            {"key": "task_legend_shrimp_vanguard", "label": "虾兵先锋", "skills": ["meteor_pierce_moon"]},
            {"key": "task_legend_east_sea_dragon", "label": "东海龙王", "skills": ["dragon_roar"]},
        ],
        "enemy_technology": {"level": 9, "guest_level": 94, "guest_bonus": 0.46},
        "drop_table": {
            "silver": 120000,
            "grain": 150000,
            "guild_contribution": 300,
            "red_ruby": {"chance": 0.2, "count": {"min": 1, "max": 3}},
        },
        "available_weekdays": [],
        "base_travel_time": 1500,
        "daily_limit": 10,
        "mission_card_daily_limit": 0,
    },
    "guild_personal_yanziwu_secret_letter": {
        "name": "宝莲灯·劈山救母",
        "display_order": 1303,
        "difficulty": "advanced",
        "description": "三圣母因与凡人相爱，被二郎神压在华山之下。沉香长大后得知身世，历经艰险，终于持斧来到山前。山腹深处，母亲的声音依稀可闻；山门之外，守山之阵尚未松动。\n"
        "\n"
        "梅山守将披甲当关，哮天犬踏石扑来，二郎神持三尖两刃刀立于高处。帮会愿为这场救母之行出一份力，请你带门客迎战守军，为沉香争得劈山的一刻，让母子终能相见。\n",
        "battle_type": "task1",
        "guest_only": True,
        "enemy_guests": [
            {
                "key": "task_legend_meishan_guard",
                "label": "梅山守将",
                "skills": ["iron_wall_heart", "draw_enemy_blades"],
            },
            {
                "key": "task_legend_celestial_hound",
                "label": "哮天犬",
                "skills": ["meteor_pierce_moon", "desperate_beast"],
            },
            {"key": "task_legend_erlang_shen", "label": "二郎神", "skills": ["shenyi_poxu", "steadfast_planning"]},
        ],
        "enemy_technology": {"level": 10, "guest_level": 100, "guest_bonus": 0.68},
        "drop_table": {
            "silver": 280000,
            "grain": 350000,
            "guild_contribution": 600,
            "red_ruby": {"chance": 0.28, "count": {"min": 1, "max": 5}},
        },
        "available_weekdays": [],
        "base_travel_time": 1800,
        "daily_limit": 10,
        "mission_card_daily_limit": 0,
    },
}


def sync_folk_legend_missions(apps, schema_editor):
    database = schema_editor.connection.alias
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    MissionTemplate = apps.get_model("gameplay", "MissionTemplate")
    for key, defaults in ENEMY_DEFAULTS.items():
        GuestTemplate.objects.using(database).update_or_create(key=key, defaults=deepcopy(defaults))
    # 复用已存在的杨戬头像；其余守军使用姓名徽记，不借用无关人物的身份。
    avatar = (
        GuestTemplate.objects.using(database)
        .filter(key="task_wanxian_yangjian")
        .values_list("avatar", flat=True)
        .first()
    )
    if avatar:
        GuestTemplate.objects.using(database).filter(key="task_legend_erlang_shen").update(avatar=avatar)
    for key, defaults in MISSION_DEFAULTS.items():
        MissionTemplate.objects.using(database).update_or_create(key=key, defaults=deepcopy(defaults))


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0216_arena_wheel_battle"),
        ("guests", "0092_guest_rarity_pill_used"),
    ]

    operations = [migrations.RunPython(sync_folk_legend_missions, migrations.RunPython.noop)]
