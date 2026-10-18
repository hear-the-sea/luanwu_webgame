from django.db import migrations

EXCHANGE_PRICES = {
    "experience_peach_recipe": 50,
    "experience_pineapple_recipe": 100,
    "experience_watermelon_recipe": 200,
    "experience_fruit": 1,
    "soul_container": 5000,
    "guild_skill_book_box_purple": 500,
    "guild_skill_book_box_refined": 1000,
    "guild_skill_book_box_master": 2000,
    "blueprint_taotieding": 2500,
    "guild_slave_card": 500,
    "guild_injury_card": 700,
    "guild_grain_boost_card": 200,
    "guild_silver_boost_card": 200,
    "green_to_blue_pill": 1000,
    "xisuidan": 3000,
}


def update_warehouse_exchange_prices(apps, schema_editor):
    """仅同步指定物品的旧库存价格，保留数量与兑换记录。"""
    warehouse = apps.get_model("guilds", "GuildWarehouse")
    for item_key, price in EXCHANGE_PRICES.items():
        warehouse.objects.using(schema_editor.connection.alias).filter(item_key=item_key).update(
            contribution_cost=price
        )


class Migration(migrations.Migration):
    dependencies = [("guilds", "0033_cavalry_training")]

    operations = [migrations.RunPython(update_warehouse_exchange_prices, migrations.RunPython.noop)]
