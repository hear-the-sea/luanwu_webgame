from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.utils import timezone

from common.utils.celery import safe_apply_async
from core.exceptions import ForgeOperationError
from core.utils.time_scale import scale_duration
from gameplay.constants import BuildingKeys
from gameplay.models import EquipmentProduction, InventoryItem, ItemTemplate
from gameplay.models import Manor as ManorModel
from gameplay.services.utils.notifications import notify_user

from .. import technology as technology_service
from ..inventory.core import add_item_to_inventory_locked, consume_inventory_item_locked
from .forge_flow_helpers import (
    build_total_material_costs,
    consume_forging_materials_locked,
    create_equipment_production_record,
    finalize_equipment_production_locked,
    schedule_forging_completion_task,
    send_equipment_forging_completion_notification,
    validate_forging_quantity,
)

logger = logging.getLogger(__name__)


def _normalize_forge_runtime_positive_int(raw_value: object, *, contract_name: str) -> int:
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


def _normalize_forge_runtime_materials(raw_value: object, *, contract_name: str) -> dict[str, int]:
    if not isinstance(raw_value, dict):
        raise AssertionError(f"invalid {contract_name}: {raw_value!r}")
    normalized: dict[str, int] = {}
    for material_key, raw_amount in raw_value.items():
        if not isinstance(material_key, str) or not material_key.strip():
            raise AssertionError(f"invalid {contract_name} key: {material_key!r}")
        normalized[material_key.strip()] = _normalize_forge_runtime_positive_int(
            raw_amount,
            contract_name=f"{contract_name} amount",
        )
    return normalized


def _normalize_forge_runtime_config_entry(raw_config: object, *, contract_name: str) -> dict[str, Any]:
    if not isinstance(raw_config, dict):
        raise AssertionError(f"invalid {contract_name}: {raw_config!r}")
    return {
        **raw_config,
        "required_forging": _normalize_forge_runtime_positive_int(
            raw_config.get("required_forging"),
            contract_name=f"{contract_name} required_forging",
        ),
        "base_duration": _normalize_forge_runtime_positive_int(
            raw_config.get("base_duration"),
            contract_name=f"{contract_name} base_duration",
        ),
        "materials": _normalize_forge_runtime_materials(
            raw_config.get("materials"),
            contract_name=f"{contract_name} materials",
        ),
    }


def _get_item_name_map(keys: set[str]) -> dict[str, str]:
    if not keys:
        return {}
    return {tpl.key: tpl.name for tpl in ItemTemplate.objects.filter(key__in=keys).only("key", "name")}


def get_forge_speed_bonus(manor: Any) -> float:
    level = manor.get_building_level(BuildingKeys.FORGE)
    return level * 0.05


def get_max_forging_quantity(manor: Any) -> int:
    forging_level = technology_service.get_player_technology_level(manor, "forging")
    return max(1, forging_level * 50)


def calculate_forging_duration(base_duration: int, manor: Any) -> int:
    bonus = get_forge_speed_bonus(manor)
    duration = max(1, int(base_duration * (1 - bonus)))
    return scale_duration(duration, minimum=1)


def has_active_forging(manor: Any) -> bool:
    return manor.equipment_productions.filter(status=EquipmentProduction.Status.FORGING).exists()


def schedule_forging_completion(production: EquipmentProduction, eta_seconds: int) -> None:
    schedule_forging_completion_task(
        production,
        eta_seconds,
        logger=logger,
        transaction_module=transaction,
        safe_apply_async_func=safe_apply_async,
    )


def start_equipment_forging(
    manor: Any,
    equipment_key: str,
    quantity: int = 1,
    *,
    equipment_config: dict[str, dict[str, Any]],
    material_name_fallback_map: dict[str, str],
) -> Any:
    if equipment_key not in equipment_config:
        raise ForgeOperationError("无效的装备类型")

    config = _normalize_forge_runtime_config_entry(
        equipment_config[equipment_key],
        contract_name=f"forge runtime equipment config {equipment_key}",
    )
    required_level = config["required_forging"]
    equipment_name_map = _get_item_name_map({equipment_key})
    equipment_name = equipment_name_map.get(equipment_key, equipment_key)

    forging_level = technology_service.get_player_technology_level(manor, "forging")
    if forging_level < required_level:
        raise ForgeOperationError(f"需要锻造技{required_level}级才能锻造{equipment_name}")

    max_quantity = get_max_forging_quantity(manor)
    validate_forging_quantity(quantity=quantity, max_quantity=max_quantity)

    total_costs = build_total_material_costs(materials=config["materials"], quantity=quantity)
    material_name_map = _get_item_name_map(set(total_costs.keys()))

    with transaction.atomic():
        locked_manor = ManorModel.objects.select_for_update().get(pk=manor.pk)

        if has_active_forging(locked_manor):
            raise ForgeOperationError("已有装备正在锻造中，同时只能锻造一种装备")

        consume_forging_materials_locked(
            inventory_item_model=InventoryItem,
            locked_manor=locked_manor,
            total_costs=total_costs,
            material_name_map=material_name_map,
            material_name_fallback_map=material_name_fallback_map,
            consume_inventory_item_locked=consume_inventory_item_locked,
        )

        unit_duration = calculate_forging_duration(config["base_duration"], locked_manor)
        actual_duration = unit_duration * quantity
        production = create_equipment_production_record(
            equipment_production_model=EquipmentProduction,
            locked_manor=locked_manor,
            equipment_key=equipment_key,
            equipment_name=equipment_name,
            quantity=quantity,
            total_costs=total_costs,
            base_duration=config["base_duration"],
            actual_duration=actual_duration,
            current_time=timezone.now(),
        )
        schedule_forging_completion(production, actual_duration)

    return production


def finalize_equipment_forging(
    production: Any,
    *,
    send_notification: bool,
) -> bool:
    with transaction.atomic():
        locked_production = finalize_equipment_production_locked(
            equipment_production_model=EquipmentProduction,
            production=production,
            current_time=timezone.now(),
            add_item_to_inventory_locked=add_item_to_inventory_locked,
        )
        if locked_production is None:
            return False

    if send_notification:
        send_equipment_forging_completion_notification(
            production=locked_production,
            logger=logger,
            notify_user_func=notify_user,
        )

    return True


def refresh_equipment_forgings(
    manor: Any,
) -> int:
    completed = 0
    forging = manor.equipment_productions.filter(
        status=EquipmentProduction.Status.FORGING,
        complete_at__lte=timezone.now(),
    )
    for production in forging:
        if finalize_equipment_forging(production, send_notification=True):
            completed += 1
    return completed


def get_active_forgings(manor: Any, *, equipment_production_model: Any) -> list[Any]:
    return list(
        manor.equipment_productions.filter(status=equipment_production_model.Status.FORGING).order_by("complete_at")
    )
