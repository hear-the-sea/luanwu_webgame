"""运输和拦截采用15分钟基础行军、45分钟交付窗口及有效补给奖励。"""

from copy import deepcopy

from django.db import migrations
from django.utils import timezone


def update_transport_balance(apps, schema_editor):
    Season = apps.get_model("gameplay", "Season")
    for season in (
        Season.objects.using(schema_editor.connection.alias)
        .select_for_update()
        .filter(closed=False, ends_at__gt=timezone.now())
        .iterator()
    ):
        rules = deepcopy(season.rules)
        exp = rules.get("campaign", {}).get("expeditions")
        if not exp:
            continue
        exp.setdefault("legacy_transport_seconds", exp.get("transport_seconds", 900))
        exp["mode_travel_seconds"] = {**exp.get("mode_travel_seconds", {}), "transport": 900, "intercept": 900}
        exp.update(transport_seconds=2700, transport_reward_base=30, transport_reward_threshold=200)
        season.rules = rules
        season.save(using=schema_editor.connection.alias, update_fields=["rules"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0262_season_garrison_merit_and_quota")]
    operations = [migrations.RunPython(update_transport_balance, migrations.RunPython.noop)]
