from __future__ import annotations

from gameplay.services import chat as chat_service


def test_chat_service_does_not_expose_consume_trumpet():
    assert not hasattr(chat_service, "consume_trumpet")


def test_chat_service_does_not_expose_refund_trumpet():
    assert not hasattr(chat_service, "refund_trumpet")
