from django.db import migrations, models


def _backfill_admission_attempt_high_water(apps, _schema_editor) -> None:
    Demand = apps.get_model("gameplay", "ArenaVirtualDemand")
    demands = Demand.objects.annotate(
        current_member_count=models.Count("reserve_members"),
        exhausted_member_count=models.Count(
            "reserve_members",
            filter=models.Q(reserve_members__state="exhausted"),
        ),
    ).iterator(chunk_size=200)
    for demand in demands:
        exhausted_baseline = int(demand.exhausted_member_count or 0)
        effective_existing_attempts = max(
            0,
            int(demand.current_member_count or 0) - exhausted_baseline,
        )
        demand.admission_legacy_exhausted_baseline_count = exhausted_baseline
        demand.admission_attempt_high_water = max(
            int(demand.created_profile_count or 0),
            effective_existing_attempts,
        )
        demand.save(
            update_fields=[
                "admission_legacy_exhausted_baseline_count",
                "admission_attempt_high_water",
            ]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0157_arena_growth_effective_progress"),
    ]

    operations = [
        migrations.AddField(
            model_name="arenavirtualdemand",
            name="admission_attempt_high_water",
            field=models.PositiveIntegerField(default=0, verbose_name="准入尝试高水位"),
        ),
        migrations.AddField(
            model_name="arenavirtualdemand",
            name="admission_legacy_exhausted_baseline_count",
            field=models.PositiveIntegerField(default=0, verbose_name="历史耗尽成员兼容基线"),
        ),
        migrations.RunPython(
            _backfill_admission_attempt_high_water,
            migrations.RunPython.noop,
        ),
        migrations.AddField(
            model_name="arenavirtualreservemember",
            name="arena_growth_budget_entries",
            field=models.JSONField(blank=True, default=list, verbose_name="竞技场成长预算窗口"),
        ),
    ]
