from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gameplay", "0137_arena_match_integrity"),
    ]

    operations = [
        migrations.AlterField(
            model_name="equipmentproduction",
            name="status",
            field=models.CharField(
                choices=[
                    ("forging", "锻造中"),
                    ("completed", "已完成"),
                    ("cancelled", "已取消"),
                ],
                default="forging",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="horseproduction",
            name="status",
            field=models.CharField(
                choices=[
                    ("producing", "生产中"),
                    ("completed", "已完成"),
                    ("cancelled", "已取消"),
                ],
                default="producing",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="livestockproduction",
            name="status",
            field=models.CharField(
                choices=[
                    ("producing", "养殖中"),
                    ("completed", "已完成"),
                    ("cancelled", "已取消"),
                ],
                default="producing",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="smeltingproduction",
            name="status",
            field=models.CharField(
                choices=[
                    ("producing", "冶炼中"),
                    ("completed", "已完成"),
                    ("cancelled", "已取消"),
                ],
                default="producing",
                max_length=16,
            ),
        ),
    ]
