from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from gameplay.services.manor.core import ensure_manor


@pytest.mark.django_db
def test_auction_bid_view_messages_first_and_raise(monkeypatch, client, django_user_model):
    user = django_user_model.objects.create_user(username="auction_bid", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    monkeypatch.setattr("trade.views.place_bid", lambda *_args, **_kwargs: (object(), True))
    resp = client.post(reverse("trade:auction_bid", args=[1]), {"amount": "5"})
    msgs = [m.message for m in get_messages(resp.wsgi_request)]
    assert any("成功出价" in m for m in msgs)

    monkeypatch.setattr("trade.views.place_bid", lambda *_args, **_kwargs: (object(), False))
    resp = client.post(reverse("trade:auction_bid", args=[1]), {"amount": "6"})
    msgs = [m.message for m in get_messages(resp.wsgi_request)]
    assert any("成功加价" in m for m in msgs)


@pytest.mark.django_db
def test_auction_bid_view_rejects_invalid_amount_without_service_call(monkeypatch, client, django_user_model):
    called = {"count": 0}

    def _unexpected_bid(*_args, **_kwargs):
        called["count"] += 1
        return object(), True

    monkeypatch.setattr("trade.views.place_bid", _unexpected_bid)

    user = django_user_model.objects.create_user(username="auction_bid_invalid_amount", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    resp = client.post(reverse("trade:auction_bid", args=[1]), {"amount": "bad"})
    assert resp.status_code == 302
    assert resp["Location"].endswith("?tab=auction")
    msgs = [m.message for m in get_messages(resp.wsgi_request)]
    assert any("出价参数无效" in m for m in msgs)
    assert called["count"] == 0


@pytest.mark.django_db
def test_auction_bid_view_tolerates_missing_threshold_setting(monkeypatch, client, django_user_model):
    user = django_user_model.objects.create_user(username="auction_bid_missing_threshold", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    monkeypatch.setattr("trade.views.place_bid", lambda *_args, **_kwargs: (object(), True))
    monkeypatch.setattr("trade.view_helpers.settings.AUCTION_HIGH_BID_THRESHOLD", None, raising=False)

    resp = client.post(reverse("trade:auction_bid", args=[1]), {"amount": "5"})
    assert resp.status_code == 302
    msgs = [m.message for m in get_messages(resp.wsgi_request)]
    assert any("成功出价" in m for m in msgs)


@pytest.mark.django_db
def test_auction_bid_view_tolerates_invalid_threshold_setting(monkeypatch, client, django_user_model):
    user = django_user_model.objects.create_user(username="auction_bid_invalid_threshold", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    monkeypatch.setattr("trade.views.place_bid", lambda *_args, **_kwargs: (object(), True))
    monkeypatch.setattr("trade.view_helpers.settings.AUCTION_HIGH_BID_THRESHOLD", "invalid", raising=False)

    resp = client.post(reverse("trade:auction_bid", args=[1]), {"amount": "5"})
    msgs = [m.message for m in get_messages(resp.wsgi_request)]
    assert any("成功出价" in m for m in msgs)


@pytest.mark.django_db
def test_auction_bid_view_uses_shared_threshold_warning_helper(monkeypatch, client, django_user_model):
    calls: list[dict[str, object]] = []

    user = django_user_model.objects.create_user(username="auction_bid_shared_threshold", password="pass12345")
    _ = ensure_manor(user)
    client.force_login(user)

    monkeypatch.setattr("trade.views.place_bid", lambda *_args, **_kwargs: (object(), True))
    monkeypatch.setattr(
        "trade.view_helpers.warn_if_threshold_exceeded",
        lambda **kwargs: calls.append(kwargs),
    )

    resp = client.post(reverse("trade:auction_bid", args=[9]), {"amount": "77"})
    assert resp.status_code == 302
    assert calls == [
        {
            "setting_name": "AUCTION_HIGH_BID_THRESHOLD",
            "default": 200,
            "value": 77,
            "log_message": "High-value auction bid: user_id=%s slot_id=%s amount=%s",
            "log_args": (user.id, 9, 77),
        }
    ]
