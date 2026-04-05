from __future__ import annotations

from importlib import import_module

import pytest

from gameplay.services.manor.core import ensure_manor
from guilds.models import Guild, GuildWarehouse


@pytest.mark.django_db
def test_resource_promotion_migration_merges_remaining_old_fields_into_existing_resource_rows(django_user_model):
    migration_module = import_module("guilds.migrations.0012_promote_grain_gold_bar_to_warehouse_items")

    leader = django_user_model.objects.create_user(username="guild_migration_existing_row", password="pass123")
    ensure_manor(leader)
    guild = Guild.objects.create(
        name="迁移冲突帮",
        founder=leader,
        is_active=True,
        grain=12,
        gold_bar=3,
    )
    GuildWarehouse.objects.create(guild=guild, item_key="grain", quantity=1, contribution_cost=9, total_produced=4)
    GuildWarehouse.objects.create(guild=guild, item_key="gold_bar", quantity=5, contribution_cost=7, total_produced=8)

    class _Apps:
        @staticmethod
        def get_model(app_label, model_name):
            assert app_label == "guilds"
            if model_name == "Guild":
                return Guild
            if model_name == "GuildWarehouse":
                return GuildWarehouse
            raise LookupError(model_name)

    migration_module.forward_promote_resources(_Apps(), None)

    guild.refresh_from_db()
    grain_row = GuildWarehouse.objects.get(guild=guild, item_key="grain")
    gold_bar_row = GuildWarehouse.objects.get(guild=guild, item_key="gold_bar")

    assert guild.grain == 0
    assert guild.gold_bar == 0
    assert grain_row.quantity == 13
    assert grain_row.total_produced == 16
    assert grain_row.contribution_cost == 2
    assert gold_bar_row.quantity == 8
    assert gold_bar_row.total_produced == 11
    assert gold_bar_row.contribution_cost == 50


@pytest.mark.django_db
def test_resource_promotion_migration_moves_resources_into_warehouse_and_can_roll_back(django_user_model):
    migration_module = import_module("guilds.migrations.0012_promote_grain_gold_bar_to_warehouse_items")

    leader = django_user_model.objects.create_user(username="guild_migration_roundtrip", password="pass123")
    ensure_manor(leader)
    guild = Guild.objects.create(
        name="迁移往返帮",
        founder=leader,
        is_active=True,
        grain=12,
        gold_bar=3,
    )

    class _Apps:
        @staticmethod
        def get_model(app_label, model_name):
            assert app_label == "guilds"
            if model_name == "Guild":
                return Guild
            if model_name == "GuildWarehouse":
                return GuildWarehouse
            raise LookupError(model_name)

    migration_module.forward_promote_resources(_Apps(), None)

    guild.refresh_from_db()
    grain_row = GuildWarehouse.objects.get(guild=guild, item_key="grain")
    gold_bar_row = GuildWarehouse.objects.get(guild=guild, item_key="gold_bar")

    assert guild.grain == 0
    assert guild.gold_bar == 0
    assert grain_row.quantity == 12
    assert grain_row.total_produced == 12
    assert grain_row.contribution_cost == 2
    assert gold_bar_row.quantity == 3
    assert gold_bar_row.total_produced == 3
    assert gold_bar_row.contribution_cost == 50

    migration_module.backward_restore_resources(_Apps(), None)

    guild.refresh_from_db()

    assert guild.grain == 12
    assert guild.gold_bar == 3
    assert GuildWarehouse.objects.filter(guild=guild, item_key__in=["grain", "gold_bar"]).exists() is False


@pytest.mark.django_db
def test_resource_promotion_migration_roll_back_fails_closed_when_existing_resource_rows_were_merged(django_user_model):
    migration_module = import_module("guilds.migrations.0012_promote_grain_gold_bar_to_warehouse_items")

    leader = django_user_model.objects.create_user(username="guild_migration_reverse_guard", password="pass123")
    ensure_manor(leader)
    guild = Guild.objects.create(
        name="迁移回滚保护帮",
        founder=leader,
        is_active=True,
        grain=12,
        gold_bar=3,
    )
    GuildWarehouse.objects.create(
        guild=guild,
        item_key="grain",
        quantity=1,
        contribution_cost=9,
        total_produced=4,
        total_exchanged=2,
    )

    class _Apps:
        @staticmethod
        def get_model(app_label, model_name):
            assert app_label == "guilds"
            if model_name == "Guild":
                return Guild
            if model_name == "GuildWarehouse":
                return GuildWarehouse
            raise LookupError(model_name)

    migration_module.forward_promote_resources(_Apps(), None)

    with pytest.raises(RuntimeError, match="cannot safely reverse"):
        migration_module.backward_restore_resources(_Apps(), None)

    guild.refresh_from_db()
    grain_row = GuildWarehouse.objects.get(guild=guild, item_key="grain")

    assert guild.grain == 0
    assert guild.gold_bar == 0
    assert grain_row.quantity == 13
    assert grain_row.total_produced == 16
    assert grain_row.total_exchanged == 2
