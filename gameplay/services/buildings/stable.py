"""
马房服务模块

提供马匹生产相关功能。
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
from ...models import HorseProduction, Manor

logger = logging.getLogger(__name__)
STABLE_MESSAGE_BEST_EFFORT_EXCEPTIONS: InfrastructureExceptions = combine_infrastructure_exceptions(
    MessageError,
    infrastructure_exceptions=DATABASE_INFRASTRUCTURE_EXCEPTIONS,
)

# 马匹配置
DEFAULT_HORSE_CONFIG: Dict[str, Dict[str, Any]] = {}


def _normalize_stable_item_key(raw_key: Any) -> str:
    if not isinstance(raw_key, str) or not raw_key.strip():
        raise AssertionError(f"invalid stable production item key: {raw_key!r}")
    return raw_key.strip()


def _normalize_stable_positive_int(raw_value: Any, *, field_name: str) -> int:
    if raw_value is None or isinstance(raw_value, bool):
        raise AssertionError(f"invalid stable production {field_name}: {raw_value!r}")
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid stable production {field_name}: {raw_value!r}") from exc
    if value <= 0:
        raise AssertionError(f"invalid stable production {field_name}: {raw_value!r}")
    return value


def _normalize_stable_production_config(raw: Any) -> Dict[str, Dict[str, Any]]:
    root = raw
    if isinstance(raw, dict) and "production" in raw:
        root = raw.get("production")
    if not isinstance(root, dict):
        raise AssertionError(f"invalid stable production config root: {raw!r}")

    config: Dict[str, Dict[str, Any]] = {}
    for raw_key, raw_item in root.items():
        item_key = _normalize_stable_item_key(raw_key)
        if not isinstance(raw_item, dict):
            raise AssertionError(f"invalid stable production item payload: {raw_item!r}")
        config[item_key] = {
            "grain_cost": _normalize_stable_positive_int(raw_item.get("grain_cost"), field_name="grain_cost"),
            "base_duration": _normalize_stable_positive_int(raw_item.get("base_duration"), field_name="base_duration"),
            "required_horsemanship": _normalize_stable_positive_int(
                raw_item.get("required_horsemanship"),
                field_name="required_horsemanship",
            ),
        }
    return config


@lru_cache(maxsize=1)
def load_stable_production_config() -> Dict[str, Dict[str, Any]]:
    path = settings.BASE_DIR / "data" / "stable_production.yaml"
    raw = load_yaml_data(
        path,
        logger=logger,
        context="horse_config config",
        default={"production": DEFAULT_HORSE_CONFIG},
    )
    return _normalize_stable_production_config(raw)


def clear_stable_production_cache() -> None:
    global HORSE_CONFIG
    load_stable_production_config.cache_clear()
    HORSE_CONFIG = load_stable_production_config()


HORSE_CONFIG: Dict[str, Dict[str, Any]] = load_stable_production_config()


def _normalize_stable_runtime_config_entry(raw_config: object, *, contract_name: str) -> Dict[str, int]:
    if not isinstance(raw_config, dict):
        raise AssertionError(f"invalid {contract_name}: {raw_config!r}")
    return {
        "grain_cost": _normalize_stable_positive_int(raw_config.get("grain_cost"), field_name="grain_cost"),
        "base_duration": _normalize_stable_positive_int(raw_config.get("base_duration"), field_name="base_duration"),
        "required_horsemanship": _normalize_stable_positive_int(
            raw_config.get("required_horsemanship"),
            field_name="required_horsemanship",
        ),
    }


def _get_item_name_map(keys: set[str]) -> Dict[str, str]:
    if not keys:
        return {}
    from ...models import ItemTemplate

    return {tpl.key: tpl.name for tpl in ItemTemplate.objects.filter(key__in=keys).only("key", "name")}


def get_stable_speed_bonus(manor: Manor) -> float:
    """
    获取马房速度加成。

    10级满级提升50%，每级约5%。

    Args:
        manor: 庄园实例

    Returns:
        速度加成倍率（如0.5表示减少50%时间）
    """
    level = manor.get_building_level(BuildingKeys.STABLE)
    # 10级满级提升50%
    return level * 0.05


def get_max_production_quantity(manor: Manor) -> int:
    """
    获取单次生产马匹的最大数量。

    驯马术每级增加100匹上限，满级5级=500匹。

    Args:
        manor: 庄园实例

    Returns:
        最大生产数量
    """
    from ..technology import get_player_technology_level

    horsemanship_level = get_player_technology_level(manor, "horsemanship")
    # 每级100匹，最少1匹（0级时也能生产1匹）
    return max(1, horsemanship_level * 100)


def calculate_production_duration(base_duration: int, manor: Manor) -> int:
    """
    计算实际生产时间。

    Args:
        base_duration: 基础时间（秒）
        manor: 庄园实例

    Returns:
        实际生产时间（秒）
    """
    bonus = get_stable_speed_bonus(manor)
    # 加成越高，时间越短
    duration = max(1, int(base_duration * (1 - bonus)))
    return scale_duration(duration, minimum=1)


def has_active_production(manor: Manor) -> bool:
    """
    检查是否有正在进行的马匹生产。

    Args:
        manor: 庄园实例

    Returns:
        是否有生产中的马匹
    """
    return manor.horse_productions.filter(status=HorseProduction.Status.PRODUCING).exists()


def get_horse_options(manor: Manor) -> List[Dict[str, Any]]:
    """
    获取马匹生产选项列表。

    Args:
        manor: 庄园实例

    Returns:
        马匹选项列表
    """
    from ..technology import get_player_technology_level

    horsemanship_level = get_player_technology_level(manor, "horsemanship")
    max_quantity = get_max_production_quantity(manor)
    is_producing = has_active_production(manor)
    horse_name_map = _get_item_name_map(set(HORSE_CONFIG.keys()))

    options = []
    for horse_key, raw_config in HORSE_CONFIG.items():
        config = _normalize_stable_runtime_config_entry(
            raw_config,
            contract_name=f"stable runtime production config {horse_key}",
        )
        actual_duration = calculate_production_duration(config["base_duration"], manor)
        required_level = config["required_horsemanship"]
        is_unlocked = horsemanship_level >= required_level
        horse_name = horse_name_map.get(horse_key, horse_key)
        options.append(
            {
                "key": horse_key,
                "name": horse_name,
                "grain_cost": config["grain_cost"],
                "base_duration": config["base_duration"],
                "actual_duration": actual_duration,
                "can_afford": manor.grain >= config["grain_cost"],
                "required_horsemanship": required_level,
                "is_unlocked": is_unlocked,
                "max_quantity": max_quantity,
                "is_producing": is_producing,
            }
        )
    return options


def start_horse_production(manor: Manor, horse_key: str, quantity: int = 1) -> HorseProduction:
    """
    开始生产马匹。

    Args:
        manor: 庄园实例
        horse_key: 马匹key
        quantity: 生产数量

    Returns:
        HorseProduction实例

    Raises:
        ProductionStartError: 参数错误、科技等级不足或已有生产进行中
    """
    if horse_key not in HORSE_CONFIG:
        raise ProductionStartError("无效的马匹类型")

    config = _normalize_stable_runtime_config_entry(
        HORSE_CONFIG[horse_key],
        contract_name=f"stable runtime production config {horse_key}",
    )
    required_level = config["required_horsemanship"]
    horse_name_map = _get_item_name_map({horse_key})
    horse_name = horse_name_map.get(horse_key, horse_key)

    # 检查驯马术等级
    from ..technology import get_player_technology_level

    horsemanship_level = get_player_technology_level(manor, "horsemanship")
    if horsemanship_level < required_level:
        raise ProductionStartError(f"需要驯马术{required_level}级才能生产{horse_name}")

    # 验证生产数量
    max_quantity = get_max_production_quantity(manor)
    if quantity < 1:
        raise ProductionStartError("生产数量至少为1")
    if quantity > max_quantity:
        raise ProductionStartError(f"驯马术等级限制，单次最多生产{max_quantity}匹")

    # 计算总消耗
    total_grain_cost = config["grain_cost"] * quantity

    with transaction.atomic():
        from gameplay.models import Manor as ManorModel

        from ...models import ResourceEvent
        from ..resources import spend_resources_locked

        locked_manor = ManorModel.objects.select_for_update().get(pk=manor.pk)

        # 锁内再次检查，避免并发下绕过限制
        if has_active_production(locked_manor):
            raise ProductionStartError("已有马匹正在生产中，同时只能生产一种马匹")
        try:
            spend_resources_locked(
                locked_manor,
                {"grain": total_grain_cost},
                note=f"生产{horse_name}x{quantity}",
                reason=ResourceEvent.Reason.UPGRADE_COST,
            )
        except InsufficientResourceError as exc:
            raise InsufficientResourceError(
                "grain",
                total_grain_cost,
                int(locked_manor.grain),
                message=f"粮食不足，需要{total_grain_cost}点粮食",
            ) from exc

        unit_duration = calculate_production_duration(config["base_duration"], manor)
        actual_duration = unit_duration * quantity

        # 创建生产记录
        production = HorseProduction.objects.create(
            manor=locked_manor,
            horse_key=horse_key,
            horse_name=horse_name,
            quantity=quantity,
            grain_cost=total_grain_cost,
            base_duration=config["base_duration"],
            actual_duration=actual_duration,
            complete_at=timezone.now() + timedelta(seconds=actual_duration),
        )

        # 调度 Celery 任务
        _schedule_production_completion(production, actual_duration)

    return production


def _schedule_production_completion(production: HorseProduction, eta_seconds: int) -> None:
    """
    调度生产完成任务。

    Args:
        production: HorseProduction实例
        eta_seconds: 预计完成时间（秒）
    """
    from django.db import transaction as db_transaction

    countdown = max(0, int(eta_seconds))

    try:
        from gameplay.tasks import complete_horse_production
    except ImportError as exc:
        if not is_missing_target_import(exc, "gameplay.tasks"):
            raise
        logger.warning("Unable to import complete_horse_production task; skip scheduling", exc_info=True)
        return

    def _dispatch_completion() -> None:
        dispatched = safe_apply_async(
            complete_horse_production,
            args=[production.id],
            countdown=countdown,
            logger=logger,
            log_message="complete_horse_production dispatch failed",
        )
        if not dispatched:
            logger.error(
                "complete_horse_production dispatch returned False; production may remain pending",
                extra={
                    "task_name": "complete_horse_production",
                    "production_id": getattr(production, "id", None),
                    "manor_id": getattr(production, "manor_id", None),
                },
            )

    db_transaction.on_commit(_dispatch_completion)


def finalize_horse_production(production: HorseProduction, send_notification: bool = False) -> bool:
    """
    完成马匹生产，将马匹添加到玩家仓库。

    Args:
        production: HorseProduction实例
        send_notification: 是否发送通知

    Returns:
        是否成功完成
    """
    from ...models import Message
    from ..utils.notifications import notify_user

    if production.complete_at > timezone.now():
        return False

    completed_production = production
    with transaction.atomic():
        # 先在事务内锁定并重新读取生产记录，确保并发 worker 只有一个能看到 PRODUCING 并发货；
        # 其余 worker 读到最新状态后直接返回，保证完成结算幂等。
        locked_production = HorseProduction.objects.select_for_update().select_related("manor").get(pk=production.pk)
        if locked_production.status != HorseProduction.Status.PRODUCING:
            production.status = locked_production.status
            production.finished_at = locked_production.finished_at
            return False
        if locked_production.complete_at > timezone.now():
            return False

        # 添加马匹到仓库（按数量添加）
        from ..inventory.core import add_item_to_inventory_locked

        add_item_to_inventory_locked(locked_production.manor, locked_production.horse_key, locked_production.quantity)

        # 更新生产状态
        finished_at = timezone.now()
        locked_production.status = HorseProduction.Status.COMPLETED
        locked_production.finished_at = finished_at
        locked_production.save(update_fields=["status", "finished_at"])
        production.status = locked_production.status
        production.finished_at = finished_at
        completed_production = locked_production

    if send_notification:
        from ..utils.messages import create_message

        quantity_text = f"x{completed_production.quantity}" if completed_production.quantity > 1 else ""
        try:
            create_message(
                manor=completed_production.manor,
                kind=Message.Kind.SYSTEM,
                title=f"{completed_production.horse_name}{quantity_text}生产完成",
                body=f"您的{completed_production.horse_name}{quantity_text}已生产完成，请到仓库查收。",
            )
        except STABLE_MESSAGE_BEST_EFFORT_EXCEPTIONS as exc:
            logger.warning(
                "horse production message creation failed: production_id=%s manor_id=%s error=%s",
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
                    "title": f"{completed_production.horse_name}{quantity_text}生产完成",
                    "horse_key": getattr(completed_production, "horse_key", None),
                    "quantity": getattr(completed_production, "quantity", None),
                },
                log_context="horse production notification",
            )
        except NOTIFICATION_INFRASTRUCTURE_EXCEPTIONS as exc:
            logger.warning(
                "horse production notification failed: production_id=%s manor_id=%s error=%s",
                completed_production.id,
                completed_production.manor_id,
                exc,
                exc_info=True,
            )

    return True


def refresh_horse_productions(manor: Manor) -> int:
    """
    刷新马匹生产状态，完成所有到期的生产。

    Args:
        manor: 庄园实例

    Returns:
        完成的生产数量
    """
    completed = 0
    producing = manor.horse_productions.filter(status=HorseProduction.Status.PRODUCING, complete_at__lte=timezone.now())

    for production in producing:
        if finalize_horse_production(production, send_notification=True):
            completed += 1

    return completed


def get_active_productions(manor: Manor) -> List[HorseProduction]:
    """
    获取正在进行的生产列表。

    Args:
        manor: 庄园实例

    Returns:
        生产列表
    """
    return list(manor.horse_productions.filter(status=HorseProduction.Status.PRODUCING).order_by("complete_at"))
