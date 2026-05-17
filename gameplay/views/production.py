"""
生产系统视图：马房、畜牧、冶炼、锻造
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from core.decorators import flash_unexpected_view_error
from core.exceptions import GameError
from core.utils import safe_positive_int, sanitize_error_message
from core.utils.rate_limit import rate_limit_json, rate_limit_redirect
from gameplay.constants import UIConstants
from gameplay.models import Manor
from gameplay.selectors.production import (
    get_forge_page_context,
    get_ranch_page_context,
    get_smithy_page_context,
    get_stable_page_context,
)
from gameplay.services.buildings import forge as forge_service
from gameplay.services.buildings import ranch as ranch_service
from gameplay.services.buildings import smithy as smithy_service
from gameplay.services.buildings import stable as stable_service
from gameplay.services.buildings.stable import start_horse_production
from gameplay.services.manor.core import get_manor
from gameplay.services.resources import project_resource_production_for_read
from gameplay.views.production_forge_handlers import (
    handle_decompose_equipment,
    handle_start_equipment_forging,
    handle_synthesize_blueprint_equipment,
)
from gameplay.views.read_helpers import get_prepared_manor_for_read

from .runtime_refresh_support import run_refresh_api

logger = logging.getLogger(__name__)


def _parse_positive_quantity(raw_quantity: str | None) -> int | None:
    return safe_positive_int(raw_quantity, default=None)


def _normalize_production_result_non_empty_string(raw_value: object, *, contract_name: str) -> str:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    return raw_value.strip()


def _normalize_production_result_positive_int(raw_value: object, *, contract_name: str) -> int:
    if raw_value is None or isinstance(raw_value, bool):
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    raw_for_int: Any = raw_value
    try:
        parsed_value = int(raw_for_int)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}") from exc
    if parsed_value <= 0:
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    return parsed_value


def _build_basic_production_success_message(
    raw_result: object,
    *,
    name_attr: str,
    name_contract: str,
    action_label: str,
) -> str:
    item_name = _normalize_production_result_non_empty_string(
        getattr(raw_result, name_attr, None),
        contract_name=name_contract,
    )
    quantity = _normalize_production_result_positive_int(
        getattr(raw_result, "quantity", None),
        contract_name="production result quantity",
    )
    actual_duration = _normalize_production_result_positive_int(
        getattr(raw_result, "actual_duration", None),
        contract_name="production result actual_duration",
    )
    quantity_text = f"x{quantity}" if quantity > 1 else ""
    return f"{item_name}{quantity_text} {action_label}，预计 {actual_duration} 秒后完成"


def _handle_unexpected_production_error(
    request: HttpRequest,
    exc: Exception,
    *,
    log_message: str,
    log_args: tuple[object, ...],
) -> None:
    flash_unexpected_view_error(
        request,
        exc,
        log_message=log_message,
        log_args=log_args,
        logger_instance=logger,
    )


def _get_prepared_production_manor(request: HttpRequest, *, source: str) -> Manor:
    return get_prepared_manor_for_read(
        request,
        project_fn=project_resource_production_for_read,
        logger=logger,
        source=source,
    )


def _run_basic_production_start(
    request: HttpRequest,
    *,
    item_key: str,
    raw_quantity: str | None,
    redirect_name: str,
    missing_key_message: str,
    start_operation: Callable[[Manor, str, int], Any],
    success_message: Callable[[Any], str],
    log_message: str,
) -> HttpResponse:
    manor = get_manor(request.user)
    quantity = _parse_positive_quantity(raw_quantity)

    if not item_key:
        messages.error(request, missing_key_message)
        return redirect(redirect_name)
    if quantity is None:
        messages.error(request, "无效的数量")
        return redirect(redirect_name)

    try:
        production = start_operation(manor, item_key, quantity)
        messages.success(request, success_message(production))
    except GameError as exc:
        messages.error(request, sanitize_error_message(exc))
    except DatabaseError as exc:
        _handle_unexpected_production_error(
            request,
            exc,
            log_message=log_message,
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                item_key,
                raw_quantity,
            ),
        )

    return redirect(redirect_name)


def _refresh_all_active_productions(manor: Manor) -> int:
    return (
        int(stable_service.refresh_horse_productions(manor))
        + int(ranch_service.refresh_livestock_productions(manor))
        + int(smithy_service.refresh_smelting_productions(manor))
        + int(forge_service.refresh_equipment_forgings(manor))
    )


class StableView(LoginRequiredMixin, TemplateView):
    """马房页面"""

    template_name = "gameplay/stable.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        manor = _get_prepared_production_manor(self.request, source="stable_view")
        context["manor"] = manor
        context.update(get_stable_page_context(manor))
        return context


@login_required
@require_POST
@rate_limit_json(
    "production_runtime_refresh", limit=30, window_seconds=60, error_message="状态刷新过于频繁，请稍后再试"
)
def refresh_production_runtime_api(request: HttpRequest) -> JsonResponse:
    manor = get_manor(request.user)
    return run_refresh_api(
        operation=lambda: _refresh_all_active_productions(manor),
        logger_instance=logger,
        log_message="Unexpected production refresh error: manor_id=%s user_id=%s",
        log_args=(
            getattr(manor, "id", None),
            getattr(request.user, "id", None),
        ),
    )


@login_required
@require_POST
@rate_limit_redirect("horse_production", limit=10, window_seconds=60)
def start_horse_production_view(request: HttpRequest) -> HttpResponse:
    """开始马匹生产"""
    horse_key = (request.POST.get("horse_key") or "").strip()
    return _run_basic_production_start(
        request,
        item_key=horse_key,
        raw_quantity=request.POST.get("quantity"),
        redirect_name="gameplay:stable",
        missing_key_message="请选择马匹类型",
        start_operation=start_horse_production,
        success_message=lambda production: _build_basic_production_success_message(
            production,
            name_attr="horse_name",
            name_contract="horse production result horse_name",
            action_label="开始生产",
        ),
        log_message="Unexpected horse production start error: manor_id=%s user_id=%s horse_key=%s quantity=%s",
    )


class RanchView(LoginRequiredMixin, TemplateView):
    """畜牧场页面"""

    template_name = "gameplay/ranch.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        manor = _get_prepared_production_manor(self.request, source="ranch_view")
        context["manor"] = manor
        context.update(get_ranch_page_context(manor))
        return context


@login_required
@require_POST
@rate_limit_redirect("livestock_production", limit=10, window_seconds=60)
def start_livestock_production_view(request: HttpRequest) -> HttpResponse:
    """开始家畜养殖"""
    livestock_key = (request.POST.get("livestock_key") or "").strip()
    return _run_basic_production_start(
        request,
        item_key=livestock_key,
        raw_quantity=request.POST.get("quantity"),
        redirect_name="gameplay:ranch",
        missing_key_message="请选择家畜类型",
        start_operation=ranch_service.start_livestock_production,
        success_message=lambda production: _build_basic_production_success_message(
            production,
            name_attr="livestock_name",
            name_contract="livestock production result livestock_name",
            action_label="开始养殖",
        ),
        log_message="Unexpected livestock production start error: manor_id=%s user_id=%s livestock_key=%s quantity=%s",
    )


class SmithyView(LoginRequiredMixin, TemplateView):
    """冶炼坊页面"""

    template_name = "gameplay/smithy.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        manor = _get_prepared_production_manor(self.request, source="smithy_view")
        context["manor"] = manor
        context.update(get_smithy_page_context(manor, current_category=self.request.GET.get("category")))
        return context


@login_required
@require_POST
@rate_limit_redirect("smelting_production", limit=10, window_seconds=60)
def start_smelting_production_view(request: HttpRequest) -> HttpResponse:
    """开始冶炼坊制作"""
    metal_key = (request.POST.get("metal_key") or "").strip()
    return _run_basic_production_start(
        request,
        item_key=metal_key,
        raw_quantity=request.POST.get("quantity"),
        redirect_name="gameplay:smithy",
        missing_key_message="请选择物品类型",
        start_operation=smithy_service.start_smelting_production,
        success_message=lambda production: _build_basic_production_success_message(
            production,
            name_attr="metal_name",
            name_contract="smelting production result metal_name",
            action_label="开始制作",
        ),
        log_message="Unexpected smelting production start error: manor_id=%s user_id=%s metal_key=%s quantity=%s",
    )


class ForgeView(LoginRequiredMixin, TemplateView):
    """铁匠铺装备锻造页面"""

    template_name = "gameplay/forge.html"
    ITEMS_PER_PAGE = UIConstants.FORGE_ITEMS_PER_PAGE
    DECOMPOSE_ITEMS_PER_PAGE = 9

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        manor = _get_prepared_production_manor(self.request, source="forge_view")
        context["manor"] = manor
        context.update(
            get_forge_page_context(
                manor,
                current_mode=self.request.GET.get("mode") or "synthesize",
                current_category=self.request.GET.get("category") or "all",
                page=self.request.GET.get("page", 1),
                items_per_page=self.ITEMS_PER_PAGE,
                decompose_items_per_page=self.DECOMPOSE_ITEMS_PER_PAGE,
            )
        )
        return context


@login_required
@require_POST
@rate_limit_redirect("equipment_forging", limit=10, window_seconds=60)
def start_equipment_forging_view(request: HttpRequest) -> HttpResponse:
    """开始装备锻造"""
    category = request.POST.get("category", "all")
    manor = get_manor(request.user)
    return handle_start_equipment_forging(
        request,
        manor=manor,
        category=category,
        on_database_error=lambda exc: _handle_unexpected_production_error(
            request,
            exc,
            log_message="Unexpected equipment forging start error: manor_id=%s user_id=%s equipment_key=%s quantity=%s",
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                (request.POST.get("equipment_key") or "").strip(),
                request.POST.get("quantity"),
            ),
        ),
    )


@login_required
@require_POST
@rate_limit_redirect("equipment_decompose", limit=10, window_seconds=60)
def decompose_equipment_view(request: HttpRequest) -> HttpResponse:
    """分解装备"""
    category = request.POST.get("category", "all")
    manor = get_manor(request.user)
    return handle_decompose_equipment(
        request,
        manor=manor,
        category=category,
        on_database_error=lambda exc: _handle_unexpected_production_error(
            request,
            exc,
            log_message="Unexpected equipment decompose error: manor_id=%s user_id=%s equipment_key=%s quantity=%s",
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                (request.POST.get("equipment_key") or "").strip(),
                request.POST.get("quantity"),
            ),
        ),
    )


@login_required
@require_POST
@rate_limit_redirect("blueprint_synthesize", limit=10, window_seconds=60)
def synthesize_blueprint_equipment_view(request: HttpRequest) -> HttpResponse:
    """按图纸合成装备"""
    category = request.POST.get("category", "all")
    manor = get_manor(request.user)
    return handle_synthesize_blueprint_equipment(
        request,
        manor=manor,
        category=category,
        on_database_error=lambda exc: _handle_unexpected_production_error(
            request,
            exc,
            log_message="Unexpected blueprint synthesize error: manor_id=%s user_id=%s blueprint_key=%s quantity=%s",
            log_args=(
                getattr(manor, "id", None),
                getattr(request.user, "id", None),
                (request.POST.get("blueprint_key") or "").strip(),
                request.POST.get("quantity"),
            ),
        ),
    )
