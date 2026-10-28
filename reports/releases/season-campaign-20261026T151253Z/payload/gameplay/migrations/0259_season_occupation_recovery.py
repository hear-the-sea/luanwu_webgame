"""占领休整停计分、城防自动恢复与资源保供；不追溯扣血或重置进度。"""

from copy import deepcopy
from datetime import timedelta

from django.db import migrations, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

OCCUPATION = {
    "enabled": True,
    "capture_hp": 1,
    "capture_score_ticks": 2,
    "capture_score_daily_limit": 1,
    "automatic_repair": True,
    "operating_grain_per_active": 100,
    "repair_reserved_cycles": 1,
}


def enable_recovery(apps, schema_editor):
    Season = apps.get_model("gameplay", "Season")
    Site = apps.get_model("gameplay", "SeasonSite")
    database = schema_editor.connection.alias
    now = timezone.now()
    with transaction.atomic(using=database):
        for season in Season.objects.using(database).select_for_update().filter(closed=False, ends_at__gt=now):
            rules = deepcopy(season.rules)
            campaign = rules.get("campaign", {})
            if not campaign.get("fortifications", {}).get("hitpoints", {}).get("enabled") or campaign.get(
                "occupation", {}
            ).get("enabled"):
                continue
            previous = {key: row["protection_seconds"] for key, row in campaign["sites"].items()}
            for key, row in campaign["sites"].items():
                row["protection_seconds"] = max(
                    row["protection_seconds"], 28800 if key in {"mountain:gate", "river:town"} else 14400
                )
            campaign["occupation"] = deepcopy(OCCUPATION)
            season.rules = rules
            season.save(using=database, update_fields=["rules"])
            for site in Site.objects.using(database).filter(season_id=season.pk).exclude(owner=""):
                defenses = deepcopy(site.defenses)
                captured = parse_datetime(defenses.get("captured_at", ""))
                if captured is None:
                    captured = (
                        site.protected_until - timedelta(seconds=previous[site.key]) if site.protected_until else now
                    )
                defenses["captured_at"] = captured.isoformat()
                defenses["auto_repair_at"] = now.isoformat()
                site.defenses = defenses
                if site.protected_until and site.protected_until > now:
                    site.protected_until = max(
                        site.protected_until,
                        captured + timedelta(seconds=campaign["sites"][site.key]["protection_seconds"]),
                    )
                    site.last_score_at = max(site.last_score_at, site.protected_until)
                site.save(using=database, update_fields=["defenses", "protected_until", "last_score_at"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0258_season_logistics_and_guest_quota")]
    operations = [migrations.RunPython(enable_recovery, migrations.RunPython.noop)]
