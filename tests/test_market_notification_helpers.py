from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django_redis.exceptions import ConnectionInterrupted

from core.exceptions import MessageError
from trade.services import market_notification_helpers


def test_safe_send_market_message_returns_false_on_exception():
    logger = MagicMock()

    result = market_notification_helpers.safe_send_market_message(
        create_message_func=lambda **_kwargs: (_ for _ in ()).throw(ConnectionError("mail down")),
        logger=logger,
        log_message="market create_message failed",
        manor=SimpleNamespace(id=1),
        kind="system",
        title="t",
        body="b",
    )

    assert result is False
    logger.warning.assert_called_once()


def test_safe_send_market_message_re_raises_non_infrastructure_errors():
    logger = MagicMock()

    with pytest.raises(ValueError):
        market_notification_helpers.safe_send_market_message(
            create_message_func=lambda **_kwargs: (_ for _ in ()).throw(ValueError("bad body")),
            logger=logger,
            log_message="market create_message failed",
            manor=SimpleNamespace(id=1),
            kind="system",
            title="t",
            body="b",
        )

    logger.exception.assert_not_called()
    logger.warning.assert_not_called()


def test_safe_send_market_notification_swallows_exception():
    logger = MagicMock()

    market_notification_helpers.safe_send_market_notification(
        notify_user_func=lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("notify down")),
        logger=logger,
        user_id=9,
        payload={"kind": "market_sold"},
        log_context="market sold notification",
        log_message="market notify_user failed",
    )

    logger.warning.assert_called_once()


def test_safe_send_market_notification_re_raises_non_infrastructure_errors():
    logger = MagicMock()

    with pytest.raises(AttributeError):
        market_notification_helpers.safe_send_market_notification(
            notify_user_func=lambda *_args, **_kwargs: (_ for _ in ()).throw(AttributeError("bad payload")),
            logger=logger,
            user_id=9,
            payload={"kind": "market_sold"},
            log_context="market sold notification",
            log_message="market notify_user failed",
        )

    logger.exception.assert_not_called()
    logger.warning.assert_not_called()


def test_safe_send_market_notification_runtime_marker_bubbles_up():
    logger = MagicMock()

    with pytest.raises(RuntimeError, match="notify backend down"):
        market_notification_helpers.safe_send_market_notification(
            notify_user_func=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("notify backend down")),
            logger=logger,
            user_id=9,
            payload={"kind": "market_sold"},
            log_context="market sold notification",
            log_message="market notify_user failed",
        )

    logger.exception.assert_not_called()
    logger.warning.assert_not_called()


def test_safe_send_market_message_swallows_message_error():
    logger = MagicMock()

    result = market_notification_helpers.safe_send_market_message(
        create_message_func=lambda **_kwargs: (_ for _ in ()).throw(MessageError("message backend down")),
        logger=logger,
        log_message="market create_message failed",
        manor=SimpleNamespace(id=1),
        kind="system",
        title="t",
        body="b",
    )

    assert result is False
    logger.warning.assert_called_once()
    logger.exception.assert_not_called()


def test_safe_send_market_message_runtime_marker_bubbles_up():
    logger = MagicMock()

    with pytest.raises(RuntimeError, match="message backend down"):
        market_notification_helpers.safe_send_market_message(
            create_message_func=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("message backend down")),
            logger=logger,
            log_message="market create_message failed",
            manor=SimpleNamespace(id=1),
            kind="system",
            title="t",
            body="b",
        )

    logger.warning.assert_not_called()
    logger.exception.assert_not_called()


def test_safe_send_market_notification_swallows_connection_interrupted():
    logger = MagicMock()

    market_notification_helpers.safe_send_market_notification(
        notify_user_func=lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionInterrupted("redis down")),
        logger=logger,
        user_id=9,
        payload={"kind": "market_sold"},
        log_context="market sold notification",
        log_message="market notify_user failed",
    )

    logger.warning.assert_called_once()


def test_send_purchase_notifications_returns_mail_flags_and_notifies_seller():
    calls = {"messages": [], "notifications": []}

    def _safe_create_message(**kwargs):
        calls["messages"].append(kwargs)
        return True

    def _safe_notify_user(user_id, payload, *, log_context):
        calls["notifications"].append((user_id, payload, log_context))

    buyer = SimpleNamespace(display_name="买家庄园", user=SimpleNamespace(username="buyer"))
    seller = SimpleNamespace(display_name="卖家庄园", user=SimpleNamespace(username="seller"), user_id=9)
    listing = SimpleNamespace(
        item_template=SimpleNamespace(name="青锋剑", key="equip_qingfeng"),
        quantity=2,
        total_price=5000,
        unit_price=2500,
        seller=seller,
        seller_id=1,
        sold_at=datetime(2026, 3, 12, 4, 0, 0, tzinfo=UTC),
    )

    buyer_sent, seller_sent = market_notification_helpers.send_purchase_notifications(
        buyer=buyer,
        listing=listing,
        tax_amount=500,
        seller_received=4500,
        safe_create_message=_safe_create_message,
        safe_notify_user=_safe_notify_user,
    )

    assert buyer_sent is True
    assert seller_sent is True
    assert len(calls["messages"]) == 2
    assert "- 卖家：卖家庄园\n" in calls["messages"][0]["body"]
    assert "- 买家：买家庄园\n" in calls["messages"][1]["body"]
    assert "- 成交时间：2026-03-12 12:00:00" in calls["messages"][0]["body"]
    assert "- 成交时间：2026-03-12 12:00:00" in calls["messages"][1]["body"]
    assert "- 卖家：seller\n" not in calls["messages"][0]["body"]
    assert "- 买家：buyer\n" not in calls["messages"][1]["body"]
    assert calls["notifications"] == [
        (
            9,
            {
                "kind": "market_sold",
                "title": "【交易成功】您的物品已售出",
                "item_name": "青锋剑",
                "item_key": "equip_qingfeng",
                "quantity": 2,
                "silver_received": 4500,
            },
            "market sold notification",
        )
    ]


def test_send_purchase_notifications_skips_seller_branch_without_seller_id():
    buyer = SimpleNamespace(display_name="买家庄园", user=SimpleNamespace(username="buyer"))
    listing = SimpleNamespace(
        item_template=SimpleNamespace(name="匿名物品", key="item"),
        quantity=1,
        total_price=100,
        unit_price=100,
        seller=SimpleNamespace(display_name="NPC庄园", user=SimpleNamespace(username="npc"), user_id=0),
        seller_id=None,
        sold_at=datetime(2026, 3, 12, 4, 0, 0, tzinfo=UTC),
    )

    calls = {"messages": 0, "notifications": 0}

    buyer_sent, seller_sent = market_notification_helpers.send_purchase_notifications(
        buyer=buyer,
        listing=listing,
        tax_amount=10,
        seller_received=90,
        safe_create_message=lambda **_kwargs: calls.__setitem__("messages", calls["messages"] + 1) or True,
        safe_notify_user=lambda *_args, **_kwargs: calls.__setitem__("notifications", calls["notifications"] + 1),
    )

    assert buyer_sent is True
    assert seller_sent is False
    assert calls == {"messages": 1, "notifications": 0}


def test_build_cancel_listing_result_returns_expected_shape():
    listing = SimpleNamespace(item_template=SimpleNamespace(name="铁甲"), quantity=3)

    assert market_notification_helpers.build_cancel_listing_result(listing=listing) == {
        "item_name": "铁甲",
        "quantity": 3,
    }


def test_restore_cancelled_listing_inventory_delegates_to_inventory_platform():
    calls: list[tuple[object, str, int]] = []
    manor = SimpleNamespace(id=7)
    listing = SimpleNamespace(item_template=SimpleNamespace(key="equip_tiejia"), quantity=3)

    market_notification_helpers.restore_cancelled_listing_inventory(
        manor=manor,
        listing=listing,
        grant_item_locked=lambda locked_manor, *, item_key, quantity: calls.append((locked_manor, item_key, quantity)),
    )

    assert calls == [(manor, "equip_tiejia", 3)]
