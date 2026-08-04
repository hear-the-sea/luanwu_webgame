"""
资源计算工具模块

提供资源检查、产量计算等工具函数。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, DefaultDict, Dict

from core.exceptions import TroopLoadoutError
from core.utils import safe_int
from core.utils.time_scale import scale_duration

if TYPE_CHECKING:
    from ..models import Manor

from ..constants import BuildingKeys
from ..models import ResourceType

# 资源字段列表
RESOURCE_FIELDS = [
    ResourceType.GRAIN,
    ResourceType.SILVER,
]

# 人员耗粮规则（每小时）
RETAINER_GRAIN_COST_PER_HOUR = 1
TROOP_GRAIN_COST_PER_HOUR = 1
GUEST_GRAIN_COST_PER_HOUR = 100


def calculate_personnel_grain_cost_per_hour(
    *,
    retainer_count: int,
    guest_count: int,
    troop_count: int,
    bank_troop_count: int,
) -> int:
    total_troops = max(0, int(troop_count)) + max(0, int(bank_troop_count))
    return int(
        max(0, int(retainer_count)) * RETAINER_GRAIN_COST_PER_HOUR
        + total_troops * TROOP_GRAIN_COST_PER_HOUR
        + max(0, int(guest_count)) * GUEST_GRAIN_COST_PER_HOUR
    )


def get_personnel_grain_cost_per_hour(manor: "Manor") -> int:
    """
    计算人员每小时耗粮。

    规则：
    - 每位家丁：1 粮食/小时
    - 每位护院：1 粮食/小时（含庄园与钱庄中的护院）
    - 每名门客：100 粮食/小时
    """
    from django.db.models import Sum
    from django.db.models.functions import Coalesce

    retainer_count = safe_int(getattr(manor, "retainer_count", 0), default=0, min_val=0) or 0
    guest_count = safe_int(manor.guests.count(), default=0, min_val=0) or 0
    troop_count = (
        safe_int(
            manor.troops.aggregate(total=Coalesce(Sum("count"), 0)).get("total"),
            default=0,
            min_val=0,
        )
        or 0
    )
    bank_troop_count = (
        safe_int(
            manor.troop_bank_storages.aggregate(total=Coalesce(Sum("count"), 0)).get("total"),
            default=0,
            min_val=0,
        )
        or 0
    )

    return calculate_personnel_grain_cost_per_hour(
        retainer_count=retainer_count,
        guest_count=guest_count,
        troop_count=troop_count,
        bank_troop_count=bank_troop_count,
    )


def has_resources(manor: "Manor", cost: Dict[str, int]) -> bool:
    """
    检查庄园是否有足够的资源。

    Args:
        manor: 庄园实例
        cost: 资源成本字典 {"grain": 50, "silver": 100, ...}

    Returns:
        如果所有资源都足够返回 True，否则返回 False

    Examples:
        >>> # 假设庄园有 grain=200, silver=100
        >>> has_resources(manor, {"grain": 150, "silver": 50})
        True
        >>> has_resources(manor, {"silver": 250})
        False
    """
    grain_quantity = None
    for resource, amount in cost.items():
        if resource == ResourceType.GRAIN:
            if grain_quantity is None:
                from ..services.inventory.core import get_warehouse_grain_quantity

                grain_quantity = get_warehouse_grain_quantity(manor)
            available = grain_quantity
        else:
            available = getattr(manor, resource)
        if available < amount:
            return False
    return True


def calculate_hourly_rates(
    buildings: Iterable[Any],
    technology_levels: Mapping[str, int],
) -> Dict[str, float]:
    from ..services.technology import get_resource_production_bonus_from_levels

    rates: DefaultDict[str, float] = defaultdict(float)
    for building in buildings:
        base_rate = building.hourly_rate()
        resource_type = building.building_type.resource_type
        bonus = get_resource_production_bonus_from_levels(
            dict(technology_levels),
            resource_type,
            building_key=building.building_type.key,
        )
        rate = base_rate * (1.0 + bonus)
        rates[resource_type] += rate
        if building.building_type.key == BuildingKeys.LATRINE:
            rates[ResourceType.SILVER] += rate
    return dict(rates)


def get_hourly_rates(manor: "Manor") -> Dict[str, float]:
    """
    计算庄园每小时的资源产量。

    遍历所有建筑，累加各资源类型的产量。
    技术加成按建筑级别应用（如农耕术只增加农田产量）。
    茅厕除了产粮食外，还额外产出等量银两。

    Args:
        manor: 庄园实例

    Returns:
        资源产量字典 {"grain": 120.0, "silver": 95.0, ...}
    """
    from ..services.technology import get_player_technologies

    tech_levels = get_player_technologies(manor)
    return calculate_hourly_rates(
        manor.buildings.select_related("building_type"),
        tech_levels,
    )


def normalize_mission_loadout(raw: Dict[str, int] | None, troop_templates: Dict[str, Dict]) -> Dict[str, int]:
    """
    标准化兵力配置，过滤无效数据并填充默认值。

    Args:
        raw: 原始兵力配置
        troop_templates: 兵种模板字典

    Returns:
        标准化后的兵力配置

    Raises:
        TroopLoadoutError: 如果 raw 包含不存在的护院类型（安全检查）

    Examples:
        >>> normalize_mission_loadout({"infantry": "100", "invalid": -5}, templates)
        {"infantry": 100, "cavalry": 0, "archer": 0}
    """
    if not troop_templates:
        return {}

    if raw is None:
        raw = {}
    elif not isinstance(raw, dict):
        raise AssertionError(f"invalid mission troop loadout payload: {raw!r}")

    # 安全检查：检测并拒绝不存在的护院类型
    invalid_keys = set(raw.keys()) - set(troop_templates.keys())
    if invalid_keys:
        # 过滤掉数量为0的key（可能是前端传递的空值）
        invalid_nonzero = {
            k: v for k, v in raw.items() if k in invalid_keys and (safe_int(v, default=0, min_val=0) or 0) > 0
        }
        if invalid_nonzero:
            raise TroopLoadoutError(
                "护院配置包含无效类型",
                invalid_troop_keys=sorted(invalid_nonzero),
            )

    loadout: Dict[str, int] = {}
    for key in troop_templates.keys():
        value = raw.get(key, 0)
        loadout[key] = _resolve_mission_loadout_quantity(value, field_name=f"troop loadout quantity: {key}")

    return loadout


# 旅行时间计算常量
MISSION_AGILITY_REDUCTION_CAP = 0.50  # 门客平均敏捷最多减少50%任务行军时间
MIN_TRAVEL_TIME = 10  # 最小旅行时间（秒）


def _resolve_mission_loadout_quantity(raw: Any, *, field_name: str) -> int:
    if isinstance(raw, bool):
        raise AssertionError(f"invalid mission {field_name}: {raw!r}")
    try:
        quantity = int(raw)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid mission {field_name}: {raw!r}") from exc
    if quantity < 0:
        raise AssertionError(f"invalid mission {field_name}: {raw!r}")
    return quantity


def calculate_travel_time(base_time: int, guests, troop_loadout: Dict[str, int], troop_templates: Dict) -> int:
    """
    计算任务旅行时间，考虑门客平均敏捷加成。

    计算规则：
    - 平均敏捷每10点减少1%时间
    - 敏捷最多减少50%时间
    - 兵种配置仅参与载荷校验，不影响任务时长
    - 最少旅行时间为10秒

    Args:
        base_time: 基础旅行时间（秒）
        guests: 门客列表
        troop_loadout: 兵力配置
        troop_templates: 兵种模板

    Returns:
        实际旅行时间（秒）
    """
    if not isinstance(troop_loadout, dict):
        raise AssertionError(f"invalid mission troop loadout payload: {troop_loadout!r}")
    normalized_loadout: Dict[str, int] = {}
    for key, raw_count in troop_loadout.items():
        if not isinstance(key, str) or not key.strip():
            raise AssertionError(f"invalid mission troop loadout key: {key!r}")
        count = _resolve_mission_loadout_quantity(raw_count, field_name=f"troop loadout quantity: {key}")
        if count > 0 and key not in troop_templates:
            raise AssertionError(f"invalid mission troop loadout key: {key!r}")
        normalized_loadout[key] = count

    final_time = int(base_time)
    guest_count = len(guests)
    if guest_count > 0:
        avg_agility = sum(getattr(guest, "agility", 0) for guest in guests) / guest_count
        agility_reduction = min(MISSION_AGILITY_REDUCTION_CAP, avg_agility / 1000)
        final_time = int(base_time * (1 - agility_reduction))
    final_time = max(MIN_TRAVEL_TIME, final_time)

    # 应用全局时间流速倍率
    return scale_duration(final_time, minimum=MIN_TRAVEL_TIME)
