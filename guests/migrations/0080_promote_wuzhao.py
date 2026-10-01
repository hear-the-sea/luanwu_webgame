from django.db import migrations

# Freeze the civil profiles so later balance changes cannot alter this migration.
TEMPLATE_KEY = "hist_suitang_0003_wuzhao"
OLD_PROFILE = (42, 46, 22, 48, 141, 800)
NEW_PROFILE = (45, 50, 24, 52, 165, 1000)
OLD_GROWTH = (8, 11)
NEW_GROWTH = (8, 13)
WEIGHTS = (12, 54, 22, 12)
TEMPLATE_STAT_FIELDS = (
    "base_attack",
    "base_intellect",
    "base_defense",
    "base_agility",
    "base_luck",
    "base_hp",
)
ATTRIBUTE_FIELDS = ("force", "intellect", "defense_stat", "agility")
INITIAL_FIELDS = ("initial_force", "initial_intellect", "initial_defense", "initial_agility")
HP_FLOOR = 200
DEFENSE_TO_HP = 50
BATCH_SIZE = 500


def _allocate_expected_growth(points):
    """Preserve past rolls and distribute only the expected extra growth."""
    shares = [points * weight // 100 for weight in WEIGHTS]
    remainders = [points * weight % 100 for weight in WEIGHTS]
    for index in sorted(range(4), key=lambda item: (-remainders[item], item))[: points - sum(shares)]:
        shares[index] += 1
    return tuple(shares)


def _sync_owned_guests(Guest, database, template):
    base_deltas = [new - old for old, new in zip(OLD_PROFILE[:5], NEW_PROFILE[:5])]
    # The YAML importer may already have adjusted HP for the new template base.
    old_hp_base = int(template.base_hp)
    guests = Guest.objects.using(database).select_for_update().filter(template_id=template.pk).order_by("pk")
    last_pk = 0
    update_fields = (*ATTRIBUTE_FIELDS, *INITIAL_FIELDS, "luck", "current_hp")
    while batch := list(guests.filter(pk__gt=last_pk)[:BATCH_SIZE]):
        last_pk = batch[-1].pk
        for guest in batch:
            old_max_hp = max(
                HP_FLOOR,
                old_hp_base + int(guest.hp_bonus) + int(guest.defense_stat) * DEFENSE_TO_HP,
            )
            # The average growth rises from 9.5 to 10.5 points per level.
            growth_deltas = _allocate_expected_growth(max(0, int(guest.level) - 1))
            for index, (stat_field, initial_field) in enumerate(zip(ATTRIBUTE_FIELDS, INITIAL_FIELDS)):
                setattr(
                    guest,
                    stat_field,
                    int(getattr(guest, stat_field)) + base_deltas[index] + growth_deltas[index],
                )
                setattr(guest, initial_field, int(getattr(guest, initial_field)) + base_deltas[index])
            guest.luck = int(guest.luck) + base_deltas[4]

            new_max_hp = max(
                HP_FLOOR,
                NEW_PROFILE[5] + int(guest.hp_bonus) + int(guest.defense_stat) * DEFENSE_TO_HP,
            )
            if guest.current_hp <= 0:
                guest.current_hp = 0
            elif guest.current_hp >= old_max_hp:
                guest.current_hp = new_max_hp
            else:
                guest.current_hp = max(1, min(new_max_hp, int(guest.current_hp) * new_max_hp // old_max_hp))

        Guest.objects.using(database).bulk_update(batch, update_fields, batch_size=BATCH_SIZE)


def promote_wuzhao(apps, schema_editor):
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    Guest = apps.get_model("guests", "Guest")
    RecruitmentCandidate = apps.get_model("guests", "RecruitmentCandidate")
    database = schema_editor.connection.alias
    template = GuestTemplate.objects.using(database).select_for_update().filter(key=TEMPLATE_KEY).first()
    if template is None:
        # Fresh databases load the YAML roster after migrations.
        return

    actual_state = (
        template.rarity,
        tuple(int(getattr(template, field)) for field in TEMPLATE_STAT_FIELDS),
        tuple(template.growth_range or ()),
    )
    old_state = ("purple", OLD_PROFILE, OLD_GROWTH)
    new_state = ("orange", NEW_PROFILE, NEW_GROWTH)
    if actual_state not in (old_state, new_state):
        raise ValueError(f"Unexpected template state for {template.key}: {actual_state!r}")

    _sync_owned_guests(Guest, database, template)
    template.rarity = "orange"
    for field, value in zip(TEMPLATE_STAT_FIELDS, NEW_PROFILE):
        setattr(template, field, value)
    template.growth_range = list(NEW_GROWTH)
    template.attribute_weights = dict(zip(("force", "intellect", "defense", "agility"), WEIGHTS))
    template.save(update_fields=["rarity", *TEMPLATE_STAT_FIELDS, "growth_range", "attribute_weights"])
    RecruitmentCandidate.objects.using(database).filter(template_id=template.pk).update(rarity="orange")


def reverse_only_without_promoted_template(apps, schema_editor):
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    if GuestTemplate.objects.using(schema_editor.connection.alias).filter(key=TEMPLATE_KEY).exists():
        raise RuntimeError("Wuzhao promotion cannot be reversed safely after guest growth or player allocations.")


class Migration(migrations.Migration):
    dependencies = [("guests", "0079_promote_huangzhong")]

    operations = [migrations.RunPython(promote_wuzhao, reverse_only_without_promoted_template)]
