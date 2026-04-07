from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from django.http import HttpRequest

from gameplay.services.resources import project_resource_production_for_read
from gameplay.views.read_helpers import get_prepared_manor_for_read
from trade.selectors import get_trade_context

logger = logging.getLogger(__name__)


def build_trade_request_params(request: HttpRequest) -> dict[str, str]:
    """Normalize trade-page request params before handing them to selectors."""
    return {
        "tab": (request.GET.get("tab") or "shop").strip() or "shop",
        "view": (request.GET.get("view") or "").strip(),
        "category": (request.GET.get("category") or "all").strip() or "all",
        "rarity": (request.GET.get("rarity") or "all").strip() or "all",
        "order_by": (request.GET.get("order_by") or "").strip(),
        "page": (request.GET.get("page") or "1").strip() or "1",
        "buy_page": (request.GET.get("buy_page") or "1").strip() or "1",
        "sell_page": (request.GET.get("sell_page") or "1").strip() or "1",
        "status": (request.GET.get("status") or "all").strip() or "all",
        "troop_category": (request.GET.get("troop_category") or "all").strip() or "all",
    }


def build_trade_page_context(request: HttpRequest) -> dict[str, Any]:
    """Assemble the trade page read model for the current authenticated user."""
    manor = get_prepared_manor_for_read(
        request,
        project_fn=project_resource_production_for_read,
        logger=logger,
        source="trade_view",
    )
    params: Mapping[str, str] = build_trade_request_params(request)
    return get_trade_context(manor=manor, params=params)
