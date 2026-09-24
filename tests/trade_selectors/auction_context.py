from __future__ import annotations

import pytest
from django.db import DatabaseError
from django.test import RequestFactory

from tests.trade_selectors.support import create_manor
from trade.page_context import build_trade_request_params
from trade.selectors import get_trade_context
from trade.services.auction.access import AUCTION_MIN_PRESTIGE


def _auction_manor(django_user_model, *, username: str):
    manor = create_manor(django_user_model, username=username)
    manor.prestige = AUCTION_MIN_PRESTIGE
    manor.save(update_fields=["prestige"])
    return manor


@pytest.mark.django_db
def test_get_trade_context_auction_browse_sets_bid_info(monkeypatch, django_user_model):
    monkeypatch.setattr("trade.selectors.get_auction_stats", lambda *_args, **_kwargs: {"round": 1})

    class Slot:
        def __init__(self, slot_id: int):
            self.id = slot_id

    slots = [Slot(1), Slot(2)]
    monkeypatch.setattr("trade.selectors.get_active_slots", lambda **_kwargs: list(slots))
    monkeypatch.setattr(
        "trade.services.auction_service.get_slots_bid_info_batch",
        lambda slots_list, _manor: {slot.id: {"bid": slot.id} for slot in slots_list},
    )

    manor = _auction_manor(django_user_model, username="trade_ctx_auction")
    request = RequestFactory().get("/trade", {"tab": "auction", "view": "browse"})

    context = get_trade_context(manor=manor, params=build_trade_request_params(request))
    assert context["current_tab"] == "auction"
    assert context["auction_view"] == "browse"
    assert context["auction_stats"] == {"round": 1}
    assert [slot.bid_info for slot in context["auction_slots"]] == [{"bid": 1}, {"bid": 2}]


@pytest.mark.django_db
def test_get_trade_context_auction_defaults_to_browse_without_view(monkeypatch, django_user_model):
    monkeypatch.setattr("trade.selectors.get_auction_stats", lambda *_args, **_kwargs: {"round": 1})

    class Slot:
        id = 1

    monkeypatch.setattr("trade.selectors.get_active_slots", lambda **_kwargs: [Slot()])
    monkeypatch.setattr(
        "trade.services.auction_service.get_slots_bid_info_batch",
        lambda slots_list, _manor: {slot.id: {"bid": slot.id} for slot in slots_list},
    )

    manor = _auction_manor(django_user_model, username="trade_ctx_auction_default")
    request = RequestFactory().get("/trade", {"tab": "auction"})

    context = get_trade_context(manor=manor, params=build_trade_request_params(request))
    assert context["auction_view"] == "browse"
    assert [slot.bid_info for slot in context["auction_slots"]] == [{"bid": 1}]


@pytest.mark.django_db
def test_get_trade_context_auction_browse_tolerates_bid_info_batch_error(monkeypatch, django_user_model):
    monkeypatch.setattr("trade.selectors.get_auction_stats", lambda *_args, **_kwargs: {"round": 1})

    class Slot:
        def __init__(self, slot_id: int):
            self.id = slot_id

    slots = [Slot(1), Slot(2)]
    monkeypatch.setattr("trade.selectors.get_active_slots", lambda **_kwargs: list(slots))
    monkeypatch.setattr(
        "trade.services.auction_service.get_slots_bid_info_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("batch failed")),
    )

    manor = _auction_manor(django_user_model, username="trade_ctx_auction_bidinfo_err")
    request = RequestFactory().get("/trade", {"tab": "auction", "view": "browse"})

    context = get_trade_context(manor=manor, params=build_trade_request_params(request))
    assert context["current_tab"] == "auction"
    assert context["auction_view"] == "browse"
    assert [slot.bid_info for slot in context["auction_slots"]] == [{}, {}]


@pytest.mark.django_db
def test_get_trade_context_auction_browse_tolerates_stats_and_slot_errors(monkeypatch, django_user_model):
    monkeypatch.setattr(
        "trade.selectors.get_auction_stats",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("stats failed")),
    )
    monkeypatch.setattr(
        "trade.selectors.get_active_slots",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("slots failed")),
    )

    manor = _auction_manor(django_user_model, username="trade_ctx_auction_load_err")
    request = RequestFactory().get("/trade", {"tab": "auction", "view": "browse"})

    context = get_trade_context(manor=manor, params=build_trade_request_params(request))
    assert context["current_tab"] == "auction"
    assert context["auction_view"] == "browse"
    assert context["auction_stats"] == {}
    assert context["auction_slots"] == []
    assert context["page_obj"].number == 1


@pytest.mark.django_db
def test_get_trade_context_auction_my_bids_tolerates_loading_errors(monkeypatch, django_user_model):
    monkeypatch.setattr("trade.selectors.get_auction_stats", lambda *_args, **_kwargs: {"round": 1})
    monkeypatch.setattr(
        "trade.selectors.get_my_bids",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("my bids failed")),
    )
    monkeypatch.setattr(
        "trade.selectors.get_my_leading_bids",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("leading bids failed")),
    )

    manor = _auction_manor(django_user_model, username="trade_ctx_auction_my_err")
    request = RequestFactory().get("/trade", {"tab": "auction", "view": "my_bids"})

    context = get_trade_context(manor=manor, params=build_trade_request_params(request))
    assert context["current_tab"] == "auction"
    assert context["auction_view"] == "my_bids"
    assert context["my_bids"] == []
    assert context["my_leading_slots"] == []
