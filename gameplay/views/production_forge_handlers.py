from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.contrib import messages
from django.db import DatabaseError
from django.shortcuts import redirect
from django.urls import reverse

from core.exceptions import GameError
from core.utils import safe_positive_int, sanitize_error_message
from gameplay.models import ItemTemplate, Manor
from gameplay.services.buildings import forge as forge_service


def _parse_positive_quantity(raw_quantity: str | None) -> int | None:
    return safe_positive_int(raw_quantity, default=None)


def _normalize_forge_result_non_empty_string(raw_value: object, *, contract_name: str) -> str:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    return raw_value.strip()


def _normalize_forge_result_positive_int(raw_value: object, *, contract_name: str) -> int:
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


def _normalize_forge_rewards_mapping(raw_value: object, *, contract_name: str) -> dict[str, int]:
    if not isinstance(raw_value, dict):
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    normalized: dict[str, int] = {}
    for reward_key, reward_amount in raw_value.items():
        normalized_key = _normalize_forge_result_non_empty_string(
            reward_key,
            contract_name=f"{contract_name} key",
        )
        normalized[normalized_key] = _normalize_forge_result_positive_int(
            reward_amount,
            contract_name=f"{contract_name} amount",
        )
    return normalized


def _normalize_forge_mode(raw_mode: str | None, *, default: str = "synthesize") -> str:
    mode = (raw_mode or default).strip()
    if mode not in {"synthesize", "decompose"}:
        return default
    return mode


def _forge_redirect_url(category: str, mode: str) -> str:
    return f"{reverse('gameplay:forge')}?mode={mode}&category={category}"


def _build_decompose_reward_text(raw_result: object) -> str:
    if not isinstance(raw_result, dict):
        raise AssertionError(f"invalid forge decompose result payload: {raw_result!r}")
    reward_map = _normalize_forge_rewards_mapping(
        raw_result.get("rewards"),
        contract_name="forge decompose result rewards",
    )
    reward_templates = {
        template.key: template.name
        for template in ItemTemplate.objects.filter(key__in=reward_map.keys()).only("key", "name")
    }
    reward_parts = [f"{reward_templates.get(key, '未知物品')}×{amount}" for key, amount in reward_map.items()]
    return f"，获得：{'、'.join(reward_parts)}" if reward_parts else ""


def _build_start_equipment_forging_success_message(raw_result: object) -> str:
    equipment_name = _normalize_forge_result_non_empty_string(
        getattr(raw_result, "equipment_name", None),
        contract_name="forge production result equipment_name",
    )
    quantity = _normalize_forge_result_positive_int(
        getattr(raw_result, "quantity", None),
        contract_name="forge production result quantity",
    )
    actual_duration = _normalize_forge_result_positive_int(
        getattr(raw_result, "actual_duration", None),
        contract_name="forge production result actual_duration",
    )
    quantity_text = f"×{quantity}" if quantity > 1 else ""
    return f"{equipment_name}{quantity_text} 开始锻造，预计 {actual_duration} 秒后完成"


def _build_decompose_success_message(raw_result: object) -> str:
    if not isinstance(raw_result, dict):
        raise AssertionError(f"invalid forge decompose result payload: {raw_result!r}")
    equipment_name = _normalize_forge_result_non_empty_string(
        raw_result.get("equipment_name"),
        contract_name="forge decompose result equipment_name",
    )
    quantity = _normalize_forge_result_positive_int(
        raw_result.get("quantity"),
        contract_name="forge decompose result quantity",
    )
    quantity_text = f"×{quantity}" if quantity > 1 else ""
    return f"{equipment_name}{quantity_text} 分解完成{_build_decompose_reward_text(raw_result)}"


def _build_blueprint_synthesize_success_message(raw_result: object) -> str:
    if not isinstance(raw_result, dict):
        raise AssertionError(f"invalid forge blueprint result payload: {raw_result!r}")
    result_name = _normalize_forge_result_non_empty_string(
        raw_result.get("result_name"),
        contract_name="forge blueprint result result_name",
    )
    quantity = _normalize_forge_result_positive_int(
        raw_result.get("quantity"),
        contract_name="forge blueprint result quantity",
    )
    quantity_text = f"×{quantity}" if quantity > 1 else ""
    return f"{result_name}{quantity_text} 合成完成"


def run_forge_post_action(
    request,
    *,
    manor: Manor,
    item_key: str,
    raw_quantity: str | None,
    category: str,
    raw_mode: str | None,
    default_mode: str,
    missing_key_message: str,
    operation: Callable[[Manor, str, int], Any],
    success_message: Callable[[Any], str],
    on_database_error: Callable[[Exception], None],
) -> Any:
    quantity = _parse_positive_quantity(raw_quantity)
    mode = _normalize_forge_mode(raw_mode, default=default_mode)
    redirect_url = _forge_redirect_url(category, mode)

    if not item_key:
        messages.error(request, missing_key_message)
        return redirect(redirect_url)
    if quantity is None:
        messages.error(request, "无效的数量")
        return redirect(redirect_url)

    try:
        result = operation(manor, item_key, quantity)
        messages.success(request, success_message(result))
    except GameError as exc:
        messages.error(request, sanitize_error_message(exc))
    except DatabaseError as exc:
        on_database_error(exc)

    return redirect(redirect_url)


def handle_start_equipment_forging(
    request, *, manor: Manor, category: str, on_database_error: Callable[[Exception], None]
):
    equipment_key = (request.POST.get("equipment_key") or "").strip()
    return run_forge_post_action(
        request,
        manor=manor,
        item_key=equipment_key,
        raw_quantity=request.POST.get("quantity"),
        category=category,
        raw_mode=request.POST.get("mode"),
        default_mode="synthesize",
        missing_key_message="请选择装备类型",
        operation=forge_service.start_equipment_forging,
        success_message=_build_start_equipment_forging_success_message,
        on_database_error=on_database_error,
    )


def handle_decompose_equipment(request, *, manor: Manor, category: str, on_database_error: Callable[[Exception], None]):
    equipment_key = (request.POST.get("equipment_key") or "").strip()
    return run_forge_post_action(
        request,
        manor=manor,
        item_key=equipment_key,
        raw_quantity=request.POST.get("quantity"),
        category=category,
        raw_mode=request.POST.get("mode"),
        default_mode="decompose",
        missing_key_message="请选择要分解的装备",
        operation=forge_service.decompose_equipment,
        success_message=_build_decompose_success_message,
        on_database_error=on_database_error,
    )


def handle_synthesize_blueprint_equipment(
    request,
    *,
    manor: Manor,
    category: str,
    on_database_error: Callable[[Exception], None],
):
    blueprint_key = (request.POST.get("blueprint_key") or "").strip()
    return run_forge_post_action(
        request,
        manor=manor,
        item_key=blueprint_key,
        raw_quantity=request.POST.get("quantity"),
        category=category,
        raw_mode=request.POST.get("mode"),
        default_mode="synthesize",
        missing_key_message="请选择图纸",
        operation=forge_service.synthesize_equipment_with_blueprint,
        success_message=_build_blueprint_synthesize_success_message,
        on_database_error=on_database_error,
    )
