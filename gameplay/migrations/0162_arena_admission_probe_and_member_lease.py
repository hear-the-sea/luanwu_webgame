from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone

import gameplay.models.arena_virtual


def _backfill_reserve_member_lease_deadlines(apps, _schema_editor) -> None:
    ReserveMember = apps.get_model("gameplay", "ArenaVirtualReserveMember")
    RoutingState = apps.get_model("gameplay", "BotRuntimeRoutingState")
    migration_time = timezone.now()
    paused_routing = (
        RoutingState.objects.filter(key="virtual_players").values_list("maintenance_mode", flat=True).first()
        == "v2_paused"
    )
    pending = []
    for member in ReserveMember.objects.only("id", "created_at", "state").iterator(chunk_size=500):
        deadline = member.created_at + timedelta(hours=12)
        member.lease_paused_at = None
        if paused_routing and member.state == "training":
            # Existing rows do not carry the historical routing transition
            # timestamp.  Start the accounting boundary at migration time;
            # when an old deadline is already past, grant one fresh bounded
            # lease so the first post-migration scan can recover the member.
            member.lease_paused_at = migration_time
            deadline = max(deadline, migration_time + timedelta(hours=12))
        member.lease_expires_at = deadline
        pending.append(member)
        if len(pending) >= 500:
            ReserveMember.objects.bulk_update(pending, ["lease_expires_at", "lease_paused_at"])
            pending.clear()
    if pending:
        ReserveMember.objects.bulk_update(pending, ["lease_expires_at", "lease_paused_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0161_arena_growth_digest_schema"),
    ]

    operations = [
        migrations.AddField(
            model_name="arenavirtualdemand",
            name="admission_probe_target_ordinal",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                verbose_name="准入探测目标序号",
            ),
        ),
        migrations.AddField(
            model_name="arenavirtualreservemember",
            name="lease_expires_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="培养租期截止时间",
            ),
        ),
        migrations.AddField(
            model_name="arenavirtualreservemember",
            name="lease_paused_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="培养租期暂停时间",
            ),
        ),
        migrations.RunPython(
            _backfill_reserve_member_lease_deadlines,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="arenavirtualreservemember",
            name="lease_expires_at",
            field=models.DateTimeField(
                default=gameplay.models.arena_virtual.default_arena_reserve_member_lease_expires_at,
                verbose_name="培养租期截止时间",
            ),
        ),
        migrations.AddConstraint(
            model_name="arenavirtualdemand",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(admission_probe_target_ordinal__isnull=True)
                    | (
                        models.Q(admission_pause_reason="no_effective_progress")
                        & models.Q(admission_paused_at__isnull=False)
                        & models.Q(admission_probe_target_ordinal__gte=1)
                        & models.Q(admission_probe_target_ordinal__lte=models.F("max_reserve_target_count"))
                        & (
                            models.Q(admission_probe_target_ordinal=models.F("admission_attempt_high_water"))
                            | models.Q(admission_probe_target_ordinal=models.F("admission_attempt_high_water") + 1)
                        )
                    )
                ),
                name="arena_vd_admission_probe_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="arenavirtualreservemember",
            constraint=models.CheckConstraint(
                condition=models.Q(lease_expires_at__gt=models.F("created_at")),
                name="arena_vm_lease_deadline_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="arenavirtualreservemember",
            constraint=models.CheckConstraint(
                condition=(models.Q(lease_paused_at__isnull=True) | models.Q(state="training")),
                name="arena_vm_lease_pause_training",
            ),
        ),
    ]
