"""桥头张飞不再携带疑兵被动，低血量转换并入虎啸龙吟。"""

from copy import deepcopy
from importlib import import_module

from django.core.cache import cache
from django.db import migrations, transaction

bridge = import_module("gameplay.migrations.0233_bridge_zhangfei_fury")
SKILL_DEFAULTS = deepcopy(bridge.SKILL_DEFAULTS)
SKILL_DEFAULTS["zhanlong_bridge_tiger_dragon_roar"]["passive_config"]["triggers"] = deepcopy(
    bridge.SKILL_UPDATES["zhanlong_dust_ruse"]["passive_config"]["triggers"][1:]
)
GUEST_SKILLS = {"task_zhanlong_zhangfei_bridge": ["zhanlong_bridge_tiger_dragon_roar"]}


def merge_bridge_roar(apps, schema_editor):
    database = schema_editor.connection.alias
    Skill = apps.get_model("guests", "Skill")
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    for key, defaults in SKILL_DEFAULTS.items():
        Skill.objects.using(database).update_or_create(key=key, defaults=deepcopy(defaults))
    for key, skills in GUEST_SKILLS.items():
        guest = GuestTemplate.objects.using(database).filter(key=key).first()
        if guest is not None:
            guest.initial_skills.set(Skill.objects.using(database).filter(key__in=skills))
    transaction.on_commit(lambda: cache.delete("battle:guest_templates"), using=database, robust=True)


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0234_zhaoyun_dragon_assault")]
    operations = [migrations.RunPython(merge_bridge_roar, migrations.RunPython.noop, atomic=True)]
