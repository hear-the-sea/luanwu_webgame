from __future__ import annotations

import threading
import uuid

import pytest
from django.db import connection

from core.exceptions import GuildContributionError, GuildWarehouseError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from guilds.models import Guild, GuildDonationLog, GuildExchangeLog, GuildMember, GuildWarehouse
from guilds.services.contribution import donate_resource
from guilds.services.warehouse import exchange_item

pytestmark = [pytest.mark.integration]


@pytest.mark.django_db(transaction=True)
def test_donate_gold_bar_concurrent_requests_allow_only_one_success(monkeypatch, django_user_model):
    if connection.vendor == "sqlite":
        pytest.skip("SQLite does not provide row-level select_for_update semantics for this concurrency scenario")

    user = django_user_model.objects.create_user(
        username=f"guild_donate_concurrent_{uuid.uuid4().hex[:8]}",
        password="pass123",
    )
    manor = ensure_manor(user)
    gold_bar_template, _ = ItemTemplate.objects.get_or_create(
        key="gold_bar",
        defaults={
            "name": "金条",
            "effect_type": ItemTemplate.EffectType.TOOL,
            "is_usable": False,
            "tradeable": False,
        },
    )
    InventoryItem.objects.update_or_create(
        manor=manor,
        template=gold_bar_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        defaults={"quantity": 10},
    )
    guild = Guild.objects.create(name=f"并发捐赠帮{uuid.uuid4().hex[:6]}", founder=user, is_active=True)
    member = GuildMember.objects.create(guild=guild, user=user, position="leader", is_active=True)

    patched_limits = {"silver": 100000, "grain": 50000, "gold_bar": 1}
    monkeypatch.setattr("guilds.constants.DAILY_DONATION_LIMITS", patched_limits)

    barrier = threading.Barrier(2)
    successes: list[int] = []
    errors: list[Exception] = []

    def _worker() -> None:
        try:
            local_member = GuildMember.objects.get(pk=member.pk)
            barrier.wait(timeout=5)
            donate_resource(local_member, "gold_bar", 1)
            successes.append(local_member.pk)
        except Exception as exc:  # pragma: no cover - validated by assertions below
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    guild.refresh_from_db()
    member.refresh_from_db()
    inventory_item = InventoryItem.objects.get(
        manor=manor,
        template=gold_bar_template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    assert len(successes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], GuildContributionError)
    assert "今日金条捐赠已达上限" in str(errors[0])
    assert guild.gold_bar == 1
    assert member.daily_donation_gold_bar == 1
    assert inventory_item.quantity == 9
    assert GuildDonationLog.objects.filter(member=member, resource_type="gold_bar", amount=1).count() == 1


@pytest.mark.django_db(transaction=True)
def test_exchange_item_concurrent_requests_allow_only_one_stock_consumer(django_user_model):
    if connection.vendor == "sqlite":
        pytest.skip("SQLite does not provide row-level select_for_update semantics for this concurrency scenario")

    user = django_user_model.objects.create_user(
        username=f"guild_exchange_concurrent_{uuid.uuid4().hex[:8]}",
        password="pass123",
    )
    manor = ensure_manor(user)
    guild = Guild.objects.create(name=f"并发仓库帮{uuid.uuid4().hex[:6]}", founder=user, is_active=True)
    member = GuildMember.objects.create(
        guild=guild,
        user=user,
        position="leader",
        is_active=True,
        current_contribution=100,
    )
    item_key = f"guild_concurrency_item_{uuid.uuid4().hex[:8]}"
    template = ItemTemplate.objects.create(
        key=item_key,
        name="并发仓库道具",
        effect_type=ItemTemplate.EffectType.TOOL,
        is_usable=True,
        tradeable=False,
    )
    GuildWarehouse.objects.create(guild=guild, item_key=item_key, quantity=1, contribution_cost=5)

    barrier = threading.Barrier(2)
    successes: list[int] = []
    errors: list[Exception] = []

    def _worker() -> None:
        try:
            local_member = GuildMember.objects.get(pk=member.pk)
            barrier.wait(timeout=5)
            exchange_item(local_member, item_key, 1)
            successes.append(local_member.pk)
        except Exception as exc:  # pragma: no cover - validated by assertions below
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    member.refresh_from_db()
    inventory_item = InventoryItem.objects.get(
        manor=manor,
        template=template,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )

    assert len(successes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], GuildWarehouseError)
    assert str(errors[0]) in {"库存不足，兑换失败", "库存不足，剩余0件", "物品不存在"}
    assert member.current_contribution == 95
    assert member.daily_exchange_count == 1
    assert inventory_item.quantity == 1
    assert GuildWarehouse.objects.filter(guild=guild, item_key=item_key).exists() is False
    assert GuildExchangeLog.objects.filter(member=member, item_key=item_key, quantity=1).count() == 1
