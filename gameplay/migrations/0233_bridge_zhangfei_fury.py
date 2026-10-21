"""强化桥头张飞并配置千名嗜血铁骑，演武张飞保持原样。"""

from copy import deepcopy

from django.core.cache import cache
from django.db import migrations, transaction

MISSION_UPDATES = {
    "zhanlong_changban_duanqiao": {
        "description": "长坂桥前，张飞横矛立马，怒目巡视来路。桥后树林烟尘滚滚，旌旗时隐时现，战马奔踏之声连绵不绝。逃来的散卒惊呼林中伏有百万大军，连追至岸边的曹军也勒马驻足，不敢贸然向前。\n"
        "\n"
        "庄园主率门客赶到桥头，尚未来得及通报身份，便被张飞一声断喝拦住。如今败军之中真假难辨，曹军又有乔装混入之嫌，他奉命守桥，绝不肯放来历未明的武装队伍靠近。桥后尚有百姓等待撤离，要取得信任，还须当面试过本领与胆色。\n"
        "\n"
        "桥后尘土愈起愈高，林中究竟藏着多少兵马，无人看得分明。眼前那杆丈八蛇矛却已缓缓抬起。此行需消耗一枚「长坂护主令」，请庄园主整顿队伍，迎接张翼德的桥头考验。桥头张飞敏捷为400，虎啸龙吟伤害倍率为演武版本的两倍，生命低于两成后改为同时攻击两人。\n",
        "enemy_troops": {"bloodthirsty_cavalry": 1000},
        "enemy_troops_display": {"bloodthirsty_cavalry": 1000000},
        "enemy_technology": {
            "level": 10,
            "levels": {"wolf_force": 10, "wolf_defense": 10, "wolf_hp": 10, "wolf_agility": 10, "wolf_berserk": 5},
            "guest_level": 100,
            "guest_bonus": 0.74,
        },
    }
}
SKILL_DEFAULTS = {
    "zhanlong_bridge_tiger_dragon_roar": {
        "name": "虎啸龙吟",
        "rarity": "orange",
        "kind": "active",
        "description": "桥头张飞以25%固定概率怒喝出矛，伤害倍率为演武虎啸龙吟的两倍；生命低于20%后，每次触发攻击两名目标。",
        "targets": 1,
        "damage_formula": {"ally": {"force": 4.0}, "enemy": {"defense": 0.4}},
        "passive_config": {"active": {"trigger_chance": 0.25, "damage_multiplier": 4.6}},
    }
}
SKILL_UPDATES = {
    "zhanlong_dust_ruse": {
        "description": "开场揭示桥后扬尘疑兵，百万声势不计入实际参战兵力。桥头张飞生命低于20%后，虎啸龙吟改为攻击两名目标，每场仅转换一次，持续至战斗结束。",
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
                },
                {
                    "timing": "hit_taken",
                    "conditions": {"hp_ratio_lt": 0.2},
                    "effects": [
                        {
                            "type": "enrage",
                            "luck_bonus": 0,
                            "skill_key": "zhanlong_bridge_tiger_dragon_roar",
                            "targets": 2,
                            "log_name": "虎啸破阵",
                            "message": "桥头张飞怒啸震桥，虎啸龙吟转为同时攻击两人！",
                        }
                    ],
                },
                {
                    "timing": "action_before",
                    "conditions": {"hp_ratio_lt": 0.2},
                    "effects": [
                        {
                            "type": "enrage",
                            "luck_bonus": 0,
                            "skill_key": "zhanlong_bridge_tiger_dragon_roar",
                            "targets": 2,
                            "log_name": "虎啸破阵",
                            "message": "桥头张飞怒啸震桥，虎啸龙吟转为同时攻击两人！",
                        }
                    ],
                },
            ]
        },
    }
}
GUEST_UPDATES = {"task_zhanlong_zhangfei_bridge": {"base_agility": 400}}
GUEST_SKILLS = {"task_zhanlong_zhangfei_bridge": ["zhanlong_bridge_tiger_dragon_roar", "zhanlong_dust_ruse"]}


def sync_bridge_zhangfei_fury(apps, schema_editor):
    database = schema_editor.connection.alias
    MissionTemplate = apps.get_model("gameplay", "MissionTemplate")
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    Skill = apps.get_model("guests", "Skill")
    for key, defaults in SKILL_DEFAULTS.items():
        Skill.objects.using(database).update_or_create(key=key, defaults=deepcopy(defaults))
    for key, updates in SKILL_UPDATES.items():
        Skill.objects.using(database).filter(key=key).update(**deepcopy(updates))
    for key, updates in MISSION_UPDATES.items():
        MissionTemplate.objects.using(database).filter(key=key).update(**deepcopy(updates))
    for key, updates in GUEST_UPDATES.items():
        GuestTemplate.objects.using(database).filter(key=key).update(**updates)
        guest = GuestTemplate.objects.using(database).filter(key=key).first()
        if guest is not None:
            guest.initial_skills.set(Skill.objects.using(database).filter(key__in=GUEST_SKILLS[key]))
    transaction.on_commit(lambda: cache.delete("battle:guest_templates"), using=database, robust=True)


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0232_caochun_enrage_and_cavalry")]
    operations = [migrations.RunPython(sync_bridge_zhangfei_fury, migrations.RunPython.noop, atomic=True)]
