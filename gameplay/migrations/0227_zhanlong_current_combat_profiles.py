"""固化战龙当前兵力、专用敌将生命与运势、蚩尤兑换门客运势。"""

from copy import deepcopy

from django.core.cache import cache
from django.db import migrations, transaction

# 只更新明确调整的模板字段，保留头像、技能、玩家实例和唯一归属。
MISSION_UPDATES = {
    "zhanlong_yanshan_shizi": {"enemy_troops": {"dao_sheng": 500, "qiang_wang": 500, "arrow_god": 5000}},
    "zhanlong_changban_duanqiao": {"enemy_troops": {"qiang_wang": 500}},
}
GUEST_UPDATES = {
    "task_zhanlong_tiebeixiong": {"base_luck": 135, "base_hp": 650000},
    "task_zhanlong_caochun": {"base_luck": 120, "base_hp": 800000},
    "task_zhanlong_zhangfei_bridge": {"base_luck": 160, "base_hp": 850000},
    "task_zhanlong_guanyu": {"base_hp": 330000},
    "task_zhanlong_zhangfei": {"base_hp": 280000},
    "task_zhanlong_zhaoyun": {"base_hp": 350000},
    "task_zhanlong_machao": {"base_hp": 220000},
    "task_zhanlong_huangzhong": {"base_hp": 190000},
    "hero_yang_zheng_purple": {"base_luck": 98},
    "hero_di_qinglin": {"base_luck": 98},
}


def sync_current_combat_profiles(apps, schema_editor):
    database = schema_editor.connection.alias
    MissionTemplate = apps.get_model("gameplay", "MissionTemplate")
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    for key, updates in MISSION_UPDATES.items():
        MissionTemplate.objects.using(database).filter(key=key).update(**deepcopy(updates))
    for key, updates in GUEST_UPDATES.items():
        GuestTemplate.objects.using(database).filter(key=key).update(**updates)
    transaction.on_commit(lambda: cache.delete("battle:guest_templates"), using=database, robust=True)


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0226_zhaoyun_equipment_art")]
    operations = [migrations.RunPython(sync_current_combat_profiles, migrations.RunPython.noop, atomic=True)]
