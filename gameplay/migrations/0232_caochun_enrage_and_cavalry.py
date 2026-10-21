"""曹纯三成血狂暴与三千满科技嗜血铁骑，只同步任务和技能配置。"""

from copy import deepcopy

from django.core.cache import cache
from django.db import migrations, transaction

MISSION_UPDATES = {
    "zhanlong_changban_tuwei": {
        "description": "荆襄骤变，曹军铁骑昼夜南下。刘备携百姓撤往江陵，军民相杂，车仗连绵，行至当阳长坂时，被曹军前锋追及。仓促之间，队伍四散，粮车倾覆，家眷失落；漫山遍野尽是奔逃的人影，呼救声淹没在马蹄与战鼓之中。\n"
        "\n"
        "混乱之际，有人看见一员白袍将军逆着人潮杀回阵中，正是昔日燕山相识的赵子龙。刘备的家眷仍陷在重围深处，他不肯独自撤离，正在曹军阵间往返寻人。虎豹骑统领曹纯却已收拢精骑，以迅疾突击截杀外围接应之人，几条通往长坂桥的道路正被逐一封锁。\n"
        "\n"
        "旧日所赠的白马银枪令尚在手中，故人却已身陷生死之地。此行需消耗一枚「白马银枪令」，请庄园主率门客突入长坂乱军，击破曹纯与三千嗜血铁骑的拦截，替赵云打开接应之路。铁骑已研习全部满级专属科技；曹纯生命低于三成时将进入狂暴，运势增加100，其他属性不变，虎豹奔袭转为攻击三人。\n",
        "enemy_troops": {"bloodthirsty_cavalry": 3000},
        "enemy_technology": {
            "level": 10,
            "levels": {"wolf_force": 10, "wolf_defense": 10, "wolf_hp": 10, "wolf_agility": 10, "wolf_berserk": 5},
            "guest_level": 96,
            "guest_bonus": 0.66,
        },
    }
}
SKILL_UPDATES = {
    "zhanlong_cavalry_assault": {"description": "率精骑疾冲，对两名目标造成武力与敏捷伤害；狂暴后改为攻击三名目标。"},
    "zhanlong_cavalry_command": {
        "description": "每轮令己方护院伤害提高12%；统领阵亡后，下一轮不再获得督战增益。生命低于30%时进入狂暴，运势增加100，虎豹奔袭改为攻击三名目标；其他属性不变，每场战斗仅触发一次，持续至战斗结束。",
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
                },
                {
                    "timing": "hit_taken",
                    "conditions": {"hp_ratio_lt": 0.3},
                    "effects": [
                        {
                            "type": "enrage",
                            "luck_bonus": 100,
                            "skill_key": "zhanlong_cavalry_assault",
                            "targets": 3,
                            "log_name": "精骑狂暴",
                            "message": "曹纯血战狂啸，运势增加100，虎豹奔袭转为攻击三人！",
                        }
                    ],
                },
                {
                    "timing": "action_before",
                    "conditions": {"hp_ratio_lt": 0.3},
                    "effects": [
                        {
                            "type": "enrage",
                            "luck_bonus": 100,
                            "skill_key": "zhanlong_cavalry_assault",
                            "targets": 3,
                            "log_name": "精骑狂暴",
                            "message": "曹纯血战狂啸，运势增加100，虎豹奔袭转为攻击三人！",
                        }
                    ],
                },
            ]
        },
    },
}


def sync_caochun_enrage_and_cavalry(apps, schema_editor):
    database = schema_editor.connection.alias
    MissionTemplate = apps.get_model("gameplay", "MissionTemplate")
    Skill = apps.get_model("guests", "Skill")
    for key, updates in MISSION_UPDATES.items():
        MissionTemplate.objects.using(database).filter(key=key).update(**deepcopy(updates))
    for key, updates in SKILL_UPDATES.items():
        Skill.objects.using(database).filter(key=key).update(**deepcopy(updates))
    transaction.on_commit(lambda: cache.delete("battle:guest_templates"), using=database, robust=True)


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0231_zhanlong_mission_cards")]
    operations = [migrations.RunPython(sync_caochun_enrage_and_cavalry, migrations.RunPython.noop, atomic=True)]
