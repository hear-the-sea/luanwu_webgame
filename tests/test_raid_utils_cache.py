from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.utils import timezone
from django_redis.exceptions import ConnectionInterrupted

from gameplay.services.raid import utils as raid_utils


def test_get_recent_attacks_24h_uses_cache_when_enabled(monkeypatch):
    defender = SimpleNamespace(id=9)
    calls = {"count": 0}
    cache_store: dict[str, int] = {}

    class _QS:
        @staticmethod
        def count():
            calls["count"] += 1
            return 3

    class _Objects:
        @staticmethod
        def filter(**_kwargs):
            return _QS()

    monkeypatch.setattr(raid_utils, "RaidRun", type("_RaidRun", (), {"objects": _Objects()}))
    monkeypatch.setattr(raid_utils, "_safe_cache_get", lambda key: cache_store.get(key))
    monkeypatch.setattr(raid_utils, "_safe_cache_set", lambda key, value, timeout: cache_store.__setitem__(key, value))

    now = timezone.now()
    assert raid_utils.get_recent_attacks_24h(defender, now=now, use_cache=True) == 3
    assert raid_utils.get_recent_attacks_24h(defender, now=now, use_cache=True) == 3
    assert calls["count"] == 1


def test_get_recent_attacks_24h_skips_cache_when_disabled(monkeypatch):
    defender = SimpleNamespace(id=11)
    calls = {"count": 0}

    class _QS:
        @staticmethod
        def count():
            calls["count"] += 1
            return 2

    class _Objects:
        @staticmethod
        def filter(**_kwargs):
            return _QS()

    monkeypatch.setattr(raid_utils, "RaidRun", type("_RaidRun", (), {"objects": _Objects()}))
    monkeypatch.setattr(
        raid_utils,
        "_safe_cache_get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cache should not be used")),
    )
    monkeypatch.setattr(
        raid_utils,
        "_safe_cache_set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cache should not be used")),
    )

    now = timezone.now()
    assert raid_utils.get_recent_attacks_24h(defender, now=now, use_cache=False) == 2
    assert raid_utils.get_recent_attacks_24h(defender, now=now, use_cache=False) == 2
    assert calls["count"] == 2


def test_invalidate_recent_attacks_cache_deletes_expected_key(monkeypatch):
    deleted = {"key": None}
    monkeypatch.setattr(raid_utils, "_safe_cache_delete", lambda key: deleted.__setitem__("key", key))

    raid_utils.invalidate_recent_attacks_cache(15)

    assert deleted["key"] == "raid:recent_attacks_24h:15"


def test_recent_attacks_cache_ttl_seconds_falls_back_for_invalid_value(monkeypatch):
    monkeypatch.setattr(raid_utils.settings, "RAID_RECENT_ATTACKS_CACHE_TTL_SECONDS", "bad", raising=False)

    assert raid_utils._recent_attacks_cache_ttl_seconds() == 5


def test_recent_attacks_cache_ttl_seconds_programming_error_bubbles_up(monkeypatch):
    class _BrokenSettings:
        def __getattr__(self, _name):
            raise AssertionError("broken settings contract")

    monkeypatch.setattr(raid_utils, "settings", _BrokenSettings())

    with pytest.raises(AssertionError, match="broken settings contract"):
        raid_utils._recent_attacks_cache_ttl_seconds()


def test_safe_cache_get_tolerates_cache_infrastructure_failure(monkeypatch):
    monkeypatch.setattr(
        raid_utils.cache,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionInterrupted("cache down")),
    )

    assert raid_utils._safe_cache_get("raid:test:get") is None


def test_safe_cache_get_programming_error_bubbles_up(monkeypatch):
    monkeypatch.setattr(
        raid_utils.cache,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken cache contract")),
    )

    with pytest.raises(AssertionError, match="broken cache contract"):
        raid_utils._safe_cache_get("raid:test:get")


def test_safe_cache_get_runtime_marker_error_bubbles_up(monkeypatch):
    monkeypatch.setattr(
        raid_utils.cache,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cache down")),
    )

    with pytest.raises(RuntimeError, match="cache down"):
        raid_utils._safe_cache_get("raid:test:get")


def test_safe_cache_set_programming_error_bubbles_up(monkeypatch):
    monkeypatch.setattr(
        raid_utils.cache,
        "set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken cache contract")),
    )

    with pytest.raises(AssertionError, match="broken cache contract"):
        raid_utils._safe_cache_set("raid:test:set", 3, 30)


def test_safe_cache_set_runtime_marker_error_bubbles_up(monkeypatch):
    monkeypatch.setattr(
        raid_utils.cache,
        "set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cache set failed")),
    )

    with pytest.raises(RuntimeError, match="cache set failed"):
        raid_utils._safe_cache_set("raid:test:set", 3, 30)


def test_safe_cache_delete_programming_error_bubbles_up(monkeypatch):
    monkeypatch.setattr(
        raid_utils.cache,
        "delete",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broken cache contract")),
    )

    with pytest.raises(AssertionError, match="broken cache contract"):
        raid_utils._safe_cache_delete("raid:test:delete")


def test_safe_cache_delete_runtime_marker_error_bubbles_up(monkeypatch):
    monkeypatch.setattr(
        raid_utils.cache,
        "delete",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("cache delete failed")),
    )

    with pytest.raises(RuntimeError, match="cache delete failed"):
        raid_utils._safe_cache_delete("raid:test:delete")


@pytest.mark.django_db
def test_can_attack_target_can_bypass_cached_recent_attacks(monkeypatch):
    attacker = SimpleNamespace(
        id=1,
        is_under_newbie_protection=False,
        is_under_defeat_protection=False,
        is_under_peace_shield=False,
        prestige=1000,
    )
    defender = SimpleNamespace(
        id=2,
        is_under_newbie_protection=False,
        is_under_defeat_protection=False,
        is_under_peace_shield=False,
        prestige=1000,
    )

    limit = raid_utils.PVPConstants.RAID_MAX_DAILY_ATTACKS_RECEIVED

    def _fake_recent_attacks(_defender, now=None, *, use_cache=True):
        return limit if use_cache else 0

    monkeypatch.setattr(raid_utils, "get_recent_attacks_24h", _fake_recent_attacks)

    blocked, blocked_reason = raid_utils.can_attack_target(attacker, defender, use_cached_recent_attacks=True)
    allowed, allowed_reason = raid_utils.can_attack_target(attacker, defender, use_cached_recent_attacks=False)

    assert blocked is False
    assert "多次攻击" in blocked_reason
    assert allowed is True
    assert allowed_reason == ""


@pytest.mark.django_db
def test_can_attack_target_blocks_defender_defeat_protection():
    attacker = SimpleNamespace(
        id=1,
        is_under_newbie_protection=False,
        is_under_defeat_protection=False,
        is_under_peace_shield=False,
        prestige=1000,
    )
    defender = SimpleNamespace(
        id=2,
        is_under_newbie_protection=False,
        is_under_defeat_protection=True,
        is_under_peace_shield=False,
        prestige=1000,
    )

    allowed, reason = raid_utils.can_attack_target(attacker, defender)
    assert allowed is False
    assert "战败保护期" in reason


@pytest.mark.django_db
def test_can_attack_target_ignores_prestige_gap_when_both_manors_reach_cutoff(monkeypatch):
    cutoff = raid_utils.PVPConstants.RAID_PRESTIGE_PROTECTION_CUTOFF
    attacker = SimpleNamespace(
        id=1,
        is_under_newbie_protection=False,
        is_under_defeat_protection=False,
        is_under_peace_shield=False,
        prestige=cutoff,
    )
    defender = SimpleNamespace(
        id=2,
        is_under_newbie_protection=False,
        is_under_defeat_protection=False,
        is_under_peace_shield=False,
        prestige=cutoff + 10000,
    )

    monkeypatch.setattr(raid_utils, "get_recent_attacks_24h", lambda *_args, **_kwargs: 0)

    allowed, reason = raid_utils.can_attack_target(attacker, defender)

    assert raid_utils.get_prestige_color(attacker.prestige, defender.prestige) == "white"
    assert allowed is True
    assert reason == ""


@pytest.mark.django_db
def test_can_attack_target_still_blocks_large_prestige_gap_below_cutoff(monkeypatch):
    cutoff = raid_utils.PVPConstants.RAID_PRESTIGE_PROTECTION_CUTOFF
    attacker = SimpleNamespace(
        id=1,
        is_under_newbie_protection=False,
        is_under_defeat_protection=False,
        is_under_peace_shield=False,
        prestige=cutoff - 1,
    )
    defender = SimpleNamespace(
        id=2,
        is_under_newbie_protection=False,
        is_under_defeat_protection=False,
        is_under_peace_shield=False,
        prestige=cutoff + 10000,
    )

    monkeypatch.setattr(raid_utils, "get_recent_attacks_24h", lambda *_args, **_kwargs: 0)

    allowed, reason = raid_utils.can_attack_target(attacker, defender)

    assert raid_utils.get_prestige_color(attacker.prestige, defender.prestige) == "red"
    assert allowed is False
    assert "声望过高" in reason


@pytest.mark.django_db
def test_can_attack_target_uses_dynamic_prestige_range_below_cutoff(monkeypatch):
    attacker = SimpleNamespace(
        id=1,
        is_under_newbie_protection=False,
        is_under_defeat_protection=False,
        is_under_peace_shield=False,
        prestige=12000,
    )
    defender_allowed = SimpleNamespace(
        id=2,
        is_under_newbie_protection=False,
        is_under_defeat_protection=False,
        is_under_peace_shield=False,
        prestige=14900,
    )
    defender_blocked = SimpleNamespace(
        id=3,
        is_under_newbie_protection=False,
        is_under_defeat_protection=False,
        is_under_peace_shield=False,
        prestige=16050,
    )

    monkeypatch.setattr(raid_utils, "get_recent_attacks_24h", lambda *_args, **_kwargs: 0)

    assert raid_utils.get_prestige_protection_range(attacker.prestige, defender_allowed.prestige) == 3000
    allowed, allowed_reason = raid_utils.can_attack_target(attacker, defender_allowed)
    blocked, blocked_reason = raid_utils.can_attack_target(attacker, defender_blocked)

    assert allowed is True
    assert allowed_reason == ""
    assert blocked is False
    assert "声望过高" in blocked_reason
