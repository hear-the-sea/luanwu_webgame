from django.db import migrations, models


def _backfill_active_growth_claim_eligible_counts(apps, _schema_editor) -> None:
    ReserveMember = apps.get_model("gameplay", "ArenaVirtualReserveMember")
    Guest = apps.get_model("guests", "Guest")
    claimed_members = list(
        ReserveMember.objects.filter(
            growth_claim_token__isnull=False,
            growth_eligible_guest_count_before__isnull=True,
        ).select_related("profile")
    )
    if not claimed_members:
        return

    manor_ids = {int(member.profile.manor_id) for member in claimed_members}
    eligible_counts = {manor_id: 0 for manor_id in manor_ids}
    for manor_id in Guest.objects.filter(
        manor_id__in=manor_ids,
        status="idle",
    ).values_list("manor_id", flat=True):
        normalized_manor_id = int(manor_id)
        eligible_counts[normalized_manor_id] += 1

    for member in claimed_members:
        member.growth_eligible_guest_count_before = min(
            65_535,
            eligible_counts[int(member.profile.manor_id)],
        )
    ReserveMember.objects.bulk_update(
        claimed_members,
        ["growth_eligible_guest_count_before"],
    )


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0156_arena_virtual_reserve_roster_target"),
    ]

    operations = [
        migrations.AddField(
            model_name="arenavirtualreservemember",
            name="growth_eligible_guest_count_before",
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                verbose_name="成长前可参赛门客数",
            ),
        ),
        migrations.RunPython(
            _backfill_active_growth_claim_eligible_counts,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="arenavirtualreservemember",
            name="arena_vm_growth_claim_fields_together",
        ),
        migrations.AddConstraint(
            model_name="arenavirtualreservemember",
            constraint=models.CheckConstraint(
                condition=(
                    (
                        models.Q(growth_claim_token__isnull=True)
                        & models.Q(growth_claimed_at__isnull=True)
                        & models.Q(growth_claim_expires_at__isnull=True)
                        & models.Q(growth_requested_at__isnull=True)
                        & models.Q(growth_operation_id="")
                        & models.Q(growth_attempt_ordinal=0)
                        & models.Q(growth_demand_version__isnull=True)
                        & models.Q(growth_member_version__isnull=True)
                        & models.Q(growth_power_before__isnull=True)
                        & models.Q(growth_eligible_guest_count_before__isnull=True)
                        & models.Q(growth_minimum_guest_count__isnull=True)
                        & models.Q(growth_minimum_guest_level__isnull=True)
                        & models.Q(growth_guest_rarity_cap="")
                    )
                    | (
                        models.Q(growth_claim_token__isnull=False)
                        & models.Q(growth_claimed_at__isnull=False)
                        & models.Q(growth_claim_expires_at__isnull=False)
                        & models.Q(growth_requested_at__isnull=False)
                        & ~models.Q(growth_operation_id="")
                        & models.Q(growth_attempt_ordinal__gte=1)
                        & models.Q(growth_demand_version__isnull=False)
                        & models.Q(growth_member_version__isnull=False)
                        & models.Q(growth_power_before__isnull=False)
                        & models.Q(growth_eligible_guest_count_before__isnull=False)
                        & models.Q(growth_minimum_guest_count__isnull=False)
                        & models.Q(growth_minimum_guest_level__isnull=False)
                    )
                ),
                name="arena_vm_growth_claim_fields_together",
            ),
        ),
    ]
