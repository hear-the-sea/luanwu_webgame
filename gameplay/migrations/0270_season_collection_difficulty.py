"""下调默认采集后两场；不覆盖自定义配置或已经出征的快照。"""

from copy import deepcopy
from importlib import import_module

from django.db import migrations
from django.utils import timezone

OLD_WAVES = import_module("gameplay.migrations.0269_season_npc_encounters").DEFAULT_ENCOUNTERS["collection"]["waves"]
PLAYER_SKILLS = ["iron_wall_heart", "fatal_chain_sword"]
NEW_WAVES = {
    1: {**OLD_WAVES[1], "skills": ["fatal_chain_sword"]},
    2: {**OLD_WAVES[2], "skills": ["fatal_chain_sword"]},
    3: {**OLD_WAVES[3], "level_bonus": 70, "troops": {"dao_sheng": 150}, "skills": PLAYER_SKILLS},
    4: {**OLD_WAVES[4], "level_bonus": 90, "count": 2, "troops": {"dao_sheng": 200}, "skills": PLAYER_SKILLS},
}


def soften_collection(apps, schema_editor):
    Season = apps.get_model("gameplay", "Season")
    alias = schema_editor.connection.alias
    seasons = Season.objects.using(alias).select_for_update().filter(closed=False, ends_at__gt=timezone.now())
    for season in seasons.iterator():
        rules = deepcopy(season.rules)
        campaign = rules.get("campaign", {})
        encounters = campaign.get("npc_encounters", {}) if isinstance(campaign, dict) else {}
        if not isinstance(encounters, dict) or not encounters.get("enabled"):
            continue
        collection = encounters.get("collection", {})
        waves = collection.get("waves") if isinstance(collection, dict) else None
        if not isinstance(waves, list):
            continue
        changed = False
        for index, updated in NEW_WAVES.items():
            if index < len(waves) and waves[index] == OLD_WAVES[index]:
                waves[index] = deepcopy(updated)
                changed = True
        guard = encounters.get("garrison", {})
        if isinstance(guard, dict) and guard.get("skills") == OLD_WAVES[4]["skills"]:
            guard["skills"] = deepcopy(PLAYER_SKILLS)
            changed = True
        if changed:
            season.rules = rules
            season.save(using=alias, update_fields=["rules"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0269_season_npc_encounters")]
    operations = [migrations.RunPython(soften_collection, migrations.RunPython.noop)]
