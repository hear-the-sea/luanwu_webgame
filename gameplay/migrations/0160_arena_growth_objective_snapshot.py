from django.db import migrations, models

TOURNAMENT_LINEUP_MAX_SIZE = 10
ARENA_MAX_GUEST_LEVEL_STEP = 6
MIN_LINEUP_POWER_PERCENT = 80
MAX_LINEUP_POWER_PERCENT = 120


def _backfill_growth_objective_payload(apps, _schema_editor) -> None:
    ReserveMember = apps.get_model("gameplay", "ArenaVirtualReserveMember")
    claimed_members = (
        ReserveMember.objects.filter(
            growth_claim_token__isnull=False,
            growth_objective_payload={},
        )
        .select_related("demand", "demand__coop_event")
        .iterator(chunk_size=200)
    )
    for member in claimed_members:
        demand = member.demand
        critical_count = int(member.growth_minimum_guest_count or demand.target_guest_count or 0)
        target_power = int(demand.target_team_power or 0)
        if critical_count < 1 or target_power < 1 or member.growth_power_before is None:
            continue
        if demand.tournament_id is not None:
            lineup_mode = "tournament"
            lineup_event_id = int(demand.tournament_id)
            lineup_max_size = TOURNAMENT_LINEUP_MAX_SIZE
        elif demand.coop_event_id is not None:
            lineup_mode = "coop"
            lineup_event_id = int(demand.coop_event_id)
            lineup_max_size = max(1, int(demand.coop_event.guest_limit_per_entry))
        else:
            continue
        member.growth_objective_payload = {
            "critical_guest_count": critical_count,
            "preferred_guest_count": max(
                critical_count,
                int(member.roster_target_count or critical_count),
            ),
            "selected_power_lower_bound": (target_power * MIN_LINEUP_POWER_PERCENT + 99) // 100,
            "selected_power_upper_bound": (target_power * MAX_LINEUP_POWER_PERCENT) // 100,
            "selected_power_before": int(member.growth_power_before),
            "target_team_power": target_power,
            "lineup_mode": lineup_mode,
            "lineup_event_id": lineup_event_id,
            "lineup_max_size": lineup_max_size,
            "minimum_guest_level": int(member.growth_minimum_guest_level or 1),
            "recruitment_rarity_cap": member.growth_guest_rarity_cap or None,
            "max_guest_level_step": ARENA_MAX_GUEST_LEVEL_STEP,
        }
        member.save(update_fields=["growth_objective_payload"])


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0159_arena_virtual_demand_admission_guard"),
    ]

    operations = [
        migrations.AddField(
            model_name="arenavirtualreservemember",
            name="growth_objective_payload",
            field=models.JSONField(blank=True, default=dict, verbose_name="成长目标快照"),
        ),
        migrations.RunPython(
            _backfill_growth_objective_payload,
            migrations.RunPython.noop,
        ),
    ]
