"""为蚩尤甲及已有词缀版本增加运势，并同步已穿戴门客。"""

from collections import defaultdict

from django.db import migrations
from django.db.models import F

EQUIPMENT_PAYLOAD_OVERRIDES = {"equip_chiyoujia": {"luck": 15}}
ARMOR_KEYS = (
    "equip_chiyoujia",
    *(f"affix_{affix}__equip_chiyoujia" for affix in ("agility", "force", "defense", "intellect")),
)


def sync_chiyou_armor_luck(apps, schema_editor):
    database = schema_editor.connection.alias
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    Manor = apps.get_model("gameplay", "Manor")
    GearTemplate = apps.get_model("guests", "GearTemplate")
    GearItem = apps.get_model("guests", "GearItem")
    Guest = apps.get_model("guests", "Guest")
    luck = EQUIPMENT_PAYLOAD_OVERRIDES["equip_chiyoujia"]["luck"]

    # 遵循既有装备迁移的庄园 -> 装备 -> 门客锁顺序。
    list(Manor.objects.using(database).select_for_update().order_by("pk").values_list("pk", flat=True))
    for item in ItemTemplate.objects.using(database).select_for_update().filter(key__in=ARMOR_KEYS).order_by("pk"):
        payload = dict(item.effect_payload or {})
        if payload.get("luck") != luck:
            item.effect_payload = {**payload, "luck": luck}
            item.save(using=database, update_fields=["effect_payload"])

    changes = {}
    for template in GearTemplate.objects.using(database).select_for_update().filter(key__in=ARMOR_KEYS).order_by("pk"):
        stats = dict(template.extra_stats or {})
        delta = luck - int(stats.get("luck", 0))
        if delta:
            changes[template.pk] = (template, stats, delta)

    guest_deltas = defaultdict(int)
    for gear in (
        GearItem.objects.using(database)
        .select_for_update()
        .filter(template_id__in=changes, guest_id__isnull=False)
        .order_by("pk")
    ):
        guest_deltas[gear.guest_id] += changes[gear.template_id][2]
    for guest_id in sorted(guest_deltas):
        Guest.objects.using(database).filter(pk=guest_id).update(luck=F("luck") + guest_deltas[guest_id])
    for template, stats, _delta in changes.values():
        template.extra_stats = {**stats, "luck": luck}
        template.save(using=database, update_fields=["extra_stats"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0229_personal_guild_guest_profile_alignment")]
    operations = [migrations.RunPython(sync_chiyou_armor_luck, migrations.RunPython.noop, atomic=True)]
