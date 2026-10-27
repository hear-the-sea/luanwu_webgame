"""为默认采集敌将配置不同的玩家技能，保留自定义及出征快照。"""

from copy import deepcopy
from importlib import import_module

from django.db import migrations
from django.utils import timezone

OLD_WAVES = import_module("gameplay.migrations.0270_season_collection_difficulty").NEW_WAVES
NEW_WAVES = {
    1: {**OLD_WAVES[1], "skills": ["endless_chatter"]},
    2: {**OLD_WAVES[2], "skills": [], "skill_sets": [["fatal_chain_sword"], ["endless_chatter"]]},
    3: {
        **OLD_WAVES[3],
        "level_bonus": 65,
        "troops": {"dao_sheng": 125},
        "skills": [],
        "skill_sets": [["iron_wall_heart", "fatal_chain_sword"], ["iron_wall_heart", "prison_break_blade"]],
    },
    4: {
        **OLD_WAVES[4],
        "skills": [],
        "skill_sets": [["iron_wall_heart", "meteor_pierce_moon"], ["iron_wall_heart", "prison_break_blade"]],
    },
}


def vary_collection_skills(apps, schema_editor):
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
        if changed:
            season.rules = rules
            season.save(using=alias, update_fields=["rules"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0270_season_collection_difficulty")]
    operations = [migrations.RunPython(vary_collection_skills, migrations.RunPython.noop)]
