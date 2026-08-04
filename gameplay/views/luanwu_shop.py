"""乱舞商城页面与购买动作。"""

from __future__ import annotations

import logging
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from core.exceptions import GameError
from core.utils import safe_positive_int, sanitize_error_message
from core.utils.rate_limit import rate_limit_redirect
from gameplay.services.luanwu_shop import build_luanwu_shop_context, purchase_luanwu_shop_item
from gameplay.services.manor.core import get_manor, project_manor_activity_for_read
from gameplay.views.read_helpers import get_prepared_manor_for_read

logger = logging.getLogger(__name__)


class LuanwuShopView(LoginRequiredMixin, TemplateView):
    """乱舞商城页面。"""

    template_name = "gameplay/luanwu_shop.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        manor = get_prepared_manor_for_read(
            self.request,
            project_fn=project_manor_activity_for_read,
            logger=logger,
            source="luanwu_shop_view",
        )
        context["manor"] = manor
        context.update(build_luanwu_shop_context(manor))
        return context


@login_required
@require_POST
@rate_limit_redirect(
    "luanwu_shop_purchase",
    limit=30,
    window_seconds=60,
    redirect_url="gameplay:luanwu_shop",
)
def purchase_luanwu_shop_item_view(request: HttpRequest) -> HttpResponse:
    """处理乱舞商城购买请求。"""

    product_key = str(request.POST.get("product_key") or request.POST.get("item_key") or "").strip()
    quantity = safe_positive_int(request.POST.get("quantity"), default=None)
    if quantity is None:
        messages.error(request, "购买数量无效")
        return redirect("gameplay:luanwu_shop")

    manor = get_manor(request.user)
    try:
        result = purchase_luanwu_shop_item(manor, product_key, quantity)
    except GameError as exc:
        messages.error(request, sanitize_error_message(exc))
    except DatabaseError:
        logger.exception(
            "Unexpected luanwu shop purchase error: manor_id=%s user_id=%s product_key=%s quantity=%s",
            getattr(manor, "id", None),
            getattr(request.user, "id", None),
            product_key,
            quantity,
        )
        messages.error(request, "商城暂时无法完成购买，请稍后再试")
    else:
        messages.success(
            request,
            f"购买成功：获得 {result['reward_summary']}，消耗春秋币 {result['total_cost']} 枚。",
        )

    return redirect("gameplay:luanwu_shop")
