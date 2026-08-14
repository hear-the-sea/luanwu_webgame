"""
畜牧场服务模块

提供家畜养殖相关功能。
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
from ...models import LivestockProduction, Manor
from ..utils.notifications import notify_user
from .production_cancel import cancel_active_production

logger = logging.getLogger(__name__)
RANCH_MESSAGE_BEST_EFFORT_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    MessageError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)

# 家畜配置
# 养殖术等级需求：1级鸡，3级鸭，5级鹅，7级猪，9级牛
DEFAULT_LIVESTOCK_CONFIG: Dict[str, Dict[str, Any]] = {}


def _normalize_ranch_item_key(raw_key: Any) -> str:
    if not isinstance(raw_key, str) or not raw_key.strip():
        raise AssertionError(f"invalid ranch production item key: {raw_key!r}")
    return raw_key.strip()


def _normalize_ranch_positive_int(raw_value: Any, *, field_name: str) -> int:
    if raw_value is None or isinstance(raw_value, bool):
        raise AssertionError(f"invalid ranch production {field_name}: {raw_value!r}")
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid ranch production {field_name}: {raw_value!r}") from exc
    if value <= 0:
        raise AssertionError(f"invalid ranch production {field_name}: {raw_value!r}")
    return value


def _normalize_ranch_production_config(raw: Any) -> Dict[str, Dict[str, Any]]:
    root = raw
    if isinstance(raw, dict) and "production" in raw:
        root = raw.get("production")
    if not isinstance(root, dict):
        raise AssertionError(f"invalid ranch production config root: {raw!r}")

    config: Dict[str, Dict[str, Any]] = {}
    for raw_key, raw_item in root.items():
        item_key = _normalize_ranch_item_key(raw_key)
        if not isinstance(raw_item, dict):
            raise AssertionError(f"invalid ranch production item payload: {raw_item!r}")
        config[item_key] = {
            "grain_cost": _normalize_ranch_positive_int(raw_item.get("grain_cost"), field_name="grain_cost"),
            "base_duration": _normalize_ranch_positive_int(raw_item.get("base_duration"), field_name="base_duration"),
            "required_animal_husbandry": _normalize_ranch_positive_int(
                raw_item.get("required_animal_husbandry"),
                field_name="required_animal_husbandry",
            ),
        }
    return config


@lru_cache(maxsize=1)
def load_ranch_production_config() -> Dict[str, Dict[str, Any]]:
    path = settings.BASE_DIR / "data" / "ranch_production.yaml"
    raw = load_yaml_data(
        path,
        logger=logger,
        context="livestock_config config",
        default={"production": DEFAULT_LIVESTOCK_CONFIG},
    )
    return _normalize_ranch_production_config(raw)


def clear_ranch_production_cache() -> None:
    global LIVESTOCK_CONFIG
    load_ranch_production_config.cache_clear()
    LIVESTOCK_CONFIG = load_ranch_production_config()


LIVESTOCK_CONFIG: Dict[str, Dict[str, Any]] = load_ranch_production_config()


def _normalize_ranch_runtime_config_entry(raw_config: object, *, contract_name: str) -> Dict[str, int]:
    if not isinstance(raw_config, dict):
        raise AssertionError(f"invalid {contract_name}: {raw_config!r}")
    return {
        "grain_cost": _normalize_ranch_positive_int(raw_config.get("grain_cost"), field_name="grain_cost"),
        "base_duration": _normalize_ranch_positive_int(raw_config.get("base_duration"), field_name="base_duration"),
        "required_animal_husbandry": _normalize_ranch_positive_int(
            raw_config.get("required_animal_husbandry"),
            field_name="required_animal_husbandry",
        ),
    }


def _get_item_name_map(keys: set[str]) -> Dict[str, str]:
    if not keys:
        return {}
    from ...models import ItemTemplate

    return {tpl.key: tpl.name for tpl in ItemTemplate.objects.filter(key__in=keys).only("key", "name")}


def get_ranch_speed_bonus(manor: Manor) -> float:
    """
    获取畜牧场速度加成。

    10级满级提升50%，每级约5%。

    Args:
        manor: 庄园实例

    Returns:
        速度加成倍率（如0.5表示减少50%时间）
    """
    level = manor.get_building_level(BuildingKeys.RANCH)
    return level * 0.05


def get_max_livestock_quantity(manor: Manor) -> int:
    """
    获取单次养殖家畜的最大数量。

    养殖术每级增加50只上限，满级10级=500只。

    Args:
        manor: 庄园实例

    Returns:
        最大养殖数量
    """
    from ..technology import get_player_technology_level

    animal_husbandry_level = get_player_technology_level(manor, "animal_husbandry")
    # 每级50只，最少1只（0级时也能养殖1只）
    return max(1, animal_husbandry_level * 50)


def calculate_livestock_duration(base_duration: int, manor: Manor) -> int:
    """
    计算实际养殖时间。

    Args:
        base_duration: 基础时间（秒）
        manor: 庄园实例

    Returns:
        实际养殖时间（秒）
    """
    bonus = get_ranch_speed_bonus(manor)
    # 加成越高，时间越短
    duration = max(1, int(base_duration * (1 - bonus)))
    return scale_duration(duration, minimum=1)


def has_active_livestock_production(manor: Manor) -> bool:
    """
    检查是否有正在进行的家畜养殖。

    Args:
        manor: 庄园实例

    Returns:
        是否有养殖中的家畜
    """
    return manor.livestock_productions.filter(status=LivestockProduction.Status.PRODUCING).exists()


def get_livestock_options(manor: Manor) -> List[Dict[str, Any]]:
    """
    获取家畜养殖选项列表。

    Args:
        manor: 庄园实例

    Returns:
        家畜选项列表
    """
    from ..inventory.core import get_warehouse_grain_quantity
    from ..technology import get_player_technology_level

    animal_husbandry_level = get_player_technology_level(manor, "animal_husbandry")
    max_quantity = get_max_livestock_quantity(manor)
    is_producing = has_active_livestock_production(manor)
    warehouse_grain_quantity = get_warehouse_grain_quantity(manor)
    livestock_name_map = _get_item_name_map(set(LIVESTOCK_CONFIG.keys()))

    options = []
    for livestock_key, raw_config in LIVESTOCK_CONFIG.items():
        config = _normalize_ranch_runtime_config_entry(
            raw_config,
            contract_name=f"ranch runtime production config {livestock_key}",
        )
        actual_duration = calculate_livestock_duration(config["base_duration"], manor)
        required_level = config["required_animal_husbandry"]
        is_unlocked = animal_husbandry_level >= required_level
        livestock_name = livestock_name_map.get(livestock_key, "未知家畜")
        options.append(
            {
                "key": livestock_key,
                "name": livestock_name,
                "grain_cost": config["grain_cost"],
                "base_duration": config["base_duration"],
                "actual_duration": actual_duration,
                "can_afford": warehouse_grain_quantity >= config["grain_cost"],
                "required_animal_husbandry": required_level,
                "is_unlocked": is_unlocked,
                "max_quantity": max_quantity,
                "is_producing": is_producing,
            }
        )
    return options


def start_livestock_production(manor: Manor, livestock_key: str, quantity: int = 1) -> LivestockProduction:
    """
    开始养殖家畜。

    Args:
        manor: 庄园实例
        livestock_key: 家畜key
        quantity: 养殖数量

    Returns:
        LivestockProduction实例

    Raises:
        ProductionStartError: 参数错误、科技等级不足或已有养殖进行中
    """
    if livestock_key not in LIVESTOCK_CONFIG:
        raise ProductionStartError("无效的家畜类型")

    config = _normalize_ranch_runtime_config_entry(
        LIVESTOCK_CONFIG[livestock_key],
        contract_name=f"ranch runtime production config {livestock_key}",
    )
    required_level = config["required_animal_husbandry"]
    livestock_name_map = _get_item_name_map({livestock_key})
    livestock_name = livestock_name_map.get(livestock_key, "未知家畜")

    # 检查养殖术等级
    from ..technology import get_player_technology_level

    animal_husbandry_level = get_player_technology_level(manor, "animal_husbandry")
    if animal_husbandry_level < required_level:
        raise ProductionStartError(f"需要养殖术{required_level}级才能养殖{livestock_name}")

    # 验证养殖数量
    max_quantity = get_max_livestock_quantity(manor)
    if quantity < 1:
        raise ProductionStartError("养殖数量至少为1")
    if quantity > max_quantity:
        raise ProductionStartError(f"养殖术等级限制，单次最多养殖{max_quantity}只")

    # 计算总消耗
    total_grain_cost = config["grain_cost"] * quantity

    with transaction.atomic():
        from gameplay.models import Manor as ManorModel

        from ...models import ResourceEvent
        from ..resources import spend_resources_locked

        locked_manor = ManorModel.objects.select_for_update().get(pk=manor.pk)

        if has_active_livestock_production(locked_manor):
            raise ProductionStartError("已有家畜正在养殖中，同时只能养殖一种家畜")
        try:
            spend_resources_locked(
                locked_manor,
                {"grain": total_grain_cost},
                note=f"养殖{livestock_name}×{quantity}",
                reason=ResourceEvent.Reason.UPGRADE_COST,
            )
        except InsufficientResourceError as exc:
            raise InsufficientResourceError(
                "grain",
                total_grain_cost,
                int(locked_manor.grain),
                message=f"粮食不足，需要{total_grain_cost}点粮食",
            ) from exc

        unit_duration = calculate_livestock_duration(config["base_duration"], manor)
        actual_duration = unit_duration * quantity

        # 创建养殖记录
        production = LivestockProduction.objects.create(
            manor=locked_manor,
            livestock_key=livestock_key,
            livestock_name=livestock_name,
            quantity=quantity,
            grain_cost=total_grain_cost,
            base_duration=config["base_duration"],
            actual_duration=actual_duration,
            complete_at=timezone.now() + timedelta(seconds=actual_duration),
        )

        # 调度 Celery 任务
        _schedule_livestock_completion(production, actual_duration)

    return production


def cancel_livestock_production(manor: Manor, production_id: int) -> LivestockProduction:
    """取消仍在进行的家畜养殖，已消耗的粮食不返还。"""
    return cancel_active_production(
        production_model=LivestockProduction,
        manor=manor,
        production_id=production_id,
        active_status=LivestockProduction.Status.PRODUCING,
        cancelled_status=LivestockProduction.Status.CANCELLED,
    )


def _schedule_livestock_completion(production: LivestockProduction, eta_seconds: int) -> None:
    """
    调度养殖完成任务。

    Args:
        production: LivestockProduction实例
        eta_seconds: 预计完成时间（秒）
    """
    from django.db import transaction as db_transaction

    countdown = max(0, int(eta_seconds))

    try:
        from gameplay.tasks import complete_livestock_production
    except ImportError as exc:
        if not is_missing_target_import(exc, "gameplay.tasks"):
            raise
        logger.warning("Unable to import complete_livestock_production task; skip scheduling", exc_info=True)
        return

    def _dispatch_completion() -> None:
        dispatched = safe_apply_async(
            complete_livestock_production,
            args=[production.id],
            countdown=countdown,
            logger=logger,
            log_message="complete_livestock_production dispatch failed",
        )
        if not dispatched:
            logger.error(
                "complete_livestock_production dispatch returned False; production may remain pending",
                extra={
                    "task_name": "complete_livestock_production",
                    "production_id": getattr(production, "id", None),
                    "manor_id": getattr(production, "manor_id", None),
                },
            )

    db_transaction.on_commit(_dispatch_completion)


def finalize_livestock_production(production: LivestockProduction, send_notification: bool = False) -> bool:
    """
    完成家畜养殖，将家畜添加到玩家仓库。

    Args:
        production: LivestockProduction实例
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
        # 重新读取养殖记录后只有一个 worker 能看到 PRODUCING 并发货；
        # 其余 worker 读到最新状态后直接返回，保证完成结算幂等。
        locked_manor = Manor.objects.select_for_update().get(pk=production.manor_id)
        locked_production = (
            LivestockProduction.objects.select_for_update().select_related("manor").get(pk=production.pk)
        )
        if locked_production.status != LivestockProduction.Status.PRODUCING:
            production.status = locked_production.status
            production.finished_at = locked_production.finished_at
            return False
        if locked_production.complete_at > timezone.now():
            return False

        # 添加家畜到仓库（按数量添加）
        from ..inventory.core import add_item_to_inventory_locked

        add_item_to_inventory_locked(
            locked_manor,
            locked_production.livestock_key,
            locked_production.quantity,
        )

        # 更新养殖状态
        finished_at = timezone.now()
        locked_production.status = LivestockProduction.Status.COMPLETED
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
                title=f"{completed_production.livestock_name}{quantity_text}养殖完成",
                body=f"您的{completed_production.livestock_name}{quantity_text}已养殖完成，请到仓库查收。",
            )
        except RANCH_MESSAGE_BEST_EFFORT_EXCEPTIONS as exc:
            logger.warning(
                "livestock production message creation failed: production_id=%s manor_id=%s error=%s",
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
                    "title": f"{completed_production.livestock_name}{quantity_text}养殖完成",
                    "livestock_key": getattr(completed_production, "livestock_key", None),
                    "quantity": getattr(completed_production, "quantity", None),
                },
                log_context="livestock production notification",
            )
        except NOTIFICATION_INFRASTRUCTURE_EXCEPTIONS as exc:
            logger.warning(
                "livestock production notification failed: production_id=%s manor_id=%s error=%s",
                completed_production.id,
                completed_production.manor_id,
                exc,
                exc_info=True,
            )

    return True


def refresh_livestock_productions(manor: Manor) -> int:
    """
    刷新家畜养殖状态，完成所有到期的养殖。

    Args:
        manor: 庄园实例

    Returns:
        完成的养殖数量
    """
    completed = 0
    producing = manor.livestock_productions.filter(
        status=LivestockProduction.Status.PRODUCING, complete_at__lte=timezone.now()
    )

    for production in producing:
        if finalize_livestock_production(production, send_notification=True):
            completed += 1

    return completed


def get_active_livestock_productions(manor: Manor) -> List[LivestockProduction]:
    """
    获取正在进行的养殖列表。

    Args:
        manor: 庄园实例

    Returns:
        养殖列表
    """
    return list(manor.livestock_productions.filter(status=LivestockProduction.Status.PRODUCING).order_by("complete_at"))
