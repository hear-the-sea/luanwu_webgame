from django.db import migrations

EXCHANGE_PRICES = {
    "guild_gear_box_green": 70,
    "guild_gear_box_blue": 300,
    "guild_gear_box_purple": 1000,
    "guild_gear_box_refined_purple": 1500,
    "guild_gear_box_master": 2500,
}


def update_warehouse_exchange_prices(apps, schema_editor):
    """同步已有装备箱单价，保留库存与历史兑换统计。"""
    warehouse = apps.get_model("guilds", "GuildWarehouse")
    for item_key, price in EXCHANGE_PRICES.items():
        warehouse.objects.using(schema_editor.connection.alias).filter(item_key=item_key).update(
            contribution_cost=price
        )


class Migration(migrations.Migration):
    dependencies = [("guilds", "0036_guild_assets_copy")]

    operations = [migrations.RunPython(update_warehouse_exchange_prices, migrations.RunPython.noop)]
