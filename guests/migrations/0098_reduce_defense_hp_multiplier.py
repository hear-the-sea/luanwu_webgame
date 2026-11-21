"""防御生命系数由 50 降至 30 时，已有门客保留原有生命比例。"""

from django.db import migrations

OLD_DEFENSE_TO_HP = 50
NEW_DEFENSE_TO_HP = 30
LEVEL_HP_GAIN = 60
HP_FLOOR = 200
BATCH_SIZE = 500


def _rescale_guest_hp(apps, schema_editor, *, reverse=False):
    Guest = apps.get_model("guests", "Guest")
    database = schema_editor.connection.alias
    old_multiplier = NEW_DEFENSE_TO_HP if reverse else OLD_DEFENSE_TO_HP
    new_multiplier = OLD_DEFENSE_TO_HP if reverse else NEW_DEFENSE_TO_HP
    guests = (
        Guest.objects.using(database)
        .select_for_update()
        .select_related("template")
        .filter(current_hp__gt=0)
        .only("id", "level", "current_hp", "hp_bonus", "defense_stat", "template__base_hp")
        .order_by("id")
    )
    pending = []
    for guest in guests.iterator(chunk_size=BATCH_SIZE):
        base_hp = int(guest.template.base_hp) + int(guest.hp_bonus)
        level_hp = max(0, int(guest.level) - 1) * LEVEL_HP_GAIN
        old_max_hp = max(HP_FLOOR, base_hp + int(guest.defense_stat) * old_multiplier) + level_hp
        new_max_hp = max(HP_FLOOR, base_hp + int(guest.defense_stat) * new_multiplier) + level_hp
        current_hp = min(int(guest.current_hp), old_max_hp)
        guest.current_hp = max(1, current_hp * new_max_hp // old_max_hp)
        pending.append(guest)
        if len(pending) >= BATCH_SIZE:
            Guest.objects.using(database).bulk_update(pending, ["current_hp"], batch_size=BATCH_SIZE)
            pending.clear()
    if pending:
        Guest.objects.using(database).bulk_update(pending, ["current_hp"], batch_size=BATCH_SIZE)


def reduce_defense_hp(apps, schema_editor):
    _rescale_guest_hp(apps, schema_editor)


def restore_defense_hp(apps, schema_editor):
    _rescale_guest_hp(apps, schema_editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = [("guests", "0097_merge_duplicate_guest_templates")]

    operations = [migrations.RunPython(reduce_defense_hp, restore_defense_hp)]
