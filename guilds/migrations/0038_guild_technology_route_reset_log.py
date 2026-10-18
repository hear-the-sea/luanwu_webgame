from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("guilds", "0037_equipment_box_prices")]

    operations = [
        migrations.AlterField(
            model_name="guildresourcelog",
            name="action",
            field=models.CharField(
                choices=[
                    ("donation", "成员捐赠"),
                    ("tech_upgrade", "科技升级"),
                    ("tech_reset", "科技路线重置"),
                    ("guild_upgrade", "帮会升级"),
                    ("production", "科技产出"),
                    ("troop_recruit", "铁骑训练"),
                ],
                max_length=20,
                verbose_name="操作类型",
            ),
        ),
    ]
