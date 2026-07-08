from django.db import migrations, models
from django.db.models import Count


def ensure_no_duplicate_pending_applications(apps, schema_editor):
    GuildApplication = apps.get_model("guilds", "GuildApplication")
    duplicate = (
        GuildApplication.objects.filter(status="pending")
        .values("guild_id", "applicant_id")
        .annotate(row_count=Count("id"))
        .filter(row_count__gt=1)
        .order_by("guild_id", "applicant_id")
        .first()
    )
    if duplicate:
        raise RuntimeError(
            "Duplicate pending GuildApplication rows found "
            f"for guild_id={duplicate['guild_id']} applicant_id={duplicate['applicant_id']} "
            f"count={duplicate['row_count']}. "
            "Resolve duplicate pending guild applications before applying "
            "constraint uniq_pending_guild_application."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("guilds", "0019_add_guard_armory_technology"),
    ]

    operations = [
        migrations.RunPython(ensure_no_duplicate_pending_applications, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="guildapplication",
            constraint=models.UniqueConstraint(
                condition=models.Q(status="pending"),
                fields=("guild", "applicant"),
                name="uniq_pending_guild_application",
            ),
        ),
    ]
