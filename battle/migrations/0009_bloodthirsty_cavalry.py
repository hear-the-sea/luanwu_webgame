from django.db import migrations


def add_bloodthirsty_cavalry(apps, schema_editor):
    troop_template = apps.get_model("battle", "TroopTemplate")
    troop_template.objects.update_or_create(
        key="bloodthirsty_cavalry",
        defaults={
            "name": "嗜血铁骑",
            "description": "帮会训练的精锐骑兵，攻防与生命略胜现有顶级护院，不参与普通兵种克制。",
            "base_attack": 24,
            "base_defense": 11,
            "base_hp": 76,
            "speed_bonus": 12,
            "priority": 0,
            "default_count": 0,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("battle", "0008_battlereport_equipment_bonuses"),
    ]

    operations = [
        migrations.RunPython(add_bloodthirsty_cavalry, migrations.RunPython.noop),
    ]
