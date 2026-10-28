"""提高每日领兵额度，启用按实际驻守时间结算贡献，不改在途队伍。"""

from copy import deepcopy

from django.db import migrations
from django.utils import timezone


def update_garrison_rules(apps, schema_editor):
    Season = apps.get_model("gameplay", "Season")
    database = schema_editor.connection.alias
    for season in (
        Season.objects.using(database).select_for_update().filter(closed=False, ends_at__gt=timezone.now()).iterator()
    ):
        rules = deepcopy(season.rules)
        campaign = rules.get("campaign", {})
        if "economy" not in campaign or "expeditions" not in campaign:
            continue
        campaign["economy"]["daily_troop_quota"] = 1500
        campaign["expeditions"]["garrison_merit_per_hour"] = 10
        hours = campaign["expeditions"].get("station_hours_options", [1, 2, 4])
        campaign["expeditions"]["station_hours_options"] = sorted(set([*hours, 8]))
        season.rules = rules
        season.save(using=database, update_fields=["rules"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0261_season_dispatch_duration")]
    operations = [migrations.RunPython(update_garrison_rules, migrations.RunPython.noop)]
