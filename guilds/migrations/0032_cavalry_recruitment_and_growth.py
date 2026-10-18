from django.db import migrations, models


def update_cavalry_technologies(apps, schema_editor):
    guild_model = apps.get_model("guilds", "Guild")
    technology = apps.get_model("guilds", "GuildTechnology")
    troop = apps.get_model("battle", "TroopTemplate")
    database = schema_editor.connection.alias
    maximums = {
        "wolf_force": 10,
        "wolf_defense": 10,
        "wolf_agility": 10,
        "wolf_hp": 10,
        "wolf_berserk": 5,
    }
    for key, maximum in maximums.items():
        technology.objects.using(database).filter(tech_key=key).update(max_level=maximum)
    # 保留已研究等级；旧狼系已完成的帮会可以在当前路线结束后补研新增等级。
    for guild in guild_model.objects.using(database).all().iterator():
        completed = list(guild.completed_technology_routes or [])
        if "wolf" not in completed:
            continue
        incomplete = any(
            technology.objects.using(database).filter(guild_id=guild.pk, tech_key=key, level__lt=maximum).exists()
            for key, maximum in maximums.items()
        )
        if incomplete:
            guild_model.objects.using(database).filter(pk=guild.pk).update(
                completed_technology_routes=[key for key in completed if key != "wolf"]
            )
    troop.objects.using(database).filter(key="bloodthirsty_cavalry").update(
        description="帮会统一招募的精锐铁骑，以贡献兑换。浴血冲阵随伤势增加触发率，攻击两个目标，不参与普通兵种克制。"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("guilds", "0031_guild_technology_tree"),
        ("battle", "0009_bloodthirsty_cavalry"),
    ]

    operations = [
        migrations.AlterField(
            model_name="guildresourcelog",
            name="action",
            field=models.CharField(
                choices=[
                    ("donation", "成员捐赠"),
                    ("tech_upgrade", "科技升级"),
                    ("guild_upgrade", "帮会升级"),
                    ("production", "科技产出"),
                    ("troop_recruit", "帮会募兵"),
                ],
                max_length=20,
                verbose_name="操作类型",
            ),
        ),
        migrations.RunPython(update_cavalry_technologies, migrations.RunPython.noop),
    ]
