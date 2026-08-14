from django.db import migrations, models
from django.db.models import Count

GUEST_COUNT_TARGET_MAX = 12


def _guest_count_target(*, guest_count: int, growth_stage: int, roster_focus: object) -> int:
    if guest_count <= 0:
        return 0
    try:
        focus_value = roster_focus if isinstance(roster_focus, (int, float, str)) else 0.5
        focus = min(1.0, max(0.0, float(focus_value)))
    except (TypeError, ValueError):
        focus = 0.5
    focus_bonus = max(1, min(3, round(focus * 2)))
    stage_bonus = min(3, max(0, (max(1, int(growth_stage)) - 1) // 3))
    return min(GUEST_COUNT_TARGET_MAX, int(guest_count) + focus_bonus + stage_bonus)


def populate_guest_count_targets(apps, schema_editor) -> None:
    database_alias = schema_editor.connection.alias
    BotProfile = apps.get_model("gameplay", "BotProfile")
    Guest = apps.get_model("guests", "Guest")
    guest_counts = dict(
        Guest.objects.using(database_alias)
        .values("manor_id")
        .annotate(guest_count=Count("id"))
        .values_list("manor_id", "guest_count")
    )
    profiles = (
        BotProfile.objects.using(database_alias)
        .filter(engine_version=2, policy_version=2)
        .only(
            "id",
            "manor_id",
            "growth_stage",
            "development_profile",
        )
        .iterator(chunk_size=500)
    )
    batch = []
    for profile in profiles:
        development_profile = profile.development_profile if isinstance(profile.development_profile, dict) else {}
        profile.guest_count_target = _guest_count_target(
            guest_count=int(guest_counts.get(profile.manor_id, 0)),
            growth_stage=int(profile.growth_stage or 1),
            roster_focus=development_profile.get("roster_focus", 0.5),
        )
        batch.append(profile)
        if len(batch) >= 500:
            BotProfile.objects.using(database_alias).bulk_update(batch, ["guest_count_target"], batch_size=500)
            batch = []
    if batch:
        BotProfile.objects.using(database_alias).bulk_update(batch, ["guest_count_target"], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0177_virtual_player_attempt_trigger_dimensions_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="botprofile",
            name="guest_count_target",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="普通培养门客目标数"),
        ),
        migrations.RunPython(populate_guest_count_targets, migrations.RunPython.noop),
    ]
