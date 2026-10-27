"""强化玩家后勤、扩大库容并启用门客每日两次出征与单场城门攻击。"""

from copy import deepcopy

from django.db import migrations, transaction
from django.utils import timezone

PVE = {
    "grain_run": {"grain": 6000, "wood": 600, "iron": 300},
    "wood_run": {"grain": 1200, "wood": 3000, "iron": 600},
    "iron_run": {"grain": 1200, "wood": 600, "iron": 3000},
    "mixed_run": {"grain": 4000, "wood": 1500, "iron": 1500},
}


def enable_logistics(apps, schema_editor):
    Season = apps.get_model("gameplay", "Season")
    database = schema_editor.connection.alias
    with transaction.atomic(using=database):
        for season in (
            Season.objects.using(database).select_for_update().filter(closed=False, ends_at__gt=timezone.now())
        ):
            rules = deepcopy(season.rules)
            campaign = rules.get("campaign", {})
            if not campaign.get("expeditions") or campaign.get("logistics_revision", 0) >= 1:
                continue
            campaign["expeditions"]["guest_daily_limit"] = 2
            hp = campaign.get("fortifications", {}).get("hitpoints", {})
            if hp.get("enabled"):
                hp["gate_normal_rounds"] = 8
            economy = campaign.get("resource_economy", {})
            if economy.get("enabled"):
                economy["pve"].update(deepcopy(PVE))
                economy["pve"].update(action_merit=30, effective_material_merit=True)
                reference = economy["storage"]["reference_pve_daily"]
                for key, amount in {"grain": 8000, "wood": 3000, "iron": 3000}.items():
                    reference[key] = max(reference.get(key, 0), amount)
            campaign["logistics_revision"] = 1
            season.rules = rules
            season.save(using=database, update_fields=["rules"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0257_season_training_capacity")]
    operations = [migrations.RunPython(enable_logistics, migrations.RunPython.noop)]
