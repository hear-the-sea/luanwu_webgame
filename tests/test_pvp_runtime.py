from __future__ import annotations

from gameplay.services.pvp_runtime import protection as runtime_protection


def test_runtime_protection_reuses_recent_attack_cap():
    result = runtime_protection.BlockedTargetResult(
        blocked=True,
        reason="该目标今日已被多次攻击，暂时无法攻击",
        return_time_seconds=120,
    )
    assert result.blocked is True
    assert "多次攻击" in result.reason


def test_runtime_loot_normalizes_positive_mapping():
    from gameplay.services.pvp_runtime import loot as runtime_loot

    assert runtime_loot.normalize_positive_int_mapping({"grain": 3, "bad": -1}) == {"grain": 3}
