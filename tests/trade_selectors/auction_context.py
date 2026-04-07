from __future__ import annotations

import pytest
from django.db import DatabaseError
from django.test import RequestFactory

from tests.trade_selectors.support import create_manor
from trade.page_context import build_trade_request_params
from trade.selectors import get_trade_context


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

    manor = create_manor(django_user_model, username="trade_ctx_auction")
    request = RequestFactory().get("/trade", {"tab": "auction", "view": "browse"})

    context = get_trade_context(manor=manor, params=build_trade_request_params(request))
    assert context["current_tab"] == "auction"
    assert context["auction_view"] == "browse"
    assert context["auction_stats"] == {"round": 1}
    assert [slot.bid_info for slot in context["auction_slots"]] == [{"bid": 1}, {"bid": 2}]


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

    manor = create_manor(django_user_model, username="trade_ctx_auction_bidinfo_err")
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

    manor = create_manor(django_user_model, username="trade_ctx_auction_load_err")
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

    manor = create_manor(django_user_model, username="trade_ctx_auction_my_err")
    request = RequestFactory().get("/trade", {"tab": "auction", "view": "my_bids"})

    context = get_trade_context(manor=manor, params=build_trade_request_params(request))
    assert context["current_tab"] == "auction"
    assert context["auction_view"] == "my_bids"
    assert context["my_bids"] == []
    assert context["my_leading_slots"] == []
