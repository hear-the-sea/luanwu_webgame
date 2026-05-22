from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gameplay", "0116_botprofile"),
    ]

    operations = [
        migrations.CreateModel(
            name="BotInventoryDailyCounter",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("category", models.CharField("类别", max_length=32)),
                ("counter_date", models.DateField("计数日期")),
                ("quantity", models.PositiveIntegerField("数量", default=0)),
                ("created_at", models.DateTimeField("创建时间", auto_now_add=True)),
                ("updated_at", models.DateTimeField("更新时间", auto_now=True)),
            ],
            options={
                "verbose_name": "虚拟玩家每日库存计数",
                "verbose_name_plural": "虚拟玩家每日库存计数",
            },
        ),
        migrations.AddConstraint(
            model_name="botinventorydailycounter",
            constraint=models.UniqueConstraint(
                fields=("category", "counter_date"),
                name="bot_inventory_daily_counter_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="botinventorydailycounter",
            index=models.Index(fields=["counter_date", "category"], name="bot_inv_counter_day_cat_idx"),
        ),
    ]
