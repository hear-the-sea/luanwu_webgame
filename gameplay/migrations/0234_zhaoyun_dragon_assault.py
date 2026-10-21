"""演武赵云改为三目标残血增伤主动技能，保留其他五虎配置和玩家数据。"""

from copy import deepcopy

from django.core.cache import cache
from django.db import migrations, transaction

MISSION_UPDATES = {
    "zhanlong_wuhu_shizhen": {
        "description": "自长坂一别，岁月流转，蜀中五虎如今已齐聚军前。庄园主昔日在燕山护民、长坂接应的经历，经赵云举荐传入刘备耳中。一封演武邀书送到庄园，邀众门客入营会武，也请庄园主当面谈一谈乱世之中如何待人、如何用人。\n"
        "\n"
        "演武场上，关羽按刀镇住阵脚，张飞持矛蓄势，赵云银枪穿阵，马超策马游走，黄忠在后方搭箭观阵。五人各有所长，彼此照应：重刀可破甲，蛇矛能聚力，银枪横扫三人并追击残血，快马压迫全阵，强弓专寻伤处。赵云虽是旧识，今日仍将亲自入阵，检验庄园门客的武艺与主人的临阵取舍。\n"
        "\n"
        "仁义之名须见诸行事，举荐之情也须以本领相承。此行需消耗一封「当阳义勇书」，请庄园主率众赴约，在五虎合阵之前展现庄园的担当。\n"
    }
}
SKILL_DEFAULTS = {
    "zhanlong_dragon_assault": {
        "name": "龙胆破阵",
        "rarity": "orange",
        "kind": "active",
        "description": "银枪横扫三名目标，以武力与敏捷造成伤害；每个目标生命低于35%时，对该目标的本次伤害提高50%。",
        "targets": 3,
        "damage_formula": {"base": 700, "ally": {"force": 3.6, "agility": 2.4}, "enemy": {"defense": 0.25}},
        "passive_config": {"active": {"execute_hp_ratio_lt": 0.35, "execute_multiplier": 1.5}},
    }
}
GUEST_UPDATES = {
    "task_zhanlong_zhaoyun": {
        "flavor": "昔日燕山与长坂并肩的故人，如今执枪列于演武阵中。赵云银枪疾突、横扫破阵，专寻敌阵伤处追击，要亲自检验庄园门客应对连续攻势的本领。"
    }
}
GUEST_SKILLS = {"task_zhanlong_zhaoyun": ["zhanlong_dragon_assault"]}


def sync_zhaoyun_dragon_assault(apps, schema_editor):
    database = schema_editor.connection.alias
    MissionTemplate = apps.get_model("gameplay", "MissionTemplate")
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    Skill = apps.get_model("guests", "Skill")
    for key, defaults in SKILL_DEFAULTS.items():
        Skill.objects.using(database).update_or_create(key=key, defaults=deepcopy(defaults))
    for key, updates in MISSION_UPDATES.items():
        MissionTemplate.objects.using(database).filter(key=key).update(**deepcopy(updates))
    for key, updates in GUEST_UPDATES.items():
        GuestTemplate.objects.using(database).filter(key=key).update(**updates)
        guest = GuestTemplate.objects.using(database).filter(key=key).first()
        if guest is not None:
            guest.initial_skills.set(Skill.objects.using(database).filter(key__in=GUEST_SKILLS[key]))
    transaction.on_commit(lambda: cache.delete("battle:guest_templates"), using=database, robust=True)


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0233_bridge_zhangfei_fury")]
    operations = [migrations.RunPython(sync_zhaoyun_dragon_assault, migrations.RunPython.noop, atomic=True)]
