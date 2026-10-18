from django.db import migrations


def update_equalization_cards(apps, schema_editor):
    ItemTemplate = apps.get_model("gameplay", "ItemTemplate")
    templates = ItemTemplate.objects.using(schema_editor.connection.alias)
    for resource, name, capacity in (("grain", "均粮卡", "粮仓"), ("silver", "均贫卡", "银库")):
        label = "粮食" if resource == "grain" else "银两"
        templates.filter(key=f"guild_{resource}_boost_card").update(
            description=(
                f"对可攻击的目标庄园使用，将双方现有{label}相加后平分，双方各取平均值（向下取整）；"
                f"超出我方{capacity}上限的部分直接丢弃。同一庄园1小时内最多受到一张{name}影响。"
            ),
            effect_payload={"action": f"transfer_target_{resource}"},
        )


class Migration(migrations.Migration):
    dependencies = [("gameplay", "0212_guild_sheep_wrath_items")]

    operations = [migrations.RunPython(update_equalization_cards, migrations.RunPython.noop)]
