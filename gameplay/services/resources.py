"""
资源管理服务
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Tuple

from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, Sum
from django.utils import timezone

from core.exceptions import InsufficientResourceError, InsufficientSilverError
from core.utils.time_scale import scale_value

from ..models import (
    Building,
    ItemTemplate,
    Manor,
    PlayerTechnology,
    PlayerTroop,
    ResourceEvent,
    ResourceType,
    TroopBankStorage,
)
from ..utils.resource_calculator import (
    RESOURCE_FIELDS,
    calculate_hourly_rates,
    calculate_personnel_grain_cost_per_hour,
    get_hourly_rates,
    get_personnel_grain_cost_per_hour,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ResourceProductionBasis:
    hourly_rates: tuple[tuple[str, float], ...]
    personnel_grain_cost_per_hour: int


def load_resource_production_bases(
    manors: Sequence[Manor],
    *,
    guest_counts: Mapping[int, int] | None = None,
    troop_counts: Mapping[int, int] | None = None,
    retainer_counts: Mapping[int, int] | None = None,
    buildings_by_manor: Mapping[int, Sequence[Building]] | None = None,
    technology_levels: Mapping[int, Mapping[str, int]] | None = None,
) -> dict[int, ResourceProductionBasis]:
    """Load immutable production inputs for multiple persisted Manors."""

    manor_by_id: dict[int, Manor] = {}
    for manor in manors:
        if not getattr(manor, "pk", None):
            raise AssertionError("production basis requires persisted manors")
        manor_by_id[int(manor.pk)] = manor
    manor_ids = tuple(manor_by_id)
    if not manor_ids:
        return {}

    if technology_levels is None:
        resolved_technology_levels: dict[int, dict[str, int]] = defaultdict(dict)
        for manor_id, tech_key, level in PlayerTechnology.objects.filter(manor_id__in=manor_ids).values_list(
            "manor_id", "tech_key", "level"
        ):
            resolved_technology_levels[int(manor_id)][str(tech_key)] = int(level)
    else:
        resolved_technology_levels = {
            int(manor_id): {str(tech_key): int(level) for tech_key, level in levels.items()}
            for manor_id, levels in technology_levels.items()
            if int(manor_id) in manor_by_id
        }

    if buildings_by_manor is None:
        resolved_buildings_by_manor: dict[int, list[Building]] = defaultdict(list)
        for building in Building.objects.filter(manor_id__in=manor_ids).select_related("building_type"):
            resolved_buildings_by_manor[int(building.manor_id)].append(building)
    else:
        resolved_buildings_by_manor = {
            int(manor_id): list(buildings)
            for manor_id, buildings in buildings_by_manor.items()
            if int(manor_id) in manor_by_id
        }
        if any(
            int(building.manor_id) != manor_id
            for manor_id, buildings in resolved_buildings_by_manor.items()
            for building in buildings
        ):
            raise AssertionError("production basis building belongs to another manor")

    if guest_counts is None:
        from guests.models import Guest

        resolved_guest_counts = {
            int(row["manor_id"]): int(row["total"])
            for row in Guest.objects.filter(manor_id__in=manor_ids).values("manor_id").annotate(total=Count("id"))
        }
    else:
        resolved_guest_counts = {
            int(manor_id): max(0, int(count))
            for manor_id, count in guest_counts.items()
            if int(manor_id) in manor_by_id
        }

    if troop_counts is None:
        troop_totals = {
            int(row["manor_id"]): int(row["total"] or 0)
            for row in PlayerTroop.objects.filter(manor_id__in=manor_ids)
            .values("manor_id")
            .annotate(total=Sum("count"))
        }
    else:
        troop_totals = {
            int(manor_id): max(0, int(count))
            for manor_id, count in troop_counts.items()
            if int(manor_id) in manor_by_id
        }
    resolved_retainer_counts = (
        {
            int(manor_id): max(0, int(count))
            for manor_id, count in retainer_counts.items()
            if int(manor_id) in manor_by_id
        }
        if retainer_counts is not None
        else {manor_id: max(0, int(manor.retainer_count or 0)) for manor_id, manor in manor_by_id.items()}
    )
    bank_troop_totals = {
        int(row["manor_id"]): int(row["total"] or 0)
        for row in TroopBankStorage.objects.filter(manor_id__in=manor_ids)
        .values("manor_id")
        .annotate(total=Sum("count"))
    }

    return {
        manor_id: ResourceProductionBasis(
            hourly_rates=tuple(
                sorted(
                    calculate_hourly_rates(
                        resolved_buildings_by_manor.get(manor_id, ()),
                        resolved_technology_levels.get(manor_id, {}),
                    ).items()
                )
            ),
            personnel_grain_cost_per_hour=calculate_personnel_grain_cost_per_hour(
                retainer_count=resolved_retainer_counts.get(manor_id, int(manor.retainer_count or 0)),
                guest_count=resolved_guest_counts.get(manor_id, 0),
                troop_count=troop_totals.get(manor_id, 0),
                bank_troop_count=bank_troop_totals.get(manor_id, 0),
            ),
        )
        for manor_id, manor in manor_by_id.items()
    }


def load_resource_production_basis(
    manor: Manor,
    *,
    guest_count: int | None = None,
    troop_count: int | None = None,
    retainer_count: int | None = None,
    buildings: Sequence[Building] | None = None,
    technology_levels: Mapping[str, int] | None = None,
) -> ResourceProductionBasis:
    if not getattr(manor, "pk", None):
        raise AssertionError("production basis requires a persisted manor")
    if retainer_count is not None and all(
        value is None for value in (guest_count, troop_count, buildings, technology_levels)
    ):
        # Preserve the single-manor production adapter (including test and
        # runtime overrides of get_hourly_rates) while allowing V2 to replace
        # only the retainer component of the personnel cost.
        current_retainer_count = max(0, int(manor.retainer_count or 0))
        personnel_cost = get_personnel_grain_cost_per_hour(manor)
        personnel_cost += max(0, int(retainer_count)) - current_retainer_count
        return ResourceProductionBasis(
            hourly_rates=tuple(sorted(get_hourly_rates(manor).items())),
            personnel_grain_cost_per_hour=max(0, int(personnel_cost)),
        )
    if any(value is not None for value in (guest_count, troop_count, buildings, technology_levels)):
        return load_resource_production_bases(
            (manor,),
            guest_counts=(None if guest_count is None else {int(manor.pk): guest_count}),
            troop_counts=(None if troop_count is None else {int(manor.pk): troop_count}),
            retainer_counts=(None if retainer_count is None else {int(manor.pk): retainer_count}),
            buildings_by_manor=(None if buildings is None else {int(manor.pk): tuple(buildings)}),
            technology_levels=(None if technology_levels is None else {int(manor.pk): dict(technology_levels)}),
        )[int(manor.pk)]
    return ResourceProductionBasis(
        hourly_rates=tuple(sorted(get_hourly_rates(manor).items())),
        personnel_grain_cost_per_hour=get_personnel_grain_cost_per_hour(manor),
    )


def _require_resource_key(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise AssertionError(f"invalid resource key: {raw!r}")
    return raw.strip()


def _require_resource_amount(raw: Any, *, allow_negative: bool = False) -> int:
    if isinstance(raw, bool):
        raise AssertionError(f"invalid resource amount: {raw!r}")
    if not isinstance(raw, int):
        raise AssertionError(f"invalid resource amount: {raw!r}")
    if allow_negative:
        return raw
    if raw < 0:
        raise AssertionError(f"invalid resource amount: {raw!r}")
    return raw


def _normalize_resource_mapping(raw: Any, *, field_name: str, allow_negative: bool = False) -> Dict[str, int]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AssertionError(f"invalid {field_name}: {raw!r}")
    normalized: Dict[str, int] = {}
    for resource, amount in raw.items():
        normalized[_require_resource_key(resource)] = _require_resource_amount(amount, allow_negative=allow_negative)
    return normalized


def _load_warehouse_grain_quantity_locked(
    manor: Manor,
    *,
    grain_template: ItemTemplate | None = None,
    grain_template_resolved: bool = False,
) -> int:
    # Delay import to avoid circular dependency:
    # resources -> inventory package -> inventory.use -> resources.
    from .inventory.core import get_warehouse_grain_quantity_locked

    return get_warehouse_grain_quantity_locked(
        manor,
        grain_template=grain_template,
        grain_template_resolved=grain_template_resolved,
    )


def _set_warehouse_grain_quantity_locked(
    manor: Manor,
    quantity: int,
    *,
    grain_template: ItemTemplate | None = None,
    grain_template_resolved: bool = False,
) -> int:
    from .inventory.core import set_warehouse_grain_quantity_locked

    return set_warehouse_grain_quantity_locked(
        manor,
        quantity,
        grain_template=grain_template,
        grain_template_resolved=grain_template_resolved,
    )


def _load_warehouse_grain_quantity_for_read(manor: Manor) -> int:
    from .inventory.core import get_warehouse_grain_quantity

    return get_warehouse_grain_quantity(manor)


def _clear_warehouse_grain_projection(manor: Manor) -> None:
    from .inventory.core import clear_warehouse_grain_projection

    clear_warehouse_grain_projection(manor)


def _get_resource_capacity(manor: Manor, resource: str) -> Tuple[int, bool]:
    """
    获取指定资源的容量上限。

    DRY 修复：提取重复的容量判断逻辑为辅助函数。

    Args:
        manor: 庄园对象（应该是锁定后的对象以保证事务一致性）
        resource: 资源类型

    Returns:
        (容量值, 是否为有效资源类型)
    """
    if resource == ResourceType.SILVER:
        return manor.silver_capacity, True
    elif resource == ResourceType.GRAIN:
        return manor.grain_capacity, True
    else:
        return 0, False


def _handle_unknown_resource(manor: Manor, resource: str, amount: int) -> None:
    if settings.DEBUG:
        raise AssertionError(f"unknown resource type: {resource}")
    logger.error(
        "未知资源类型被跳过: %s=%s",
        resource,
        amount,
        extra={"manor_id": manor.id, "resource": resource, "amount": amount},
    )


def _current_resource_value(
    manor: Manor,
    resource: str,
    *,
    current_resources: Mapping[str, int] | None = None,
) -> int:
    value = (
        current_resources[resource]
        if current_resources is not None and resource in current_resources
        else getattr(manor, resource, 0)
    )
    return _require_resource_amount(value)


def _calculate_resource_credit(
    manor: Manor,
    resource: str,
    amount: int,
    *,
    current_resources: Mapping[str, int] | None = None,
) -> Tuple[int, int] | None:
    if amount <= 0:
        return None

    capacity, is_valid = _get_resource_capacity(manor, resource)
    if not is_valid:
        _handle_unknown_resource(manor, resource, amount)
        return None

    current_value = _current_resource_value(
        manor,
        resource,
        current_resources=current_resources,
    )
    available_capacity = max(0, capacity - current_value)
    added = min(amount, available_capacity)
    overflowed = amount - added
    return added, overflowed


def _credit_resource(manor: Manor, resource: str, amount: int) -> Tuple[int, int] | None:
    credit_result = _calculate_resource_credit(manor, resource, amount)
    if credit_result is None:
        return None

    added, overflowed = credit_result
    if added > 0:
        setattr(manor, resource, getattr(manor, resource, 0) + added)
    return added, overflowed


def preview_resource_grant(manor: Manor, rewards: Dict[str, int]) -> Tuple[Dict[str, int], Dict[str, int]]:
    """预览资源奖励的实际入账与溢出数量，不修改庄园状态。"""
    current_resources: Dict[str, int] = {
        str(ResourceType.SILVER): _require_resource_amount(manor.silver),
        str(ResourceType.GRAIN): _load_warehouse_grain_quantity_for_read(manor),
    }
    normalized_rewards = _normalize_resource_mapping(rewards, field_name="resource rewards")
    credited: Dict[str, int] = {}
    overflow: Dict[str, int] = {}

    for resource, amount in normalized_rewards.items():
        credit_result = _calculate_resource_credit(
            manor,
            resource,
            amount,
            current_resources=current_resources,
        )
        if credit_result is None:
            continue
        added, overflowed = credit_result
        if added > 0:
            credited[resource] = added
        if overflowed > 0:
            overflow[resource] = overflowed

    return credited, overflow


def _require_atomic_block(name: str) -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(f"{name} must be called inside transaction.atomic()")


def _raise_insufficient_resource_error(manor: Manor, cost: Dict[str, int]) -> None:
    normalized_cost = _normalize_resource_mapping(cost, field_name="resource cost")
    for resource, required in normalized_cost.items():
        if required <= 0:
            continue
        available = _require_resource_amount(getattr(manor, resource, 0))
        if available >= required:
            continue
        if resource == ResourceType.SILVER:
            raise InsufficientSilverError(required, available)
        raise InsufficientResourceError(resource, required, available)
    raise InsufficientResourceError("unknown", 0, 0, message="资源不足")


def _build_production_snapshot(
    manor: Manor,
    *,
    now: datetime,
    production_basis: ResourceProductionBasis | None = None,
    resource_fields: Sequence[str] | None = None,
    current_resources: Mapping[str, int] | None = None,
) -> tuple[Dict[str, int], Dict[str, int], bool]:
    elapsed_seconds = (now - manor.resource_updated_at).total_seconds()
    if elapsed_seconds <= 0:
        return {}, {}, False

    scaled_elapsed_seconds = scale_value(elapsed_seconds)
    if production_basis is None:
        hourly_rates = get_hourly_rates(manor)
        personnel_grain_cost = get_personnel_grain_cost_per_hour(manor)
    else:
        hourly_rates = dict(production_basis.hourly_rates)
        personnel_grain_cost = production_basis.personnel_grain_cost_per_hour
    hourly_rates[ResourceType.GRAIN] = hourly_rates.get(ResourceType.GRAIN, 0) - personnel_grain_cost

    projected_values: Dict[str, int] = {}
    produced: Dict[str, int] = {}
    fields = RESOURCE_FIELDS if resource_fields is None else tuple(resource_fields)
    for resource in fields:
        per_hour = hourly_rates.get(resource, 0)
        delta = int(per_hour * (scaled_elapsed_seconds / 3600))
        if delta == 0:
            continue

        current_value = _current_resource_value(
            manor,
            resource,
            current_resources=current_resources,
        )
        capacity, is_valid = _get_resource_capacity(manor, resource)
        if not is_valid:
            continue

        if delta > 0:
            # A legacy/imported balance may already exceed today's capacity.
            # Natural production must never delete that existing balance or
            # emit a misleading negative ``produce`` event.  Until the
            # balance falls back under capacity, new positive production is
            # discarded rather than increasing the over-cap amount.
            new_value = max(current_value, min(capacity, current_value + delta))
        else:
            new_value = max(0, current_value + delta)
        actual_delta = new_value - current_value
        if actual_delta == 0:
            continue

        projected_values[resource] = new_value
        produced[resource] = actual_delta

    return projected_values, produced, True


def preview_resource_production(
    manor: Manor,
    *,
    now: datetime | None = None,
    production_basis: ResourceProductionBasis | None = None,
    current_resources: Mapping[str, int] | None = None,
) -> Dict[str, int]:
    """只读计算截至 ``now`` 的资源产出增量。"""
    resolved_current_resources: Mapping[str, int] = (
        {
            str(ResourceType.SILVER): _require_resource_amount(manor.silver),
            str(ResourceType.GRAIN): _load_warehouse_grain_quantity_for_read(manor),
        }
        if current_resources is None
        else current_resources
    )
    current_time = now or timezone.now()
    _projected_values, produced, _should_advance_timestamp = _build_production_snapshot(
        manor,
        now=current_time,
        production_basis=production_basis,
        current_resources=resolved_current_resources,
    )
    return dict(produced)


def _apply_resource_projection(manor: Manor, projected_values: Dict[str, int], *, now: datetime) -> None:
    for resource, value in projected_values.items():
        setattr(manor, resource, value)
        if resource == ResourceType.GRAIN:
            setattr(manor, "warehouse_grain_quantity", value)
    manor.resource_updated_at = now


def settle_resource_production_locked(
    manor: Manor,
    *,
    now: datetime | None = None,
    positive_limits: Dict[str, int] | None = None,
    note: str = "离线产出",
    production_basis: ResourceProductionBasis | None = None,
    grain_template: ItemTemplate | None = None,
    grain_template_resolved: bool = False,
) -> Dict[str, int]:
    """在 Manor 锁内结算产出，可按资源限制正向增量。"""
    _require_atomic_block("settle_resource_production_locked")
    current_time = now or timezone.now()
    normalized_limits = (
        None
        if positive_limits is None
        else _normalize_resource_mapping(
            positive_limits,
            field_name="resource production positive limits",
        )
    )
    if normalized_limits is not None:
        unknown = set(normalized_limits) - set(RESOURCE_FIELDS)
        if unknown:
            raise AssertionError(f"invalid resource production positive limit keys: {sorted(unknown)!r}")

    _load_warehouse_grain_quantity_locked(
        manor,
        grain_template=grain_template,
        grain_template_resolved=grain_template_resolved,
    )

    _projected_values, produced, should_advance_timestamp = _build_production_snapshot(
        manor,
        now=current_time,
        production_basis=production_basis,
    )
    if not should_advance_timestamp:
        return {}

    settled: Dict[str, int] = {}
    projected_values: Dict[str, int] = {}
    for resource, delta in produced.items():
        applied_delta = int(delta)
        if applied_delta > 0 and normalized_limits is not None:
            applied_delta = min(applied_delta, normalized_limits.get(resource, 0))
        if applied_delta == 0:
            continue
        projected_values[resource] = int(getattr(manor, resource)) + applied_delta
        settled[resource] = applied_delta

    _apply_resource_projection(manor, projected_values, now=current_time)

    update_fields = list(projected_values.keys()) + ["resource_updated_at"]
    manor.save(update_fields=update_fields)
    if int(settled.get(ResourceType.GRAIN, 0) or 0) != 0:
        _set_warehouse_grain_quantity_locked(
            manor,
            int(manor.grain),
            grain_template=grain_template,
            grain_template_resolved=grain_template_resolved,
        )

    if settled:
        log_resource_gain(
            manor,
            {str(k): int(v) for k, v in settled.items()},
            ResourceEvent.Reason.PRODUCE,
            note=note,
        )

    return settled


def _sync_resource_production_locked(
    manor: Manor,
    *,
    now: datetime | None = None,
    production_basis: ResourceProductionBasis | None = None,
    grain_template: ItemTemplate | None = None,
    grain_template_resolved: bool = False,
) -> Dict[str, int]:
    return settle_resource_production_locked(
        manor,
        now=now,
        production_basis=production_basis,
        grain_template=grain_template,
        grain_template_resolved=grain_template_resolved,
    )


def spend_resources_locked(
    manor: Manor,
    cost: Dict[str, int],
    note: str,
    reason: str = ResourceEvent.Reason.UPGRADE_COST,
    *,
    sync_production: bool = True,
    grain_template: ItemTemplate | None = None,
    grain_template_resolved: bool = False,
) -> None:
    """
    消耗庄园资源（假设调用方已在 transaction.atomic 中完成所需的并发控制）。

    该函数不会创建新的事务块，也不会额外对 Manor 行加锁；适用于上层服务函数已经
    `select_for_update()` 锁定 manor 行的场景，避免重复锁与嵌套事务的冗余开销。
    """
    normalized_cost = _normalize_resource_mapping(cost, field_name="resource cost")
    if not normalized_cost:
        return
    _require_atomic_block("spend_resources_locked")
    if sync_production:
        _sync_resource_production_locked(
            manor,
            grain_template=grain_template,
            grain_template_resolved=grain_template_resolved,
        )
    elif ResourceType.GRAIN in normalized_cost:
        _load_warehouse_grain_quantity_locked(
            manor,
            grain_template=grain_template,
            grain_template_resolved=grain_template_resolved,
        )

    filters = {f"{key}__gte": value for key, value in normalized_cost.items()}
    updates = {key: F(key) - value for key, value in normalized_cost.items()}
    updated = Manor.objects.filter(pk=manor.pk, **filters).update(**updates)
    if not updated:
        _raise_insufficient_resource_error(manor, normalized_cost)

    for resource, amount in normalized_cost.items():
        setattr(manor, resource, int(getattr(manor, resource)) - amount)
    if normalized_cost.get(ResourceType.GRAIN, 0) > 0:
        _set_warehouse_grain_quantity_locked(
            manor,
            int(manor.grain),
            grain_template=grain_template,
            grain_template_resolved=grain_template_resolved,
        )
    negative = {key: -val for key, val in normalized_cost.items()}
    log_resource_gain(manor, negative, reason, note)


def grant_resources_locked(
    manor: Manor,
    rewards: Dict[str, int],
    note: str,
    reason: str = ResourceEvent.Reason.TASK_REWARD,
    *,
    sync_production: bool = True,
) -> Tuple[Dict[str, int], Dict[str, int]]:
    """
    发放资源奖励给庄园（假设调用方已在 transaction.atomic 中持有 manor 行锁）。

    该函数不会创建新的事务块，也不会额外对 Manor 行加锁；适用于上层服务函数已经
    `select_for_update()` 锁定 manor 行的场景，避免重复锁与嵌套事务的冗余开销。

    Returns:
        (credited, overflow) - 实际入账资源和溢出资源字典
    """
    normalized_rewards = _normalize_resource_mapping(rewards, field_name="resource rewards")
    if not normalized_rewards:
        return {}, {}
    _require_atomic_block("grant_resources_locked")
    _load_warehouse_grain_quantity_locked(manor)
    if sync_production:
        _sync_resource_production_locked(manor)

    credited: Dict[str, int] = {}
    overflow: Dict[str, int] = {}

    for resource, amount in normalized_rewards.items():
        credit_result = _credit_resource(manor, resource, amount)
        if credit_result is None:
            continue

        added, overflowed = credit_result
        if added <= 0:
            overflow[resource] = amount
            continue

        credited[resource] = added
        if overflowed > 0:
            overflow[resource] = overflowed

    if credited:
        manor.save(update_fields=list(credited.keys()))
        if credited.get(ResourceType.GRAIN, 0) > 0:
            _set_warehouse_grain_quantity_locked(manor, int(manor.grain))
        log_resource_gain(manor, credited, reason, note)

    # 记录溢出情况便于调试
    if overflow:
        logger.debug(
            "资源溢出被丢弃: %s",
            overflow,
            extra={"manor_id": manor.id, "overflow": overflow},
        )

    return credited, overflow


def sync_resource_production(
    manor: Manor,
    *,
    persist: bool = True,
    refresh: bool = True,
    grain_template: ItemTemplate | None = None,
    grain_template_resolved: bool = False,
) -> ResourceProductionBasis | None:
    """
    同步庄园资源产出，根据离线时间计算并发放资源。

    `persist=True` 时会在锁内落库并默认刷新传入对象。
    当调用方不会继续使用传入对象时，可传入 `refresh=False` 避免一次无用回读。
    `persist=False` 时仅将计算结果投影到传入对象本身，不写入数据库。
    批量调用方可传入已解析的粮食模板，避免每个庄园重复查询同一模板。

    Uses row-level locking to prevent concurrent race conditions that could
    lead to duplicate resource awards when persistence is enabled.

    Args:
        manor: 庄园对象；仅当 `refresh=True` 时会回读以反映最新状态
        persist: 是否持久化到数据库
        refresh: 持久化后是否回读传入的庄园对象
        grain_template: 可复用的粮食物品模板
        grain_template_resolved: 是否已完成粮食模板解析（包括解析为空）
    """
    now = timezone.now()
    if not persist:
        grain_quantity = _load_warehouse_grain_quantity_for_read(manor)
        # This compatibility API intentionally projects onto the caller's
        # instance. Keep that mutation local and explicit; generic preview
        # helpers remain read-only.
        manor.grain = grain_quantity
        setattr(manor, "warehouse_grain_quantity", grain_quantity)

    min_interval = getattr(settings, "RESOURCE_SYNC_MIN_INTERVAL_SECONDS", 0)
    elapsed_hint = (now - manor.resource_updated_at).total_seconds()
    if min_interval > 0:
        if elapsed_hint < min_interval:
            if persist:
                if refresh:
                    manor.refresh_from_db(fields=RESOURCE_FIELDS + ["resource_updated_at"])
                _clear_warehouse_grain_projection(manor)
            return None

    if not persist:
        production_basis = None
        if elapsed_hint > 0:
            production_basis = load_resource_production_basis(manor)
        projected_values, _produced, should_advance_timestamp = _build_production_snapshot(
            manor,
            now=now,
            production_basis=production_basis,
            resource_fields=(ResourceType.SILVER, ResourceType.GRAIN),
            current_resources={
                ResourceType.SILVER: _require_resource_amount(manor.silver),
                ResourceType.GRAIN: grain_quantity,
            },
        )
        if should_advance_timestamp:
            _apply_resource_projection(manor, projected_values, now=now)
        return production_basis

    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        _sync_resource_production_locked(
            locked_manor,
            now=now,
            production_basis=None,
            grain_template=grain_template,
            grain_template_resolved=grain_template_resolved,
        )

    if refresh:
        manor.refresh_from_db(fields=RESOURCE_FIELDS + ["resource_updated_at"])
    _clear_warehouse_grain_projection(manor)
    return None


def sync_resource_production_batch(
    manor_ids: Sequence[int],
    *,
    grain_template: ItemTemplate | None = None,
    grain_template_resolved: bool = False,
    now: datetime | None = None,
) -> int:
    """在一个受控分块事务内结算多个庄园的资源产出。

    调用方只提供候选庄园 ID；锁、生产基准读取和逐庄园账本写入均由资源服务
    负责。生产基准在庄园行锁建立后批量读取，避免每个庄园重复读取建筑、科技、
    门客和兵力统计，同时保留 ``Manor`` 行锁防止重复发放资源。返回本次实际
    进入锁内结算的庄园数量。
    """
    normalized_ids = tuple(dict.fromkeys(int(manor_id) for manor_id in manor_ids))
    if not normalized_ids:
        return 0

    current_time = now or timezone.now()
    min_interval = max(0, int(getattr(settings, "RESOURCE_SYNC_MIN_INTERVAL_SECONDS", 0)))
    eligible_before = current_time - timedelta(seconds=min_interval)
    with transaction.atomic():
        eligible_queryset = Manor.objects.filter(pk__in=normalized_ids)
        if min_interval > 0:
            eligible_queryset = eligible_queryset.filter(resource_updated_at__lte=eligible_before)
        locked_manors = list(
            eligible_queryset.select_for_update(skip_locked=True).order_by("resource_updated_at", "id")
        )
        if not locked_manors:
            return 0

        if not grain_template_resolved:
            from .inventory.core import GRAIN_ITEM_KEY

            grain_template = ItemTemplate.objects.filter(key=GRAIN_ITEM_KEY).only("id", "key").first()
            grain_template_resolved = True

        production_bases = load_resource_production_bases(locked_manors)
        processed = 0
        for manor in locked_manors:
            production_basis = production_bases.get(int(manor.pk))
            if production_basis is None:
                raise RuntimeError(f"missing resource production basis for manor_id={manor.pk}")
            _sync_resource_production_locked(
                manor,
                now=current_time,
                production_basis=production_basis,
                grain_template=grain_template,
                grain_template_resolved=grain_template_resolved,
            )
            processed += 1

        return processed


def project_resource_production_for_read(manor: Manor) -> ResourceProductionBasis | None:
    """
    在读路径中投影庄园资源状态。

    这是页面读取入口应使用的显式接口，避免调用方直接依赖
    `sync_resource_production(..., persist=False)` 的实现细节。
    """
    return sync_resource_production(manor, persist=False)


def log_resource_gain(manor: Manor, payload: Dict[str, int], reason: str, note: str = "") -> None:
    """
    记录资源变化日志。

    Args:
        manor: 庄园对象
        payload: 资源变化字典 {resource_type: delta}
        reason: 变化原因
        note: 备注信息
    """
    normalized_payload = _normalize_resource_mapping(payload, field_name="resource event payload", allow_negative=True)
    events = [
        ResourceEvent(manor=manor, resource_type=resource, delta=delta, reason=reason, note=note)
        for resource, delta in normalized_payload.items()
        if delta
    ]
    if events:
        ResourceEvent.objects.bulk_create(events)


def spend_resources(
    manor: Manor,
    cost: Dict[str, int],
    note: str,
    reason: str = ResourceEvent.Reason.UPGRADE_COST,
) -> None:
    """
    消耗庄园资源。

    Args:
        manor: 庄园对象
        cost: 资源消耗字典 {resource_type: amount}
        note: 消耗说明
        reason: 消耗原因

    Raises:
        InsufficientResourceError: 资源不足时抛出
    """
    normalized_cost = _normalize_resource_mapping(cost, field_name="resource cost")
    if not normalized_cost:
        return

    with transaction.atomic():
        # 安全修复：正确获取锁定后的 manor 对象并传递给 spend_resources_locked
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        spend_resources_locked(locked_manor, normalized_cost, note=note, reason=reason)

    # 刷新原始 manor 对象以反映最新状态
    manor.refresh_from_db(fields=RESOURCE_FIELDS + ["resource_updated_at"])
    _clear_warehouse_grain_projection(manor)


def grant_resources(
    manor: Manor,
    rewards: Dict[str, int],
    note: str,
    reason: str = ResourceEvent.Reason.TASK_REWARD,
    *,
    sync_production: bool = True,
) -> Dict[str, int]:
    """
    发放资源奖励给庄园。

    Uses row-level locking to ensure thread-safe updates and respects
    storage capacity limits. Rewards beyond capacity are ignored.

    Args:
        manor: 庄园对象
        rewards: 资源奖励字典 {resource_type: amount}
        note: 奖励说明
        reason: 奖励原因

    Returns:
        实际入账资源字典 {resource_type: credited_amount}
    """
    normalized_rewards = _normalize_resource_mapping(rewards, field_name="resource rewards")
    if not normalized_rewards:
        return {}

    with transaction.atomic():
        locked_manor = Manor.objects.select_for_update().get(pk=manor.pk)
        # 修复：正确解构 grant_resources_locked 的返回值
        credited, _overflow = grant_resources_locked(
            locked_manor,
            normalized_rewards,
            note=note,
            reason=reason,
            sync_production=sync_production,
        )

    manor.refresh_from_db(fields=RESOURCE_FIELDS + ["resource_updated_at"])
    _clear_warehouse_grain_projection(manor)
    return credited
