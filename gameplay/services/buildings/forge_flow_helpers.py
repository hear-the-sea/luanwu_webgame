from __future__ import annotations

from datetime import datetime, timedelta
from logging import Logger
from typing import TYPE_CHECKING, Any, Callable, Iterable

from core.exceptions import ForgeOperationError, MessageError
from core.utils.imports import is_missing_target_import
from core.utils.infrastructure import (
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    NOTIFICATION_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)

if TYPE_CHECKING:
    from ...models import EquipmentProduction, InventoryItem, Manor

FilteredConfigs = list[tuple[str, dict[str, Any]]]
FORGE_MESSAGE_BEST_EFFORT_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    MessageError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)


def build_filtered_equipment_configs(
    *,
    equipment_config: dict[str, dict[str, Any]],
    category: str | None,
) -> FilteredConfigs:
    return [
        (equip_key, config)
        for equip_key, config in equipment_config.items()
        if not category or config.get("category") == category
    ]


def load_material_quantity_map(
    *,
    inventory_item_model: type[InventoryItem],
    manor: Manor,
    material_keys: set[str],
    build_inventory_quantity_map: Callable[[Iterable[Any]], dict[str, int]],
) -> dict[str, int]:
    if not material_keys:
        return {}

    material_inventory = (
        inventory_item_model.objects.filter(
            manor=manor,
            template__key__in=material_keys,
            storage_location=inventory_item_model.StorageLocation.WAREHOUSE,
        )
        .select_related("template")
        .order_by("id")
    )
    return build_inventory_quantity_map(material_inventory)


def build_equipment_options(
    *,
    manor: Manor,
    filtered_configs: FilteredConfigs,
    item_name_map: dict[str, str],
    material_quantities: dict[str, int],
    material_name_fallback_map: dict[str, str],
    equipment_categories: dict[str, str],
    calculate_forging_duration: Callable[[int, Manor], int],
    build_equipment_option: Callable[..., dict[str, Any]],
    forging_level: int,
    max_quantity: int,
    is_forging: bool,
) -> list[dict[str, Any]]:
    return [
        build_equipment_option(
            equip_key,
            config,
            item_name_map=item_name_map,
            material_quantities=material_quantities,
            material_name_fallback_map=material_name_fallback_map,
            equipment_categories=equipment_categories,
            actual_duration=calculate_forging_duration(int(config["base_duration"]), manor),
            required_level=int(config.get("required_forging", 1) or 1),
            forging_level=forging_level,
            max_quantity=max_quantity,
            is_forging=is_forging,
        )
        for equip_key, config in filtered_configs
    ]


def validate_forging_quantity(*, quantity: int, max_quantity: int) -> None:
    if quantity < 1:
        raise ForgeOperationError("锻造数量至少为1")
    if quantity > max_quantity:
        raise ForgeOperationError(f"锻造技等级限制，单次最多锻造{max_quantity}件")


def build_total_material_costs(*, materials: dict[str, int], quantity: int) -> dict[str, int]:
    return {mat_key: mat_amount * quantity for mat_key, mat_amount in materials.items()}


def consume_forging_materials_locked(
    *,
    inventory_item_model: type[InventoryItem],
    locked_manor: Manor,
    total_costs: dict[str, int],
    material_name_map: dict[str, str],
    material_name_fallback_map: dict[str, str],
    consume_inventory_item_locked: Callable[[InventoryItem, int], None],
) -> None:
    for mat_key, total_amount in total_costs.items():
        item = (
            inventory_item_model.objects.select_for_update()
            .select_related("template", "manor")
            .filter(
                manor=locked_manor,
                template__key=mat_key,
                storage_location=inventory_item_model.StorageLocation.WAREHOUSE,
            )
            .first()
        )
        mat_name = material_name_map.get(mat_key, material_name_fallback_map.get(mat_key, "所需材料"))
        if not item or item.quantity < total_amount:
            raise ForgeOperationError(f"{mat_name}不足")
        consume_inventory_item_locked(item, total_amount)


def create_equipment_production_record(
    *,
    equipment_production_model: type[EquipmentProduction],
    locked_manor: Manor,
    equipment_key: str,
    equipment_name: str,
    quantity: int,
    total_costs: dict[str, int],
    base_duration: int,
    actual_duration: int,
    current_time: datetime,
) -> EquipmentProduction:
    return equipment_production_model.objects.create(
        manor=locked_manor,
        equipment_key=equipment_key,
        equipment_name=equipment_name,
        quantity=quantity,
        material_costs=total_costs,
        base_duration=base_duration,
        actual_duration=actual_duration,
        complete_at=current_time + timedelta(seconds=actual_duration),
    )


def schedule_forging_completion_task(
    production: EquipmentProduction,
    eta_seconds: int,
    *,
    logger: Logger,
    transaction_module: Any,
    safe_apply_async_func: Callable[..., Any],
) -> None:
    countdown = max(0, int(eta_seconds))
    try:
        from gameplay.tasks import complete_equipment_forging
    except ImportError as exc:
        if not is_missing_target_import(exc, "gameplay.tasks"):
            raise
        logger.warning("Unable to import complete_equipment_forging task; skip scheduling", exc_info=True)
        return

    transaction_module.on_commit(
        lambda: safe_apply_async_func(
            complete_equipment_forging,
            args=[production.id],
            countdown=countdown,
            logger=logger,
            log_message="complete_equipment_forging dispatch failed",
        )
    )


def finalize_equipment_production_locked(
    *,
    equipment_production_model: type[EquipmentProduction],
    production: EquipmentProduction,
    current_time: datetime,
    add_item_to_inventory_locked: Callable[[Manor, str, int], Any],
) -> EquipmentProduction | None:
    if not getattr(production, "pk", None):
        return None

    locked_production = (
        equipment_production_model.objects.select_for_update()
        .select_related("manor", "manor__user")
        .get(pk=production.pk)
    )
    if locked_production.status != equipment_production_model.Status.FORGING:
        return None
    if locked_production.complete_at > current_time:
        return None

    add_item_to_inventory_locked(
        locked_production.manor,
        locked_production.equipment_key,
        locked_production.quantity,
    )
    locked_production.status = equipment_production_model.Status.COMPLETED
    locked_production.finished_at = current_time
    locked_production.save(update_fields=["status", "finished_at"])
    return locked_production


def build_forging_quantity_text(quantity: int) -> str:
    return f"×{quantity}" if int(quantity) > 1 else ""


def send_equipment_forging_completion_notification(
    *,
    production: EquipmentProduction,
    logger: Logger,
    notify_user_func: Callable[..., Any],
) -> None:
    from ...models import Message
    from ..utils.messages import create_message

    quantity_text = build_forging_quantity_text(int(getattr(production, "quantity", 0) or 0))
    try:
        create_message(
            manor=production.manor,
            kind=Message.Kind.SYSTEM,
            title=f"{production.equipment_name}{quantity_text}锻造完成",
            body=f"您的{production.equipment_name}{quantity_text}已锻造完成，请到仓库查收。",
        )
    except FORGE_MESSAGE_BEST_EFFORT_EXCEPTIONS as exc:
        logger.warning(
            "equipment forging message creation failed: production_id=%s manor_id=%s error=%s",
            getattr(production, "id", None),
            getattr(production, "manor_id", None),
            exc,
            exc_info=True,
        )
        return

    try:
        notify_user_func(
            production.manor.user_id,
            {
                "kind": "system",
                "title": f"{production.equipment_name}{quantity_text}锻造完成",
                "equipment_key": production.equipment_key,
                "quantity": production.quantity,
            },
            log_context="equipment forging notification",
        )
    except NOTIFICATION_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning(
            "equipment forging notification failed: production_id=%s manor_id=%s error=%s",
            getattr(production, "id", None),
            getattr(production, "manor_id", None),
            exc,
            exc_info=True,
        )
