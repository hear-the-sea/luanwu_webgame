"""精准回调 0275 中过削的紫/橙核心装备并消除跨品质战力倒挂（不降任何现有单项属性）。"""

from __future__ import annotations

from copy import deepcopy
from importlib import import_module
from typing import Any

from django.db import migrations

from core.config import GUEST

_CALIBRATION_275: Any = import_module("gameplay.migrations.0275_equipment_balance_calibration")
STAT_FIELDS: dict[str, str] = dict(_CALIBRATION_275.STAT_FIELDS)
AFFIXES: tuple[str, ...] = tuple(_CALIBRATION_275.AFFIXES)


def _with_set_meta(base_key: str, stats: dict[str, Any]) -> dict[str, Any]:
    source = _CALIBRATION_275.EQUIPMENT_PAYLOADS[base_key]
    required_level = stats.get("required_level")
    merged: dict[str, Any] = {
        **{k: v for k, v in stats.items() if k != "required_level"},
        "set_key": source["set_key"],
        "set_description": source["set_description"],
        "set_bonus": deepcopy(source["set_bonus"]),
    }
    if required_level is not None:
        merged["required_level"] = required_level
    return merged


EQUIPMENT_PAYLOADS: dict[str, dict[str, Any]] = {
    # 橙装回调
    "equip_shandianqiulongju": {
        "hp": 300,
        "defense": 12,
        "force": 28,
        "agility": 38,
        "luck": 6,
        "required_level": 80,
    },
    "equip_qiankunshengong": {
        "hp": 180,
        "defense": 12,
        "force": 58,
        "agility": 26,
        "required_level": 75,
    },
    "equip_xiaolifeidao": {
        "hp": 120,
        "force": 38,
        "agility": 34,
        "luck": 16,
        "required_level": 75,
    },
    "equip_kongquelin": {
        "hp": 160,
        "force": 18,
        "intellect": 56,
        "agility": 22,
        "luck": 18,
        "required_level": 80,
    },
    "equip_heshibi": {
        "hp": 460,
        "defense": 28,
        "intellect": 30,
        "luck": 28,
        "required_level": 65,
    },
    # 紫装武器回调
    "equip_xiuhuazhen": {"hp": 60, "force": 26, "agility": 24, "luck": 12, "required_level": 55},
    "equip_jinshezui": {"hp": 100, "force": 32, "intellect": 30, "agility": 20, "luck": 8, "required_level": 55},
    "equip_ziweiruanjian": {"hp": 120, "force": 40, "intellect": 16, "agility": 22, "required_level": 55},
    "equip_yuanyangdao": {
        "hp": 260,
        "force": 40,
        "intellect": 24,
        "agility": 8,
        "luck": 18,
        "required_level": 60,
    },
    "equip_duomingfeidao": {"hp": 80, "force": 40, "agility": 18, "luck": 12, "required_level": 55},
    "equip_jinlun": {"hp": 280, "defense": 14, "force": 54, "intellect": 24, "agility": 8, "required_level": 60},
    "equip_qixingjian": {
        "hp": 220,
        "defense": 14,
        "force": 20,
        "intellect": 48,
        "agility": 8,
        "luck": 10,
        "required_level": 45,
    },
    "equip_duoqinghuan": {
        "hp": 240,
        "defense": 16,
        "force": 40,
        "intellect": 36,
        "agility": 6,
        "luck": 8,
        "required_level": 45,
    },
    "equip_yuxiao": {"hp": 180, "intellect": 54, "agility": 16, "luck": 10, "required_level": 60},
    "equip_xuanwuchui": _with_set_meta(
        "equip_xuanwuchui", {"hp": 420, "defense": 30, "force": 46, "required_level": 55}
    ),
    "equip_fenglingshan": _with_set_meta(
        "equip_fenglingshan", {"hp": 200, "defense": 10, "intellect": 58, "luck": 18, "required_level": 55}
    ),
    "equip_xuedao": {"hp": 320, "force": 60, "agility": 12, "required_level": 50},
    "equip_huxianjian": _with_set_meta(
        "equip_huxianjian",
        {"hp": 240, "defense": 8, "force": 36, "intellect": 54, "agility": 10, "required_level": 50},
    ),
    "equip_shuanghuagong": {"hp": 100, "defense": 10, "force": 40, "agility": 20, "required_level": 50},
    # 紫装坐骑回调
    "equip_jueying": {"hp": 260, "defense": 12, "force": 8, "agility": 26, "luck": 12, "required_level": 45},
    "equip_zhaohuangfeidian": {
        "hp": 260,
        "defense": 12,
        "force": 18,
        "agility": 27,
        "luck": 6,
        "required_level": 50,
    },
    "equip_wuzhuima": {"hp": 340, "defense": 18, "force": 38, "agility": 16, "required_level": 45},
    # 紫装头盔与鞋子回调
    "equip_fenglingguan": _with_set_meta(
        "equip_fenglingguan", {"hp": 360, "defense": 22, "intellect": 34, "luck": 12, "required_level": 55}
    ),
    "equip_pojunkui": {
        "hp": 320,
        "defense": 24,
        "force": 26,
        "agility": 11,
        "troop_capacity": 14,
        "required_level": 55,
    },
    "equip_xuanwuxue": _with_set_meta(
        "equip_xuanwuxue", {"hp": 400, "defense": 32, "agility": 20, "required_level": 55}
    ),
    # 紫装护甲回调
    "equip_tianquankaijia": {"hp": 680, "defense": 48, "luck": 6, "required_level": 55},
    "equip_tianshukaijia": {"hp": 680, "defense": 36, "force": 14, "luck": 4, "required_level": 55},
    "equip_tianjikaijia": {"hp": 480, "defense": 26, "intellect": 38, "agility": 6, "required_level": 55},
    "equip_tianxuankaijia": {"hp": 400, "defense": 24, "force": 44, "agility": 4, "required_level": 55},
    "equip_xuanwujia": _with_set_meta(
        "equip_xuanwujia", {"hp": 580, "defense": 40, "intellect": 22, "required_level": 55}
    ),
    # 紫装饰品回调
    "equip_pojunhufu": {"hp": 260, "force": 26, "agility": 12, "troop_capacity": 58, "required_level": 55},
    "equip_shenghuoling": {"hp": 260, "intellect": 28, "luck": 16, "troop_capacity": 58, "required_level": 50},
    "equip_xuanmingdeng": {"hp": 200, "intellect": 50, "agility": 6, "luck": 12, "required_level": 55},
    "equip_xuanwuyufu": {"hp": 500, "defense": 36, "intellect": 16, "luck": 6, "required_level": 60},
    "equip_zijinhulu": {"hp": 280, "defense": 22, "intellect": 42, "agility": 6, "required_level": 55},
    "equip_hunyuansan": {"hp": 420, "defense": 24, "force": 36, "agility": 4, "required_level": 60},
    "equip_fenghuangyu": _with_set_meta(
        "equip_fenghuangyu", {"hp": 180, "defense": 10, "intellect": 40, "luck": 18, "required_level": 55}
    ),
}


def apply_equipment_callback_rebalance(apps: Any, schema_editor: Any) -> None:
    database = schema_editor.connection.alias
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    Manor = apps.get_model("gameplay", "Manor")
    GearTemplate = apps.get_model("guests", "GearTemplate")
    GearItem = apps.get_model("guests", "GearItem")
    Guest = apps.get_model("guests", "Guest")

    # 保持与 0275/0278 一致的锁顺序：庄园 -> 模板 -> 门客装备 -> 门客。
    list(Manor.objects.using(database).select_for_update().order_by("pk").values_list("pk", flat=True))

    bases: dict[str, Any] = {}
    payloads: dict[str, dict[str, Any]] = {}
    for item in (
        ItemTemplate.objects.using(database).select_for_update().filter(key__in=EQUIPMENT_PAYLOADS).order_by("pk")
    ):
        payload = deepcopy(EQUIPMENT_PAYLOADS[item.key])
        if item.effect_payload != payload:
            item.effect_payload = payload
            item.save(using=database, update_fields=["effect_payload"])
        bases[item.key] = item
        payloads[item.key] = payload

    limit = ItemTemplate._meta.get_field("key").max_length or 50
    variants = {
        _CALIBRATION_275._affix_key(key, affix, limit): (base, affix)
        for key, base in bases.items()
        for affix in AFFIXES
    }
    for key, (base, affix) in variants.items():
        payloads[key] = _CALIBRATION_275._affix_payload(base, affix)

    for item in ItemTemplate.objects.using(database).select_for_update().filter(key__in=variants).order_by("pk"):
        payload = payloads[item.key]
        if item.effect_payload != payload:
            item.effect_payload = payload
            item.save(using=database, update_fields=["effect_payload"])

    changes: dict[int, tuple[Any, dict[str, int]]] = {}
    for template in GearTemplate.objects.using(database).select_for_update().filter(key__in=payloads).order_by("pk"):
        payload = payloads[template.key]
        stats = {stat: int(payload[stat]) for stat in STAT_FIELDS if stat in payload}
        if template.extra_stats != stats:
            changes[template.pk] = (template, stats)

    if not changes:
        return

    affected_gears = list(
        GearItem.objects.using(database)
        .select_for_update()
        .filter(template_id__in=changes, guest_id__isnull=False)
        .order_by("pk")
    )
    guest_ids = {gear.guest_id for gear in affected_gears}
    guests: dict[int, Any] = {
        guest.pk: guest
        for guest in Guest.objects.using(database)
        .select_for_update()
        .filter(pk__in=guest_ids)
        .select_related("template")
        .order_by("pk")
    }
    updates: dict[int, set[str]] = {pk: set() for pk in guests}

    for gear in affected_gears:
        guest = guests[gear.guest_id]
        template, new_stats = changes[gear.template_id]
        old_stats = template.extra_stats or {}
        for stat, field in STAT_FIELDS.items():
            delta = new_stats.get(stat, 0) - int(old_stats.get(stat, 0))
            if delta:
                setattr(guest, field, max(0, int(getattr(guest, field)) + delta))
                updates[guest.pk].add(field)

    for template, new_stats in changes.values():
        template.extra_stats = new_stats
        template.save(using=database, update_fields=["extra_stats"])

    for pk, guest in guests.items():
        maximum_hp = max(
            int(GUEST.MIN_HP_FLOOR),
            int(guest.template.base_hp)
            + int(guest.hp_bonus)
            + int(GUEST.DEFENSE_TO_HP_MULTIPLIER) * int(guest.defense_stat),
        ) + max(0, int(guest.level) - 1) * int(GUEST.LEVEL_HP_GAIN)
        if guest.current_hp > maximum_hp:
            guest.current_hp = maximum_hp
            updates[pk].add("current_hp")
        if updates[pk]:
            guest.save(using=database, update_fields=sorted(updates[pk]))


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0278_equipment_luck_progression"),
    ]

    operations = [
        migrations.RunPython(apply_equipment_callback_rebalance, migrations.RunPython.noop, atomic=True),
    ]
