from django.db import migrations

MIN_HP_FLOOR = 200
DEFENSE_TO_HP_MULTIPLIER = 50
BATCH_SIZE = 500


def _move_set_defense(apps, schema_editor, *, direction):
    Guest = apps.get_model("guests", "Guest")
    database = schema_editor.connection.alias
    guests = (
        Guest.objects.using(database)
        .select_related("template")
        .only(
            "id",
            "template__base_hp",
            "gear_set_bonus",
            "defense_stat",
            "defense_bonus",
            "hp_bonus",
            "current_hp",
        )
        .order_by("pk")
    )
    last_pk = 0
    while batch := list(guests.filter(pk__gt=last_pk)[:BATCH_SIZE]):
        last_pk = batch[-1].pk
        pending = []
        for guest in batch:
            active_bonus = guest.gear_set_bonus or {}
            if not isinstance(active_bonus, dict):
                raise ValueError(f"invalid gear_set_bonus for guest {guest.pk}")
            raw_defense = active_bonus.get("defense", 0)
            if isinstance(raw_defense, bool):
                raise ValueError(f"invalid set defense for guest {guest.pk}")
            defense = int(raw_defense or 0)
            if not defense:
                continue

            old_defense_stat = int(guest.defense_stat)
            old_max_hp = max(
                MIN_HP_FLOOR,
                int(guest.template.base_hp) + int(guest.hp_bonus) + old_defense_stat * DEFENSE_TO_HP_MULTIPLIER,
            )
            guest.defense_stat = old_defense_stat + direction * defense
            if guest.defense_stat < 0:
                raise ValueError(f"negative defense_stat for guest {guest.pk}")
            guest.defense_bonus = int(guest.defense_bonus) - direction * defense
            new_max_hp = max(
                MIN_HP_FLOOR,
                int(guest.template.base_hp) + int(guest.hp_bonus) + int(guest.defense_stat) * DEFENSE_TO_HP_MULTIPLIER,
            )
            if guest.current_hp > 0:
                missing_hp = max(0, old_max_hp - int(guest.current_hp))
                guest.current_hp = max(1, new_max_hp - missing_hp)
            pending.append(guest)

        if pending:
            Guest.objects.using(database).bulk_update(
                pending, ["defense_stat", "defense_bonus", "current_hp"], batch_size=BATCH_SIZE
            )


def forwards(apps, schema_editor):
    _move_set_defense(apps, schema_editor, direction=1)


def backwards(apps, schema_editor):
    _move_set_defense(apps, schema_editor, direction=-1)


class Migration(migrations.Migration):
    dependencies = [
        ("guests", "0075_guest_avatar_asset_key"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
