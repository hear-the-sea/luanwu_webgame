"""启用新的 NPC 档位；已出征队伍继续使用各自的敌军快照。"""

from copy import deepcopy

from django.db import migrations
from django.utils import timezone

DEFAULT_ENCOUNTERS = {
    "enabled": True,
    "pools": {
        "green": ["hero_ding_busan", "hero_ilrock_04838", "hero_ilrock_04842"],
        "blue": ["hero_xiao_gao", "hero_xue_xiaoren", "hero_feng_siniang"],
        "purple": ["hero_meng_xinghun", "hero_ding_peng", "hero_lian_xing"],
    },
    "collection": {
        "base_levels": {
            "mountain:inn": 20,
            "mountain:ruins": 30,
            "river:docks": 40,
            "forest:lodge": 50,
            "forest:granary": 50,
        },
        "waves": [
            {"level_bonus": 0, "count": 1, "rarity": "green", "skills": [], "troops": {}},
            {"level_bonus": 20, "count": 1, "rarity": "green", "skills": ["iron_wall_heart"], "troops": {}},
            {
                "level_bonus": 50,
                "count": 2,
                "rarity": "blue",
                "skills": ["iron_wall_heart"],
                "troops": {"dao_sheng": 100},
            },
            {
                "level_bonus": 80,
                "count": 2,
                "rarity": "blue",
                "skills": ["iron_wall_heart", "guild_wulinzhai_shadow_raid"],
                "troops": {"dao_sheng": 200},
            },
            {
                "level_bonus": 110,
                "count": 3,
                "rarity": "purple",
                "skills": ["iron_wall_heart", "guild_wulinzhai_shadow_raid"],
                "troops": {"dao_sheng": 300},
            },
        ],
    },
    "garrison": {
        "base_levels": {
            "mountain:gate": 120,
            "river:town": 150,
            "forest:camp": 100,
            "forest:granary": 100,
            "river:docks": 100,
            "river:market": 100,
        },
        "rarity": "blue",
        "guests_per_player": 3,
        "max_guests": 15,
        "troops_per_player": {"dao_sheng": 500},
        "skills": ["iron_wall_heart", "guild_wulinzhai_shadow_raid"],
    },
}


def enable_npc_encounters(apps, schema_editor):
    Season = apps.get_model("gameplay", "Season")
    alias = schema_editor.connection.alias
    rows = Season.objects.using(alias).select_for_update().filter(closed=False, ends_at__gt=timezone.now())
    for season in rows.iterator():
        rules = deepcopy(season.rules)
        campaign = rules.get("campaign")
        if not isinstance(campaign, dict) or "npc_encounters" in campaign:
            continue
        if not campaign.get("resource_economy", {}).get("enabled"):
            continue
        waves = campaign["resource_economy"].get("pve", {}).get("collection_waves", {})
        maximum = waves.get("max_waves", 5) if isinstance(waves, dict) else None
        if type(maximum) is not int or maximum > len(DEFAULT_ENCOUNTERS["collection"]["waves"]):
            continue
        # 自定义节点不能用默认档位，避免破坏现有季的配置。
        if not set(campaign.get("sites", {})) <= set(DEFAULT_ENCOUNTERS["garrison"]["base_levels"]):
            continue
        if not set(campaign.get("economy", {}).get("pve", {})) <= set(DEFAULT_ENCOUNTERS["collection"]["base_levels"]):
            continue
        campaign["npc_encounters"] = deepcopy(DEFAULT_ENCOUNTERS)
        season.rules = rules
        season.save(using=alias, update_fields=["rules"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0268_season_collection_waves")]
    operations = [migrations.RunPython(enable_npc_encounters, migrations.RunPython.noop)]
