"""专属门客固定使用橙色文臣五项基础属性，保留已有养成。"""

from django.db import migrations

TEMPLATE_KEYS = tuple(f"custom_guest_{rarity}" for rarity in ("green", "blue", "purple", "orange"))
BASE_STATS = {"force": 45, "intellect": 50, "defense": 24, "agility": 52, "luck": 165}
TEMPLATE_FIELDS = {
    "force": "base_attack",
    "intellect": "base_intellect",
    "defense": "base_defense",
    "agility": "base_agility",
    "luck": "base_luck",
}


def update_custom_guest_base(apps, schema_editor):
    database = schema_editor.connection.alias
    Manor = apps.get_model("gameplay", "Manor")
    Guest = apps.get_model("guests", "Guest")
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    # 创建、重生和升级也先锁庄园，避免同步期间按旧基础值创建新实例。
    list(Manor.objects.using(database).select_for_update().order_by("pk").values_list("pk", flat=True))
    templates = list(GuestTemplate.objects.using(database).select_for_update().filter(key__in=TEMPLATE_KEYS))
    old_luck = {template.pk: template.base_luck for template in templates}
    for guest in Guest.objects.using(database).select_for_update().filter(template_id__in=old_luck):
        fields = []
        for stat in ("force", "intellect", "defense", "agility"):
            initial = f"initial_{stat}"
            field = "defense_stat" if stat == "defense" else stat
            delta = BASE_STATS[stat] - getattr(guest, initial)
            if delta:
                setattr(guest, field, getattr(guest, field) + delta)
                setattr(guest, initial, BASE_STATS[stat])
                fields.extend((field, initial))
        luck_delta = BASE_STATS["luck"] - old_luck[guest.template_id]
        if luck_delta:
            guest.luck += luck_delta
            fields.append("luck")
        # 防御基础降低时只压低超出上限的当前生命，不自动治疗。
        template = next(template for template in templates if template.pk == guest.template_id)
        maximum_hp = max(200, template.base_hp + guest.hp_bonus + guest.defense_stat * 50)
        if guest.current_hp > maximum_hp:
            guest.current_hp = maximum_hp
            fields.append("current_hp")
        if fields:
            guest.save(using=database, update_fields=fields)
    for template in templates:
        changes = {field: BASE_STATS[stat] for stat, field in TEMPLATE_FIELDS.items()}
        GuestTemplate.objects.using(database).filter(pk=template.pk).update(**changes)


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0202_custom_guest_items_and_templates")]
    operations = [migrations.RunPython(update_custom_guest_base, migrations.RunPython.noop, atomic=True)]
