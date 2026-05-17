"""
护院募兵服务模块

提供护院招募相关功能：
- 加载兵种募兵配置
- 检查募兵条件（科技等级、装备、家丁）
- 开始募兵（消耗装备和家丁）
- 完成募兵（生成护院）
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from core.exceptions import TroopRecruitmentAlreadyInProgressError, TroopRecruitmentError
from core.utils import safe_non_negative_int, safe_positive_int
from core.utils.time_scale import scale_duration

from ...constants import BuildingKeys
from ...models import Manor, TroopRecruitment
from ..inventory.core import get_item_quantity
from ..technology import get_player_technology_level, get_technology_template
from .lifecycle import finalize_troop_recruitment
from .lifecycle import schedule_recruitment_completion as _schedule_recruitment_completion
from .queries import get_active_recruitments, get_player_troops, refresh_troop_recruitments
from .templates import clear_troop_cache, get_recruit_config, get_troop_template, load_troop_templates

logger = logging.getLogger(__name__)
TROOP_RECRUITMENT_DEFAULT_MAX_QUANTITY = 10
__all__ = [
    "calculate_recruitment_duration",
    "check_recruitment_requirements",
    "clear_troop_cache",
    "finalize_troop_recruitment",
    "get_active_recruitments",
    "get_player_troops",
    "get_recruit_config",
    "get_recruitment_options",
    "get_troop_template",
    "has_active_recruitment",
    "load_troop_templates",
    "refresh_troop_recruitments",
    "start_troop_recruitment",
]


def calculate_recruitment_duration(base_duration: int, manor: Manor) -> int:
    """
    计算实际募兵时间。

    应用两种加成：
    1. 练功场加成：速度倍率（10级满级提升30%，每级约3.33%）
    2. 祠堂加成：速度倍率（5级满级提升120%，每级30%）

    计算公式：实际时间 = 基础时间 / (练功场倍率 × 祠堂倍率)

    Args:
        base_duration: 基础时间（秒）
        manor: 庄园实例

    Returns:
        实际募兵时间（秒）
    """
    # 练功场加成（速度倍率）
    training_multiplier = manor.guard_training_speed_multiplier

    # 祠堂加成（速度倍率）
    citang_multiplier = manor.citang_recruitment_speed_multiplier

    # 综合速度倍率
    total_multiplier = training_multiplier * citang_multiplier

    duration = max(1, int(base_duration / total_multiplier))
    return scale_duration(duration, minimum=1)


def has_active_recruitment(manor: Manor) -> bool:
    """
    检查是否有正在进行的募兵。

    Args:
        manor: 庄园实例

    Returns:
        是否有募兵中的任务
    """
    return manor.troop_recruitments.filter(status=TroopRecruitment.Status.RECRUITING).exists()


def get_max_recruit_quantity(
    manor: Manor,
    troop_key: str,
    recruit_config: Optional[Dict[str, Any]] = None,
    tech_level: Optional[int] = None,
) -> int:
    """
    获取单次最大募兵数量。

    规则：
    - 有造兵术科技时：按 `tech_level * effect_per_level` 计算；
    - 无科技要求（如探子）或配置缺失时：使用默认上限。
    """
    config = recruit_config if isinstance(recruit_config, dict) else get_recruit_config(troop_key)
    if not config:
        return TROOP_RECRUITMENT_DEFAULT_MAX_QUANTITY

    tech_key = str(config.get("tech_key") or "").strip()
    if not tech_key:
        return TROOP_RECRUITMENT_DEFAULT_MAX_QUANTITY

    resolved_tech_level = (
        safe_non_negative_int(tech_level)
        if tech_level is not None
        else max(0, get_player_technology_level(manor, tech_key))
    )
    if resolved_tech_level <= 0:
        return TROOP_RECRUITMENT_DEFAULT_MAX_QUANTITY

    tech_template = get_technology_template(tech_key) or {}
    effect_per_level = safe_positive_int(
        tech_template.get("effect_per_level"),
        TROOP_RECRUITMENT_DEFAULT_MAX_QUANTITY,
    )
    return max(1, resolved_tech_level * effect_per_level)


def check_recruitment_requirements(
    manor: Manor,
    troop_key: str,
    quantity: int = 1,
) -> Dict[str, Any]:
    """
    检查募兵条件是否满足。

    Args:
        manor: 庄园实例
        troop_key: 兵种标识
        quantity: 募兵数量

    Returns:
        {
            "can_recruit": bool,
            "errors": list[str],
            "tech_satisfied": bool,
            "equipment_satisfied": bool,
            "retainer_satisfied": bool,
            "equipment_status": dict,  # {item_key: {"required": int, "have": int, "satisfied": bool}}
        }
    """
    result: Dict[str, Any] = {
        "can_recruit": False,
        "errors": [],
        "tech_satisfied": False,
        "equipment_satisfied": False,
        "retainer_satisfied": False,
        "equipment_status": {},
    }

    troop = get_troop_template(troop_key)
    if not troop:
        result["errors"].append("无效的兵种类型")
        return result

    recruit_config = troop.get("recruit")
    if not recruit_config:
        result["errors"].append("该兵种不可募兵")
        return result

    # 检查科技等级
    tech_key = recruit_config.get("tech_key")
    tech_level_required = recruit_config.get("tech_level", 0)

    if tech_key:
        player_tech_level = get_player_technology_level(manor, tech_key)
        if player_tech_level >= tech_level_required:
            result["tech_satisfied"] = True
        else:
            result["errors"].append(f"需要{_get_tech_name(tech_key)}{tech_level_required}级")
    else:
        result["tech_satisfied"] = True

    # 检查装备 - 优化：批量预加载 ItemTemplate，避免 N+1 查询
    equipment_list = recruit_config.get("equipment", [])
    all_equipment_satisfied = True

    # 批量加载所有需要的 ItemTemplate
    from ...models import ItemTemplate

    item_templates = {t.key: t for t in ItemTemplate.objects.filter(key__in=equipment_list)}

    for item_key in equipment_list:
        required = quantity
        have = get_item_quantity(manor, item_key)
        satisfied = have >= required

        result["equipment_status"][item_key] = {
            "required": required,
            "have": have,
            "satisfied": satisfied,
        }

        if not satisfied:
            all_equipment_satisfied = False
            item = item_templates.get(item_key)
            if item:
                result["errors"].append(f"{item.name}不足（需要{required}，拥有{have}）")
            else:
                result["errors"].append(f"装备{item_key}不足")

    result["equipment_satisfied"] = all_equipment_satisfied

    # 检查家丁
    retainer_cost = recruit_config.get("retainer_cost", 1) * quantity
    if manor.retainer_count >= retainer_cost:
        result["retainer_satisfied"] = True
    else:
        result["errors"].append(f"家丁不足（需要{retainer_cost}，拥有{manor.retainer_count}）")

    # 综合判断
    result["can_recruit"] = result["tech_satisfied"] and result["equipment_satisfied"] and result["retainer_satisfied"]

    return result


def _get_tech_name(tech_key: str) -> str:
    """获取科技名称。"""
    from ..technology import get_technology_template

    template = get_technology_template(tech_key)
    return template["name"] if template else tech_key


def get_recruitment_options(manor: Manor) -> List[Dict[str, Any]]:
    """
    获取募兵选项列表。

    Args:
        manor: 庄园实例

    Returns:
        募兵选项列表

    优化说明：
    - 批量预加载所有 ItemTemplate，避免 N+1 查询
    - 批量预加载玩家科技等级，减少重复查询
    - 批量预加载玩家物品数量，减少重复查询
    """
    from ...models import ItemTemplate

    data = load_troop_templates()
    troops = data.get("troops", [])
    is_recruiting = has_active_recruitment(manor)

    # 收集所有需要的 item_key 和 tech_key，用于批量查询
    all_item_keys: set[str] = set()
    all_tech_keys: set[str] = set()
    for troop in troops:
        recruit_config = troop.get("recruit")
        if not recruit_config:
            continue
        all_item_keys.update(recruit_config.get("equipment", []))
        tech_key = recruit_config.get("tech_key")
        if tech_key:
            all_tech_keys.add(tech_key)

    # 批量加载 ItemTemplate
    item_templates = {t.key: t for t in ItemTemplate.objects.filter(key__in=all_item_keys)}

    # 批量加载玩家物品数量
    item_quantities = _batch_get_item_quantities(manor, all_item_keys)

    # 批量加载玩家科技等级
    tech_levels = _batch_get_tech_levels(manor, all_tech_keys)

    options = []
    for troop in troops:
        recruit_config = troop.get("recruit")
        if not recruit_config:
            continue

        troop_key = troop["key"]
        tech_key = recruit_config.get("tech_key")
        tech_level_required = recruit_config.get("tech_level", 0)

        # 获取科技等级（从缓存的批量查询结果）
        if tech_key:
            player_tech_level = tech_levels.get(tech_key, 0)
            is_unlocked = player_tech_level >= tech_level_required
        else:
            player_tech_level = 0
            is_unlocked = True

        max_quantity = get_max_recruit_quantity(
            manor,
            troop_key,
            recruit_config,
            tech_level=player_tech_level if tech_key else None,
        )

        # 计算时间
        base_duration = recruit_config.get("base_duration", 120)
        actual_duration = calculate_recruitment_duration(base_duration, manor)

        # 检查装备状态（从缓存的批量查询结果）
        equipment_list = recruit_config.get("equipment", [])
        equipment_status = []
        can_afford = True

        for item_key in equipment_list:
            have = item_quantities.get(item_key, 0)
            item = item_templates.get(item_key)
            item_name = item.name if item else item_key

            equipment_status.append(
                {
                    "key": item_key,
                    "name": item_name,
                    "required": 1,
                    "have": have,
                    "satisfied": have >= 1,
                }
            )
            if have < 1:
                can_afford = False

        # 检查家丁
        retainer_cost = recruit_config.get("retainer_cost", 1)
        retainer_satisfied = manor.retainer_count >= retainer_cost

        options.append(
            {
                "key": troop_key,
                "name": troop["name"],
                "description": troop.get("description", ""),
                "base_attack": troop.get("base_attack", 0),
                "base_defense": troop.get("base_defense", 0),
                "base_hp": troop.get("base_hp", 0),
                "speed_bonus": troop.get("speed_bonus", 0),
                "avatar": troop.get("avatar", ""),
                "tech_key": tech_key,
                "tech_name": _get_tech_name(tech_key) if tech_key else None,
                "tech_level_required": tech_level_required,
                "player_tech_level": player_tech_level,
                "is_unlocked": is_unlocked,
                "equipment": equipment_status,
                "retainer_cost": retainer_cost,
                "retainer_satisfied": retainer_satisfied,
                "base_duration": base_duration,
                "actual_duration": actual_duration,
                "max_quantity": max_quantity,
                "can_afford": can_afford and retainer_satisfied,
                "is_recruiting": is_recruiting,
            }
        )

    return options


def _batch_get_item_quantities(manor: Manor, item_keys: set[str]) -> Dict[str, int]:
    """
    批量获取玩家物品数量。

    Args:
        manor: 庄园实例
        item_keys: 物品 key 集合

    Returns:
        {item_key: quantity} 字典
    """
    if not item_keys:
        return {}

    from ...models import InventoryItem

    items = InventoryItem.objects.filter(
        manor=manor,
        template__key__in=item_keys,
        storage_location=InventoryItem.StorageLocation.WAREHOUSE,
    ).select_related("template")

    result: Dict[str, int] = {}
    for item in items:
        key = item.template.key
        result[key] = result.get(key, 0) + item.quantity

    return result


def _batch_get_tech_levels(manor: Manor, tech_keys: set[str]) -> Dict[str, int]:
    """
    批量获取玩家科技等级。

    Args:
        manor: 庄园实例
        tech_keys: 科技 key 集合

    Returns:
        {tech_key: level} 字典
    """
    if not tech_keys:
        return {}

    from ...models import PlayerTechnology

    techs = PlayerTechnology.objects.filter(
        manor=manor,
        tech_key__in=tech_keys,
    )

    return {t.tech_key: t.level for t in techs}


def _validate_start_recruitment_inputs(manor: Manor, troop_key: str, quantity: int) -> dict:
    if quantity < 1:
        raise TroopRecruitmentError("募兵数量至少为1")
    if has_active_recruitment(manor):
        raise TroopRecruitmentAlreadyInProgressError()
    if manor.get_building_level(BuildingKeys.LIANGGONG_CHANG) < 1:
        raise TroopRecruitmentError("练功场等级不足，需要1级以上")

    troop = get_troop_template(troop_key)
    if not troop:
        raise TroopRecruitmentError("无效的兵种类型")
    recruit_config = troop.get("recruit")
    if not recruit_config:
        raise TroopRecruitmentError("该兵种不可募兵")

    max_quantity = get_max_recruit_quantity(manor, troop_key, recruit_config)
    if quantity > max_quantity:
        raise TroopRecruitmentError(f"造兵术等级限制，单次最多招募{max_quantity}人")

    check_result = check_recruitment_requirements(manor, troop_key, quantity)
    if not check_result["can_recruit"]:
        raise TroopRecruitmentError("、".join(check_result["errors"]))
    return troop


def _consume_equipment_for_recruitment(manor: Manor, equipment_list: list[str], quantity: int) -> Dict[str, int]:
    from ...models import InventoryItem

    if not equipment_list:
        return {}

    locked_items = {
        item.template.key: item
        for item in InventoryItem.objects.select_for_update()
        .filter(
            manor=manor,
            template__key__in=equipment_list,
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .select_related("template")
    }

    equipment_costs: Dict[str, int] = {}
    to_update: list[InventoryItem] = []
    delete_ids: list[int] = []

    for item_key in equipment_list:
        item = locked_items.get(item_key)
        if not item or item.quantity < quantity:
            raise TroopRecruitmentError(f"装备不足: {item_key}")

        item.quantity -= quantity
        if item.quantity <= 0:
            delete_ids.append(item.id)
        else:
            to_update.append(item)
        equipment_costs[item_key] = quantity

    if delete_ids:
        InventoryItem.objects.filter(id__in=delete_ids).delete()
    if to_update:
        InventoryItem.objects.bulk_update(to_update, ["quantity"])

    return equipment_costs


def _consume_retainers_for_recruitment(manor: Manor, retainer_cost: int) -> None:
    available_retainers = safe_non_negative_int(getattr(manor, "retainer_count", 0))
    if available_retainers < retainer_cost:
        raise TroopRecruitmentError(f"家丁不足，需要{retainer_cost}")

    updated = Manor.objects.filter(pk=manor.pk, retainer_count__gte=retainer_cost).update(
        retainer_count=F("retainer_count") - retainer_cost
    )
    if updated != 1:
        raise TroopRecruitmentError(f"家丁不足，需要{retainer_cost}")

    manor.refresh_from_db(fields=["retainer_count"])


def _lock_manor_for_recruitment(manor: Manor) -> Manor:
    return Manor.objects.select_for_update().get(pk=manor.pk)


def _ensure_no_active_recruitment_locked(manor: Manor) -> None:
    if TroopRecruitment.objects.filter(manor=manor, status=TroopRecruitment.Status.RECRUITING).exists():
        raise TroopRecruitmentAlreadyInProgressError()


def _create_troop_recruitment_record(
    manor: Manor,
    troop: Dict[str, Any],
    troop_key: str,
    quantity: int,
    equipment_costs: Dict[str, int],
    retainer_cost: int,
    base_duration: int,
) -> tuple[TroopRecruitment, int]:
    unit_duration = calculate_recruitment_duration(base_duration, manor)
    actual_duration = unit_duration * quantity
    recruitment = TroopRecruitment.objects.create(
        manor=manor,
        troop_key=troop_key,
        troop_name=troop["name"],
        quantity=quantity,
        equipment_costs=equipment_costs,
        retainer_cost=retainer_cost,
        base_duration=base_duration,
        actual_duration=actual_duration,
        complete_at=timezone.now() + timedelta(seconds=actual_duration),
    )
    return recruitment, actual_duration


def start_troop_recruitment(
    manor: Manor,
    troop_key: str,
    quantity: int = 1,
) -> TroopRecruitment:
    """
    开始募兵。

    Args:
        manor: 庄园实例
        troop_key: 兵种标识
        quantity: 募兵数量

    Returns:
        TroopRecruitment 实例

    Raises:
        TroopRecruitmentError: 参数错误、条件不满足或已有募兵进行中
    """
    troop = _validate_start_recruitment_inputs(manor, troop_key, quantity)
    recruit_config = troop.get("recruit") or {}

    # 获取配置
    equipment_list = recruit_config.get("equipment", [])
    retainer_cost = recruit_config.get("retainer_cost", 1) * quantity
    base_duration = recruit_config.get("base_duration", 120)

    with transaction.atomic():
        locked_manor = _lock_manor_for_recruitment(manor)
        _ensure_no_active_recruitment_locked(locked_manor)
        equipment_costs = _consume_equipment_for_recruitment(locked_manor, equipment_list, quantity)
        _consume_retainers_for_recruitment(locked_manor, retainer_cost)
        try:
            recruitment, actual_duration = _create_troop_recruitment_record(
                locked_manor,
                troop,
                troop_key,
                quantity,
                equipment_costs,
                retainer_cost,
                base_duration,
            )
        except IntegrityError:
            raise TroopRecruitmentAlreadyInProgressError()
        _schedule_recruitment_completion(recruitment, actual_duration)

    return recruitment
