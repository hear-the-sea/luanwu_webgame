import django.db.models.deletion
from django.db import migrations, models


def backfill_transaction_quantities(apps, schema_editor):
    listing = apps.get_model("trade", "MarketListing")
    transaction = apps.get_model("trade", "MarketTransaction")
    alias = schema_editor.connection.alias
    transaction.objects.using(alias).update(
        quantity=models.Subquery(
            listing.objects.using(alias).filter(pk=models.OuterRef("listing_id")).values("quantity")[:1]
        )
    )


class Migration(migrations.Migration):
    dependencies = [("trade", "0011_goldbarexchangelog_shanghai_date")]

    operations = [
        migrations.AlterField(
            model_name="markettransaction",
            name="listing",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="transactions",
                to="trade.marketlisting",
                verbose_name="挂单",
            ),
        ),
        migrations.AddField(
            model_name="markettransaction",
            name="quantity",
            field=models.PositiveIntegerField(null=True, verbose_name="成交数量"),
        ),
        # 部分成交后不能再还原为一对一关系，保留不可逆标记。
        migrations.RunPython(backfill_transaction_quantities),
        migrations.AlterField(
            model_name="markettransaction",
            name="quantity",
            field=models.PositiveIntegerField(verbose_name="成交数量"),
        ),
    ]
