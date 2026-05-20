from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0111_add_city_defense_buildings"),
    ]

    operations = [
        migrations.AlterField(
            model_name="buildingtype",
            name="category",
            field=models.CharField(
                choices=[
                    ("resource", "资源生产"),
                    ("storage", "仓储设施"),
                    ("production", "生产加工"),
                    ("personnel", "人员管理"),
                    ("special", "特殊建筑"),
                    ("city_defense", "城防建筑"),
                ],
                default="resource",
                max_length=16,
                verbose_name="建筑分类",
            ),
        ),
    ]
