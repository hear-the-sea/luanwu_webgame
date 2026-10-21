"""同步李氏任务与地窖第二层的当前平衡配置，保留玩家和其他模板数据。"""

from copy import deepcopy

from django.core.cache import cache
from django.db import migrations, transaction

MISSION_UPDATES = {
    "surname_legend_li": {
        "enemy_troops": {"dao_sheng": 800},
        "enemy_technology": {"level": 10, "guest_level": 100},
    },
    "tianpeng_dijiao_2": {"enemy_technology": {"guest_level": 100}},
}
GUEST_UPDATES = {
    "hero_li_lizhen": {
        "growth_range": [6, 12],
        "attribute_weights": {"force": 10, "intellect": 45, "defense": 20, "agility": 25},
    },
    "hero_heishan_laoyao": {"growth_range": [6, 12]},
    "task_surname_li_elai": {"base_hp": 300000},
}


def sync_current_mission_balance(apps, schema_editor):
    database = schema_editor.connection.alias
    MissionTemplate = apps.get_model("gameplay", "MissionTemplate")
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    for key, field_updates in MISSION_UPDATES.items():
        mission = MissionTemplate.objects.using(database).filter(key=key).first()
        if mission is None:
            continue
        updates = {}
        for field, patch in field_updates.items():
            value = deepcopy(getattr(mission, field))
            value.update(deepcopy(patch))
            updates[field] = value
        MissionTemplate.objects.using(database).filter(pk=mission.pk).update(**updates)
    for key, updates in GUEST_UPDATES.items():
        GuestTemplate.objects.using(database).filter(key=key).update(**deepcopy(updates))
    transaction.on_commit(lambda: cache.delete("battle:guest_templates"), using=database, robust=True)


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0240_zhanlong_progressive_difficulty")]
    operations = [migrations.RunPython(sync_current_mission_balance, migrations.RunPython.noop, atomic=True)]
