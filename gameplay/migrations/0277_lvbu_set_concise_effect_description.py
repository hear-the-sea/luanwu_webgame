"""精简吕布套装 4 件套战斗效果描述，保持各端展示一致且简洁可读。"""

from __future__ import annotations

from copy import deepcopy
from importlib import import_module

from django.db import migrations

from guests.services.equipment_payloads import build_gear_template_defaults
from guests.utils.equipment_utils import EQUIP_SLOT_MAP

LVBU_SET_KEYS = (
    "equip_shufazijinkuan",
    "equip_longlinzhanjia",
    "equip_lvbuzhanxue",
    "equip_fangtianhuaji",
    "equip_chituma",
)
CONCISE_EFFECT_DESCRIPTION = "近战受击有40%概率以6.5倍武力反击"


def _with_concise_description(raw_payload: object) -> dict[str, object] | None:
    if not isinstance(raw_payload, dict):
        return None
    set_bonus = raw_payload.get("set_bonus")
    if not isinstance(set_bonus, dict):
        return None
    if set_bonus.get("effect_description") == CONCISE_EFFECT_DESCRIPTION:
        return None
    updated = deepcopy(raw_payload)
    updated_bonus = updated.get("set_bonus")
    if isinstance(updated_bonus, dict):
        updated_bonus["effect_description"] = CONCISE_EFFECT_DESCRIPTION
    return updated


EQUIPMENT_PAYLOADS: dict[str, dict[str, object]] = {
    key: _with_concise_description(payload) or deepcopy(payload)
    for key, payload in import_module("gameplay.migrations.0276_lvbu_equipment_set").EQUIPMENT_PAYLOADS.items()
}


def apply_concise_lvbu_effect_description(apps, schema_editor) -> None:
    database = schema_editor.connection.alias
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    GearTemplate = apps.get_model("guests", "GearTemplate")

    base_items = ItemTemplate.objects.using(database).filter(key__in=LVBU_SET_KEYS).order_by("pk")
    affix_items = (
        ItemTemplate.objects.using(database)
        .filter(effect_type__startswith="equip_", effect_payload__equipment_base_key__in=list(LVBU_SET_KEYS))
        .order_by("pk")
    )
    for item in (*base_items, *affix_items):
        updated_payload = _with_concise_description(item.effect_payload)
        if updated_payload is not None:
            item.effect_payload = updated_payload
            item.save(using=database, update_fields=["effect_payload"])
        slot = EQUIP_SLOT_MAP.get(item.effect_type)
        if slot:
            defaults = build_gear_template_defaults(item, slot=slot)
            GearTemplate.objects.using(database).update_or_create(key=item.key, defaults=defaults)


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0276_lvbu_equipment_set"),
    ]

    operations = [
        migrations.RunPython(apply_concise_lvbu_effect_description, migrations.RunPython.noop, atomic=True),
    ]
