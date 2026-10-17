"""建立专属门客模板及道具，不增加材料来源。"""

from copy import deepcopy

from django.db import migrations

ITEM_DEFAULTS = {
    "custom_guest_card": {
        "name": "自定义门客卡",
        "description": "意识穿越到春秋乱世有一段时间了，使用自定义门客卡即可获得专属肉身，还自带吞噬强化系统，出任CEO，赢娶白富美，走上人生巅峰就在眼前。",
        "rarity": "green",
        "tradeable": False,
        "default_bound": True,
        "effect_payload": {"action": "create_custom_guest"},
    },
    "yijinjing": {
        "name": "易筋经",
        "description": "记载易筋锻骨之法的武学经卷，可用于专属门客升阶及橙色强化。尝试无论成败均会消耗，失败后本阶段成功率提升。",
        "rarity": "purple",
        "tradeable": True,
        "default_bound": False,
        "effect_payload": {"action": "advance_custom_guest"},
    },
    "millennium_zhuguo": {
        "name": "千年朱果",
        "description": "千年方熟的灵果，可将专属门客的武力、智力、防御与敏捷成长权重重置为各500，保留现有品质与强化成果。",
        "rarity": "orange",
        "tradeable": True,
        "default_bound": False,
        "effect_payload": {"action": "reset_custom_guest_weights"},
    },
}
TEMPLATE_DEFAULTS = {
    f"custom_guest_{rarity}": {
        "name": "专属门客",
        "archetype": "military",
        "rarity": rarity,
        "base_attack": 30,
        "base_intellect": 30,
        "base_defense": 30,
        "base_agility": 30,
        "base_luck": 100,
        "base_hp": {"green": 400, "blue": 600, "purple": 800, "orange": 1000}[rarity],
        "recruitable": False,
        "growth_range": growth,
        "attribute_weights": {"force": 500, "intellect": 500, "defense": 500, "agility": 500},
    }
    for rarity, growth in (("green", [3, 7]), ("blue", [5, 9]), ("purple", [6, 11]), ("orange", [6, 14]))
}


def create_configs(apps, schema_editor):
    database = schema_editor.connection.alias
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    GuestTemplate = apps.get_model("guests", "GuestTemplate")
    for key, data in ITEM_DEFAULTS.items():
        defaults = deepcopy(data)
        defaults.update(
            effect_type="tool", image=f"items/{key}.png", price=0, storage_space=1, is_usable=key == "custom_guest_card"
        )
        ItemTemplate.objects.using(database).update_or_create(key=key, defaults=defaults)
    for key, defaults in TEMPLATE_DEFAULTS.items():
        GuestTemplate.objects.using(database).update_or_create(key=key, defaults=deepcopy(defaults))


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0201_shenyi_poxu_reward_sources"), ("guests", "0090_custom_guest_progress")]
    operations = [migrations.RunPython(create_configs, migrations.RunPython.noop)]
