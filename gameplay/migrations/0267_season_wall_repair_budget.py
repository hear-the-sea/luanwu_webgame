"""为进行中的默认赛季启用城门日维修上限，不追溯生效前的维修。"""

from copy import deepcopy

from django.db import migrations
from django.utils import timezone


def enable_wall_repair_budget(apps, schema_editor):
    Season = apps.get_model("gameplay", "Season")
    alias = schema_editor.connection.alias
    now = timezone.now()
    seasons = Season.objects.using(alias).select_for_update().filter(closed=False, ends_at__gt=now).iterator()
    for season in seasons:
        rules = deepcopy(season.rules)
        campaign = rules.get("campaign", {})
        if not isinstance(campaign, dict):
            continue
        occupation = campaign.get("occupation")
        fortifications = campaign.get("fortifications", {})
        hitpoints = fortifications.get("hitpoints", {}) if isinstance(fortifications, dict) else {}
        if not (
            isinstance(occupation, dict)
            and occupation.get("enabled") is True
            and occupation.get("automatic_repair") is True
            and isinstance(fortifications, dict)
            and fortifications.get("enabled") is True
            and isinstance(hitpoints, dict)
            and hitpoints.get("enabled") is True
            and hitpoints.get("repair_amount") == 100000
            and hitpoints.get("repair_interval_seconds") == 1800
            and "wall_repair_daily_limit" not in occupation
            and "wall_repair_daily_effective_at" not in occupation
        ):
            continue
        occupation["wall_repair_daily_limit"] = 50000
        occupation["wall_repair_daily_effective_at"] = now.isoformat()
        season.rules = rules
        season.save(using=alias, update_fields=["rules"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0266_season_small_server_cooperation")]
    operations = [migrations.RunPython(enable_wall_repair_budget, migrations.RunPython.noop)]
