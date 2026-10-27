"""给进行中的赛季开启分波资源收集；出征快照保证旧队伍原样结算。"""

from copy import deepcopy

from django.db import migrations
from django.utils import timezone


def enable_collection_waves(apps, schema_editor):
    Season = apps.get_model("gameplay", "Season")
    alias = schema_editor.connection.alias
    seasons = Season.objects.using(alias).select_for_update().filter(closed=False, ends_at__gt=timezone.now())
    for season in seasons.iterator():
        rules = deepcopy(season.rules)
        campaign = rules.get("campaign", {})
        resource = campaign.get("resource_economy") if isinstance(campaign, dict) else None
        if not isinstance(resource, dict) or resource.get("enabled") is not True:
            continue
        pve = resource.get("pve")
        if not isinstance(pve, dict) or "collection_waves" in pve:
            continue
        pve["collection_waves"] = {
            "enabled": True,
            "max_waves": 5,
            "interval_seconds": 300,
            "level_step": 2,
            "win_percent": 25,
        }
        season.rules = rules
        season.save(using=alias, update_fields=["rules"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0267_season_wall_repair_budget")]
    operations = [migrations.RunPython(enable_collection_waves, migrations.RunPython.noop)]
