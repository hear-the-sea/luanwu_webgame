from django.db import migrations

GRAIN_ITEM_KEY = "grain"
GOLD_BAR_ITEM_KEY = "gold_bar"
GRAIN_CONTRIBUTION_COST = 2
GOLD_BAR_CONTRIBUTION_COST = 50


def _promote_resource_to_warehouse(GuildWarehouse, guild_id, item_key, quantity, contribution_cost):
    if quantity <= 0:
        return

    warehouse_row, _created = GuildWarehouse.objects.get_or_create(
        guild_id=guild_id,
        item_key=item_key,
        defaults={
            "quantity": 0,
            "contribution_cost": contribution_cost,
            "total_produced": 0,
            "total_exchanged": 0,
        },
    )
    warehouse_row.quantity = int(warehouse_row.quantity or 0) + quantity
    warehouse_row.total_produced = int(warehouse_row.total_produced or 0) + quantity
    warehouse_row.contribution_cost = contribution_cost
    warehouse_row.save(update_fields=["quantity", "total_produced", "contribution_cost"])


def forward_promote_resources(apps, schema_editor):
    Guild = apps.get_model("guilds", "Guild")
    GuildWarehouse = apps.get_model("guilds", "GuildWarehouse")

    for guild in Guild.objects.all().iterator():
        grain_quantity = int(guild.grain or 0)
        gold_bar_quantity = int(guild.gold_bar or 0)

        _promote_resource_to_warehouse(
            GuildWarehouse,
            guild.id,
            GRAIN_ITEM_KEY,
            grain_quantity,
            GRAIN_CONTRIBUTION_COST,
        )
        _promote_resource_to_warehouse(
            GuildWarehouse,
            guild.id,
            GOLD_BAR_ITEM_KEY,
            gold_bar_quantity,
            GOLD_BAR_CONTRIBUTION_COST,
        )

        if grain_quantity > 0 or gold_bar_quantity > 0:
            Guild.objects.filter(pk=guild.pk).update(grain=0, gold_bar=0)


def _load_resource_row(GuildWarehouse, guild_id, item_key):
    return GuildWarehouse.objects.filter(guild_id=guild_id, item_key=item_key).first()


def _assert_safe_to_reverse_resource_row(resource_row, item_key):
    if resource_row is None:
        return

    quantity = int(resource_row.quantity or 0)
    total_produced = int(resource_row.total_produced or 0)
    total_exchanged = int(resource_row.total_exchanged or 0)
    if total_exchanged > 0 or total_produced != quantity:
        raise RuntimeError(
            f"cannot safely reverse promoted resource row: item_key={item_key} "
            f"quantity={quantity} total_produced={total_produced} total_exchanged={total_exchanged}"
        )


def backward_restore_resources(apps, schema_editor):
    Guild = apps.get_model("guilds", "Guild")
    GuildWarehouse = apps.get_model("guilds", "GuildWarehouse")

    for guild in Guild.objects.all().iterator():
        grain_row = _load_resource_row(GuildWarehouse, guild.id, GRAIN_ITEM_KEY)
        gold_bar_row = _load_resource_row(GuildWarehouse, guild.id, GOLD_BAR_ITEM_KEY)
        _assert_safe_to_reverse_resource_row(grain_row, GRAIN_ITEM_KEY)
        _assert_safe_to_reverse_resource_row(gold_bar_row, GOLD_BAR_ITEM_KEY)

        grain_quantity = int((grain_row.quantity if grain_row is not None else 0) or 0)
        gold_bar_quantity = int((gold_bar_row.quantity if gold_bar_row is not None else 0) or 0)

        if grain_quantity or gold_bar_quantity:
            Guild.objects.filter(pk=guild.pk).update(grain=grain_quantity, gold_bar=gold_bar_quantity)

        GuildWarehouse.objects.filter(guild_id=guild.id, item_key__in=[GRAIN_ITEM_KEY, GOLD_BAR_ITEM_KEY]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("guilds", "0011_guildmember_daily_donation_gold_bar"),
    ]

    operations = [
        migrations.RunPython(forward_promote_resources, backward_restore_resources),
    ]
