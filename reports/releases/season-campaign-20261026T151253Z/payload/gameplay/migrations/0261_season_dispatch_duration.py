"""统一后续赛季派遣的基础单程时间，保留在途计时与已有进度。"""

from copy import deepcopy

from django.db import migrations
from django.utils import timezone


def update_dispatch_duration(apps, schema_editor):
    Season = apps.get_model("gameplay", "Season")
    database = schema_editor.connection.alias
    for season in (
        Season.objects.using(database).select_for_update().filter(closed=False, ends_at__gt=timezone.now()).iterator()
    ):
        rules = deepcopy(season.rules)
        travel = rules.get("campaign", {}).get("expeditions", {}).get("travel_seconds")
        if not isinstance(travel, dict) or not travel:
            continue
        rules["campaign"]["expeditions"]["travel_seconds"] = {key: 1800 for key in travel}
        season.rules = rules
        season.save(using=database, update_fields=["rules"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0260_jingyanggang_night_warning")]
    operations = [migrations.RunPython(update_dispatch_duration, migrations.RunPython.noop)]
