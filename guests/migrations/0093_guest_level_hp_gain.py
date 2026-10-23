"""等级生命成长生效时，已有门客保留原有生命比例。"""

from django.db import migrations

LEVEL_HP_GAIN = 60
BATCH_SIZE = 500


def _rescale_guest_hp(apps, schema_editor, *, reverse=False):
    Guest = apps.get_model("guests", "Guest")
    database = schema_editor.connection.alias
    guests = (
        Guest.objects.using(database)
        .select_for_update()
        .select_related("template")
        .filter(level__gt=1, current_hp__gt=0)
        .only("id", "level", "current_hp", "hp_bonus", "defense_stat", "template__base_hp")
        .order_by("id")
    )
    pending = []
    for guest in guests.iterator(chunk_size=BATCH_SIZE):
        base_max_hp = max(200, int(guest.template.base_hp) + int(guest.hp_bonus) + int(guest.defense_stat) * 50)
        level_hp = (int(guest.level) - 1) * LEVEL_HP_GAIN
        old_max_hp, new_max_hp = base_max_hp, base_max_hp + level_hp
        if reverse:
            old_max_hp, new_max_hp = new_max_hp, old_max_hp
        current_hp = min(int(guest.current_hp), old_max_hp)
        guest.current_hp = max(1, current_hp * new_max_hp // old_max_hp)
        pending.append(guest)
        if len(pending) >= BATCH_SIZE:
            Guest.objects.using(database).bulk_update(pending, ["current_hp"], batch_size=BATCH_SIZE)
            pending.clear()
    if pending:
        Guest.objects.using(database).bulk_update(pending, ["current_hp"], batch_size=BATCH_SIZE)


def add_level_hp(apps, schema_editor):
    _rescale_guest_hp(apps, schema_editor)


def remove_level_hp(apps, schema_editor):
    _rescale_guest_hp(apps, schema_editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = [("guests", "0092_guest_rarity_pill_used")]

    operations = [migrations.RunPython(add_level_hp, remove_level_hp)]
