from __future__ import annotations

import pytest
from django.core.cache import cache
from django_redis.exceptions import ConnectionInterrupted

from core.exceptions import ItemNotFoundError, TradeValidationError
from gameplay.models import InventoryItem, ItemTemplate
from gameplay.services.manor.core import ensure_manor
from trade.models import GoldBarExchangeLog
from trade.services import bank_service, bank_supply_runtime


def _ensure_gold_bar_template() -> ItemTemplate:
    template, _ = ItemTemplate.objects.get_or_create(
        key=bank_service.GOLD_BAR_ITEM_KEY,
        defaults={
            "name": "金条",
            "effect_type": ItemTemplate.EffectType.TOOL,
            "is_usable": False,
            "tradeable": False,
        },
    )
    return template


def _fund_manor_for_exchange(manor, *, silver: int = 1_000_000_000) -> None:
    """Fund exchange tests without creating an over-capacity resource state."""

    manor.silver_capacity = silver
    manor.silver = silver
    manor.save(update_fields=["silver_capacity", "silver"])


@pytest.mark.django_db
def test_calculate_progressive_factor_caps():
    assert bank_service.calculate_progressive_factor(-5) == 1.0
    assert bank_service.calculate_progressive_factor(0) == 1.0
    assert bank_service.calculate_progressive_factor(1) == 1.05
    assert bank_service.calculate_progressive_factor(100) == 1.60


@pytest.mark.django_db
def test_calculate_supply_factor_clamps(monkeypatch):
    monkeypatch.setattr(bank_service, "get_effective_gold_supply", lambda: 0)
    assert bank_service.calculate_supply_factor() == 0.85

    monkeypatch.setattr(bank_service, "get_effective_gold_supply", lambda: bank_service.GOLD_BAR_TARGET_SUPPLY)
    assert bank_service.calculate_supply_factor() == pytest.approx(1.0)

    monkeypatch.setattr(
        bank_service,
        "get_effective_gold_supply",
        lambda: bank_service.GOLD_BAR_TARGET_SUPPLY * 10_000,
    )
    assert bank_service.calculate_supply_factor() == 1.40


@pytest.mark.django_db
def test_calculate_gold_bar_cost_includes_fee(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="bank_cost", password="pass12345")
    manor = ensure_manor(user)

    monkeypatch.setattr(bank_service, "calculate_supply_factor", lambda: 1.0)
    monkeypatch.setattr(bank_service, "get_today_exchange_count", lambda *_args, **_kwargs: 0)

    cost = bank_service.calculate_gold_bar_cost(manor, 2)

    expected_rates = [
        max(
            bank_service.GOLD_BAR_MIN_PRICE,
            min(
                bank_service.GOLD_BAR_MAX_PRICE,
                int(bank_service.GOLD_BAR_BASE_PRICE * bank_service.calculate_progressive_factor(0)),
            ),
        ),
        max(
            bank_service.GOLD_BAR_MIN_PRICE,
            min(
                bank_service.GOLD_BAR_MAX_PRICE,
                int(bank_service.GOLD_BAR_BASE_PRICE * bank_service.calculate_progressive_factor(1)),
            ),
        ),
    ]

    assert cost["rate_details"] == expected_rates
    assert cost["base_cost"] == sum(expected_rates)
    assert cost["fee"] == int(cost["base_cost"] * bank_service.GOLD_BAR_FEE_RATE)
    assert cost["total_cost"] == cost["base_cost"] + cost["fee"]


@pytest.mark.django_db
def test_calculate_gold_bar_cost_rejects_invalid_quantity(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="bank_cost_invalid_qty", password="pass12345")
    manor = ensure_manor(user)
    monkeypatch.setattr(bank_service, "calculate_supply_factor", lambda: 1.0)
    monkeypatch.setattr(bank_service, "get_today_exchange_count", lambda *_args, **_kwargs: 0)

    with pytest.raises(TradeValidationError, match="兑换数量必须大于0"):
        bank_service.calculate_gold_bar_cost(manor, "invalid")


@pytest.mark.django_db
def test_calculate_gold_bar_cost_clamps_negative_today_count(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="bank_cost_negative_today", password="pass12345")
    manor = ensure_manor(user)

    monkeypatch.setattr(bank_service, "calculate_supply_factor", lambda: 1.0)
    monkeypatch.setattr(bank_service, "get_today_exchange_count", lambda *_args, **_kwargs: -20)

    cost = bank_service.calculate_gold_bar_cost(manor, 1)
    assert cost["avg_rate"] == 1_000_000


@pytest.mark.django_db
def test_exchange_gold_bar_deducts_silver_and_creates_inventory(monkeypatch, django_user_model):
    cache.clear()

    user = django_user_model.objects.create_user(username="bank_exchange", password="pass12345")
    manor = ensure_manor(user)
    _fund_manor_for_exchange(manor)

    _ = _ensure_gold_bar_template()

    monkeypatch.setattr(
        bank_service,
        "calculate_gold_bar_cost",
        lambda *_args, **_kwargs: {
            "base_cost": 100,
            "fee": 10,
            "total_cost": 110,
            "rate_details": [50, 50],
            "avg_rate": 50,
        },
    )
    monkeypatch.setattr(bank_service, "calculate_next_rate", lambda *_args, **_kwargs: 123)

    result = bank_service.exchange_gold_bar(manor, 2)
    assert result["total_cost"] == 110
    assert result["quantity"] == 2
    assert result["next_rate"] == 123

    manor.refresh_from_db()
    assert manor.silver == 1_000_000_000 - 110

    inventory_item = InventoryItem.objects.get(
        manor=manor,
        template__key=bank_service.GOLD_BAR_ITEM_KEY,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    )
    assert inventory_item.quantity == 2

    log = GoldBarExchangeLog.objects.get(manor=manor)
    assert log.quantity == 2
    assert log.silver_cost == 110


@pytest.mark.django_db
def test_get_effective_gold_supply_falls_back_when_cache_errors(monkeypatch):
    monkeypatch.setattr(
        bank_service.cache,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionInterrupted("cache read failed")),
    )
    monkeypatch.setattr(
        bank_service.cache,
        "add",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionInterrupted("cache add failed")),
    )

    value = bank_service.get_effective_gold_supply()
    assert value == bank_service.GOLD_BAR_TARGET_SUPPLY


@pytest.mark.django_db
def test_get_effective_gold_supply_handles_corrupted_stale_cache(monkeypatch):
    def fake_get(key, default=None):
        if key == bank_service.SUPPLY_CACHE_KEY:
            return None
        if key == bank_service.SUPPLY_STALE_CACHE_KEY:
            return "not-an-int"
        return default

    monkeypatch.setattr(bank_service.cache, "get", fake_get)
    monkeypatch.setattr(bank_service.cache, "add", lambda *_args, **_kwargs: False)

    value = bank_service.get_effective_gold_supply()
    assert value == bank_service.GOLD_BAR_TARGET_SUPPLY


@pytest.mark.django_db
def test_exchange_gold_bar_tolerates_cache_delete_failure(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="bank_cache_delete", password="pass12345")
    manor = ensure_manor(user)
    _fund_manor_for_exchange(manor)

    _ = _ensure_gold_bar_template()

    monkeypatch.setattr(
        bank_service,
        "calculate_gold_bar_cost",
        lambda *_args, **_kwargs: {
            "base_cost": 100,
            "fee": 10,
            "total_cost": 110,
            "rate_details": [50],
            "avg_rate": 50,
        },
    )
    monkeypatch.setattr(bank_service, "calculate_next_rate", lambda *_args, **_kwargs: 123)
    monkeypatch.setattr(
        bank_service.cache,
        "delete",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionInterrupted("cache delete failed")),
    )

    result = bank_service.exchange_gold_bar(manor, 1)
    assert result["quantity"] == 1
    assert result["total_cost"] == 110


@pytest.mark.django_db
def test_get_effective_gold_supply_runtime_marker_cache_error_bubbles_up(monkeypatch):
    monkeypatch.setattr(
        bank_service.cache,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cache read failed")),
    )

    with pytest.raises(RuntimeError, match="cache read failed"):
        bank_service.get_effective_gold_supply()


@pytest.mark.django_db
def test_exchange_gold_bar_rejects_invalid_quantity(django_user_model):
    user = django_user_model.objects.create_user(username="bank_exchange_invalid_qty", password="pass12345")
    manor = ensure_manor(user)

    with pytest.raises(TradeValidationError, match="兑换数量必须大于0"):
        bank_service.exchange_gold_bar(manor, "invalid")


@pytest.mark.django_db
def test_exchange_gold_bar_translates_missing_gold_bar_template(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="bank_exchange_missing_gold_tpl", password="pass12345")
    manor = ensure_manor(user)
    _fund_manor_for_exchange(manor)

    monkeypatch.setattr(
        bank_service,
        "calculate_gold_bar_cost",
        lambda *_args, **_kwargs: {
            "base_cost": 100,
            "fee": 10,
            "total_cost": 110,
            "rate_details": [50],
            "avg_rate": 50,
        },
    )
    monkeypatch.setattr(bank_service, "calculate_next_rate", lambda *_args, **_kwargs: 123)
    monkeypatch.setattr(
        "trade.services.bank_service.add_item_to_inventory_locked",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ItemNotFoundError("物品模板不存在: gold_bar")),
    )

    with pytest.raises(TradeValidationError, match="金条物品不存在，请联系管理员"):
        bank_service.exchange_gold_bar(manor, 1)


@pytest.mark.django_db
def test_exchange_gold_bar_fails_closed_when_pricing_falls_back_to_stale_cache(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="bank_exchange_fail_closed", password="pass12345")
    manor = ensure_manor(user)
    _fund_manor_for_exchange(manor)
    _ = _ensure_gold_bar_template()

    def fake_get(key, default=None):
        if key == bank_service.SUPPLY_CACHE_KEY:
            return None
        if key == bank_service.SUPPLY_STALE_CACHE_KEY:
            return 4321
        return default

    monkeypatch.setattr(bank_service.cache, "get", fake_get)
    monkeypatch.setattr(bank_service.cache, "add", lambda *_args, **_kwargs: False)

    with pytest.raises(bank_service.GoldBarPricingUnavailableError, match="钱庄汇率暂时不可用"):
        bank_service.exchange_gold_bar(manor, 1)


@pytest.mark.django_db
def test_get_bank_info_marks_pricing_as_degraded_when_using_stale_cache(monkeypatch, django_user_model):
    user = django_user_model.objects.create_user(username="bank_info_stale", password="pass12345")
    manor = ensure_manor(user)

    def fake_get(key, default=None):
        if key == bank_service.SUPPLY_CACHE_KEY:
            return None
        if key == bank_service.SUPPLY_STALE_CACHE_KEY:
            return 765
        return default

    monkeypatch.setattr(bank_service.cache, "get", fake_get)
    monkeypatch.setattr(bank_service.cache, "add", lambda *_args, **_kwargs: False)

    info = bank_service.get_bank_info(manor)
    assert info["pricing_source"] == "stale_cache"
    assert info["pricing_degraded"] is True
    assert info["exchange_available"] is False
    assert "已暂时关闭兑换" in info["pricing_status_message"]


@pytest.mark.django_db
def test_get_effective_gold_supply_does_not_release_foreign_lock(monkeypatch):
    lock_key = f"{bank_service.SUPPLY_CACHE_KEY}:lock"
    lock_token_holder: dict[str, str] = {}
    delete_calls: list[str] = []

    def fake_safe_cache_get(key, default=None):
        if key == bank_service.SUPPLY_CACHE_KEY:
            return None
        if key == lock_key:
            return "foreign-lock-token"
        return default

    def fake_safe_cache_add(key, value, timeout):
        assert key == lock_key
        lock_token_holder["token"] = value
        return True

    monkeypatch.setattr(bank_service, "_safe_cache_get", fake_safe_cache_get)
    monkeypatch.setattr(bank_service, "_safe_cache_add", fake_safe_cache_add)
    monkeypatch.setattr(bank_service, "_safe_cache_set", lambda *args, **kwargs: None)
    monkeypatch.setattr(bank_service, "_safe_cache_delete", lambda key: delete_calls.append(key))

    bank_service.get_effective_gold_supply()
    assert lock_key not in delete_calls


@pytest.mark.django_db
def test_get_effective_gold_supply_delegates_owned_lock_release(monkeypatch):
    lock_key = f"{bank_service.SUPPLY_CACHE_KEY}:lock"
    lock_token_holder: dict[str, str] = {}
    delete_calls: list[str] = []
    release_calls: list[tuple[str, str]] = []

    def fake_safe_cache_get(key, default=None):
        if key == bank_service.SUPPLY_CACHE_KEY:
            return None
        return default

    def fake_safe_cache_add(key, value, timeout):
        assert key == lock_key
        lock_token_holder["token"] = value
        return True

    monkeypatch.setattr(bank_service, "_safe_cache_get", fake_safe_cache_get)
    monkeypatch.setattr(bank_service, "_safe_cache_add", fake_safe_cache_add)
    monkeypatch.setattr(bank_service, "_safe_cache_set", lambda *args, **kwargs: None)
    monkeypatch.setattr(bank_service, "_safe_cache_delete", lambda key: delete_calls.append(key))
    monkeypatch.setattr(
        bank_service,
        "release_cache_key_if_owner",
        lambda key, *, lock_token, **_kwargs: release_calls.append((key, lock_token)) or True,
    )

    bank_service.get_effective_gold_supply()
    assert release_calls == [(lock_key, lock_token_holder["token"])]
    assert delete_calls == []


def test_bank_lock_release_fails_closed_when_owner_release_returns_false(monkeypatch):
    cache_get_calls: list[str] = []
    cache_delete_calls: list[str] = []

    monkeypatch.setattr(bank_service, "release_cache_key_if_owner", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bank_service, "_safe_cache_get", lambda key, default=None: cache_get_calls.append(key))
    monkeypatch.setattr(bank_service, "_safe_cache_delete", lambda key: cache_delete_calls.append(key))

    bank_service._release_cache_lock_if_owner("gold-supply:lock", "expired-owner-token")

    assert cache_get_calls == []
    assert cache_delete_calls == []


def test_default_bank_supply_lock_release_fails_closed_when_owner_release_returns_false(monkeypatch):
    cache_get_calls: list[str] = []
    cache_delete_calls: list[str] = []

    monkeypatch.setattr(bank_supply_runtime._hooks, "release_cache_lock_if_owner", None)
    monkeypatch.setattr(
        bank_supply_runtime,
        "release_cache_key_if_owner",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        bank_supply_runtime,
        "safe_cache_get_value",
        lambda key, default=None: cache_get_calls.append(key),
    )
    monkeypatch.setattr(
        bank_supply_runtime,
        "safe_cache_delete_value",
        lambda key: cache_delete_calls.append(key),
    )

    bank_supply_runtime.release_cache_lock_if_owner_value(
        "gold-supply:lock",
        "expired-owner-token",
    )

    assert cache_get_calls == []
    assert cache_delete_calls == []
