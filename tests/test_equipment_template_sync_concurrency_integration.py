from __future__ import annotations

import threading
import time
from datetime import timedelta

import pytest
from django.db import close_old_connections, connection
from django.utils import timezone

from gameplay.models import InventoryItem, ItemTemplate, Manor
from gameplay.services import equipment_template_sync as sync_service
from gameplay.services.manor.core import ensure_manor
from trade.models import MarketListing
from trade.services import market_service

pytestmark = [pytest.mark.integration]


def _require_isolated_mysql() -> None:
    if connection.vendor != "mysql":
        pytest.skip("equipment template sync concurrency evidence requires MySQL row locks")
    if str(connection.settings_dict["NAME"]) != "test_webgame":
        pytest.skip("equipment template sync concurrency evidence only runs on test_webgame")


@pytest.mark.django_db(transaction=True)
def test_template_sync_waits_for_market_listing_before_locking_manor(
    django_user_model,
    monkeypatch,
) -> None:
    _require_isolated_mysql()
    user = django_user_model.objects.create_user(
        username="equipment_sync_market_lock_order",
        password="pass123",
    )
    manor = ensure_manor(user)
    legacy_item = ItemTemplate.objects.create(
        key="equip_xiaoweitoukie",
        name="旧校尉头盔",
        effect_type="equip_helmet",
        rarity="blue",
        effect_payload={"defense": 10},
        tradeable=True,
    )
    canonical_item = ItemTemplate.objects.create(
        key="equip_xiaoweitoukui",
        name="校尉头盔",
        effect_type="equip_helmet",
        rarity="blue",
        effect_payload={"defense": 20},
        tradeable=True,
    )
    listing = MarketListing.objects.create(
        seller=manor,
        item_template=legacy_item,
        quantity=2,
        unit_price=100,
        total_price=200,
        duration=MarketListing.Duration.SHORT,
        listing_fee=1,
        expires_at=timezone.now() + timedelta(hours=1),
    )

    cancel_waiting_for_manor = threading.Event()
    release_cancel = threading.Event()
    sync_trade_lock_started = threading.Event()
    cancel_results: list[dict] = []
    sync_results: list[sync_service.EquipmentTemplateSyncReport] = []
    errors: list[BaseException] = []
    result_guard = threading.Lock()

    manor_manager = Manor.objects
    original_manor_lock = manor_manager.select_for_update
    original_trade_lock = sync_service._lock_legacy_trade_references

    def _pause_cancel_before_manor(*args, **kwargs):
        if threading.current_thread().name == "market-cancel-worker":
            cancel_waiting_for_manor.set()
            if not release_cancel.wait(timeout=10):
                raise TimeoutError("timed out waiting to release market cancellation")
        return original_manor_lock(*args, **kwargs)

    def _signal_trade_lock_started() -> None:
        sync_trade_lock_started.set()
        original_trade_lock()

    monkeypatch.setattr(manor_manager, "select_for_update", _pause_cancel_before_manor)
    monkeypatch.setattr(sync_service, "_lock_legacy_trade_references", _signal_trade_lock_started)

    def _cancel_worker() -> None:
        close_old_connections()
        try:
            result = market_service.cancel_listing(manor, listing.pk)
            with result_guard:
                cancel_results.append(result)
        except BaseException as exc:  # pragma: no cover - asserted below
            with result_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    def _sync_worker() -> None:
        close_old_connections()
        try:
            report = sync_service.synchronize_equipment_templates([canonical_item.key])
            with result_guard:
                sync_results.append(report)
        except BaseException as exc:  # pragma: no cover - asserted below
            with result_guard:
                errors.append(exc)
        finally:
            close_old_connections()

    cancel_thread = threading.Thread(
        target=_cancel_worker,
        name="market-cancel-worker",
        daemon=True,
    )
    sync_thread = threading.Thread(
        target=_sync_worker,
        name="equipment-sync-worker",
        daemon=True,
    )
    cancel_thread.start()
    try:
        assert cancel_waiting_for_manor.wait(timeout=10)
        sync_thread.start()
        assert sync_trade_lock_started.wait(timeout=10)
        time.sleep(0.2)
        assert sync_thread.is_alive() is True
    finally:
        release_cancel.set()

    cancel_thread.join(timeout=20)
    sync_thread.join(timeout=20)
    assert cancel_thread.is_alive() is False
    assert sync_thread.is_alive() is False
    assert errors == []
    assert len(cancel_results) == 1
    assert len(sync_results) == 1
    assert sync_results[0].item_aliases_merged == 1

    listing.refresh_from_db()
    assert listing.status == MarketListing.Status.CANCELLED
    assert listing.item_template_id == canonical_item.pk
    assert not ItemTemplate.objects.filter(pk=legacy_item.pk).exists()
    assert (
        InventoryItem.objects.get(
            manor=manor,
            template=canonical_item,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        ).quantity
        == 2
    )
