"""提升产兵和总兵力目标；历史未结算时段沿用原速度。"""

from copy import deepcopy

from django.db import migrations, transaction
from django.utils import timezone

TRAINING = {
    "normal_daily_capacity_per_active": 1000,
    "normal_target_per_active": 3000,
    "cavalry_daily_capacity_per_active": 100,
    "cavalry_target_per_active": 500,
}


def increase_training(apps, schema_editor):
    Season = apps.get_model("gameplay", "Season")
    Faction = apps.get_model("gameplay", "SeasonFaction")
    database = schema_editor.connection.alias
    now = timezone.now()
    with transaction.atomic(using=database):
        for season in Season.objects.using(database).select_for_update().filter(closed=False, ends_at__gt=now):
            rules = deepcopy(season.rules)
            economy = rules.get("campaign", {}).get("resource_economy", {})
            if not economy.get("enabled"):
                continue
            training = economy["training"]
            if all(training.get(key, 0) >= value for key, value in TRAINING.items()):
                continue
            previous = deepcopy(training)
            training.update({key: max(training.get(key, 0), value) for key, value in TRAINING.items()})
            season.rules = rules
            season.save(using=database, update_fields=["rules"])
            for state in Faction.objects.using(database).filter(season_id=season.pk):
                state.economy_state = {
                    **state.economy_state,
                    "training_transition": {"effective_at": now.isoformat(), "previous": previous},
                }
                state.save(using=database, update_fields=["economy_state"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0256_season_structure_hitpoints")]
    operations = [migrations.RunPython(increase_training, migrations.RunPython.noop)]
