from django.db import migrations, models
from django.db.models import Count


def ensure_no_duplicate_pending_guest_recruitments(apps, schema_editor):
    GuestRecruitment = apps.get_model("guests", "GuestRecruitment")
    duplicate = (
        GuestRecruitment.objects.filter(status="pending")
        .values("manor_id")
        .annotate(row_count=Count("id"))
        .filter(row_count__gt=1)
        .order_by("manor_id")
        .first()
    )
    if duplicate:
        raise RuntimeError(
            "Duplicate pending GuestRecruitment rows found "
            f"for manor_id={duplicate['manor_id']} count={duplicate['row_count']}. "
            "Resolve duplicate pending guest recruitments before applying "
            "constraint uniq_pending_guest_recruitment_per_manor."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("guests", "0063_skill_passive_config"),
    ]

    operations = [
        migrations.RunPython(ensure_no_duplicate_pending_guest_recruitments, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="guestrecruitment",
            constraint=models.UniqueConstraint(
                condition=models.Q(status="pending"),
                fields=("manor",),
                name="uniq_pending_guest_recruitment_per_manor",
            ),
        ),
    ]
