"""普通装备专精调整；冻结快照，不修改灵魂饰品或重新随机前缀。"""

import copy
import hashlib
import json

from django.db import migrations

EQUIPMENT_PAYLOADS = json.loads(
    r"""
{
  "equip_diwangkui": {
    "hp": 400,
    "defense": 32,
    "force": 40,
    "intellect": 10,
    "troop_capacity": 30,
    "required_level": 80
  },
  "equip_taixudiguan": {
    "hp": 400,
    "defense": 20,
    "intellect": 40,
    "luck": 14,
    "required_level": 75
  },
  "equip_longlinzhanjia": {
    "hp": 550,
    "defense": 29,
    "force": 38,
    "intellect": 0,
    "required_level": 75
  },
  "equip_hundunlongpao": {
    "hp": 540,
    "defense": 15,
    "intellect": 40,
    "luck": 15,
    "required_level": 65
  },
  "equip_jinsirujia": {
    "hp": 550,
    "defense": 22,
    "agility": 28,
    "luck": 16,
    "required_level": 70
  },
  "equip_ruanweijia": {
    "hp": 550,
    "defense": 48,
    "force": 8,
    "agility": 8,
    "required_level": 80
  },
  "equip_tatianxue": {
    "hp": 220,
    "defense": 8,
    "force": 30,
    "intellect": 0,
    "agility": 33,
    "required_level": 70
  },
  "equip_shenxingxue": {
    "hp": 180,
    "defense": 5,
    "force": 1,
    "agility": 51,
    "required_level": 80
  },
  "equip_wujixingxue": {
    "hp": 240,
    "defense": 8,
    "force": 0,
    "agility": 31,
    "required_level": 75,
    "intellect": 24
  },
  "equip_xuantiechongjian": {
    "hp": 280,
    "defense": 10,
    "force": 84,
    "agility": 1,
    "required_level": 80
  },
  "equip_yitianjian": {
    "hp": 180,
    "defense": 3,
    "force": 76,
    "intellect": 8,
    "agility": 10,
    "luck": 5,
    "required_level": 65
  },
  "equip_tuwujian": {
    "hp": 160,
    "defense": 3,
    "force": 78,
    "intellect": 6,
    "agility": 10,
    "luck": 3,
    "required_level": 65
  },
  "equip_panlonggun": {
    "hp": 240,
    "defense": 10,
    "force": 78,
    "agility": 4,
    "luck": 6,
    "required_level": 65
  },
  "equip_tianyamingyuedao": {
    "hp": 220,
    "defense": 6,
    "force": 80,
    "agility": 12,
    "required_level": 70
  },
  "equip_zhuxianjian": {
    "hp": 160,
    "defense": 4,
    "force": 14,
    "intellect": 72,
    "agility": 6,
    "luck": 10,
    "required_level": 80
  },
  "equip_xiaolifeidao": {
    "hp": 100,
    "force": 52,
    "intellect": 0,
    "agility": 41,
    "luck": 18,
    "required_level": 75
  },
  "equip_ziweiruanjian": {
    "force": 38,
    "agility": 30,
    "luck": 13,
    "required_level": 55
  },
  "equip_biyudao": {
    "force": 56,
    "agility": 12,
    "luck": 13,
    "required_level": 55
  },
  "equip_xiuhuazhen": {
    "force": 36,
    "agility": 32,
    "luck": 11,
    "required_level": 55
  },
  "equip_jinshejian": {
    "force": 38,
    "agility": 27,
    "luck": 15,
    "required_level": 50
  },
  "equip_jinshezui": {
    "force": 39,
    "agility": 30,
    "luck": 12,
    "required_level": 55
  },
  "equip_wuqiaojian": {
    "force": 59,
    "agility": 10,
    "luck": 12,
    "required_level": 50
  },
  "equip_libiegou": {
    "force": 55,
    "agility": 12,
    "luck": 13,
    "required_level": 50
  },
  "equip_fengshenbang": {
    "hp": 350,
    "defense": 0,
    "intellect": 39,
    "luck": 20,
    "required_level": 70
  },
  "equip_taiqingyuce": {
    "hp": 200,
    "intellect": 38,
    "luck": 16,
    "required_level": 65
  },
  "equip_heshibi": {
    "hp": 290,
    "defense": 1,
    "intellect": 38,
    "luck": 25,
    "required_level": 65
  },
  "equip_businiaoyu": {
    "hp": 315,
    "force": 34,
    "agility": 14,
    "required_level": 65
  },
  "equip_feiyunzui": {
    "hp": 220,
    "defense": 8,
    "force": 0,
    "agility": 37,
    "required_level": 25
  },
  "equip_juanmaochitu": {
    "hp": 220,
    "defense": 10,
    "force": 12,
    "agility": 23,
    "required_level": 30
  },
  "equip_bailongju": {
    "hp": 220,
    "defense": 16,
    "agility": 19,
    "luck": 12,
    "required_level": 35
  },
  "equip_zhuidian": {
    "hp": 220,
    "defense": 5,
    "agility": 35,
    "luck": 10,
    "required_level": 40
  },
  "equip_wuzhuima": {
    "hp": 260,
    "defense": 16,
    "force": 26,
    "agility": 22,
    "required_level": 45
  },
  "equip_zhaohuangfeidian": {
    "hp": 260,
    "defense": 10,
    "force": 10,
    "agility": 44,
    "required_level": 50
  },
  "equip_jueying": {
    "hp": 260,
    "defense": 7,
    "agility": 41,
    "luck": 15,
    "required_level": 45
  },
  "equip_qingshuanglu": {
    "hp": 260,
    "defense": 18,
    "agility": 30,
    "luck": 15,
    "required_level": 55
  },
  "equip_dilu": {
    "hp": 260,
    "defense": 22,
    "agility": 26,
    "luck": 14,
    "required_level": 60
  },
  "equip_chituma": {
    "hp": 300,
    "defense": 14,
    "force": 34,
    "agility": 22,
    "troop_capacity": 40,
    "required_level": 80
  },
  "equip_hanxuebaoma": {
    "hp": 300,
    "defense": 2,
    "agility": 48,
    "required_level": 70
  },
  "equip_shendiao": {
    "hp": 300,
    "defense": 10,
    "agility": 40,
    "luck": 14,
    "required_level": 80
  },
  "equip_qilin": {
    "hp": 300,
    "defense": 24,
    "luck": 25,
    "agility": 10,
    "required_level": 65
  },
  "equip_shandianqiulongju": {
    "hp": 240,
    "defense": 10,
    "force": 8,
    "agility": 42,
    "luck": 12,
    "required_level": 55
  },
  "equip_jinjingbailongju": {
    "hp": 260,
    "defense": 10,
    "force": 0,
    "agility": 44,
    "luck": 24,
    "troop_capacity": 40,
    "required_level": 80
  },
  "equip_lingboxie": {
    "hp": 200,
    "defense": 6,
    "agility": 34,
    "required_level": 30
  },
  "equip_yingxingxue": {
    "hp": 200,
    "defense": 14,
    "agility": 26,
    "required_level": 25
  },
  "equip_zhurixue": {
    "hp": 240,
    "defense": 18,
    "force": 6,
    "agility": 42,
    "required_level": 60
  }
}
"""
)

STAT_FIELDS = {
    "hp": "hp_bonus",
    "force": "force",
    "intellect": "intellect",
    "defense": "defense_stat",
    "agility": "agility",
    "luck": "luck",
    "troop_capacity": "troop_capacity_bonus",
}
AFFIXES = ("agility", "force", "defense", "intellect")


def _affix_key(base_key, affix, limit):
    candidate = f"affix_{affix}__{base_key}"
    if len(candidate) <= limit:
        return candidate
    digest = hashlib.sha256(base_key.encode()).hexdigest()[:12]
    prefix = f"affix_{affix}__"
    return f"{prefix}{base_key[:limit-len(prefix)-13]}_{digest}"


def _affix_payload(base, affix):
    payload = copy.deepcopy(base.effect_payload)
    gain = 0
    for stat in AFFIXES:
        if stat == affix:
            continue
        loss = int(payload.get(stat, 0)) // 5
        if stat in payload:
            payload[stat] -= loss
        gain += loss
    # 无可转换点数时仍保留历史变体身份。
    if gain:
        payload[affix] = int(payload.get(affix, 0)) + gain
    payload.update(equipment_base_key=base.key, equipment_base_name=base.name, equipment_affix=affix)
    return payload


def apply_equipment_specialization(apps, schema_editor):
    database = schema_editor.connection.alias
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    Manor = apps.get_model("gameplay", "Manor")
    GearTemplate = apps.get_model("guests", "GearTemplate")
    GearItem = apps.get_model("guests", "GearItem")
    Guest = apps.get_model("guests", "Guest")
    # 与穿脱装备的庄园 -> 装备 -> 门客锁顺序一致。
    list(Manor.objects.using(database).select_for_update().order_by("pk").values_list("pk", flat=True))
    bases = {}
    payloads = {}
    for item in (
        ItemTemplate.objects.using(database).select_for_update().filter(key__in=EQUIPMENT_PAYLOADS).order_by("pk")
    ):
        payload = copy.deepcopy(EQUIPMENT_PAYLOADS[item.key])
        assert not payload.get("set_key"), "本轮只调整无套装普通装备"
        if item.effect_payload != payload:
            item.effect_payload = payload
            item.save(using=database, update_fields=["effect_payload"])
        bases[item.key] = item
        payloads[item.key] = payload
    limit = ItemTemplate._meta.get_field("key").max_length or 50
    variants = {_affix_key(key, affix, limit): (base, affix) for key, base in bases.items() for affix in AFFIXES}
    # 不新建历史物品，不改变库存、绑定、交易身份；同时兼容仅有装备模板的变体。
    for key, (base, affix) in variants.items():
        payloads[key] = _affix_payload(base, affix)
    for item in ItemTemplate.objects.using(database).select_for_update().filter(key__in=variants).order_by("pk"):
        payload = payloads[item.key]
        if item.effect_payload != payload:
            item.effect_payload = payload
            item.save(using=database, update_fields=["effect_payload"])

    changes = {}
    for template in GearTemplate.objects.using(database).select_for_update().filter(key__in=payloads).order_by("pk"):
        payload = payloads[template.key]
        stats = {stat: int(payload[stat]) for stat in STAT_FIELDS if stat in payload}
        if template.extra_stats != stats:
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
        for stat, field in STAT_FIELDS.items():
            delta = stats.get(stat, 0) - (template.extra_stats or {}).get(stat, 0)
            if delta:
                setattr(guest, field, getattr(guest, field) + delta)
                updates[guest.pk].add(field)
    for template, stats in changes.values():
        template.extra_stats = stats
        template.save(using=database, update_fields=["extra_stats"])
    # 套装、裸属性、自由加点保持原值；不治疗受伤门客，只收敛超过新上限的生命。
    for pk, guest in guests.items():
        maximum_hp = max(200, int(guest.template.base_hp) + int(guest.hp_bonus) + 50 * int(guest.defense_stat))
        if guest.current_hp > maximum_hp:
            guest.current_hp = maximum_hp
            updates[pk].add("current_hp")
        if updates[pk]:
            guest.save(using=database, update_fields=sorted(updates[pk]))


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0210_manor_resource_production_buff"),
        ("guests", "0091_custom_guest_daily_feeding"),
    ]
    operations = [migrations.RunPython(apply_equipment_specialization, migrations.RunPython.noop)]
