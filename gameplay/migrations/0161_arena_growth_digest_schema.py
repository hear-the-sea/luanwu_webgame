from django.db import migrations, models


def _mark_legacy_growth_claim_digests(apps, _schema_editor) -> None:
    ReserveMember = apps.get_model("gameplay", "ArenaVirtualReserveMember")
    ReserveMember.objects.filter(growth_claim_token__isnull=False).update(
        growth_request_digest_schema=1,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0160_arena_growth_objective_snapshot"),
    ]

    operations = [
        migrations.AddField(
            model_name="arenavirtualreservemember",
            name="growth_request_digest_schema",
            field=models.PositiveSmallIntegerField(
                default=2,
                verbose_name="成长请求摘要 schema",
            ),
        ),
        migrations.RunPython(
            _mark_legacy_growth_claim_digests,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="arenavirtualreservemember",
            constraint=models.CheckConstraint(
                condition=models.Q(growth_request_digest_schema__in=[1, 2]),
                name="arena_vm_growth_digest_schema_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="arenavirtualreservemember",
            constraint=models.CheckConstraint(
                condition=(models.Q(growth_claim_token__isnull=False) | models.Q(growth_request_digest_schema=2)),
                name="arena_vm_unclaimed_digest_schema_current",
            ),
        ),
    ]
