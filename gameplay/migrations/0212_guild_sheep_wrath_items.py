from django.db import migrations


def update_guild_card_templates(apps, schema_editor):
    database = schema_editor.connection.alias
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    templates = ItemTemplate.objects.using(database)

    templates.update_or_create(
        key="guild_silver_boost_card",
        defaults={
            "name": "均贫卡",
            "description": (
                "对可攻击的目标庄园使用，将其现有银两的一半转移到使用者庄园；"
                "超出我方银库上限的部分直接丢弃。同一庄园1小时内最多受到一张均贫卡影响。"
            ),
            "effect_type": "tool",
            "effect_payload": {"action": "transfer_target_silver"},
            "rarity": "blue",
            "tradeable": True,
            "default_bound": False,
            "price": 0,
            "storage_space": 25,
            "is_usable": True,
        },
    )
    templates.using(database).update_or_create(
        key="guild_sheep_wrath",
        defaults={
            "name": "羊的愤怒",
            "description": (
                "对可攻击的目标庄园使用，消耗使用者10根金条，使目标全部门客重伤并降至1点生命，"
                "同时清空目标庄园的银两和粮食。同一庄园1小时内最多受到一次羊的愤怒影响。"
            ),
            "effect_type": "tool",
            "effect_payload": {"action": "sheep_wrath_target"},
            "rarity": "purple",
            "tradeable": True,
            "default_bound": False,
            "price": 0,
            "storage_space": 50,
            "is_usable": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0211_equipment_specialization")]

    operations = [migrations.RunPython(update_guild_card_templates, migrations.RunPython.noop)]
