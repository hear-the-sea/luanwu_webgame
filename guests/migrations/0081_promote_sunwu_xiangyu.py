from django.db import migrations

# Freeze the profiles used when these guests were recruited.  The migration must
# not depend on future changes to the YAML loader or global growth rules.
ATTRIBUTE_PROFILES = {
    ("purple", "military"): (59, 17, 51, 42, 62, 800),
    ("orange", "military"): (64, 18, 55, 46, 85, 1000),
    ("orange", "civil"): (45, 50, 24, 52, 165, 1000),
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
        "key": "hist_xianqin_0008",
        "old_rarity": "purple",
        "old_archetype": "military",
        "new_rarity": "orange",
        "old_growth": (7, 11),
        "new_growth": (9, 14),
        "new_archetype": "civil",
        "weights": (10, 56, 18, 16),
    },
    {
        "key": "hist_qinhan_0023",
        "old_rarity": "purple",
        "old_archetype": "military",
        "new_rarity": "orange",
        "old_growth": (7, 12),
        "new_growth": (8, 14),
        "new_archetype": "military",
        "weights": (54, 8, 26, 12),
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


def _sync_template(template, new_rarity, new_archetype, new_profile, growth_range, weights):
    template.rarity = new_rarity
    template.archetype = new_archetype
    for field, value in zip(TEMPLATE_STAT_FIELDS, new_profile):
        setattr(template, field, value)
    template.growth_range = list(growth_range)
    template.attribute_weights = dict(zip(("force", "intellect", "defense", "agility"), weights))
    template.save(update_fields=["rarity", "archetype", *TEMPLATE_STAT_FIELDS, "growth_range", "attribute_weights"])


def promote_guests(apps, schema_editor):
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    Guest = apps.get_model("guests", "Guest")
    RecruitmentCandidate = apps.get_model("guests", "RecruitmentCandidate")
    database = schema_editor.connection.alias

    pending = []
    for promotion in PROMOTIONS:
        template = GuestTemplate.objects.using(database).select_for_update().filter(key=promotion["key"]).first()
        if template is None:
            # Fresh databases load the YAML roster after migrations.
            continue

        old_rarity = promotion["old_rarity"]
        new_rarity = promotion["new_rarity"]
        old_profile = ATTRIBUTE_PROFILES[(old_rarity, promotion["old_archetype"])]
        new_profile = ATTRIBUTE_PROFILES[(new_rarity, promotion["new_archetype"])]
        old_state = (old_rarity, promotion["old_archetype"], old_profile, promotion["old_growth"])
        new_state = (new_rarity, promotion["new_archetype"], new_profile, promotion["new_growth"])
        actual_state = (
            template.rarity,
            template.archetype,
            _template_stats(template),
            tuple(template.growth_range or ()),
        )
        if actual_state not in (old_state, new_state):
            raise ValueError(f"Unexpected template state for {template.key}: {actual_state!r}")

        pending.append((promotion, template, old_profile, new_profile))

    for promotion, template, old_profile, new_profile in pending:
        new_rarity = promotion["new_rarity"]
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
        _sync_template(
            template, new_rarity, promotion["new_archetype"], new_profile, promotion["new_growth"], promotion["weights"]
        )
        RecruitmentCandidate.objects.using(database).filter(template_id=template.pk).update(
            rarity=new_rarity, archetype=promotion["new_archetype"]
        )


def reverse_only_without_promoted_templates(apps, schema_editor):
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    database = schema_editor.connection.alias
    keys = [promotion["key"] for promotion in PROMOTIONS]
    if GuestTemplate.objects.using(database).filter(key__in=keys).exists():
        raise RuntimeError("Cannot reverse guest promotions while promoted templates exist")


class Migration(migrations.Migration):
    dependencies = [("guests", "0080_promote_wuzhao")]

    operations = [migrations.RunPython(promote_guests, reverse_only_without_promoted_templates)]
