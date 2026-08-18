"""虚拟玩家按声望段限制护院总量。

虚拟玩家的声望段上限是培养策略约束，不替代真实游戏的物理容量：
庄园内和钱庄仍分别执行各自的 5000 名容量。虚拟培养新增护院时，
还要把钱庄库存与进行中的募兵预留一起计入，避免通过换库存位置绕过
声望段上限。
"""

from __future__ import annotations

from collections.abc import Mapping

from django.db.models import Sum

from core.exceptions import TroopCapacityFullError
from gameplay.models import Manor, TroopBankStorage
from gameplay.services.manor.troop_capacity import (
    MANOR_TROOP_CAPACITY,
    get_manor_troop_remaining_space,
    get_manor_troop_used_space,
    get_pending_troop_recruitment_space,
)

from .prestige_targets import VirtualPrestigeTargetPolicyError, starter_snapshot_profile_for_prestige_band


class VirtualTroopCapacityPolicyError(VirtualPrestigeTargetPolicyError):
    """护院声望段上限策略格式无效。"""


def virtual_troop_capacity_for_prestige_band(
    *,
    policy_payload: Mapping[str, object],
    prestige_band: str,
) -> int:
    """读取 V2 starter snapshot 中与声望段绑定的护院总量上限。

    ``starter_snapshots.profiles.<band>.troop_total`` 已经属于策略发布内容，
    因此直接复用它可以保证培养上限和该声望段的基准强度使用同一份、可审计
    的策略数据，而不另起一套未纳入策略版本的常量。
    """

    try:
        profile = starter_snapshot_profile_for_prestige_band(
            policy_payload=policy_payload,
            prestige_band=prestige_band,
        )
    except VirtualPrestigeTargetPolicyError as exc:
        raise VirtualTroopCapacityPolicyError(str(exc)) from exc
    raw_capacity = profile.get("troop_total")
    if isinstance(raw_capacity, bool) or not isinstance(raw_capacity, int) or raw_capacity < 0:
        raise VirtualTroopCapacityPolicyError(
            f"policy starter_snapshots.profiles.{prestige_band}.troop_total must be a non-negative integer"
        )
    return min(MANOR_TROOP_CAPACITY, int(raw_capacity))


def get_troop_bank_used_space_for_virtual_player(manor: Manor) -> int:
    """返回虚拟玩家钱庄中当前护院总数。"""

    total = TroopBankStorage.objects.filter(manor_id=manor.pk).aggregate(total=Sum("count")).get("total") or 0
    return max(0, int(total))


def get_virtual_troop_used_space(
    manor: Manor,
    *,
    include_pending_recruitment: bool = True,
    exclude_recruitment_id: int | None = None,
) -> int:
    """返回虚拟玩家声望上限口径下已占用的护院数量。

    口径包含庄园库存、钱庄库存，以及已由募兵队列预留但尚未入库的数量。
    调用方若要据此写入，必须先锁定 Manor。
    """

    pending = (
        get_pending_troop_recruitment_space(
            manor,
            exclude_recruitment_id=exclude_recruitment_id,
        )
        if include_pending_recruitment
        else 0
    )
    return get_manor_troop_used_space(manor) + get_troop_bank_used_space_for_virtual_player(manor) + pending


def get_virtual_troop_remaining_space(
    manor: Manor,
    *,
    virtual_capacity: int,
    include_pending_recruitment: bool = True,
    exclude_recruitment_id: int | None = None,
) -> int:
    """返回同时满足物理容量与声望段上限的可新增护院数量。"""

    normalized_capacity = max(0, min(MANOR_TROOP_CAPACITY, int(virtual_capacity)))
    physical_remaining = get_manor_troop_remaining_space(
        manor,
        include_pending_recruitment=include_pending_recruitment,
        exclude_recruitment_id=exclude_recruitment_id,
    )
    virtual_remaining = normalized_capacity - get_virtual_troop_used_space(
        manor,
        include_pending_recruitment=include_pending_recruitment,
        exclude_recruitment_id=exclude_recruitment_id,
    )
    return max(0, min(physical_remaining, virtual_remaining))


def ensure_virtual_troop_capacity_locked(
    manor: Manor,
    quantity: int,
    *,
    virtual_capacity: int,
    exclude_recruitment_id: int | None = None,
) -> int:
    """在调用方已锁定 Manor 的事务中校验虚拟玩家新增护院容量。"""

    normalized_quantity = int(quantity)
    remaining = get_virtual_troop_remaining_space(
        manor,
        virtual_capacity=virtual_capacity,
        exclude_recruitment_id=exclude_recruitment_id,
    )
    if normalized_quantity > 0 and normalized_quantity > remaining:
        raise TroopCapacityFullError(
            required=normalized_quantity,
            available=remaining,
            capacity=min(MANOR_TROOP_CAPACITY, max(0, int(virtual_capacity))),
        )
    return max(0, remaining - max(0, normalized_quantity))


__all__ = [
    "VirtualTroopCapacityPolicyError",
    "ensure_virtual_troop_capacity_locked",
    "get_troop_bank_used_space_for_virtual_player",
    "get_virtual_troop_remaining_space",
    "get_virtual_troop_used_space",
    "virtual_troop_capacity_for_prestige_band",
]
