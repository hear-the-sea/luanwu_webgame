from importlib import import_module

from django.db import migrations

# Reuse the frozen rebasing rules of the previous purple-to-orange promotion.
rules = import_module("guests.migrations.0081_promote_sunwu_xiangyu")
KEY = "hist_sljnbc_0012"
WEIGHTS = (40, 16, 24, 20)


def promote_zhaoyun(apps, schema_editor):
    database = schema_editor.connection.alias
    Template = apps.get_model("guests", "GuestTemplate")
    Guest = apps.get_model("guests", "Guest")
    Candidate = apps.get_model("guests", "RecruitmentCandidate")
    Item = apps.get_model("gameplay", "ItemTemplate")
    template = Template.objects.using(database).select_for_update().filter(key=KEY).first()
    old = rules.ATTRIBUTE_PROFILES[("purple", "military")]
    new = rules.ATTRIBUTE_PROFILES[("orange", "military")]
    if template is not None:
        state = (
            template.rarity,
            template.archetype,
            rules._template_stats(template),
            tuple(template.growth_range or ()),
        )
        if state not in (("purple", "military", old, (7, 12)), ("orange", "military", new, (8, 14))):
            raise ValueError(f"Unexpected Zhao Yun template state: {state!r}")
        rules._sync_owned_guests(Guest, database, template, int(template.base_hp), old, new, 3, WEIGHTS)
        rules._sync_template(template, "orange", "military", new, (8, 14), WEIGHTS)
        Candidate.objects.using(database).filter(template_id=template.pk).update(rarity="orange")
    Item.objects.using(database).filter(key="zhaoyun_guest_scroll").update(
        rarity="orange",
        description="使用后可直接招募橙色门客「赵云」；若庄园已拥有该门客，则无法再次招募。",
    )


def reverse_only_without_zhaoyun(apps, schema_editor):
    if apps.get_model("guests", "GuestTemplate").objects.using(schema_editor.connection.alias).filter(key=KEY).exists():
        raise RuntimeError("Cannot reverse Zhao Yun promotion while the template exists")


class Migration(migrations.Migration):
    dependencies = [("guests", "0083_item_binding")]
    operations = [migrations.RunPython(promote_zhaoyun, reverse_only_without_zhaoyun)]
