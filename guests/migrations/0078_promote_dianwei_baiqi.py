from django.db import migrations

# Freeze the profiles used when these guests were recruited.  The migration must
# not depend on future changes to the YAML loader or global growth rules.
MILITARY_PROFILES = {
    "blue": (51, 15, 44, 37, 50, 600),
    "purple": (59, 17, 51, 42, 62, 800),
    "orange": (64, 18, 55, 46, 85, 1000),
}
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

PROMOTIONS = (
    {
        "key": "hist_sljnbc_0068",
        "old_rarity": "blue",
        "new_rarity": "purple",
        "old_growth": (6, 9),
        "new_growth": (6, 11),
        "weights": (52, 6, 32, 10),
    },
    {
        "key": "hist_xianqin_0031",
        "old_rarity": "purple",
        "new_rarity": "orange",
        "old_growth": (7, 12),
        "new_growth": (7, 13),
        "weights": (52, 12, 22, 14),
    },
)


def _template_stats(template):
    return tuple(int(getattr(template, field)) for field in TEMPLATE_STAT_FIELDS)


def _allocate_expected_growth(points, weights):
    """Allocate the expected extra points without changing player allocations."""
    if not points:
        return (0, 0, 0, 0)
    shares = [points * weight // 100 for weight in weights]
    remainders = [points * weight % 100 for weight in weights]
    for index in sorted(range(4), key=lambda item: (-remainders[item], item))[: points - sum(shares)]:
        shares[index] += 1
    return tuple(shares)


def _sync_owned_guests(Guest, database, template, old_hp_base, old_profile, new_profile, growth_change, weights):
    base_deltas = [new - old for old, new in zip(old_profile[:5], new_profile[:5])]
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
            # Old rolls cannot be reconstructed.  Match the expected growth
            # difference at the guest's current level, preserving their rolls.
            extra_points = (max(0, int(guest.level) - 1) * growth_change + 1) // 2
            growth_deltas = _allocate_expected_growth(extra_points, weights)
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
                new_profile[5] + int(guest.hp_bonus) + int(guest.defense_stat) * DEFENSE_TO_HP,
            )
            if guest.current_hp <= 0:
                guest.current_hp = 0
            elif guest.current_hp >= old_max_hp:
                guest.current_hp = new_max_hp
            else:
                guest.current_hp = max(1, min(new_max_hp, int(guest.current_hp) * new_max_hp // old_max_hp))

        Guest.objects.using(database).bulk_update(batch, update_fields, batch_size=BATCH_SIZE)


def _sync_template(template, new_rarity, new_profile, growth_range, weights):
    template.rarity = new_rarity
    for field, value in zip(TEMPLATE_STAT_FIELDS, new_profile):
        setattr(template, field, value)
    template.growth_range = list(growth_range)
    template.attribute_weights = dict(zip(("force", "intellect", "defense", "agility"), weights))
    template.save(update_fields=["rarity", *TEMPLATE_STAT_FIELDS, "growth_range", "attribute_weights"])


def promote_guests(apps, schema_editor):
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    Guest = apps.get_model("guests", "Guest")
    RecruitmentCandidate = apps.get_model("guests", "RecruitmentCandidate")
    database = schema_editor.connection.alias

    for promotion in PROMOTIONS:
        template = GuestTemplate.objects.using(database).select_for_update().filter(key=promotion["key"]).first()
        if template is None:
            # Fresh databases load the YAML roster after migrations.
            continue

        old_rarity = promotion["old_rarity"]
        new_rarity = promotion["new_rarity"]
        old_profile = MILITARY_PROFILES[old_rarity]
        new_profile = MILITARY_PROFILES[new_rarity]
        old_state = (old_rarity, old_profile, promotion["old_growth"])
        new_state = (new_rarity, new_profile, promotion["new_growth"])
        actual_state = (template.rarity, _template_stats(template), tuple(template.growth_range or ()))
        if actual_state not in (old_state, new_state):
            raise ValueError(f"Unexpected template state for {template.key}: {actual_state!r}")

        # If the YAML importer ran first, it already raised template base HP.
        # Guest attributes still need this one-time rebasing in either order.
        old_hp_base = int(template.base_hp)
        growth_change = sum(promotion["new_growth"]) - sum(promotion["old_growth"])
        _sync_owned_guests(
            Guest,
            database,
            template,
            old_hp_base,
            old_profile,
            new_profile,
            growth_change,
            promotion["weights"],
        )
        _sync_template(template, new_rarity, new_profile, promotion["new_growth"], promotion["weights"])
        RecruitmentCandidate.objects.using(database).filter(template_id=template.pk).update(rarity=new_rarity)

        if promotion["key"] == "hist_sljnbc_0068":
            # Blue-to-purple pills create a separate template and reset its
            # guest to level 1.  Its later growth already uses the purple range.
            converted_key = f"guest_rarity_conversion_purple_{template.pk}"
            converted = GuestTemplate.objects.using(database).select_for_update().filter(key=converted_key).first()
            if converted is not None:
                converted_state = (converted.rarity, _template_stats(converted))
                if converted_state not in (("purple", old_profile), ("purple", new_profile)):
                    raise ValueError(f"Unexpected template state for {converted.key}: {converted_state!r}")
                if tuple(converted.growth_range or ()) not in ((), promotion["new_growth"]):
                    raise ValueError(f"Unexpected growth range for {converted.key}: {converted.growth_range!r}")
                _sync_owned_guests(
                    Guest,
                    database,
                    converted,
                    int(converted.base_hp),
                    old_profile,
                    new_profile,
                    0,
                    promotion["weights"],
                )
                _sync_template(converted, "purple", new_profile, promotion["new_growth"], promotion["weights"])


def reverse_only_without_promoted_templates(apps, schema_editor):
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    database = schema_editor.connection.alias
    keys = [promotion["key"] for promotion in PROMOTIONS]
    if GuestTemplate.objects.using(database).filter(key__in=keys).exists():
        raise RuntimeError("Cannot reverse guest promotions while promoted templates exist")


class Migration(migrations.Migration):
    dependencies = [("guests", "0077_guest_vacation_status")]

    operations = [migrations.RunPython(promote_guests, reverse_only_without_promoted_templates)]
