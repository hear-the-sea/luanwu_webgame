from __future__ import annotations

import pytest
from django.utils import timezone

from guilds.models import Guild, GuildResourceLog, GuildTechnology, GuildWarehouse
from guilds.services import technology_production as production_service
from guilds.services.warehouse_config import ProductionItem


@pytest.mark.django_db
def test_daily_production_prioritizes_rarity_then_exchange_cost_and_stops_on_shortage(django_user_model, monkeypatch):
    founder = django_user_model.objects.create_user(username="guild_production_upkeep", password="pass123")
    guild = Guild.objects.create(
        name="产出维护测试帮",
        founder=founder,
        is_active=True,
        silver=181,
    )
    grain = GuildWarehouse.objects.create(guild=guild, item_key="grain", quantity=271)
    tech_alpha = GuildTechnology.objects.create(guild=guild, tech_key="tech_alpha", level=1)
    tech_beta = GuildTechnology.objects.create(guild=guild, tech_key="tech_beta", level=1)
    now = timezone.now()

    monkeypatch.setattr(production_service, "PRODUCTION_TECH_KEYS", ("tech_alpha", "tech_beta"))
    monkeypatch.setattr(
        production_service,
        "TECHNOLOGY_NODE_BY_KEY",
        {
            "tech_alpha": {"production_interval_days": 1},
            "tech_beta": {"production_interval_days": 1},
        },
    )
    monkeypatch.setattr(
        production_service,
        "get_technology_production_items",
        lambda tech_key, _level: {
            "tech_alpha": [
                ProductionItem("purple_item", 1, 10),
                ProductionItem("orange_low_item", 1, 2),
            ],
            "tech_beta": [ProductionItem("orange_high_item", 1, 3)],
        }[tech_key],
    )
    monkeypatch.setattr(
        production_service,
        "get_item_rarity_map",
        lambda item_keys: {
            "purple_item": "purple",
            "orange_low_item": "orange",
            "orange_high_item": "orange",
        },
    )
    monkeypatch.setattr(production_service.random, "shuffle", lambda _items: None)

    result = production_service.process_guild_technology_production(guild.pk, now=now)

    guild.refresh_from_db()
    grain.refresh_from_db()
    tech_alpha.refresh_from_db()
    tech_beta.refresh_from_db()

    assert result.due_tech_keys == ("tech_alpha", "tech_beta")
    assert result.produced_item_count == 1
    assert result.silver_spent == 180
    assert result.grain_spent == 270
    assert result.stopped_item_key == "orange_low_item"
    assert guild.silver == 1
    assert grain.quantity == 1
    assert grain.total_exchanged == 0
    assert GuildWarehouse.objects.get(guild=guild, item_key="orange_high_item").quantity == 1
    assert not GuildWarehouse.objects.filter(guild=guild, item_key="orange_low_item").exists()
    assert not GuildWarehouse.objects.filter(guild=guild, item_key="purple_item").exists()
    assert tech_alpha.last_production_at == now
    assert tech_beta.last_production_at == now

    resource_log = GuildResourceLog.objects.get(guild=guild, action="production")
    assert resource_log.silver_change == -180
    assert resource_log.grain_change == -270
    assert "停止于orange_low_item" in resource_log.note


@pytest.mark.django_db
def test_daily_production_randomizes_equal_priority_items(django_user_model, monkeypatch):
    founder = django_user_model.objects.create_user(username="guild_production_tie", password="pass123")
    guild = Guild.objects.create(name="产出同优先级测试帮", founder=founder, is_active=True, silver=121)
    GuildWarehouse.objects.create(guild=guild, item_key="grain", quantity=181)
    GuildTechnology.objects.create(guild=guild, tech_key="tech_alpha", level=1)
    now = timezone.now()

    monkeypatch.setattr(production_service, "PRODUCTION_TECH_KEYS", ("tech_alpha",))
    monkeypatch.setattr(
        production_service,
        "TECHNOLOGY_NODE_BY_KEY",
        {"tech_alpha": {"production_interval_days": 1}},
    )
    monkeypatch.setattr(
        production_service,
        "get_technology_production_items",
        lambda *_args: [
            ProductionItem("tie_first", 1, 2),
            ProductionItem("tie_second", 1, 2),
        ],
    )
    monkeypatch.setattr(
        production_service,
        "get_item_rarity_map",
        lambda _item_keys: {"tie_first": "orange", "tie_second": "orange"},
    )
    monkeypatch.setattr(production_service.random, "shuffle", lambda items: items.reverse())

    result = production_service.process_guild_technology_production(guild.pk, now=now)

    assert result.produced_item_count == 1
    assert result.stopped_item_key == "tie_first"
    assert not GuildWarehouse.objects.filter(guild=guild, item_key="tie_first").exists()
    assert GuildWarehouse.objects.get(guild=guild, item_key="tie_second").quantity == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("silver", "grain"),
    [(60, 1_000), (1_000, 90)],
    ids=["silver-shortage", "grain-shortage"],
)
def test_daily_production_stops_when_either_resource_is_insufficient(django_user_model, monkeypatch, silver, grain):
    founder = django_user_model.objects.create_user(
        username=f"guild_production_shortage_{silver}_{grain}", password="pass123"
    )
    guild = Guild.objects.create(
        name=f"资源不足测试帮{silver}-{grain}",
        founder=founder,
        is_active=True,
        silver=silver,
    )
    GuildWarehouse.objects.create(guild=guild, item_key="grain", quantity=grain)
    GuildTechnology.objects.create(guild=guild, tech_key="tech_alpha", level=1)
    now = timezone.now()

    monkeypatch.setattr(production_service, "PRODUCTION_TECH_KEYS", ("tech_alpha",))
    monkeypatch.setattr(
        production_service,
        "TECHNOLOGY_NODE_BY_KEY",
        {"tech_alpha": {"production_interval_days": 1}},
    )
    monkeypatch.setattr(
        production_service,
        "get_technology_production_items",
        lambda *_args: [ProductionItem("limited_item", 2, 1)],
    )
    monkeypatch.setattr(
        production_service,
        "get_item_rarity_map",
        lambda _item_keys: {"limited_item": "green"},
    )

    result = production_service.process_guild_technology_production(guild.pk, now=now)

    assert result.produced_item_count == 1
    assert result.silver_spent == 60
    assert result.grain_spent == 90
    assert result.stopped_item_key == "limited_item"
    assert GuildWarehouse.objects.get(guild=guild, item_key="limited_item").quantity == 1
