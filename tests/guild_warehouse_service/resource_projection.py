from __future__ import annotations

import pytest

from guilds.models import GuildWarehouse
from guilds.services import warehouse as warehouse_service

pytest_plugins = ("tests.guild_warehouse_service.support",)


@pytest.mark.django_db
def test_get_warehouse_items_only_projects_silver_after_resource_item_migration(guild_with_mixed_warehouse_resources):
    guild = guild_with_mixed_warehouse_resources

    payload = warehouse_service.get_warehouse_items(guild, page=1, per_page=20)

    projected_keys = {item.item_key for item in payload["items"] if getattr(item, "is_projected", False)}
    assert projected_keys == {"silver"}


@pytest.mark.django_db
def test_get_warehouse_items_reads_grain_and_gold_bar_from_real_rows(guild_with_mixed_warehouse_resources):
    guild = guild_with_mixed_warehouse_resources

    payload = warehouse_service.get_warehouse_items(guild, page=1, per_page=20)
    quantities = {item.item_key: item.display_quantity for item in payload["items"]}

    assert quantities["grain"] == 25
    assert quantities["gold_bar"] == 4


@pytest.mark.django_db
def test_get_warehouse_items_projects_legacy_grain_and_gold_bar_when_real_rows_missing(
    guild_member_with_projected_resources,
):
    guild, _member, _manor = guild_member_with_projected_resources

    payload = warehouse_service.get_warehouse_items(guild, page=1, per_page=20)
    projected_items = {item.item_key: item for item in payload["items"] if getattr(item, "is_projected", False)}

    assert projected_items["silver"].display_quantity == 2_000
    assert projected_items["grain"].display_quantity == 4_000
    assert projected_items["gold_bar"].display_quantity == 3


@pytest.mark.django_db
def test_get_warehouse_items_exposes_resource_bundle_exchange_rules(guild_member_with_projected_resources):
    guild, member, _manor = guild_member_with_projected_resources

    payload = warehouse_service.get_warehouse_items(guild, page=1, per_page=20, member=member)
    resources = {item.item_key: item for item in payload["items"] if item.item_key in {"silver", "grain", "gold_bar"}}

    assert (resources["silver"].exchange_unit, resources["silver"].exchange_unit_cost) == (1_000, 1)
    assert (resources["grain"].exchange_unit, resources["grain"].exchange_unit_cost) == (2_000, 1)
    assert (resources["gold_bar"].exchange_unit, resources["gold_bar"].exchange_unit_cost) == (1, 1_200)


@pytest.mark.django_db
def test_donate_grain_adds_real_guild_warehouse_item(guild_member_ready_for_grain_donation):
    from guilds.services import contribution as contribution_service

    member, manor = guild_member_ready_for_grain_donation

    contribution_service.donate_resource(member, "grain", 2_000)

    warehouse_row = GuildWarehouse.objects.get(guild=member.guild, item_key="grain")
    member.refresh_from_db()
    member.guild.refresh_from_db()
    manor.refresh_from_db()

    assert warehouse_row.quantity == 2_000
    assert warehouse_row.contribution_cost == 1
    assert member.current_contribution == 1
    assert member.guild.grain == 0
    assert manor.grain == 3_000
