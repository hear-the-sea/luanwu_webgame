from __future__ import annotations

import pytest

from trade.services.auction import rounds as auction_rounds


def test_safe_int_returns_int_when_valid():
    assert auction_rounds.safe_int(10, default=0) == 10
    assert auction_rounds.safe_int("20", default=0) == 20
    assert auction_rounds.safe_int(5.7, default=0) == 5


def test_safe_int_returns_default_when_invalid():
    assert auction_rounds.safe_int(None, default=10) == 10
    assert auction_rounds.safe_int("invalid", default=5) == 5
    assert auction_rounds.safe_int({}, default=3) == 3


def test_safe_cache_add_returns_bool(monkeypatch):
    add_called = {}

    def mock_add(key, value, timeout):
        add_called["key"] = key
        add_called["value"] = value
        add_called["timeout"] = timeout
        return True

    from django.core import cache as cache_module

    monkeypatch.setattr(cache_module.cache, "add", mock_add)

    result = auction_rounds._safe_cache_add("test_key", "test_value", 60)
    assert result is True
    assert add_called["key"] == "test_key"
    assert add_called["value"] == "test_value"
    assert add_called["timeout"] == 60


def test_safe_cache_add_returns_false_on_exception(monkeypatch):
    def mock_add_error(key, value, timeout):
        raise ConnectionError("Cache error")

    from django.core import cache as cache_module

    monkeypatch.setattr(cache_module.cache, "add", mock_add_error)

    result = auction_rounds._safe_cache_add("test_key", "test_value", 60)
    assert result is False


def test_safe_cache_add_programming_error_bubbles_up(monkeypatch):
    def mock_add_error(key, value, timeout):
        raise AssertionError("broken cache contract")

    from django.core import cache as cache_module

    monkeypatch.setattr(cache_module.cache, "add", mock_add_error)

    with pytest.raises(AssertionError, match="broken cache contract"):
        auction_rounds._safe_cache_add("test_key", "test_value", 60)


def test_safe_cache_get_returns_value(monkeypatch):
    def mock_get(key, default=None):
        return "cached_value"

    from django.core import cache as cache_module

    monkeypatch.setattr(cache_module.cache, "get", mock_get)

    result = auction_rounds._safe_cache_get("test_key", default="default_value")
    assert result == "cached_value"


def test_safe_cache_get_returns_default_on_exception(monkeypatch):
    def mock_get_error(key, default=None):
        raise ConnectionError("Cache error")

    from django.core import cache as cache_module

    monkeypatch.setattr(cache_module.cache, "get", mock_get_error)

    result = auction_rounds._safe_cache_get("test_key", default="default_value")
    assert result == "default_value"


def test_safe_cache_get_programming_error_bubbles_up(monkeypatch):
    def mock_get_error(key, default=None):
        raise AssertionError("broken cache contract")

    from django.core import cache as cache_module

    monkeypatch.setattr(cache_module.cache, "get", mock_get_error)

    with pytest.raises(AssertionError, match="broken cache contract"):
        auction_rounds._safe_cache_get("test_key", default="default_value")


def test_safe_cache_delete_tolerates_exception(monkeypatch):
    def mock_delete_error(key):
        raise ConnectionError("Cache error")

    from django.core import cache as cache_module

    monkeypatch.setattr(cache_module.cache, "delete", mock_delete_error)

    # Should not raise exception
    auction_rounds._safe_cache_delete("test_key")


def test_safe_cache_delete_programming_error_bubbles_up(monkeypatch):
    def mock_delete_error(key):
        raise AssertionError("broken cache contract")

    from django.core import cache as cache_module

    monkeypatch.setattr(cache_module.cache, "delete", mock_delete_error)

    with pytest.raises(AssertionError, match="broken cache contract"):
        auction_rounds._safe_cache_delete("test_key")
