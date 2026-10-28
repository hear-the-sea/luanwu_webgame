"""城防切换真实战斗血量，保留损坏比例和已有维修物资。"""

from copy import deepcopy
from math import ceil

from django.db import migrations, transaction
from django.utils import timezone

HITPOINTS = {
    "enabled": True,
    "gate_hp": 1500000,
    "tower_hp": 100000,
    "hp_per_work": 1000,
    "repair_amount": 100000,
    "repair_interval_seconds": 1800,
}


def enable_hitpoints(apps, schema_editor):
    Season = apps.get_model("gameplay", "Season")
    Site = apps.get_model("gameplay", "SeasonSite")
    Run = apps.get_model("gameplay", "SeasonExpedition")
    database = schema_editor.connection.alias
    with transaction.atomic(using=database):
        for season in (
            Season.objects.using(database).select_for_update().filter(closed=False, ends_at__gt=timezone.now())
        ):
            rules = deepcopy(season.rules)
            campaign = rules.get("campaign", {})
            fort = campaign.get("fortifications", {})
            if (
                not fort.get("enabled")
                or not campaign.get("coalition", {}).get("enabled")
                or fort.get("hitpoints", {}).get("enabled")
            ):
                continue
            fort["hitpoints"] = deepcopy(HITPOINTS)
            season.rules = rules
            season.save(using=database, update_fields=["rules"])
            for site in Site.objects.using(database).filter(season_id=season.pk):
                baseline = campaign["sites"][site.key]
                profile = deepcopy(site.npc_profile or baseline)
                maximum = (
                    HITPOINTS["gate_hp"]
                    if site.key in fort["sites"]
                    else baseline["durability"] * HITPOINTS["hp_per_work"]
                )
                old_max = max(1, profile["durability"])
                site.durability = min(maximum, ceil(site.durability * maximum / old_max))
                profile["durability"] = maximum
                site.npc_profile = profile
                if site.key in fort["sites"]:
                    stored = deepcopy(site.defenses.get("arrow_tower", {}))
                    old_max = max(1, stored.get("max_hp", fort["sites"][site.key]["tower_hp"]))
                    stored["hp"] = min(
                        HITPOINTS["tower_hp"], ceil(stored.get("hp", old_max) * HITPOINTS["tower_hp"] / old_max)
                    )
                    stored["max_hp"] = HITPOINTS["tower_hp"]
                    site.defenses = {**site.defenses, "arrow_tower": stored}
                site.save(using=database, update_fields=["durability", "npc_profile", "defenses"])
            for run in Run.objects.using(database).filter(
                member__season_id=season.pk, kind="repair", status="outbound"
            ):
                result = deepcopy(run.result)
                old = result.get("fortification_repair", {})
                result["fortification_repair"] = {
                    "work_amount": HITPOINTS["repair_amount"],
                    "hp_per_work": HITPOINTS["hp_per_work"],
                    "wall_prices": old.get(
                        "wall_prices",
                        result.get("operation_costs", {})
                        .get("rules", {})
                        .get("repair_cost_per_100_actual_wall", {"wood": 120, "iron": 60}),
                    ),
                    "tower_prices": old.get("tower_prices", fort["tower"]["repair_cost_per_100"]),
                }
                run.result = result
                run.save(using=database, update_fields=["result"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0255_season_fortifications")]
    operations = [migrations.RunPython(enable_hitpoints, migrations.RunPython.noop)]
