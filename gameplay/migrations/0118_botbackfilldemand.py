from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gameplay", "0117_botinventorydailycounter"),
    ]

    operations = [
        migrations.CreateModel(
            name="BotBackfillDemand",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("region", models.CharField("地区", max_length=32)),
                ("prestige_band", models.CharField("声望段", max_length=32)),
                ("needed", models.PositiveIntegerField("需求数量", default=0)),
                ("created_at", models.DateTimeField("创建时间", auto_now_add=True)),
                ("updated_at", models.DateTimeField("更新时间", auto_now=True)),
            ],
            options={
                "verbose_name": "虚拟玩家补量需求",
                "verbose_name_plural": "虚拟玩家补量需求",
            },
        ),
        migrations.AddConstraint(
            model_name="botbackfilldemand",
            constraint=models.UniqueConstraint(
                fields=("region", "prestige_band"),
                name="bot_backfill_demand_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="botbackfilldemand",
            index=models.Index(fields=["region", "prestige_band"], name="bot_backfill_region_band_idx"),
        ),
        migrations.AddIndex(
            model_name="botbackfilldemand",
            index=models.Index(fields=["updated_at"], name="bot_backfill_updated_idx"),
        ),
    ]
