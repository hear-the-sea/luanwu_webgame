"""防御生命系数调为 40 且按门客文/武类型区分每级生命成长（武门客 70/级，文门客 50/级）时，已有门客保留原有生命比例。"""

from django.db import migrations

OLD_DEFENSE_TO_HP = 30
NEW_DEFENSE_TO_HP = 40
OLD_LEVEL_HP_GAIN = 60
NEW_MILITARY_LEVEL_HP_GAIN = 70
NEW_CIVIL_LEVEL_HP_GAIN = 50
HP_FLOOR = 200
BATCH_SIZE = 500


def _level_hp_gain(archetype: str, *, reverse: bool) -> int:
    if reverse:
        return OLD_LEVEL_HP_GAIN
    if archetype == "civil":
        return NEW_CIVIL_LEVEL_HP_GAIN
    if archetype == "military":
        return NEW_MILITARY_LEVEL_HP_GAIN
    return OLD_LEVEL_HP_GAIN


def _rescale_guest_hp(apps, schema_editor, *, reverse: bool = False) -> None:
    Guest = apps.get_model("guests", "Guest")
    database = schema_editor.connection.alias
    old_def_mult = NEW_DEFENSE_TO_HP if reverse else OLD_DEFENSE_TO_HP
    new_def_mult = OLD_DEFENSE_TO_HP if reverse else NEW_DEFENSE_TO_HP
    guests = (
        Guest.objects.using(database)
        .select_for_update()
        .select_related("template")
        .filter(current_hp__gt=0)
        .only("id", "level", "current_hp", "hp_bonus", "defense_stat", "template__base_hp", "template__archetype")
        .order_by("id")
    )
    pending = []
    for guest in guests.iterator(chunk_size=BATCH_SIZE):
        archetype = str(getattr(guest.template, "archetype", "") or "")
        old_gain = _level_hp_gain(archetype, reverse=not reverse)
        new_gain = _level_hp_gain(archetype, reverse=reverse)
        levels_gained = max(0, int(guest.level) - 1)
        base_hp = int(guest.template.base_hp) + int(guest.hp_bonus)
        old_max_hp = max(HP_FLOOR, base_hp + int(guest.defense_stat) * old_def_mult) + levels_gained * old_gain
        new_max_hp = max(HP_FLOOR, base_hp + int(guest.defense_stat) * new_def_mult) + levels_gained * new_gain
        if old_max_hp == new_max_hp:
            continue
        current_hp = min(int(guest.current_hp), old_max_hp)
        guest.current_hp = max(1, current_hp * new_max_hp // old_max_hp)
        pending.append(guest)
        if len(pending) >= BATCH_SIZE:
            Guest.objects.using(database).bulk_update(pending, ["current_hp"], batch_size=BATCH_SIZE)
            pending.clear()
    if pending:
        Guest.objects.using(database).bulk_update(pending, ["current_hp"], batch_size=BATCH_SIZE)


def apply_archetype_level_hp_gain(apps, schema_editor) -> None:
    _rescale_guest_hp(apps, schema_editor, reverse=False)


def revert_archetype_level_hp_gain(apps, schema_editor) -> None:
    _rescale_guest_hp(apps, schema_editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = [("guests", "0100_merge_liuxiahui_into_zhanhuo")]

    operations = [migrations.RunPython(apply_archetype_level_hp_gain, revert_archetype_level_hp_gain)]
