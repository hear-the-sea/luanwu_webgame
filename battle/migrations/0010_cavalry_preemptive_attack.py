from django.db import migrations


def enable_cavalry_preemptive_attack(apps, schema_editor):
    troop = apps.get_model("battle", "TroopTemplate")
    troop.objects.using(schema_editor.connection.alias).filter(key="bloodthirsty_cavalry").update(
        priority=-1,
        description="帮会统一招募的精锐铁骑，以贡献兑换。可参与先攻回合，浴血冲阵以固定概率攻击两个目标，不参与普通兵种克制。",
    )


class Migration(migrations.Migration):
    dependencies = [
        ("battle", "0009_bloodthirsty_cavalry"),
        ("guilds", "0032_cavalry_recruitment_and_growth"),
    ]

    operations = [
        migrations.RunPython(enable_cavalry_preemptive_attack, migrations.RunPython.noop),
    ]
