"""Align the crane attendant migration snapshot with its approved template profile."""

from django.db import migrations

GUEST_DEFAULT_OVERRIDES = {
    "task_legend_crane_attendant": {"base_hp": 80000},
    "task_legend_deer_attendant": {"base_hp": 62000},
    "task_legend_crab_general": {"base_hp": 128000},
    "task_legend_shrimp_vanguard": {"base_hp": 122000},
    "task_legend_east_sea_dragon": {"base_hp": 300000},
    "task_legend_meishan_guard": {"base_hp": 360000},
    "task_legend_celestial_hound": {"base_hp": 280000},
    "task_legend_erlang_shen": {"base_hp": 400000},
}
ITEM_STORAGE_OVERRIDES = {"blueprint_chiyoujia": {"storage_space": 200}}


def align_guest_profiles(apps, schema_editor):
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    database = schema_editor.connection.alias
    for key, overrides in GUEST_DEFAULT_OVERRIDES.items():
        GuestTemplate.objects.using(database).filter(key=key).update(**overrides)
    for key, overrides in ITEM_STORAGE_OVERRIDES.items():
        ItemTemplate.objects.using(database).filter(key=key).update(**overrides)


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0228_mission_enemy_troops_display_label")]

    operations = [migrations.RunPython(align_guest_profiles, migrations.RunPython.noop)]
