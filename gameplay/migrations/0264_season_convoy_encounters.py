"""扩大实际补给规模并启用固定窗口多人运输遭遇战，保留所有已发兵队伍。"""

from copy import deepcopy

from django.db import migrations
from django.utils import timezone


def enable_convoy_encounters(apps, schema_editor):
    Season = apps.get_model("gameplay", "Season")
    database = schema_editor.connection.alias
    for season in (
        Season.objects.using(database).select_for_update().filter(closed=False, ends_at__gt=timezone.now()).iterator()
    ):
        rules = deepcopy(season.rules)
        campaign = rules.get("campaign", {})
        if not campaign.get("expeditions") or not campaign.get("resource_economy"):
            continue
        exp = campaign["expeditions"]
        exp.update(convoy_encounters_enabled=True, max_convoy_players=5, transport_reward_full_value=1200)
        exp["mode_travel_seconds"] = {**exp.get("mode_travel_seconds", {}), "escort": 900}
        campaign["resource_economy"]["transport"]["cargo_capacity"] = 15000
        season.rules = rules
        season.save(using=database, update_fields=["rules"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0263_season_transport_balance")]
    operations = [migrations.RunPython(enable_convoy_encounters, migrations.RunPython.noop)]
