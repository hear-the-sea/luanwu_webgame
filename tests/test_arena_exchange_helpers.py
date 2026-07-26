from datetime import date, datetime, timedelta

import pytest
from django.db import DatabaseError
from django.utils import timezone

from core.exceptions import ArenaExchangeError, MessageError
from gameplay.services.arena.exchange_helpers import (
    build_exchange_payload,
    build_exchange_summary,
    grant_exchange_items_locked,
    merge_item_grants,
    normalize_exchange_quantity,
    scale_reward_items,
    scale_reward_resources,
    select_weekly_blueprint_key,
    send_exchange_success_message,
)
from gameplay.services.arena.helpers import current_week_bounds
from gameplay.services.arena.rewards import ArenaRotatingBlueprintPool


def test_normalize_exchange_quantity_validates_positive_values():
    assert normalize_exchange_quantity(3) == 3
    assert normalize_exchange_quantity("2") == 2

    with pytest.raises(ArenaExchangeError, match="兑换数量无效"):
        normalize_exchange_quantity(0)

    with pytest.raises(ArenaExchangeError, match="兑换数量无效"):
        normalize_exchange_quantity("bad")


def test_scale_reward_resources_and_items():
    assert scale_reward_resources({"grain": 100, "silver": 50}, 2) == {"grain": 200, "silver": 100}
    assert scale_reward_items({"item_a": 1, "item_b": 3}, 2) == {"item_a": 2, "item_b": 6}


def test_merge_item_grants_accumulates_duplicate_keys():
    assert merge_item_grants({"item_a": 2}, {"item_a": 1, "item_b": 4}, {"item_b": 1}) == {
        "item_a": 3,
        "item_b": 5,
    }


def test_build_exchange_payload_and_summary_cover_all_sections():
    payload = build_exchange_payload(
        credited_resources={"grain": 100},
        overflow_resources={"silver": 50},
        granted_items={"item_a": 2},
    )
    summary = build_exchange_summary(
        credited_resources={"grain": 100},
        overflow_resources={"silver": 50},
        granted_items={"item_a": 2},
    )

    assert payload == {
        "resources": {"grain": 100},
        "resources_overflow": {"silver": 50},
        "items": {"item_a": 2},
    }
    assert summary == "资源已发放，道具已入库，部分资源因容量上限溢出"


def test_build_exchange_summary_returns_default_when_empty():
    assert build_exchange_summary(credited_resources={}, overflow_resources={}, granted_items={}) == "奖励已处理"


def test_select_weekly_blueprint_key_uses_iso_week_rotation():
    pool = ArenaRotatingBlueprintPool(
        rarity="blue",
        blueprint_keys=(
            "blueprint_rotation_a",
            "blueprint_rotation_b",
            "blueprint_rotation_c",
            "blueprint_rotation_d",
        ),
    )

    assert select_weekly_blueprint_key(pool, today=date.fromisocalendar(2026, 1, 1)) == "blueprint_rotation_a"
    assert select_weekly_blueprint_key(pool, today=date.fromisocalendar(2026, 2, 1)) == "blueprint_rotation_b"
    assert select_weekly_blueprint_key(pool, today=date.fromisocalendar(2026, 4, 1)) == "blueprint_rotation_d"
    assert select_weekly_blueprint_key(pool, today=date.fromisocalendar(2026, 5, 1)) == "blueprint_rotation_a"


def test_current_week_bounds_use_local_monday_boundary():
    now = timezone.make_aware(datetime(2026, 7, 22, 15, 30), timezone.get_current_timezone())

    start, end = current_week_bounds(now=now)

    assert start.date() == date(2026, 7, 20)
    assert end.date() == date(2026, 7, 27)
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert end - start == timedelta(days=7)


def test_grant_exchange_items_locked_merges_grants_and_calls_inventory_writer():
    calls = []

    granted = grant_exchange_items_locked(
        fixed_item_grants={"item_a": 2},
        random_item_grants={"item_a": 1, "item_b": 3},
        add_item_to_inventory_locked=lambda manor, item_key, amount: calls.append((manor, item_key, amount)),
        locked_manor="manor-1",
    )

    assert calls == [
        ("manor-1", "item_a", 2),
        ("manor-1", "item_a", 1),
        ("manor-1", "item_b", 3),
    ]
    assert granted == {"item_a": 3, "item_b": 3}


def test_send_exchange_success_message_swallows_explicit_message_errors(caplog):
    class _Reward:
        key = "grain_pack_small"
        name = "小粮包"

    class _Manor:
        id = 1

    with caplog.at_level("WARNING"):
        send_exchange_success_message(
            create_message_func=lambda **_kwargs: (_ for _ in ()).throw(MessageError("message backend down")),
            message_kind="reward",
            locked_manor=_Manor(),
            reward=_Reward(),
            total_cost=80,
            normalized_quantity=1,
            summary="资源已发放",
            logger=__import__("logging").getLogger("tests.arena.exchange"),
        )

    assert "arena exchange message failed" in caplog.text


def test_send_exchange_success_message_swallows_database_infrastructure_errors(caplog):
    class _Reward:
        key = "grain_pack_small"
        name = "小粮包"

    class _Manor:
        id = 1

    with caplog.at_level("WARNING"):
        send_exchange_success_message(
            create_message_func=lambda **_kwargs: (_ for _ in ()).throw(DatabaseError("message table unavailable")),
            message_kind="reward",
            locked_manor=_Manor(),
            reward=_Reward(),
            total_cost=80,
            normalized_quantity=1,
            summary="资源已发放",
            logger=__import__("logging").getLogger("tests.arena.exchange"),
        )

    assert "arena exchange message failed" in caplog.text


def test_send_exchange_success_message_runtime_marker_error_bubbles_up():
    class _Reward:
        key = "grain_pack_small"
        name = "小粮包"

    class _Manor:
        id = 1

    with pytest.raises(RuntimeError, match="message backend down"):
        send_exchange_success_message(
            create_message_func=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("message backend down")),
            message_kind="reward",
            locked_manor=_Manor(),
            reward=_Reward(),
            total_cost=80,
            normalized_quantity=1,
            summary="资源已发放",
            logger=__import__("logging").getLogger("tests.arena.exchange"),
        )


def test_send_exchange_success_message_programming_error_bubbles_up():
    class _Reward:
        key = "grain_pack_small"
        name = "小粮包"

    class _Manor:
        id = 1

    with pytest.raises(AssertionError, match="broken arena exchange message contract"):
        send_exchange_success_message(
            create_message_func=lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("broken arena exchange message contract")
            ),
            message_kind="reward",
            locked_manor=_Manor(),
            reward=_Reward(),
            total_cost=80,
            normalized_quantity=1,
            summary="资源已发放",
            logger=__import__("logging").getLogger("tests.arena.exchange"),
        )
