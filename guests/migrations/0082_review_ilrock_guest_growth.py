from django.db import migrations

# Freeze reviewed allocations; migrations must not read mutable roster files.
WEIGHT_KEYS = ("force", "intellect", "defense", "agility")
# key, old archetype, new archetype, old weights, new weights
CHANGES = (
    ("hero_ilrock_04594", "civil", "civil", (15, 45, 15, 25), (8, 54, 14, 24)),
    ("hero_ilrock_04632", "civil", "civil", (15, 45, 15, 25), (8, 52, 24, 16)),
    ("hero_ilrock_04639", "civil", "civil", (15, 45, 15, 25), (8, 52, 18, 22)),
    ("hero_ilrock_04667", "civil", "civil", (15, 45, 15, 25), (8, 52, 24, 16)),
    ("hero_ilrock_04666", "military", "military", (40, 15, 25, 20), (56, 6, 28, 10)),
    ("hero_ilrock_04622", "military", "military", (40, 15, 25, 20), (48, 8, 28, 16)),
    ("hero_ilrock_04646", "military", "military", (40, 15, 25, 20), (46, 6, 20, 28)),
    ("hero_ilrock_04578", "military", "military", (40, 15, 25, 20), (52, 8, 22, 18)),
    ("hero_ilrock_04579", "military", "military", (40, 15, 25, 20), (56, 6, 24, 14)),
    ("hero_ilrock_04580", "military", "military", (40, 15, 25, 20), (44, 6, 38, 12)),
    ("hero_ilrock_04581", "military", "military", (40, 15, 25, 20), (46, 6, 22, 26)),
    ("hero_ilrock_04583", "military", "military", (40, 15, 25, 20), (54, 6, 24, 16)),
    ("hero_ilrock_04584", "military", "military", (40, 15, 25, 20), (54, 6, 24, 16)),
    ("hero_ilrock_04585", "military", "military", (40, 15, 25, 20), (48, 8, 30, 14)),
    ("hero_ilrock_04586", "military", "military", (40, 15, 25, 20), (58, 4, 28, 10)),
    ("hero_ilrock_04588", "military", "military", (40, 15, 25, 20), (50, 6, 22, 22)),
    ("hero_ilrock_04589", "military", "military", (40, 15, 25, 20), (54, 6, 26, 14)),
    ("hero_ilrock_04590", "military", "military", (40, 15, 25, 20), (52, 6, 24, 18)),
    ("hero_ilrock_04592", "civil", "civil", (15, 45, 15, 25), (8, 64, 12, 16)),
    ("hero_ilrock_04629", "military", "military", (40, 15, 25, 20), (48, 8, 30, 14)),
    ("hero_ilrock_04616", "military", "military", (40, 15, 25, 20), (50, 8, 26, 16)),
    ("hero_ilrock_04653", "military", "military", (40, 15, 25, 20), (46, 6, 20, 28)),
    ("hero_ilrock_04621", "military", "military", (40, 15, 25, 20), (48, 10, 22, 20)),
    ("hero_ilrock_04574", "military", "military", (40, 15, 25, 20), (42, 6, 16, 36)),
    ("hero_ilrock_04575", "military", "military", (40, 15, 25, 20), (54, 6, 28, 12)),
    ("hero_ilrock_04577", "military", "military", (40, 15, 25, 20), (52, 8, 28, 12)),
    ("hero_ilrock_04587", "military", "military", (40, 15, 25, 20), (56, 6, 24, 14)),
    ("hero_ilrock_04576", "military", "military", (40, 15, 25, 20), (52, 6, 22, 20)),
    ("hero_ilrock_03155", "civil", "civil", (15, 45, 15, 25), (8, 54, 14, 24)),
    ("hero_ilrock_03160", "military", "military", (40, 15, 25, 20), (52, 6, 28, 14)),
    ("hero_ilrock_03161", "military", "military", (40, 15, 25, 20), (48, 6, 20, 26)),
    ("hero_ilrock_03185", "military", "military", (40, 15, 25, 20), (50, 6, 22, 22)),
    ("hero_ilrock_03356", "civil", "civil", (15, 45, 15, 25), (8, 54, 14, 24)),
    ("hero_ilrock_03154", "civil", "civil", (15, 45, 15, 25), (8, 62, 16, 14)),
    ("hero_ilrock_03158", "military", "military", (40, 15, 25, 20), (54, 6, 24, 16)),
    ("hero_ilrock_03159", "military", "military", (40, 15, 25, 20), (46, 6, 20, 28)),
    ("hero_ilrock_03165", "civil", "civil", (15, 45, 15, 25), (8, 52, 24, 16)),
    ("hero_ilrock_03199", "civil", "civil", (15, 45, 15, 25), (10, 56, 18, 16)),
    ("hero_ilrock_03200", "military", "military", (40, 15, 25, 20), (48, 8, 28, 16)),
    ("hero_ilrock_03211", "military", "military", (40, 15, 25, 20), (46, 6, 20, 28)),
    ("hero_ilrock_03228", "civil", "civil", (15, 45, 15, 25), (8, 56, 20, 16)),
    ("hero_ilrock_03246", "civil", "civil", (15, 45, 15, 25), (8, 54, 14, 24)),
    ("hero_ilrock_03349", "military", "military", (40, 15, 25, 20), (46, 6, 20, 28)),
    ("hero_ilrock_03354", "civil", "civil", (15, 45, 15, 25), (10, 56, 18, 16)),
    ("hero_ilrock_03376", "military", "civil", (40, 15, 25, 20), (8, 60, 20, 12)),
    ("hero_ilrock_03384", "military", "military", (40, 15, 25, 20), (50, 6, 28, 16)),
    ("hero_ilrock_03867", "military", "military", (40, 15, 25, 20), (46, 6, 34, 14)),
    ("hero_ilrock_03877", "military", "military", (40, 15, 25, 20), (50, 6, 30, 14)),
    ("hero_ilrock_03878", "civil", "civil", (15, 45, 15, 25), (10, 56, 20, 14)),
    ("hero_ilrock_03879", "military", "military", (40, 15, 25, 20), (48, 6, 32, 14)),
    ("hero_ilrock_03880", "civil", "civil", (15, 45, 15, 25), (10, 54, 20, 16)),
    ("hero_ilrock_03899", "civil", "civil", (15, 45, 15, 25), (8, 54, 16, 22)),
    ("hero_ilrock_03900", "civil", "civil", (15, 45, 15, 25), (8, 56, 22, 14)),
    ("hero_ilrock_03901", "civil", "civil", (15, 45, 15, 25), (8, 56, 18, 18)),
    ("hero_ilrock_03902", "civil", "civil", (15, 45, 15, 25), (8, 60, 20, 12)),
    ("hero_ilrock_03974", "military", "military", (40, 15, 25, 20), (46, 6, 20, 28)),
    ("hero_ilrock_03975", "military", "military", (40, 15, 25, 20), (46, 6, 20, 28)),
    ("hero_ilrock_03962", "civil", "civil", (15, 45, 15, 25), (8, 54, 12, 26)),
    ("hero_ilrock_03994", "military", "military", (40, 15, 25, 20), (50, 8, 26, 16)),
    ("hero_ilrock_03995", "military", "military", (40, 15, 25, 20), (50, 6, 18, 26)),
    ("hero_ilrock_03996", "military", "military", (40, 15, 25, 20), (52, 6, 22, 20)),
    ("hero_ilrock_04000", "civil", "civil", (15, 45, 15, 25), (8, 60, 18, 14)),
    ("hero_ilrock_03860", "military", "military", (40, 15, 25, 20), (54, 6, 24, 16)),
    ("hero_ilrock_03865", "military", "military", (40, 15, 25, 20), (48, 8, 24, 20)),
    ("hero_ilrock_03868", "civil", "civil", (15, 45, 15, 25), (8, 62, 18, 12)),
    ("hero_ilrock_03987", "military", "military", (40, 15, 25, 20), (50, 8, 28, 14)),
    ("hero_ilrock_03993", "military", "military", (40, 15, 25, 20), (52, 6, 26, 16)),
    ("hero_ilrock_04003", "military", "military", (40, 15, 25, 20), (48, 8, 24, 20)),
    ("hero_ilrock_04234", "military", "military", (40, 15, 25, 20), (42, 6, 16, 36)),
    ("hero_ilrock_04241", "military", "military", (40, 15, 25, 20), (56, 6, 28, 10)),
    ("hero_ilrock_04243", "civil", "civil", (15, 45, 15, 25), (8, 62, 18, 12)),
    ("hero_ilrock_04242", "civil", "civil", (15, 45, 15, 25), (10, 56, 20, 14)),
    ("hero_ilrock_04279", "civil", "civil", (15, 45, 15, 25), (6, 60, 20, 14)),
    ("hero_ilrock_04244", "military", "military", (40, 15, 25, 20), (50, 8, 28, 14)),
    ("hero_ilrock_04245", "military", "military", (40, 15, 25, 20), (46, 6, 22, 26)),
    ("hero_ilrock_04239", "military", "military", (40, 15, 25, 20), (50, 8, 24, 18)),
    ("hero_ilrock_04230", "military", "military", (40, 15, 25, 20), (48, 10, 24, 18)),
    ("hero_ilrock_04231", "military", "military", (40, 15, 25, 20), (46, 6, 18, 30)),
    ("hero_ilrock_04232", "military", "military", (40, 15, 25, 20), (52, 6, 28, 14)),
    ("hero_ilrock_04233", "military", "military", (40, 15, 25, 20), (56, 6, 28, 10)),
    ("hero_ilrock_04844", "military", "military", (50, 10, 25, 15), (40, 6, 18, 36)),
    ("hero_ilrock_04848", "military", "military", (45, 15, 20, 20), (46, 6, 18, 30)),
    ("hero_ilrock_04849", "military", "military", (50, 10, 25, 15), (46, 6, 18, 30)),
    ("hero_ilrock_04851", "civil", "civil", (10, 48, 22, 20), (8, 60, 18, 14)),
    ("hero_ilrock_04852", "military", "military", (48, 12, 25, 15), (48, 8, 28, 16)),
    ("hero_ilrock_04853", "military", "military", (45, 15, 20, 20), (50, 8, 26, 16)),
    ("hero_ilrock_04855", "military", "military", (43, 12, 20, 25), (48, 8, 24, 20)),
    ("hero_ilrock_04860", "civil", "civil", (10, 48, 22, 20), (8, 60, 18, 14)),
    ("hero_ilrock_04861", "civil", "civil", (16, 46, 18, 20), (8, 58, 24, 10)),
    ("hero_ilrock_04865", "civil", "civil", (16, 46, 18, 20), (8, 60, 20, 12)),
    ("hero_ilrock_04866", "civil", "civil", (12, 52, 18, 18), (8, 62, 18, 12)),
    ("hero_ilrock_04869", "civil", "civil", (16, 46, 18, 20), (6, 58, 24, 12)),
    ("hero_ilrock_04870", "civil", "civil", (12, 52, 18, 18), (8, 58, 22, 12)),
    ("hero_ilrock_04871", "military", "military", (43, 12, 20, 25), (46, 6, 20, 28)),
    ("hero_ilrock_04873", "military", "military", (38, 16, 12, 34), (38, 4, 12, 46)),
    ("hero_ilrock_04874", "military", "civil", (45, 15, 20, 20), (8, 56, 18, 18)),
    ("hero_ilrock_04879", "civil", "civil", (15, 50, 20, 15), (8, 58, 22, 12)),
    ("hero_ilrock_04880", "military", "military", (50, 10, 25, 15), (42, 6, 18, 34)),
    ("hero_ilrock_04881", "military", "military", (43, 12, 20, 25), (42, 6, 18, 34)),
    ("hero_ilrock_04882", "civil", "civil", (12, 52, 18, 18), (8, 56, 24, 12)),
    ("hero_ilrock_04883", "civil", "civil", (15, 50, 20, 15), (8, 56, 18, 18)),
    ("hero_ilrock_04887", "civil", "civil", (15, 50, 20, 15), (12, 50, 28, 10)),
    ("hero_ilrock_04888", "civil", "civil", (10, 48, 22, 20), (10, 50, 26, 14)),
    ("hero_ilrock_04889", "civil", "civil", (16, 46, 18, 20), (8, 52, 14, 26)),
    ("hero_ilrock_04890", "military", "military", (50, 10, 25, 15), (46, 6, 20, 28)),
    ("hero_ilrock_04891", "military", "military", (43, 12, 20, 25), (46, 6, 20, 28)),
    ("hero_ilrock_04817", "civil", "civil", (12, 52, 18, 18), (8, 58, 16, 18)),
    ("hero_ilrock_04850", "military", "military", (38, 16, 12, 34), (42, 6, 16, 36)),
    ("hero_ilrock_04829", "military", "military", (50, 10, 25, 15), (50, 6, 18, 26)),
    ("hero_ilrock_04833", "military", "military", (45, 15, 20, 20), (52, 6, 20, 22)),
    ("hero_ilrock_04834", "military", "military", (38, 16, 12, 34), (36, 4, 10, 50)),
    ("hero_ilrock_04835", "military", "military", (43, 12, 20, 25), (48, 8, 26, 18)),
    ("hero_ilrock_04843", "military", "military", (45, 15, 20, 20), (46, 8, 24, 22)),
    ("hero_ilrock_04845", "military", "military", (43, 12, 20, 25), (44, 6, 18, 32)),
    ("hero_ilrock_04820", "military", "military", (43, 12, 20, 25), (50, 8, 26, 16)),
    ("hero_ilrock_04821", "military", "military", (46, 14, 25, 15), (52, 8, 26, 14)),
    ("hero_ilrock_04822", "civil", "civil", (15, 50, 20, 15), (8, 62, 18, 12)),
    ("hero_ilrock_04823", "military", "military", (45, 15, 20, 20), (54, 6, 24, 16)),
    ("hero_ilrock_11683", "civil", "civil", (20, 45, 20, 15), (10, 54, 22, 14)),
)

# Rarity conversion templates retain the source base profile, even when their
# rarity is higher. Only the two reviewed type changes need attribute rebasing.
TYPE_PROFILES = {
    "hero_ilrock_03376": ((51, 15, 44, 37, 50, 600), (36, 40, 19, 42, 120, 600)),
    "hero_ilrock_04874": ((41, 12, 35, 29, 47, 400), (29, 32, 15, 33, 112, 400)),
}
STAT_FIELDS = ("base_attack", "base_intellect", "base_defense", "base_agility", "base_luck", "base_hp")
ATTRIBUTES = ("force", "intellect", "defense_stat", "agility")
INITIALS = ("initial_force", "initial_intellect", "initial_defense", "initial_agility")
BATCH_SIZE = 500


def _sync_type_guests(Guest, database, template, old_profile, new_profile):
    guests = Guest.objects.using(database).select_for_update().filter(template_id=template.pk).order_by("pk")
    deltas = [new - old for old, new in zip(old_profile[:5], new_profile[:5])]
    last_pk = 0
    while batch := list(guests.filter(pk__gt=last_pk)[:BATCH_SIZE]):
        last_pk = batch[-1].pk
        for guest in batch:
            old_max = max(200, int(template.base_hp) + int(guest.hp_bonus) + int(guest.defense_stat) * 50)
            # Preserve every past roll, equipment bonus and player allocation.
            for index, (attribute, initial) in enumerate(zip(ATTRIBUTES, INITIALS)):
                setattr(guest, attribute, int(getattr(guest, attribute)) + deltas[index])
                setattr(guest, initial, int(getattr(guest, initial)) + deltas[index])
            guest.luck = int(guest.luck) + deltas[4]
            new_max = max(200, new_profile[5] + int(guest.hp_bonus) + int(guest.defense_stat) * 50)
            if guest.current_hp <= 0:
                guest.current_hp = 0
            elif guest.current_hp >= old_max:
                guest.current_hp = new_max
            else:
                guest.current_hp = max(1, min(new_max, int(guest.current_hp) * new_max // old_max))
        Guest.objects.using(database).bulk_update(
            batch, [*ATTRIBUTES, *INITIALS, "luck", "current_hp"], batch_size=BATCH_SIZE
        )


def apply_reviewed_growth(apps, schema_editor):
    Template = apps.get_model("guests", "GuestTemplate")
    Guest = apps.get_model("guests", "Guest")
    Candidate = apps.get_model("guests", "RecruitmentCandidate")
    PoolEntry = apps.get_model("guests", "RecruitmentPoolEntry")
    database = schema_editor.connection.alias
    configs = {row[0]: row for row in CHANGES}
    originals = list(Template.objects.using(database).select_for_update().filter(key__in=configs).order_by("pk"))
    if not originals:
        # Fresh databases load the reviewed YAML roster after migrations.
        return
    targets = [(template, configs[template.key], template.key) for template in originals]
    owners = {template.pk: (config, source_key) for template, config, source_key in targets}
    converted = list(
        Template.objects.using(database)
        .select_for_update()
        .filter(key__startswith="guest_rarity_conversion_")
        .order_by("pk")
    )
    # Upgrade pills may have produced descendants of the original template.
    # Keep their rarity/range and propagate the reviewed distribution and type.
    while True:
        matched = []
        for template in converted:
            parent = template.key.rsplit("_", 1)[-1]
            if parent.isdigit() and int(parent) in owners and template.pk not in owners:
                config, source_key = owners[int(parent)]
                matched.append((template, config, source_key))
        if not matched:
            break
        targets.extend(matched)
        owners.update({template.pk: (config, source_key) for template, config, source_key in matched})

    # Check the entire batch before writing any rows.
    for template, config, source_key in targets:
        _, old_type, new_type, old_weights, new_weights = config
        weights = tuple((template.attribute_weights or {}).get(key) for key in WEIGHT_KEYS)
        if template.archetype not in (old_type, new_type) or weights not in (old_weights, new_weights):
            raise ValueError(f"Unexpected growth configuration for {template.key}")
        if source_key in TYPE_PROFILES:
            profiles = TYPE_PROFILES[source_key]
            state = (template.archetype, tuple(int(getattr(template, field)) for field in STAT_FIELDS))
            if state not in ((old_type, profiles[0]), (new_type, profiles[1])):
                raise ValueError(f"Unexpected attribute profile for {template.key}")

    for template, config, source_key in targets:
        _, old_type, new_type, _, new_weights = config
        update_fields = ["attribute_weights"]
        if source_key in TYPE_PROFILES:
            old_profile, new_profile = TYPE_PROFILES[source_key]
            _sync_type_guests(Guest, database, template, old_profile, new_profile)
            for field, value in zip(STAT_FIELDS, new_profile):
                setattr(template, field, value)
            template.archetype = new_type
            update_fields.extend(["archetype", *STAT_FIELDS])
            Candidate.objects.using(database).filter(template_id=template.pk).update(archetype=new_type)
            PoolEntry.objects.using(database).filter(template_id=template.pk, archetype=old_type).update(
                archetype=new_type
            )
        template.attribute_weights = dict(zip(WEIGHT_KEYS, new_weights))
        template.save(using=database, update_fields=update_fields)


def reverse_without_existing_guests(apps, schema_editor):
    Template = apps.get_model("guests", "GuestTemplate")
    database = schema_editor.connection.alias
    if Template.objects.using(database).filter(key__in=[row[0] for row in CHANGES]).exists():
        raise RuntimeError("Cannot reverse growth review after recruitment or player allocations")


class Migration(migrations.Migration):
    dependencies = [("guests", "0081_promote_sunwu_xiangyu")]
    operations = [migrations.RunPython(apply_reviewed_growth, reverse_without_existing_guests)]
