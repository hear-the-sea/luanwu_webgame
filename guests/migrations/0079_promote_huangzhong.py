from django.db import migrations

# Keep this migration independent of future guest profile and growth changes.
TEMPLATE_KEY = "hist_sljnbc_0059"
OLD_PROFILE = (51, 15, 44, 37, 50, 600)
NEW_PROFILE = (59, 17, 51, 42, 62, 800)
OLD_GROWTH = (6, 9)
PURPLE_DEFAULT_GROWTH = (6, 11)
NEW_GROWTH = (6, 12)
WEIGHTS = (42, 10, 20, 28)
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


def _template_stats(template):
    return tuple(int(getattr(template, field)) for field in TEMPLATE_STAT_FIELDS)


def _allocate_expected_growth(points):
    if not points:
        return (0, 0, 0, 0)
    shares = [points * weight // 100 for weight in WEIGHTS]
    remainders = [points * weight % 100 for weight in WEIGHTS]
    for index in sorted(range(4), key=lambda item: (-remainders[item], item))[: points - sum(shares)]:
        shares[index] += 1
    return tuple(shares)


def _sync_owned_guests(Guest, database, template, old_hp_base, growth_change, old_profile=OLD_PROFILE):
    base_deltas = [new - old for old, new in zip(old_profile[:5], NEW_PROFILE[:5])]
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
            # The old random growth rolls are unavailable; preserve them and
            # distribute only the extra expected points from the new range.
            extra_points = (max(0, int(guest.level) - 1) * growth_change + 1) // 2
            growth_deltas = _allocate_expected_growth(extra_points)
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


def _sync_template(template):
    template.rarity = "purple"
    for field, value in zip(TEMPLATE_STAT_FIELDS, NEW_PROFILE):
        setattr(template, field, value)
    template.growth_range = list(NEW_GROWTH)
    template.attribute_weights = dict(zip(("force", "intellect", "defense", "agility"), WEIGHTS))
    template.save(update_fields=["rarity", *TEMPLATE_STAT_FIELDS, "growth_range", "attribute_weights"])


def promote_huangzhong(apps, schema_editor):
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    Guest = apps.get_model("guests", "Guest")
    RecruitmentCandidate = apps.get_model("guests", "RecruitmentCandidate")
    database = schema_editor.connection.alias

    template = GuestTemplate.objects.using(database).select_for_update().filter(key=TEMPLATE_KEY).first()
    if template is None:
        # Fresh databases load the YAML roster after migrations.
        return

    old_state = ("blue", OLD_PROFILE, OLD_GROWTH)
    previous_import_state = ("purple", NEW_PROFILE, PURPLE_DEFAULT_GROWTH)
    new_state = ("purple", NEW_PROFILE, NEW_GROWTH)
    actual_state = (template.rarity, _template_stats(template), tuple(template.growth_range or ()))
    if actual_state not in (old_state, previous_import_state, new_state):
        raise ValueError(f"Unexpected template state for {template.key}: {actual_state!r}")

    # The YAML importer may have updated base HP before this migration.
    _sync_owned_guests(Guest, database, template, int(template.base_hp), sum(NEW_GROWTH) - sum(OLD_GROWTH))
    _sync_template(template)
    RecruitmentCandidate.objects.using(database).filter(template_id=template.pk).update(rarity="purple")

    # Blue-to-purple pills create a separate non-recruitable template.
    converted_key = f"guest_rarity_conversion_purple_{template.pk}"
    converted = GuestTemplate.objects.using(database).select_for_update().filter(key=converted_key).first()
    if converted is not None:
        converted_state = (converted.rarity, _template_stats(converted))
        if converted_state not in (("purple", OLD_PROFILE), ("purple", NEW_PROFILE)):
            raise ValueError(f"Unexpected template state for {converted.key}: {converted_state!r}")
        converted_range = tuple(converted.growth_range or ())
        if converted_range not in ((), PURPLE_DEFAULT_GROWTH, NEW_GROWTH):
            raise ValueError(f"Unexpected growth range for {converted.key}: {converted.growth_range!r}")
        converted_growth_change = sum(NEW_GROWTH) - sum(PURPLE_DEFAULT_GROWTH)
        if converted_state == ("purple", OLD_PROFILE):
            _sync_owned_guests(Guest, database, converted, int(converted.base_hp), converted_growth_change)
        elif converted_range != NEW_GROWTH:
            _sync_owned_guests(
                Guest,
                database,
                converted,
                int(converted.base_hp),
                converted_growth_change,
                old_profile=NEW_PROFILE,
            )
        _sync_template(converted)


def reverse_only_without_promoted_template(apps, schema_editor):
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    database = schema_editor.connection.alias
    if GuestTemplate.objects.using(database).filter(key=TEMPLATE_KEY).exists():
        raise RuntimeError("Cannot reverse Huangzhong promotion while its template exists")


class Migration(migrations.Migration):
    dependencies = [("guests", "0078_promote_dianwei_baiqi")]

    operations = [migrations.RunPython(promote_huangzhong, reverse_only_without_promoted_template)]
