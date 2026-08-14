"""
冶炼坊服务模块

提供金属冶炼与药品制作相关功能。
"""

from __future__ import annotations

import logging
from datetime import timedelta
from functools import lru_cache
from typing import Any, Dict, List

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from common.utils.celery import safe_apply_async
from core.exceptions import InsufficientResourceError, MessageError, ProductionStartError
from core.utils.imports import is_missing_target_import
from core.utils.infrastructure import (
    DATABASE_INFRASTRUCTURE_EXCEPTIONS,
    NOTIFICATION_INFRASTRUCTURE_EXCEPTIONS,
    InfrastructureExceptions,
    combine_infrastructure_exceptions,
)
from core.utils.time_scale import scale_duration
from core.utils.yaml_loader import load_yaml_data

from ...constants import BuildingKeys
from ...models import Manor, SmeltingProduction
from ..utils.notifications import notify_user
from .production_cancel import cancel_active_production

logger = logging.getLogger(__name__)
SMITHY_MESSAGE_BEST_EFFORT_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    MessageError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)

# 制作配置（向后兼容沿用 METAL_CONFIG 名称）
# 金属：
# - 冶炼技等级需求：1级铜，2级锡，3级铁
# - 消耗材料：1白银→1铜，5铜→1锡，3锡→1铁
# - 时间：1、3、5分钟
# 药品（冶炼坊等级）：
# - Lv1 止血散（50银两，5分钟）
# - Lv3 金创药（100银两，7分钟）
# - Lv5 白芨丸（150银两，10分钟）
# - Lv7 白草丹（200银两，12分钟）
# - Lv8 补血丹（300银两，12分钟）
# - Lv9 丁香丹（600银两，15分钟）
# - Lv10 天香玉露丸（1000银两，20分钟）
DEFAULT_SMITHY_PRODUCTION_CONFIG: Dict[str, Dict[str, Any]] = {}


def _normalize_smithy_item_key(raw_key: Any) -> str:
    if not isinstance(raw_key, str) or not raw_key.strip():
        raise AssertionError(f"invalid smithy production item key: {raw_key!r}")
    return raw_key.strip()


def _normalize_smithy_non_empty_string(raw_value: Any, *, field_name: str) -> str:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise AssertionError(f"invalid smithy production {field_name}: {raw_value!r}")
    return raw_value.strip()


def _normalize_smithy_positive_int(raw_value: Any, *, field_name: str) -> int:
    if raw_value is None or isinstance(raw_value, bool):
        raise AssertionError(f"invalid smithy production {field_name}: {raw_value!r}")
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid smithy production {field_name}: {raw_value!r}") from exc
    if value <= 0:
        raise AssertionError(f"invalid smithy production {field_name}: {raw_value!r}")
    return value


def _normalize_smithy_production_config(raw: Any) -> Dict[str, Dict[str, Any]]:
    root = raw
    if isinstance(raw, dict) and "production" in raw:
        root = raw.get("production")
    if not isinstance(root, dict):
        raise AssertionError(f"invalid smithy production config root: {raw!r}")

    config: Dict[str, Dict[str, Any]] = {}
    for raw_key, raw_item in root.items():
        item_key = _normalize_smithy_item_key(raw_key)
        if not isinstance(raw_item, dict):
            raise AssertionError(f"invalid smithy production item payload: {raw_item!r}")
        category = _normalize_smithy_non_empty_string(raw_item.get("category"), field_name="category")
        cost_type = _normalize_smithy_non_empty_string(raw_item.get("cost_type"), field_name="cost_type")
        cost_amount = _normalize_smithy_positive_int(raw_item.get("cost_amount"), field_name="cost_amount")
        base_duration = _normalize_smithy_positive_int(raw_item.get("base_duration"), field_name="base_duration")
        normalized = {
            "cost_type": cost_type,
            "cost_amount": cost_amount,
            "base_duration": base_duration,
            "category": category,
        }
        if raw_item.get("required_smelting") is not None:
            normalized["required_smelting"] = _normalize_smithy_positive_int(
                raw_item.get("required_smelting"),
                field_name="required_smelting",
            )
        if raw_item.get("required_smithy") is not None:
            normalized["required_smithy"] = _normalize_smithy_positive_int(
                raw_item.get("required_smithy"),
                field_name="required_smithy",
            )
        config[item_key] = normalized
    return config


@lru_cache(maxsize=1)
def load_smithy_production_config() -> Dict[str, Dict[str, Any]]:
    path = settings.BASE_DIR / "data" / "smithy_production.yaml"
    raw = load_yaml_data(
        path,
        logger=logger,
        context="smithy production config",
        default={"production": DEFAULT_SMITHY_PRODUCTION_CONFIG},
    )
    return _normalize_smithy_production_config(raw)


def clear_smithy_production_cache() -> None:
    global METAL_CONFIG
    load_smithy_production_config.cache_clear()
    METAL_CONFIG = load_smithy_production_config()


METAL_CONFIG: Dict[str, Dict[str, Any]] = load_smithy_production_config()


def _normalize_smithy_runtime_config_entry(raw_config: object, *, contract_name: str) -> Dict[str, Any]:
    if not isinstance(raw_config, dict):
        raise AssertionError(f"invalid {contract_name}: {raw_config!r}")
    normalized: Dict[str, Any] = {
        "cost_type": _normalize_smithy_non_empty_string(raw_config.get("cost_type"), field_name="cost_type"),
        "cost_amount": _normalize_smithy_positive_int(raw_config.get("cost_amount"), field_name="cost_amount"),
        "base_duration": _normalize_smithy_positive_int(raw_config.get("base_duration"), field_name="base_duration"),
        "category": _normalize_smithy_non_empty_string(raw_config.get("category"), field_name="category"),
    }
    if raw_config.get("required_smithy") is not None:
        normalized["required_smithy"] = _normalize_smithy_positive_int(
            raw_config.get("required_smithy"),
            field_name="required_smithy",
        )
    if raw_config.get("required_smelting") is not None:
        normalized["required_smelting"] = _normalize_smithy_positive_int(
            raw_config.get("required_smelting"),
            field_name="required_smelting",
        )
    return normalized


def _get_item_name_map(keys: set[str]) -> Dict[str, str]:
    if not keys:
        return {}
    from ...models import ItemTemplate

    return {tpl.key: tpl.name for tpl in ItemTemplate.objects.filter(key__in=keys).only("key", "name")}


def _get_unlock_requirement(config: Dict[str, Any], smelting_level: int, smithy_level: int) -> tuple[bool, int, str]:
    """返回解锁状态、需求等级、需求类型标签。"""
    required_smithy_raw = config.get("required_smithy")
    required_smithy = required_smithy_raw if isinstance(required_smithy_raw, int) else None
    if required_smithy is not None and required_smithy > 0:
        return smithy_level >= required_smithy, required_smithy, "冶炼坊"

    required_smelting_raw = config.get("required_smelting")
    required_smelting = required_smelting_raw if isinstance(required_smelting_raw, int) else None
    if required_smelting is not None and required_smelting > 0:
        return smelting_level >= required_smelting, required_smelting, "冶炼技"

    return True, 0, ""


def get_smithy_speed_bonus(manor: Manor) -> float:
    """
    获取冶炼坊速度加成。

    10级满级提升50%，每级约5%。

    Args:
        manor: 庄园实例

    Returns:
        速度加成倍率（如0.5表示减少50%时间）
    """
    level = manor.get_building_level(BuildingKeys.SMITHY)
    return level * 0.05


def get_max_smelting_quantity(manor: Manor) -> int:
    """
    获取单次冶炼金属的最大数量。

    冶炼技每级增加100个上限，满级5级=500个。

    Args:
        manor: 庄园实例

    Returns:
        最大冶炼数量
    """
    from ..technology import get_player_technology_level

    smelting_level = get_player_technology_level(manor, "smelting")
    # 每级100个，最少1个（0级时也能冶炼1个）
    return max(1, smelting_level * 100)


def get_max_smithy_medicine_quantity(manor: Manor) -> int:
    """
    获取单次药品制作的最大数量。

    药品按冶炼坊建筑等级解锁，批量制作上限也跟随冶炼坊等级。
    """
    smithy_level = manor.get_building_level(BuildingKeys.SMITHY)
    return max(1, smithy_level * 100)


def _get_max_quantity_for_config(manor: Manor, config: Dict[str, Any]) -> int:
    if config.get("category") == "medicine":
        return get_max_smithy_medicine_quantity(manor)
    return get_max_smelting_quantity(manor)


def _get_quantity_limit_label(config: Dict[str, Any]) -> str:
    if config.get("category") == "medicine":
        return "冶炼坊等级"
    return "冶炼技等级"


def calculate_smelting_duration(base_duration: int, manor: Manor) -> int:
    """
    计算实际冶炼时间。

    Args:
        base_duration: 基础时间（秒）
        manor: 庄园实例

    Returns:
        实际冶炼时间（秒）
    """
    bonus = get_smithy_speed_bonus(manor)
    # 加成越高，时间越短
    duration = max(1, int(base_duration * (1 - bonus)))
    return scale_duration(duration, minimum=1)


def has_active_smelting_production(manor: Manor) -> bool:
    """
    检查是否有正在进行的冶炼坊制作。

    Args:
        manor: 庄园实例

    Returns:
        是否有制作中的物品
    """
    return manor.smelting_productions.filter(status=SmeltingProduction.Status.PRODUCING).exists()


def get_metal_options(manor: Manor) -> List[Dict[str, Any]]:
    """
    获取冶炼坊制作选项列表。

    Args:
        manor: 庄园实例

    Returns:
        物品选项列表
    """
    from ..inventory.core import get_item_quantity
    from ..technology import get_player_technology_level

    smelting_level = get_player_technology_level(manor, "smelting")
    smithy_level = manor.get_building_level(BuildingKeys.SMITHY)
    is_producing = has_active_smelting_production(manor)

    normalized_configs = {
        metal_key: _normalize_smithy_runtime_config_entry(
            raw_config,
            contract_name=f"smithy runtime production config {metal_key}",
        )
        for metal_key, raw_config in METAL_CONFIG.items()
    }
    cost_types = {cfg["cost_type"] for cfg in normalized_configs.values()}
    name_keys = set(METAL_CONFIG.keys()) | {key for key in cost_types if key and key != "silver"}
    item_name_map = _get_item_name_map(name_keys)

    options = []
    for metal_key, config in normalized_configs.items():
        actual_duration = calculate_smelting_duration(config["base_duration"], manor)
        is_unlocked, required_level, required_type_label = _get_unlock_requirement(config, smelting_level, smithy_level)
        max_quantity = _get_max_quantity_for_config(manor, config)
        cost_type = config["cost_type"]
        cost_amount = config["cost_amount"]
        category = config["category"]
        metal_name = item_name_map.get(metal_key, "未知物品")
        cost_type_name = "银两" if cost_type == "silver" else item_name_map.get(cost_type, "未知材料")

        # 检查是否有足够的材料
        if cost_type == "silver":
            current_amount = manor.silver
        else:
            current_amount = get_item_quantity(manor, cost_type)

        options.append(
            {
                "key": metal_key,
                "name": metal_name,
                "cost_type": cost_type,
                "cost_type_name": cost_type_name,
                "cost_amount": cost_amount,
                "category": category,
                "category_label": "药品" if category == "medicine" else "金属",
                "base_duration": config["base_duration"],
                "actual_duration": actual_duration,
                "can_afford": current_amount >= cost_amount,
                "current_amount": current_amount,
                "required_level": required_level,
                "required_type_label": required_type_label,
                "is_unlocked": is_unlocked,
                "max_quantity": max_quantity,
                "is_producing": is_producing,
            }
        )
    return options


def start_smelting_production(manor: Manor, metal_key: str, quantity: int = 1) -> SmeltingProduction:
    """
    开始冶炼坊制作。

    Args:
        manor: 庄园实例
        metal_key: 物品key
        quantity: 制作数量

    Returns:
        SmeltingProduction实例

    Raises:
        ProductionStartError: 参数错误、等级不足或已有制作进行中
    """
    if metal_key not in METAL_CONFIG:
        raise ProductionStartError("无效的制作类型")

    config = _normalize_smithy_runtime_config_entry(
        METAL_CONFIG[metal_key],
        contract_name=f"smithy runtime production config {metal_key}",
    )

    # 检查解锁条件（冶炼技或冶炼坊等级）
    from ..technology import get_player_technology_level

    smelting_level = get_player_technology_level(manor, "smelting")
    smithy_level = manor.get_building_level(BuildingKeys.SMITHY)
    is_unlocked, required_level, required_type_label = _get_unlock_requirement(config, smelting_level, smithy_level)
    if not is_unlocked:
        item_name_map_for_level = _get_item_name_map({metal_key})
        metal_name_for_level = item_name_map_for_level.get(metal_key, "未知物品")
        raise ProductionStartError(f"需要{required_type_label}{required_level}级才能制作{metal_name_for_level}")

    # 验证制作数量
    max_quantity = _get_max_quantity_for_config(manor, config)
    if quantity < 1:
        raise ProductionStartError("制作数量至少为1")
    if quantity > max_quantity:
        limit_label = _get_quantity_limit_label(config)
        raise ProductionStartError(f"{limit_label}限制，单次最多制作{max_quantity}个")

    # 获取消耗配置
    cost_type = config["cost_type"]
    cost_amount = config["cost_amount"]
    total_cost = cost_amount * quantity

    item_name_map = _get_item_name_map({metal_key, cost_type} - {"silver"})
    metal_name = item_name_map.get(metal_key, "未知物品")
    cost_name = "银两" if cost_type == "silver" else item_name_map.get(cost_type, "未知材料")

    with transaction.atomic():
        from gameplay.models import Manor as ManorModel

        from ...models import InventoryItem, ResourceEvent
        from ..inventory.core import consume_inventory_item_locked
        from ..resources import spend_resources_locked

        locked_manor = ManorModel.objects.select_for_update().get(pk=manor.pk)

        # 锁内再次检查，避免并发下绕过限制
        if has_active_smelting_production(locked_manor):
            raise ProductionStartError("已有物品正在制作中，同时只能制作一种物品")

        # 扣除材料
        if cost_type == "silver":
            try:
                spend_resources_locked(
                    locked_manor,
                    {"silver": total_cost},
                    note=f"制作{metal_name}×{quantity}",
                    reason=ResourceEvent.Reason.UPGRADE_COST,
                )
            except InsufficientResourceError as exc:
                raise InsufficientResourceError(
                    "silver",
                    total_cost,
                    int(locked_manor.silver),
                    message=f"{cost_name}不足，需要{total_cost}{cost_name}",
                ) from exc
        else:
            # 扣除物品（铜、锡等）
            item = (
                InventoryItem.objects.select_for_update()
                .select_related("template", "manor")
                .filter(
                    manor=locked_manor,
                    template__key=cost_type,
                    storage_location=InventoryItem.StorageLocation.WAREHOUSE,
                )
                .first()
            )
            if not item or item.quantity < total_cost:
                raise ProductionStartError(f"{cost_name}不足")
            consume_inventory_item_locked(item, total_cost)

        unit_duration = calculate_smelting_duration(config["base_duration"], manor)
        actual_duration = unit_duration * quantity

        # 创建制作记录
        production = SmeltingProduction.objects.create(
            manor=locked_manor,
            metal_key=metal_key,
            metal_name=metal_name,
            quantity=quantity,
            cost_type=cost_type,
            cost_amount=total_cost,
            base_duration=config["base_duration"],
            actual_duration=actual_duration,
            complete_at=timezone.now() + timedelta(seconds=actual_duration),
        )

        # 调度 Celery 任务
        _schedule_smelting_completion(production, actual_duration)

    return production


def cancel_smelting_production(manor: Manor, production_id: int) -> SmeltingProduction:
    """取消仍在进行的冶炼坊制作，已消耗的材料不返还。"""
    return cancel_active_production(
        production_model=SmeltingProduction,
        manor=manor,
        production_id=production_id,
        active_status=SmeltingProduction.Status.PRODUCING,
        cancelled_status=SmeltingProduction.Status.CANCELLED,
    )


def _schedule_smelting_completion(production: SmeltingProduction, eta_seconds: int) -> None:
    """
    调度制作完成任务。

    Args:
        production: SmeltingProduction实例
        eta_seconds: 预计完成时间（秒）
    """
    from django.db import transaction as db_transaction

    countdown = max(0, int(eta_seconds))

    try:
        from gameplay.tasks import complete_smelting_production
    except ImportError as exc:
        if not is_missing_target_import(exc, "gameplay.tasks"):
            raise
        logger.warning("Unable to import complete_smelting_production task; skip scheduling", exc_info=True)
        return

    def _dispatch_completion() -> None:
        dispatched = safe_apply_async(
            complete_smelting_production,
            args=[production.id],
            countdown=countdown,
            logger=logger,
            log_message="complete_smelting_production dispatch failed",
        )
        if not dispatched:
            logger.error(
                "complete_smelting_production dispatch returned False; production may remain pending",
                extra={
                    "task_name": "complete_smelting_production",
                    "production_id": getattr(production, "id", None),
                    "manor_id": getattr(production, "manor_id", None),
                },
            )

    db_transaction.on_commit(_dispatch_completion)


def finalize_smelting_production(production: SmeltingProduction, send_notification: bool = False) -> bool:
    """
    完成冶炼坊制作，将物品添加到玩家仓库。

    Args:
        production: SmeltingProduction实例
        send_notification: 是否发送通知

    Returns:
        是否成功完成
    """
    from ...models import Manor, Message

    if production.complete_at > timezone.now():
        return False

    completed_production = production
    with transaction.atomic():
        # 统一锁序 Manor -> production，确保容量检查和并发 worker 都可串行化；
        # 重新读取制作记录后只有一个 worker 能看到 PRODUCING 并发货；
        # 其余 worker 读到最新状态后直接返回，保证完成结算幂等。
        locked_manor = Manor.objects.select_for_update().get(pk=production.manor_id)
        locked_production = SmeltingProduction.objects.select_for_update().select_related("manor").get(pk=production.pk)
        if locked_production.status != SmeltingProduction.Status.PRODUCING:
            production.status = locked_production.status
            production.finished_at = locked_production.finished_at
            return False
        if locked_production.complete_at > timezone.now():
            return False

        # 添加物品到仓库（按数量添加）
        from ..inventory.core import add_item_to_inventory_locked

        add_item_to_inventory_locked(locked_manor, locked_production.metal_key, locked_production.quantity)

        # 更新制作状态
        finished_at = timezone.now()
        locked_production.status = SmeltingProduction.Status.COMPLETED
        locked_production.finished_at = finished_at
        locked_production.save(update_fields=["status", "finished_at"])
        production.status = locked_production.status
        production.finished_at = finished_at
        completed_production = locked_production

    if send_notification:
        from ..utils.messages import create_message

        quantity_text = f"×{completed_production.quantity}" if completed_production.quantity > 1 else ""
        try:
            create_message(
                manor=completed_production.manor,
                kind=Message.Kind.SYSTEM,
                title=f"{completed_production.metal_name}{quantity_text}制作完成",
                body=f"您的{completed_production.metal_name}{quantity_text}已制作完成，请到仓库查收。",
            )
        except SMITHY_MESSAGE_BEST_EFFORT_EXCEPTIONS as exc:
            logger.warning(
                "smelting production message creation failed: production_id=%s manor_id=%s error=%s",
                completed_production.id,
                completed_production.manor_id,
                exc,
                exc_info=True,
            )
            return True

        try:
            notify_user(
                completed_production.manor.user_id,
                {
                    "kind": "system",
                    "title": f"{completed_production.metal_name}{quantity_text}制作完成",
                    "metal_key": getattr(completed_production, "metal_key", None),
                    "quantity": getattr(completed_production, "quantity", None),
                },
                log_context="smelting production notification",
            )
        except NOTIFICATION_INFRASTRUCTURE_EXCEPTIONS as exc:
            logger.warning(
                "smelting production notification failed: production_id=%s manor_id=%s error=%s",
                completed_production.id,
                completed_production.manor_id,
                exc,
                exc_info=True,
            )

    return True


def refresh_smelting_productions(manor: Manor) -> int:
    """
    刷新冶炼坊制作状态，完成所有到期制作。

    Args:
        manor: 庄园实例

    Returns:
        完成的制作数量
    """
    completed = 0
    producing = manor.smelting_productions.filter(
        status=SmeltingProduction.Status.PRODUCING, complete_at__lte=timezone.now()
    )

    for production in producing:
        if finalize_smelting_production(production, send_notification=True):
            completed += 1

    return completed


def get_active_smelting_productions(manor: Manor) -> List[SmeltingProduction]:
    """
    获取正在进行的制作列表。

    Args:
        manor: 庄园实例

    Returns:
        制作列表
    """
    return list(manor.smelting_productions.filter(status=SmeltingProduction.Status.PRODUCING).order_by("complete_at"))
