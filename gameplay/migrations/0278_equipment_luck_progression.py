"""理顺装备运势的跨稀有度梯度并补足部分中后期套装与橙装的运势属性。"""

from collections import defaultdict

from django.db import migrations
from django.db.models import F

AFFIXES = ("agility", "force", "defense", "intellect")

EQUIPMENT_LUCK_OVERRIDES = {
    # 饰品：修复蓝(24)=紫(24)=橙(24)卡死，拉出 绿16 -> 蓝18 -> 紫24 -> 橙32 梯度
    "equip_guanxingdeng": 18,
    "equip_tianjiluopan": 18,
    "equip_zhoutianxingpan": 32,
    "equip_heshibi": 28,
    "equip_chuanguoyuxi": 20,
    "equip_shangshanfaeling": 18,
    "equip_qiankunzhu": 18,
    "equip_taiqingyuce": 18,
    "equip_shanheshejitu": 16,
    # 器械：修复橙(8) < 紫(10)倒挂，拉出 蓝8 -> 紫10 -> 橙12 梯度
    "equip_feiyuan": 12,
    "equip_chixiaojifeng": 12,
    "equip_mojiajiguanren": 12,
    # 护甲：修复橙(15) < 紫(18)倒挂，并补足蜀缎白锦袍
    "equip_jinsirujia": 20,
    "equip_hundunlongpao": 18,
    "equip_shuduanbaijinpao": 12,
    # 鞋子：修复绿(8)=橙(8)>紫(6)倒挂，拉出 绿8 -> 蓝8 -> 紫10 -> 橙14 梯度
    "equip_liekongxue": 10,
    "equip_huxianxie": 10,
    "equip_shenxingxue": 14,
    "equip_tatianxue": 12,
    "equip_wujixingxue": 10,
    "equip_chiyouzhanxue": 8,
    # 坐骑：修复白(5)>绿(0)与紫(10)<蓝(12)倒挂，拉出 白5 -> 绿8 -> 蓝12 -> 紫14 -> 橙22 梯度
    "equip_taxuema": 8,
    "equip_qingshuanglu": 14,
    "equip_dilu": 14,
    "equip_jueying": 12,
    "equip_shendiao": 16,
    # 头盔：拉开橙装梯度并补足主流零运势套装/高阶头盔
    "equip_taixudiguan": 18,
    "equip_longhuangguan": 12,
    "equip_diwangkui": 10,
    "equip_xiaoweitoukui": 6,
    "equip_jiaodoushitoukui": 8,
    "equip_xuanwukui": 10,
    # 武器：拉开橙装梯度并补足部分主流高阶武将武器
    "equip_qinglongdao": 8,
    "equip_tianyamingyuedao": 24,
    "equip_kongquelin": 18,
    "equip_yuanyuewandao": 18,
    "equip_xiaolifeidao": 16,
    "equip_dagoubang": 12,
    "equip_panlonggun": 8,
    "equip_xuanyuanjian": 12,
    "equip_qilindao": 10,
}

EQUIPMENT_PAYLOAD_OVERRIDES = {key: {"luck": luck} for key, luck in EQUIPMENT_LUCK_OVERRIDES.items()}


def _affix_key(base_key: str, affix: str, limit: int) -> str:
    import hashlib

    candidate = f"affix_{affix}__{base_key}"
    if len(candidate) <= limit:
        return candidate
    digest = hashlib.sha256(base_key.encode()).hexdigest()[:12]
    prefix = f"affix_{affix}__"
    return f"{prefix}{base_key[:limit - len(prefix) - 13]}_{digest}"


def _build_key_to_luck(limit: int) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for base_key, luck in EQUIPMENT_LUCK_OVERRIDES.items():
        mapping[base_key] = luck
        for affix in AFFIXES:
            mapping[_affix_key(base_key, affix, limit)] = luck
    return mapping


def sync_equipment_luck_progression(apps, schema_editor):
    database = schema_editor.connection.alias
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    Manor = apps.get_model("gameplay", "Manor")
    GearTemplate = apps.get_model("guests", "GearTemplate")
    GearItem = apps.get_model("guests", "GearItem")
    Guest = apps.get_model("guests", "Guest")

    limit = ItemTemplate._meta.get_field("key").max_length or 50
    key_to_luck = _build_key_to_luck(limit)
    target_keys = tuple(key_to_luck)

    # 遵循既有装备迁移的庄园 -> 模板 -> 装备 -> 门客锁顺序。
    list(Manor.objects.using(database).select_for_update().order_by("pk").values_list("pk", flat=True))
    for item in ItemTemplate.objects.using(database).select_for_update().filter(key__in=target_keys).order_by("pk"):
        payload = dict(item.effect_payload or {})
        target_luck = key_to_luck[item.key]
        if payload.get("luck") != target_luck:
            item.effect_payload = {**payload, "luck": target_luck}
            item.save(using=database, update_fields=["effect_payload"])

    changes = {}
    for template in GearTemplate.objects.using(database).select_for_update().filter(key__in=target_keys).order_by("pk"):
        stats = dict(template.extra_stats or {})
        target_luck = key_to_luck[template.key]
        delta = target_luck - int(stats.get("luck", 0))
        if delta:
            changes[template.pk] = (template, stats, target_luck, delta)

    guest_deltas = defaultdict(int)
    for gear in (
        GearItem.objects.using(database)
        .select_for_update()
        .filter(template_id__in=changes, guest_id__isnull=False)
        .order_by("pk")
    ):
        guest_deltas[gear.guest_id] += changes[gear.template_id][3]
    for guest_id in sorted(guest_deltas):
        delta = guest_deltas[guest_id]
        if delta:
            Guest.objects.using(database).filter(pk=guest_id).update(luck=F("luck") + delta)
    for template, stats, target_luck, _delta in changes.values():
        template.extra_stats = {**stats, "luck": target_luck}
        template.save(using=database, update_fields=["extra_stats"])


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0277_lvbu_set_concise_effect_description")]
    operations = [migrations.RunPython(sync_equipment_luck_progression, migrations.RunPython.noop, atomic=True)]
