from __future__ import annotations

import threading
import uuid

import pytest
from django.db import connection

from core.exceptions import GuildContributionError, GuildValidationError, GuildWarehouseError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from guests.models import Guest, GuestArchetype, GuestRarity, GuestTemplate
from guilds.models import Guild, GuildDonationLog, GuildExchangeLog, GuildMember, GuildRaidRun, GuildWarehouse
from guilds.services import hero_pool as hero_pool_service
from guilds.services.contribution import donate_resource
from guilds.services.guild_raids import start_guild_raid
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
    warehouse_row = GuildWarehouse.objects.get(guild=guild, item_key="gold_bar")
    assert guild.gold_bar == 0
    assert warehouse_row.quantity == 1
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


@pytest.mark.django_db(transaction=True)
def test_concurrent_guild_raid_launch_allows_only_one_active_run(monkeypatch, django_user_model):
    if connection.vendor == "sqlite":
        pytest.skip("SQLite does not provide row-level select_for_update semantics for this concurrency scenario")

    attacker_user = django_user_model.objects.create_user(
        username=f"guild_raid_concurrent_attacker_{uuid.uuid4().hex[:8]}",
        password="pass123",
    )
    attacker_manor = ensure_manor(attacker_user)
    defender_user = django_user_model.objects.create_user(
        username=f"guild_raid_concurrent_defender_{uuid.uuid4().hex[:8]}",
        password="pass123",
    )
    ensure_manor(defender_user)
    attacker_guild = Guild.objects.create(
        name=f"并发进攻帮{uuid.uuid4().hex[:6]}",
        founder=attacker_user,
        is_active=True,
        silver=50000,
        level=5,
    )
    attacker_member = GuildMember.objects.create(
        guild=attacker_guild, user=attacker_user, position="leader", is_active=True
    )
    defender_guild = Guild.objects.create(
        name=f"并发防守帮{uuid.uuid4().hex[:6]}",
        founder=defender_user,
        is_active=True,
        silver=50000,
        level=5,
    )
    GuildMember.objects.create(guild=defender_guild, user=defender_user, position="leader", is_active=True)
    guest_template = GuestTemplate.objects.create(
        key=f"guild_raid_concurrent_tpl_{uuid.uuid4().hex[:8]}",
        name="并发出征模板",
        archetype=GuestArchetype.MILITARY,
        rarity=GuestRarity.GREEN,
    )
    guest = Guest.objects.create(
        manor=attacker_manor,
        template=guest_template,
        custom_name="并发门客",
        level=20,
        force=120,
        intellect=80,
        defense_stat=100,
        agility=90,
        luck=60,
    )
    pool_entry = hero_pool_service.submit_hero_pool_entry(attacker_member, guest_id=guest.id, slot_index=1).entry
    hero_pool_service.add_lineup_entry(guild=attacker_guild, operator=attacker_user, pool_entry_id=pool_entry.id)

    monkeypatch.setattr("guilds.services.guild_raids.calculate_guild_raid_travel_time", lambda *_args, **_kwargs: 60)
    monkeypatch.setattr("guilds.services.guild_raids.schedule_guild_raid_completion", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("guilds.services.guild_raids.send_guild_raid_warning_messages", lambda *_args, **_kwargs: None)

    barrier = threading.Barrier(2)
    successes: list[int] = []
    errors: list[Exception] = []

    def _worker() -> None:
        try:
            local_guild = Guild.objects.get(pk=attacker_guild.pk)
            local_defender = Guild.objects.get(pk=defender_guild.pk)
            barrier.wait(timeout=5)
            run = start_guild_raid(
                guild=local_guild,
                defender_guild=local_defender,
                operator=attacker_user,
                pool_entry_ids=[pool_entry.id],
                troop_loadout={},
            )
            successes.append(run.id)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(successes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], GuildValidationError)
    assert "当前已有帮会对战队伍出征中" in str(errors[0])
    assert GuildWarehouse.objects.filter(guild=attacker_guild, item_key="experience_fruit").exists() is False
    assert Guild.objects.get(pk=attacker_guild.pk).pvp_attack_count_today == 1
    assert GuildRaidRun.objects.filter(attacker_guild=attacker_guild, status=GuildRaidRun.Status.MARCHING).count() == 1
