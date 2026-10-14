from importlib import import_module

from django.db import migrations

# Reuse the frozen rebasing rules of the previous purple-to-orange promotion.
rules = import_module("guests.migrations.0081_promote_sunwu_xiangyu")
KEY = "hist_xianqin_0007"
OLD_PROFILE = (42, 46, 22, 48, 141, 800)
NEW_PROFILE = rules.ATTRIBUTE_PROFILES[("orange", "civil")]
WEIGHTS = (6, 64, 18, 12)


def promote_lier(apps, schema_editor):
    database = schema_editor.connection.alias
    Template = apps.get_model("guests", "GuestTemplate")
    Guest = apps.get_model("guests", "Guest")
    Candidate = apps.get_model("guests", "RecruitmentCandidate")
    template = Template.objects.using(database).select_for_update().filter(key=KEY).first()
    if template is None:
        return
    state = (template.rarity, template.archetype, rules._template_stats(template), tuple(template.growth_range or ()))
    if state not in (
        ("purple", "civil", OLD_PROFILE, (7, 12)),
        ("orange", "civil", NEW_PROFILE, (8, 14)),
    ):
        raise ValueError(f"Unexpected Li Er template state: {state!r}")
    rules._sync_owned_guests(Guest, database, template, int(template.base_hp), OLD_PROFILE, NEW_PROFILE, 3, WEIGHTS)
    rules._sync_template(template, "orange", "civil", NEW_PROFILE, (8, 14), WEIGHTS)
    Candidate.objects.using(database).filter(template_id=template.pk).update(rarity="orange")


def reverse_only_without_lier(apps, schema_editor):
    if apps.get_model("guests", "GuestTemplate").objects.using(schema_editor.connection.alias).filter(key=KEY).exists():
        raise RuntimeError("Cannot reverse Li Er promotion while the template exists")


class Migration(migrations.Migration):
    dependencies = [("guests", "0084_promote_zhaoyun")]
    operations = [migrations.RunPython(promote_lier, reverse_only_without_lier)]
