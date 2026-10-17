from importlib import import_module

from django.db import migrations

# Reuse the frozen promotion helpers, including preservation of past rolls,
# player allocations and the current health ratio.
promotion_helpers = import_module("guests.migrations.0081_promote_sunwu_xiangyu")
TEMPLATE_KEY = "hist_mingqing_0002"
OLD_PROFILE = (59, 17, 51, 42, 62, 800)
NEW_PROFILE = (64, 18, 55, 46, 85, 1000)
OLD_GROWTH = (7, 12)
NEW_GROWTH = (8, 14)
WEIGHTS = (42, 18, 24, 16)


def promote_zhudi(apps, schema_editor):
    Template = apps.get_model("guests", "GuestTemplate")
    Guest = apps.get_model("guests", "Guest")
    Candidate = apps.get_model("guests", "RecruitmentCandidate")
    database = schema_editor.connection.alias
    template = Template.objects.using(database).select_for_update().filter(key=TEMPLATE_KEY).first()
    if template is None:
        # Fresh databases load the promoted YAML roster after migrations.
        return

    state = (
        template.rarity,
        template.archetype,
        promotion_helpers._template_stats(template),
        tuple(template.growth_range or ()),
    )
    if state not in (
        ("purple", "military", OLD_PROFILE, OLD_GROWTH),
        ("orange", "military", NEW_PROFILE, NEW_GROWTH),
    ):
        raise ValueError(f"Unexpected template state for {template.key}: {state!r}")

    promotion_helpers._sync_owned_guests(
        Guest,
        database,
        template,
        int(template.base_hp),
        OLD_PROFILE,
        NEW_PROFILE,
        sum(NEW_GROWTH) - sum(OLD_GROWTH),
        WEIGHTS,
    )
    promotion_helpers._sync_template(template, "orange", "military", NEW_PROFILE, NEW_GROWTH, WEIGHTS)
    Candidate.objects.using(database).filter(template_id=template.pk).update(rarity="orange")


def reverse_without_promoted_templates(apps, schema_editor):
    Template = apps.get_model("guests", "GuestTemplate")
    if Template.objects.using(schema_editor.connection.alias).filter(key=TEMPLATE_KEY).exists():
        raise RuntimeError("Cannot reverse Zhu Di promotion while the promoted template exists")


class Migration(migrations.Migration):
    dependencies = [("guests", "0085_promote_lier")]
    operations = [migrations.RunPython(promote_zhudi, reverse_without_promoted_templates)]
