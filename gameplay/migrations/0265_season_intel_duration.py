"""将进行中赛季的默认探索情报延长至两小时，保留自定义与既有情报。"""

from copy import deepcopy

from django.db import migrations
from django.utils import timezone


def extend_default_intel(apps, schema_editor):
    Season = apps.get_model("gameplay", "Season")
    database = schema_editor.connection.alias
    for season in (
        Season.objects.using(database).select_for_update().filter(closed=False, ends_at__gt=timezone.now()).iterator()
    ):
        rules = deepcopy(season.rules)
        activities = rules.get("campaign", {}).get("activities", {})
        exploration = activities.get("explore", {})
        if exploration.get("intel_seconds") != 1800:
            continue
        exploration["intel_seconds"] = 7200
        season.rules = rules
        season.save(using=database, update_fields=["rules"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0264_season_convoy_encounters")]
    operations = [migrations.RunPython(extend_default_intel, migrations.RunPython.noop)]
