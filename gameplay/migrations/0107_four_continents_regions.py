from __future__ import annotations

import random
from collections import Counter

from django.db import migrations

COORDINATE_MIN = 1
COORDINATE_MAX = 999

CANONICAL_REGION_CODES = {
    "north",
    "east",
    "west",
    "south",
    "overseas",
}

LEGACY_REGION_TO_NEW_REGION = {
    "beijing": "north",
    "tianjin": "north",
    "hebei": "north",
    "shanxi": "north",
    "neimenggu": "north",
    "liaoning": "north",
    "jilin": "north",
    "heilongjiang": "north",
    "gansu": "north",
    "qinghai": "north",
    "ningxia": "north",
    "xinjiang": "north",
    "shanghai": "east",
    "jiangsu": "east",
    "zhejiang": "east",
    "anhui": "east",
    "fujian": "east",
    "jiangxi": "east",
    "shandong": "east",
    "taiwan": "east",
    "shaanxi": "west",
    "sichuan": "west",
    "guizhou": "west",
    "yunnan": "west",
    "xizang": "west",
    "chongqing": "south",
    "henan": "south",
    "hubei": "south",
    "hunan": "south",
    "guangdong": "south",
    "guangxi": "south",
    "hainan": "south",
    "hongkong": "south",
    "macao": "south",
    "overseas": "overseas",
}


def resolve_target_region(region: str) -> str:
    if region in CANONICAL_REGION_CODES:
        return region
    try:
        return LEGACY_REGION_TO_NEW_REGION[region]
    except KeyError as exc:
        raise RuntimeError(f"unknown legacy region: {region}") from exc


def build_target_slot(region: str, coordinate_x: int, coordinate_y: int) -> tuple[str, int, int] | None:
    if coordinate_x <= 0 or coordinate_y <= 0:
        return None
    return (region, coordinate_x, coordinate_y)


def generate_unique_coordinate(
    region: str,
    occupied_locations: set[tuple[str, int, int]] | None = None,
) -> tuple[int, int]:
    occupied_locations = occupied_locations or set()

    for _ in range(100):
        x = random.randint(COORDINATE_MIN, COORDINATE_MAX)
        y = random.randint(COORDINATE_MIN, COORDINATE_MAX)
        if (region, x, y) not in occupied_locations:
            return x, y

    raise RuntimeError(f"unable to generate unique coordinate for {region}")


def _get_manor_model(apps):
    if apps is None:
        from gameplay.models import Manor

        return Manor
    return apps.get_model("gameplay", "Manor")


def remap_manor_regions(apps, schema_editor) -> None:
    Manor = _get_manor_model(apps)
    manors = list(Manor.objects.order_by("id"))

    future_slots: Counter[tuple[str, int, int]] = Counter()
    target_regions: dict[int, str] = {}
    for manor in manors:
        target_region = resolve_target_region(str(manor.region))
        target_regions[int(manor.id)] = target_region
        target_slot = build_target_slot(target_region, int(manor.coordinate_x), int(manor.coordinate_y))
        if target_slot is not None:
            future_slots[target_slot] += 1

    occupied_locations: set[tuple[str, int, int]] = set()

    for manor in manors:
        target_region = target_regions[int(manor.id)]
        target_slot = build_target_slot(target_region, int(manor.coordinate_x), int(manor.coordinate_y))

        if target_slot is not None:
            remaining_count = future_slots[target_slot] - 1
            if remaining_count > 0:
                future_slots[target_slot] = remaining_count
            else:
                future_slots.pop(target_slot, None)

        new_x = int(manor.coordinate_x)
        new_y = int(manor.coordinate_y)
        final_slot = target_slot

        if target_slot is not None and target_slot in occupied_locations:
            blocked_locations = occupied_locations | set(future_slots.keys())
            new_x, new_y = generate_unique_coordinate(target_region, occupied_locations=blocked_locations)
            final_slot = (target_region, new_x, new_y)

        update_fields: list[str] = []
        if manor.region != target_region:
            manor.region = target_region
            update_fields.append("region")
        if new_x != manor.coordinate_x:
            manor.coordinate_x = new_x
            update_fields.append("coordinate_x")
        if new_y != manor.coordinate_y:
            manor.coordinate_y = new_y
            update_fields.append("coordinate_y")
        if update_fields:
            manor.save(update_fields=update_fields)

        if final_slot is not None:
            occupied_locations.add(final_slot)


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0106_arena_coop_event_and_manor_counter"),
    ]

    operations = [
        migrations.RunPython(remap_manor_regions, migrations.RunPython.noop),
    ]
