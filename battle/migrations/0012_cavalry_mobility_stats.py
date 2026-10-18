from django.db import migrations


def rebalance_cavalry_stats(apps, schema_editor):
    troop = apps.get_model("battle", "TroopTemplate")
    troop.objects.using(schema_editor.connection.alias).filter(key="bloodthirsty_cavalry").update(
        base_attack=15,
        base_defense=10,
        base_hp=72,
        speed_bonus=28,
        description="帮会统一招募的高速重骑，以贡献兑换。防御与生命看齐枪王，以较低攻击换取高敏捷与先攻优势。铁骑冲阵以固定概率攻击两个目标，不参与普通兵种克制。",
    )


class Migration(migrations.Migration):
    dependencies = [
        ("battle", "0011_rename_cavalry_charge"),
    ]

    operations = [
        migrations.RunPython(rebalance_cavalry_stats, migrations.RunPython.noop),
    ]
