from __future__ import annotations

import pytest
from django.db import DatabaseError
from django.test import RequestFactory

from tests.trade_selectors.support import create_manor
from trade.page_context import build_trade_request_params
from trade.selectors import get_trade_context


@pytest.mark.django_db
def test_get_trade_context_bank_includes_bank_info(monkeypatch, django_user_model):
    monkeypatch.setattr(
        "trade.selectors.get_bank_info",
        lambda *_args, **_kwargs: {
            "current_rate": 123,
            "pricing_degraded": False,
            "exchange_available": True,
            "pricing_status_message": "",
        },
    )
    monkeypatch.setattr("trade.selectors.get_troop_bank_capacity", lambda *_args, **_kwargs: 5000)
    monkeypatch.setattr("trade.selectors.get_troop_bank_used_space", lambda *_args, **_kwargs: 100)
    monkeypatch.setattr("trade.selectors.get_troop_bank_remaining_space", lambda *_args, **_kwargs: 4900)
    monkeypatch.setattr(
        "trade.selectors.get_troop_bank_rows",
        lambda *_args, **_kwargs: [{"key": "dao_jie", "name": "刀手", "player_count": 10, "bank_count": 5}],
    )

    manor = create_manor(django_user_model, username="trade_ctx_bank")
    request = RequestFactory().get("/trade", {"tab": "bank"})

    context = get_trade_context(manor=manor, params=build_trade_request_params(request))
    assert context["current_tab"] == "bank"
    assert context["bank_info"]["current_rate"] == 123
    assert context["trade_alerts"] == []
    assert context["troop_bank_capacity"] == 5000
    assert context["troop_bank_used"] == 100
    assert context["troop_bank_remaining"] == 4900
    assert len(context["troop_bank_rows"]) == 1


@pytest.mark.django_db
def test_get_trade_context_bank_marks_bank_info_error_as_degraded(monkeypatch, django_user_model):
    monkeypatch.setattr(
        "trade.selectors.get_bank_info",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DatabaseError("bank failed")),
    )

    manor = create_manor(django_user_model, username="trade_ctx_bank_info_err")
    request = RequestFactory().get("/trade", {"tab": "bank"})

    context = get_trade_context(manor=manor, params=build_trade_request_params(request))
    assert context["current_tab"] == "bank"
    assert context["bank_info"]["pricing_degraded"] is True
    assert context["bank_info"]["exchange_available"] is False
    assert "已暂时关闭兑换" in context["bank_info"]["pricing_status_message"]
    assert context["trade_alerts"][0]["section"] == "bank"
