"""新增吕布套装（5选4：束发紫金冠、吕布战靴及原三件名器），并加入虎牢关与白门楼宝箱掉落。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import migrations

from core.utils.image_utils import compress_and_resize_image
from guests.services.equipment_payloads import build_gear_template_defaults
from guests.utils.equipment_utils import EQUIP_SLOT_MAP

LVBU_SET_BONUS = {
    "pieces": 4,
    "bonus": {},
    "combat_effects": [
        {
            "type": "set_counter",
            "chance": 0.4,
            "damage_multiplier": 6.5,
            "scale_stat": "force",
            "use_defense": True,
            "melee_only": True,
            "max_per_action": 1,
            "log_name": "无双反击",
        }
    ],
    "effect_description": "近战受击有40%概率以6.5倍武力反击（过防御减伤，每次敌方行动至多1次）",
}


def _lvbu_set_meta() -> dict[str, object]:
    return {
        "set_key": "lvbu_set",
        "set_description": "吕布套装",
        "set_bonus": deepcopy(LVBU_SET_BONUS),
    }


EQUIPMENT_PAYLOADS = {
    "equip_shufazijinkuan": {
        "hp": 300,
        "defense": 18,
        "force": 44,
        "agility": 12,
        **_lvbu_set_meta(),
        "required_level": 75,
    },
    "equip_longlinzhanjia": {
        "hp": 520,
        "defense": 32,
        "force": 34,
        "agility": 14,
        "troop_capacity": 26,
        **_lvbu_set_meta(),
        "required_level": 75,
    },
    "equip_lvbuzhanxue": {
        "hp": 260,
        "defense": 18,
        "force": 42,
        "agility": 16,
        **_lvbu_set_meta(),
        "required_level": 75,
    },
    "equip_fangtianhuaji": {
        "hp": 240,
        "defense": 10,
        "force": 55,
        "agility": 8,
        "troop_capacity": 70,
        **_lvbu_set_meta(),
        "required_level": 75,
    },
    "equip_chituma": {
        "hp": 340,
        "defense": 18,
        "force": 38,
        "agility": 24,
        "troop_capacity": 36,
        **_lvbu_set_meta(),
        "required_level": 80,
    },
}

NEW_EQUIPMENT_DEFAULTS = {
    "equip_shufazijinkuan": {
        "name": "束发紫金冠",
        "description": "三叉紫金冠配双尾雉鸡翎，昔年温侯驰骋虎牢关时所戴宝冠，威仪与锐气兼具。",
        "effect_type": "equip_helmet",
        "rarity": "orange",
        "tradeable": True,
        "default_bound": False,
        "price": 48000,
        "storage_space": 200,
        "is_usable": False,
        "image": "items/lvbu/equip_shufazijinkuan.webp",
        "effect_payload": EQUIPMENT_PAYLOADS["equip_shufazijinkuan"],
    },
    "equip_lvbuzhanxue": {
        "name": "吕布战靴",
        "description": "乌金龙鳞护胫配上翘云纹靴头，以红丝络束紧，步履迅疾沉稳，专为冲阵斗将所制。",
        "effect_type": "equip_shoes",
        "rarity": "orange",
        "tradeable": True,
        "default_bound": False,
        "price": 48000,
        "storage_space": 200,
        "is_usable": False,
        "image": "items/lvbu/equip_lvbuzhanxue.webp",
        "effect_payload": EQUIPMENT_PAYLOADS["equip_lvbuzhanxue"],
    },
}

IMAGE_SOURCES = {
    "items/lvbu/equip_shufazijinkuan.webp": "items/equip_shufazijinkuan.png",
    "items/lvbu/equip_lvbuzhanxue.webp": "items/equip_lvbuzhanxue.png",
}

CHEST_NEW_DROP_GROUPS = {
    "mission_chest_hulao_qunying_zhan_lvbu": {
        "label": "飞将冠靴",
        "chance": 0.06,
        "min_quantity": 1,
        "max_quantity": 1,
        "choices": [
            {"item_key": "equip_shufazijinkuan", "weight": 1},
            {"item_key": "equip_lvbuzhanxue", "weight": 1},
        ],
    },
    "mission_chest_v3_hulao_qunying_zhan_lvbu": {
        "label": "飞将冠靴",
        "chance": 0.06,
        "min_quantity": 1,
        "max_quantity": 1,
        "choices": [
            {"item_key": "equip_shufazijinkuan", "weight": 1},
            {"item_key": "equip_lvbuzhanxue", "weight": 1},
        ],
    },
    "mission_chest_baimenlou_mingyun_jueze": {
        "label": "飞将冠靴",
        "chance": 0.08,
        "min_quantity": 1,
        "max_quantity": 1,
        "choices": [
            {"item_key": "equip_shufazijinkuan", "weight": 1},
            {"item_key": "equip_lvbuzhanxue", "weight": 1},
        ],
    },
    "mission_chest_v3_baimenlou_mingyun_jueze": {
        "label": "飞将冠靴",
        "chance": 0.08,
        "min_quantity": 1,
        "max_quantity": 1,
        "choices": [
            {"item_key": "equip_shufazijinkuan", "weight": 1},
            {"item_key": "equip_lvbuzhanxue", "weight": 1},
        ],
    },
}


def _resolve_stored_image(target: str, relative_source: str) -> str:
    if default_storage.exists(target):
        return target
    source = Path(settings.BASE_DIR) / "data" / "images" / relative_source
    if not source.is_file():
        return target
    content, _ = compress_and_resize_image(source, max_size=(200, 200), quality=85, convert_to_webp=True)
    return default_storage.save(target, content)


def _sync_gear_template(GearTemplate, database: str, item_template) -> None:
    slot = EQUIP_SLOT_MAP.get(item_template.effect_type)
    if not slot:
        return
    defaults = build_gear_template_defaults(item_template, slot=slot)
    GearTemplate.objects.using(database).update_or_create(key=item_template.key, defaults=defaults)


def apply_lvbu_equipment_set(apps, schema_editor) -> None:
    database = schema_editor.connection.alias
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    GearTemplate = apps.get_model("guests", "GearTemplate")

    stored_images = {
        target: _resolve_stored_image(target, relative_source) for target, relative_source in IMAGE_SOURCES.items()
    }

    for key, raw_defaults in NEW_EQUIPMENT_DEFAULTS.items():
        defaults = deepcopy(raw_defaults)
        defaults["image"] = stored_images[str(defaults["image"])]
        item, _ = ItemTemplate.objects.using(database).update_or_create(key=key, defaults=defaults)
        _sync_gear_template(GearTemplate, database, item)

    set_meta = _lvbu_set_meta()
    existing_keys = ("equip_fangtianhuaji", "equip_longlinzhanjia", "equip_chituma")
    for item in ItemTemplate.objects.using(database).filter(key__in=existing_keys).order_by("pk"):
        payload = deepcopy(EQUIPMENT_PAYLOADS[item.key])
        if item.effect_payload != payload:
            item.effect_payload = payload
            item.save(using=database, update_fields=["effect_payload"])
        _sync_gear_template(GearTemplate, database, item)

    # 同步已存在的词缀变体（保留四维词缀数值，只注入套装归属与效果）。
    affix_variants = list(
        ItemTemplate.objects.using(database)
        .filter(effect_type__startswith="equip_", effect_payload__equipment_base_key__in=list(EQUIPMENT_PAYLOADS))
        .order_by("pk")
    )
    for variant in affix_variants:
        payload = deepcopy(variant.effect_payload or {})
        updated_payload = {**payload, **deepcopy(set_meta)}
        if variant.effect_payload != updated_payload:
            variant.effect_payload = updated_payload
            variant.save(using=database, update_fields=["effect_payload"])
        _sync_gear_template(GearTemplate, database, variant)

    for chest_key, group_spec in CHEST_NEW_DROP_GROUPS.items():
        chest = ItemTemplate.objects.using(database).filter(key=chest_key).first()
        if chest is None:
            continue
        payload = deepcopy(chest.effect_payload or {})
        raw_groups = payload.get("random_item_groups")
        groups: list[object] = list(raw_groups) if isinstance(raw_groups, list) else []
        target_group = deepcopy(group_spec)
        replaced = False
        for idx, entry in enumerate(groups):
            if isinstance(entry, dict) and entry.get("label") == target_group["label"]:
                groups[idx] = target_group
                replaced = True
                break
        if not replaced:
            groups.append(target_group)
        payload["random_item_groups"] = groups
        if chest.effect_payload != payload:
            chest.effect_payload = payload
            chest.save(using=database, update_fields=["effect_payload"])


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0275_equipment_balance_calibration"),
        ("guests", "0098_reduce_defense_hp_multiplier"),
    ]

    operations = [
        migrations.RunPython(apply_lvbu_equipment_set, migrations.RunPython.noop, atomic=True),
    ]
