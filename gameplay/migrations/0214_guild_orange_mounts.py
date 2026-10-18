"""帮会龙驹统一橙色品质，分别强化进攻与统率定位。"""

import copy
from importlib import import_module
from types import SimpleNamespace

from django.db import migrations

specialization = import_module("gameplay.migrations.0211_equipment_specialization")

EQUIPMENT_PAYLOADS = {
    "equip_shandianqiulongju": {
        "hp": 260,
        "defense": 6,
        "force": 16,
        "agility": 48,
        "luck": 12,
        "required_level": 80,
    },
    "equip_jinjingbailongju": {
        "hp": 260,
        "defense": 20,
        "force": 0,
        "agility": 34,
        "luck": 24,
        "troop_capacity": 40,
        "required_level": 80,
    },
}
ITEM_METADATA = {
    "equip_shandianqiulongju": {
        "name": "闪电虬龙驹",
        "description": "身形矫健如电、鬃毛似龙的战驹，嘶鸣时仿佛有惊雷相随。侧重敏捷与武力，适合追求先手和进攻的门客。",
    },
    "equip_jinjingbailongju": {
        "name": "金睛白龙驹",
        "description": "通体银白、双目金黄的珍稀龙驹，传闻能踏云越岭、日行千里。侧重防御、运势与带兵，适合兼顾生存和统率的门客。",
    },
}


def upgrade_guild_mounts(apps, schema_editor):
    database = schema_editor.connection.alias
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    Manor = apps.get_model("gameplay", "Manor")
    GearTemplate = apps.get_model("guests", "GearTemplate")
    GearItem = apps.get_model("guests", "GearItem")
    Guest = apps.get_model("guests", "Guest")
    # 沿用庄园 -> 装备 -> 门客的锁顺序，保持已有库存及穿戴关系。
    list(Manor.objects.using(database).select_for_update().order_by("pk").values_list("pk", flat=True))
    limit = ItemTemplate._meta.get_field("key").max_length or 50
    payloads = {}
    descriptions = {}
    for key, payload in EQUIPMENT_PAYLOADS.items():
        payloads[key] = copy.deepcopy(payload)
        descriptions[key] = ITEM_METADATA[key]["description"]
        base = SimpleNamespace(key=key, name=ITEM_METADATA[key]["name"], effect_payload=payload)
        for affix in specialization.AFFIXES:
            variant_key = specialization._affix_key(key, affix, limit)
            payloads[variant_key] = specialization._affix_payload(base, affix)
            descriptions[variant_key] = descriptions[key]

    for item in ItemTemplate.objects.using(database).select_for_update().filter(key__in=payloads).order_by("pk"):
        item.effect_payload = payloads[item.key]
        item.rarity = "orange"
        item.price = 65000
        item.storage_space = 250
        item.description = descriptions[item.key]
        item.save(using=database, update_fields=["effect_payload", "rarity", "price", "storage_space", "description"])

    changes = {}
    for template in GearTemplate.objects.using(database).select_for_update().filter(key__in=payloads).order_by("pk"):
        payload = payloads[template.key]
        stats = {stat: int(payload[stat]) for stat in specialization.STAT_FIELDS if stat in payload}
        if template.extra_stats != stats or template.rarity != "orange" or template.required_level != 80:
            changes[template.pk] = (template, stats)
    gears = list(
        GearItem.objects.using(database)
        .select_for_update()
        .filter(template_id__in=changes, guest_id__isnull=False)
        .order_by("pk")
    )
    guests = {
        guest.pk: guest
        for guest in Guest.objects.using(database)
        .select_for_update()
        .filter(pk__in={gear.guest_id for gear in gears})
        .select_related("template")
        .order_by("pk")
    }
    updates = {pk: set() for pk in guests}
    for gear in gears:
        guest = guests[gear.guest_id]
        template, stats = changes[gear.template_id]
        for stat, field in specialization.STAT_FIELDS.items():
            delta = stats.get(stat, 0) - (template.extra_stats or {}).get(stat, 0)
            if delta:
                setattr(guest, field, getattr(guest, field) + delta)
                updates[guest.pk].add(field)
    for template, stats in changes.values():
        template.extra_stats = stats
        template.rarity = "orange"
        template.required_level = 80
        template.save(using=database, update_fields=["extra_stats", "rarity", "required_level"])
    for pk, guest in guests.items():
        maximum_hp = max(200, int(guest.template.base_hp) + int(guest.hp_bonus) + 50 * int(guest.defense_stat))
        if guest.current_hp > maximum_hp:
            guest.current_hp = maximum_hp
            updates[pk].add("current_hp")
        if updates[pk]:
            guest.save(using=database, update_fields=sorted(updates[pk]))


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0213_guild_resource_equalization_cards")]

    operations = [migrations.RunPython(upgrade_guild_mounts, migrations.RunPython.noop)]
