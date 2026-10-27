"""城防归零即占领；保留当前损坏比例并添加独立箭塔状态。"""

from copy import deepcopy

from django.db import migrations, models, transaction
from django.utils import timezone

FORTIFICATIONS = {
    "enabled": True,
    "capture_on_breach": True,
    "sites": {
        "mountain:gate": {"wall_multiplier": 2, "tower_hp": 300},
        "river:town": {"wall_multiplier": 2, "tower_hp": 600},
    },
    "tower": {
        "damage_percent": 3,
        "max_damage_percent": 6,
        "wear_per_battle": 25,
        "repair_cost_per_100": {"wood": 80, "iron": 100},
    },
    "fatigue": {"per_stack_percent": 5, "max_stacks": 2},
}


def enable_fortifications(apps, schema_editor):
    Season = apps.get_model("gameplay", "Season")
    Site = apps.get_model("gameplay", "SeasonSite")
    database = schema_editor.connection.alias
    with transaction.atomic(using=database):
        for season in (
            Season.objects.using(database).select_for_update().filter(closed=False, ends_at__gt=timezone.now())
        ):
            rules = deepcopy(season.rules)
            campaign = rules.get("campaign", {})
            if not campaign.get("coalition", {}).get("enabled") or "fortifications" in campaign:
                continue
            config = deepcopy(FORTIFICATIONS)
            config["sites"] = {key: spec for key, spec in config["sites"].items() if key in campaign.get("sites", {})}
            campaign["fortifications"] = config
            season.rules = rules
            season.save(using=database, update_fields=["rules"])
            for site in Site.objects.using(database).filter(season_id=season.pk):
                spec = config["sites"].get(site.key)
                if not spec:
                    continue
                profile = deepcopy(site.npc_profile or campaign["sites"][site.key])
                multiplier = spec["wall_multiplier"]
                profile["durability"] *= multiplier
                site.durability *= multiplier
                site.npc_profile = profile
                site.defenses = {"arrow_tower": {"hp": spec["tower_hp"], "max_hp": spec["tower_hp"]}}
                site.save(using=database, update_fields=["durability", "npc_profile", "defenses"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0254_season_playability")]
    operations = [
        migrations.AddField(model_name="seasonsite", name="defenses", field=models.JSONField(default=dict)),
        migrations.RunPython(enable_fortifications, migrations.RunPython.noop),
    ]
