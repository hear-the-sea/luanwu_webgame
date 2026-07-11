from __future__ import annotations

import pytest
from django.db import OperationalError as DatabaseOperationalError
from django.db import ProgrammingError
from django.utils import timezone
from django_redis.exceptions import ConnectionInterrupted
from kombu.exceptions import OperationalError

from trade.models import ShopStock
from trade.services.shop_config import ShopItemConfig
from trade.tasks import (
    create_auction_round_task,
    process_expired_listings,
    process_pending_auction_deliveries_task,
    refresh_shop_stock,
    settle_auction_round_task,
)


@pytest.mark.django_db
def test_refresh_shop_stock_creates_and_updates_daily_items(monkeypatch):
    today = timezone.now().date()

    config_list = [
        ShopItemConfig(item_key="daily_item", price=None, stock=5, daily_refresh=True),
        ShopItemConfig(item_key="no_refresh", price=None, stock=5, daily_refresh=False),
        ShopItemConfig(item_key="unlimited", price=None, stock=-1, daily_refresh=True),
    ]

    monkeypatch.setattr("trade.tasks.reload_shop_config", lambda: None)
    monkeypatch.setattr("trade.tasks.get_shop_config", lambda: list(config_list))

    result = refresh_shop_stock.run()
    assert result == "refreshed 1 items"

    stock = ShopStock.objects.get(item_key="daily_item")
    assert stock.current_stock == 5
    assert stock.last_refresh == today

    assert not ShopStock.objects.filter(item_key="no_refresh").exists()
    assert not ShopStock.objects.filter(item_key="unlimited").exists()


@pytest.mark.django_db
def test_refresh_shop_stock_returns_failure_summary(monkeypatch):
    config_list = [
        ShopItemConfig(item_key="ok", price=None, stock=2, daily_refresh=True),
        ShopItemConfig(item_key="bad", price=None, stock=2, daily_refresh=True),
    ]

    monkeypatch.setattr("trade.tasks.reload_shop_config", lambda: None)
    monkeypatch.setattr("trade.tasks.get_shop_config", lambda: list(config_list))

    original_update_or_create = ShopStock.objects.update_or_create

    def _update_or_create(*args, **kwargs):
        if kwargs.get("item_key") == "bad":
            raise OSError("boom")
        return original_update_or_create(*args, **kwargs)

    monkeypatch.setattr(ShopStock.objects, "update_or_create", _update_or_create)

    result = refresh_shop_stock.run()
    assert result == "refreshed 1 items, 1 failed, failed_item_keys=['bad']"

    assert ShopStock.objects.get(item_key="ok").current_stock == 2


@pytest.mark.django_db
def test_settle_auction_round_task_falls_back_to_sync_create_when_dispatch_fails(monkeypatch):
    monkeypatch.setattr(
        "trade.services.auction_service.settle_auction_round",
        lambda: {"settled": 1, "sold": 2, "unsold": 0, "total_gold_bars": 20},
    )

    def _raise_dispatch_error(*_args, **_kwargs):
        raise ConnectionError("dispatch failed")

    monkeypatch.setattr("trade.tasks.create_auction_round_task.apply_async", _raise_dispatch_error)
    called = {"sync_create": 0}
    monkeypatch.setattr(
        "trade.services.auction_service.create_auction_round",
        lambda: called.__setitem__("sync_create", called["sync_create"] + 1),
    )

    result = settle_auction_round_task.run()
    assert "结算完成" in result
    assert "售出 2 件" in result
    assert called["sync_create"] == 1


@pytest.mark.django_db
def test_settle_auction_round_task_falls_back_to_sync_create_on_broker_operational_error(monkeypatch):
    monkeypatch.setattr(
        "trade.services.auction_service.settle_auction_round",
        lambda: {"settled": 1, "sold": 2, "unsold": 0, "total_gold_bars": 20},
    )

    def _raise_operational_error(*_args, **_kwargs):
        raise OperationalError("broker unavailable")

    monkeypatch.setattr("trade.tasks.create_auction_round_task.apply_async", _raise_operational_error)
    called = {"sync_create": 0}
    monkeypatch.setattr(
        "trade.services.auction_service.create_auction_round",
        lambda: called.__setitem__("sync_create", called["sync_create"] + 1),
    )

    result = settle_auction_round_task.run()

    assert "结算完成" in result
    assert called["sync_create"] == 1


@pytest.mark.django_db
def test_refresh_shop_stock_skips_invalid_item_configs(monkeypatch):
    config_list = [
        ShopItemConfig(item_key="", price=None, stock=5, daily_refresh=True),
        ShopItemConfig(item_key="bad_stock", price=None, stock=-3, daily_refresh=True),
        ShopItemConfig(item_key="daily_item", price=None, stock=7, daily_refresh=True),
    ]

    monkeypatch.setattr("trade.tasks.reload_shop_config", lambda: None)
    monkeypatch.setattr("trade.tasks.get_shop_config", lambda: list(config_list))

    result = refresh_shop_stock.run()
    assert result == "refreshed 1 items"
    assert ShopStock.objects.filter(item_key="daily_item", current_stock=7).exists()
    assert not ShopStock.objects.filter(item_key="bad_stock").exists()


@pytest.mark.django_db
def test_refresh_shop_stock_retries_when_loading_config_fails(monkeypatch):
    monkeypatch.setattr("trade.tasks.reload_shop_config", lambda: None)
    monkeypatch.setattr(
        "trade.tasks.get_shop_config",
        lambda: (_ for _ in ()).throw(OSError("config failed")),
    )

    called = {"retry": 0}

    def _retry(exc):
        called["retry"] += 1
        raise OSError(f"retry called: {exc}")

    monkeypatch.setattr(refresh_shop_stock, "retry", _retry)

    with pytest.raises(OSError, match="retry called"):
        refresh_shop_stock.run()

    assert called["retry"] == 1


@pytest.mark.django_db
def test_refresh_shop_stock_runtime_marker_bubbles_up_without_retry(monkeypatch):
    monkeypatch.setattr("trade.tasks.reload_shop_config", lambda: None)
    monkeypatch.setattr(
        "trade.tasks.get_shop_config",
        lambda: (_ for _ in ()).throw(RuntimeError("config backend unavailable")),
    )
    monkeypatch.setattr(
        refresh_shop_stock,
        "retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )

    with pytest.raises(RuntimeError, match="config backend unavailable"):
        refresh_shop_stock.run()


@pytest.mark.django_db
def test_process_expired_listings_coerces_invalid_count(monkeypatch):
    monkeypatch.setattr("trade.services.market_service.expire_listings", lambda: "invalid")
    result = process_expired_listings.run()
    assert result == "处理了 0 个过期挂单"


@pytest.mark.django_db
def test_process_expired_listings_retries_on_error(monkeypatch):
    monkeypatch.setattr(
        "trade.services.market_service.expire_listings",
        lambda: (_ for _ in ()).throw(OSError("expire failed")),
    )

    called = {"retry": 0}

    def _retry(exc):
        called["retry"] += 1
        raise OSError(f"retry called: {exc}")

    monkeypatch.setattr(process_expired_listings, "retry", _retry)

    with pytest.raises(OSError, match="retry called"):
        process_expired_listings.run()

    assert called["retry"] == 1


@pytest.mark.django_db
def test_process_pending_auction_deliveries_task_reports_processed_count(monkeypatch):
    monkeypatch.setattr("trade.services.auction.delivery_outbox.process_pending_auction_deliveries", lambda: 3)

    result = process_pending_auction_deliveries_task.run()

    assert result == "处理了 3 个待交付拍卖奖励"


@pytest.mark.django_db
def test_process_pending_auction_deliveries_task_retries_on_database_error(monkeypatch):
    error = DatabaseOperationalError("delivery scan failed")
    monkeypatch.setattr(
        "trade.services.auction.delivery_outbox.process_pending_auction_deliveries",
        lambda: (_ for _ in ()).throw(error),
    )
    retried_with = []

    def _retry(exc):
        retried_with.append(exc)
        raise RuntimeError("retry called")

    monkeypatch.setattr(process_pending_auction_deliveries_task, "retry", _retry)

    with pytest.raises(RuntimeError, match="retry called"):
        process_pending_auction_deliveries_task.run()

    assert retried_with == [error]


@pytest.mark.django_db
def test_process_pending_auction_deliveries_task_programming_error_bubbles_without_retry(monkeypatch):
    error = ProgrammingError("delivery scan contract bug")
    monkeypatch.setattr(
        "trade.services.auction.delivery_outbox.process_pending_auction_deliveries",
        lambda: (_ for _ in ()).throw(error),
    )
    retry_calls = []

    def _retry(exc):
        retry_calls.append(exc)
        raise AssertionError("retry should not be called")

    monkeypatch.setattr(process_pending_auction_deliveries_task, "retry", _retry)

    with pytest.raises(ProgrammingError) as exc_info:
        process_pending_auction_deliveries_task.run()

    assert exc_info.value is error
    assert retry_calls == []


@pytest.mark.django_db
def test_settle_auction_round_task_retries_on_cache_infrastructure_error(monkeypatch):
    error = ConnectionInterrupted("settlement lock cache down")
    monkeypatch.setattr(
        "trade.services.auction_service.settle_auction_round",
        lambda: (_ for _ in ()).throw(error),
    )
    retried_with = []

    def _retry(exc):
        retried_with.append(exc)
        raise RuntimeError("retry called")

    monkeypatch.setattr(settle_auction_round_task, "retry", _retry)

    with pytest.raises(RuntimeError, match="retry called"):
        settle_auction_round_task.run()

    assert retried_with == [error]


@pytest.mark.django_db
def test_settle_auction_round_task_programming_error_bubbles_without_retry(monkeypatch):
    error = ProgrammingError("settlement contract bug")
    monkeypatch.setattr(
        "trade.services.auction_service.settle_auction_round",
        lambda: (_ for _ in ()).throw(error),
    )
    retry_calls = []

    def _retry(exc):
        retry_calls.append(exc)
        raise AssertionError("retry should not be called")

    monkeypatch.setattr(settle_auction_round_task, "retry", _retry)

    with pytest.raises(ProgrammingError) as exc_info:
        settle_auction_round_task.run()

    assert exc_info.value is error
    assert retry_calls == []


@pytest.mark.django_db
def test_settle_auction_round_task_tolerates_non_dict_stats(monkeypatch):
    monkeypatch.setattr("trade.services.auction_service.settle_auction_round", lambda: "invalid")
    monkeypatch.setattr(
        settle_auction_round_task,
        "retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("retry should not be called")),
    )

    result = settle_auction_round_task.run()
    assert result == "没有需要结算的拍卖轮次"


@pytest.mark.django_db
def test_settle_auction_round_task_coerces_invalid_stats_numbers(monkeypatch):
    monkeypatch.setattr(
        "trade.services.auction_service.settle_auction_round",
        lambda: {"settled": "1", "sold": "x", "unsold": None, "total_gold_bars": -7},
    )
    monkeypatch.setattr("trade.tasks.create_auction_round_task.apply_async", lambda *_args, **_kwargs: None)

    result = settle_auction_round_task.run()
    assert "结算完成" in result
    assert "售出 0 件" in result
    assert "流拍 0 件" in result
    assert "共 0 金条" in result


@pytest.mark.django_db
def test_create_auction_round_task_tolerates_slots_count_error(monkeypatch):
    class _Slots:
        def count(self):
            raise OSError("count failed")

    class _Round:
        round_number = 3
        slots = _Slots()

    monkeypatch.setattr("trade.services.auction_config.reload_auction_config", lambda: None)
    monkeypatch.setattr("trade.services.auction_service.create_auction_round", lambda: _Round())

    result = create_auction_round_task.run()
    assert result == "创建拍卖轮次 #3，拍卖位数量: 0"


@pytest.mark.django_db
def test_create_auction_round_task_slot_count_programming_error_bubbles_up(monkeypatch):
    error = ProgrammingError("broken slot count contract")

    class _Slots:
        def count(self):
            raise error

    class _Round:
        round_number = 3
        slots = _Slots()

    monkeypatch.setattr("trade.services.auction_config.reload_auction_config", lambda: None)
    monkeypatch.setattr("trade.services.auction_service.create_auction_round", lambda: _Round())
    retry_calls = []

    def _retry(exc):
        retry_calls.append(exc)
        raise AssertionError("retry should not be called")

    monkeypatch.setattr(create_auction_round_task, "retry", _retry)

    with pytest.raises(ProgrammingError) as exc_info:
        create_auction_round_task.run()

    assert exc_info.value is error
    assert retry_calls == []


@pytest.mark.django_db
def test_create_auction_round_task_retries_when_reload_fails(monkeypatch):
    monkeypatch.setattr(
        "trade.services.auction_config.reload_auction_config",
        lambda: (_ for _ in ()).throw(OSError("reload failed")),
    )

    called = {"retry": 0}

    def _retry(exc):
        called["retry"] += 1
        raise OSError(f"retry called: {exc}")

    monkeypatch.setattr(create_auction_round_task, "retry", _retry)

    with pytest.raises(OSError, match="retry called"):
        create_auction_round_task.run()

    assert called["retry"] == 1


@pytest.mark.django_db
def test_create_auction_round_task_programming_error_bubbles_without_retry(monkeypatch):
    error = ProgrammingError("create round contract bug")
    monkeypatch.setattr("trade.services.auction_config.reload_auction_config", lambda: None)
    monkeypatch.setattr(
        "trade.services.auction_service.create_auction_round",
        lambda: (_ for _ in ()).throw(error),
    )
    retry_calls = []

    def _retry(exc):
        retry_calls.append(exc)
        raise AssertionError("retry should not be called")

    monkeypatch.setattr(create_auction_round_task, "retry", _retry)

    with pytest.raises(ProgrammingError) as exc_info:
        create_auction_round_task.run()

    assert exc_info.value is error
    assert retry_calls == []


@pytest.mark.django_db
def test_settle_auction_round_task_sync_fallback_programming_error_bubbles_up(monkeypatch):
    error = ProgrammingError("sync create round contract bug")
    monkeypatch.setattr(
        "trade.services.auction_service.settle_auction_round",
        lambda: {"settled": 1, "sold": 2, "unsold": 0, "total_gold_bars": 20},
    )
    monkeypatch.setattr(
        "trade.tasks.safe_apply_async",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "trade.services.auction_service.create_auction_round",
        lambda: (_ for _ in ()).throw(error),
    )
    retry_calls = []

    def _retry(exc):
        retry_calls.append(exc)
        raise AssertionError("retry should not be called")

    monkeypatch.setattr(settle_auction_round_task, "retry", _retry)

    with pytest.raises(ProgrammingError) as exc_info:
        settle_auction_round_task.run()

    assert exc_info.value is error
    assert retry_calls == []
