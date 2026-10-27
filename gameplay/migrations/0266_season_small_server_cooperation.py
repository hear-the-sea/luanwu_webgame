"""为进行中的赛季补齐少人协作配置，不覆盖已有设置与行动快照。"""

from copy import deepcopy

from django.db import migrations
from django.utils import timezone


def enable_small_server_cooperation(apps, schema_editor):
    Season = apps.get_model("gameplay", "Season")
    database = schema_editor.connection.alias
    now = timezone.now()
    seasons = Season.objects.using(database).select_for_update().filter(closed=False, ends_at__gt=now).iterator()
    for season in seasons:
        rules = deepcopy(season.rules)
        campaign = rules.get("campaign", {})
        if not isinstance(campaign, dict):
            continue
        population = campaign.get("population")
        resource = campaign.get("resource_economy")
        activities = campaign.get("activities")
        changed = False

        if isinstance(population, dict) and population.get("enabled") is True:
            if "recommendation_max_active" not in population:
                population["recommendation_max_active"] = 20
                changed = True
            if isinstance(resource, dict) and resource.get("enabled") is True:
                economic_population = resource.get("population")
                if isinstance(economic_population, dict):
                    for key, value in (("growth_previous_weight", 0), ("growth_refresh_immediate", True)):
                        if key not in economic_population:
                            economic_population[key] = value
                            changed = True

        if (
            isinstance(resource, dict)
            and resource.get("enabled") is True
            and isinstance(activities, dict)
            and activities.get("enabled") is True
        ):
            transport = resource.get("transport")
            if isinstance(transport, dict):
                for key, value in (("participation_full_value", 80), ("participation_merit", 30)):
                    if key not in transport:
                        transport[key] = value
                        changed = True

        sites = campaign.get("sites")
        city = sites.get("river:town") if isinstance(sites, dict) else None
        if (
            isinstance(city, dict)
            and city.get("score_per_tick") == 3
            and "weekday_score_per_tick" not in city
            and "weekday_score_effective_at" not in city
        ):
            city["weekday_score_per_tick"] = 1
            city["weekday_score_effective_at"] = now.isoformat()
            changed = True

        if changed:
            season.rules = rules
            season.save(using=database, update_fields=["rules"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0265_season_intel_duration")]
    operations = [migrations.RunPython(enable_small_server_cooperation, migrations.RunPython.noop)]
