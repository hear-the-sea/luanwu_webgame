from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import gameplay.services.technology_helpers as technology_helpers


def test_build_technology_display_entry_calculates_upgrade_fields():
    entry = technology_helpers.build_technology_display_entry(
        tech={
            "key": "march_art",
            "name": "行军术",
            "description": "提升速度",
            "category": "basic",
            "effect_type": "march_speed",
            "effect_per_level": 0.15,
            "base_time": 90,
            "max_level": 5,
            "upgrade_time_budget": 10000,
            "time_curve": 1.2,
        },
        player_tech=SimpleNamespace(
            level=2,
            is_upgrading=True,
            upgrade_complete_at="2026-03-11T00:00:00Z",
            time_remaining=33,
        ),
        calculate_upgrade_cost=lambda key, level: 100 + level,
        scale_duration=lambda seconds, minimum=1: max(minimum, int(seconds)),
    )

    assert entry["key"] == "march_art"
    assert entry["upgrade_cost"] == 102
    assert entry["upgrade_duration"] == 1937
    assert entry["current_effect"] == 30.0
    assert entry["next_effect"] == __import__("pytest").approx(45.0)
    assert entry["is_upgrading"] is True
    assert entry["can_upgrade"] is False


def test_build_technology_display_entry_uses_budget_curve_for_duration():
    entry = technology_helpers.build_technology_display_entry(
        tech={
            "key": "budgeted",
            "name": "预算技术",
            "effect_per_level": 0.1,
            "max_level": 4,
            "base_time": 60,
            "upgrade_time_budget": 10000,
            "time_curve": 1.2,
        },
        player_tech=SimpleNamespace(level=2, is_upgrading=False, upgrade_complete_at=None, time_remaining=0),
        calculate_upgrade_cost=lambda *_args, **_kwargs: 100,
        scale_duration=lambda seconds, minimum=1: max(minimum, int(seconds)),
    )

    assert entry["upgrade_duration"] == 2678


def test_build_technology_display_entry_caps_upgrade_fields_at_max_level():
    entry = technology_helpers.build_technology_display_entry(
        tech={
            "key": "architecture",
            "name": "营造术",
            "effect_per_level": 0.1,
            "max_level": 3,
        },
        player_tech=SimpleNamespace(level=3, is_upgrading=False, upgrade_complete_at=None, time_remaining=0),
        calculate_upgrade_cost=lambda *_args, **_kwargs: 999,
        scale_duration=lambda seconds, minimum=1: 999,
    )

    assert entry["upgrade_cost"] is None
    assert entry["upgrade_duration"] is None
    assert entry["next_effect"] is None
    assert entry["can_upgrade"] is False


def test_group_martial_technology_entries_uses_business_order_and_name_fallback():
    grouped = technology_helpers.group_martial_technology_entries(
        [
            {"key": "jian_attack", "troop_class": "jian"},
            {"key": "dao_attack", "troop_class": "dao"},
            {"key": "unknown_attack", "troop_class": "unknown"},
        ],
        {
            "dao": {"name": "刀类"},
            "jian": {"name": "剑类"},
        },
    )

    assert [item["class_key"] for item in grouped] == ["dao", "jian"]
    assert grouped[0]["class_name"] == "刀类"
    assert grouped[1]["class_name"] == "剑类"


def test_schedule_technology_completion_task_passes_dispatch_context_to_safe_apply_async():
    callbacks: list[object] = []
    tech = SimpleNamespace(id=17, manor_id=23, tech_key="march_art")
    logger = MagicMock()
    dispatch_calls: list[dict[str, object]] = []

    def _safe_apply_async(*_args, **kwargs):
        dispatch_calls.append(kwargs)
        return False

    technology_helpers.schedule_technology_completion_task(
        tech,
        eta_seconds=45,
        logger=logger,
        transaction_module=SimpleNamespace(on_commit=callbacks.append),
        safe_apply_async_func=_safe_apply_async,
    )

    assert len(callbacks) == 1

    callbacks[0]()

    assert dispatch_calls == [
        {
            "args": [17],
            "countdown": 45,
            "logger": logger,
            "log_message": "complete_technology_upgrade dispatch failed",
            "log_extra": {
                "tech_id": 17,
                "manor_id": 23,
                "tech_key": "march_art",
                "countdown": 45,
            },
        }
    ]
    logger.warning.assert_not_called()


def test_build_technology_upgrade_response_formats_message():
    result = technology_helpers.build_technology_upgrade_response(template_name="行军术", duration=120)

    assert result == {
        "success": True,
        "message": "行军术 开始升级，预计 120 秒后完成",
        "duration": 120,
    }
