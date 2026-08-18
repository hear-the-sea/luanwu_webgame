"""庄园内外护院容量规则。

庄园内的 ``PlayerTroop`` 与庄园外的钱庄 ``TroopBankStorage`` 是两套
独立存储，分别执行 5000 名上限。所有运行时增加护院的服务都应在已锁定
Manor 的事务中调用本模块，避免真实玩家、虚拟玩家或并发请求绕过容量校验。
"""

from __future__ import annotations

from django.db.models import Sum

from core.exceptions import TroopCapacityFullError

from ...models import Manor, PlayerTroop, TroopRecruitment

MANOR_TROOP_CAPACITY = 5000
TROOP_BANK_CAPACITY = 5000


def get_manor_troop_used_space(manor: Manor) -> int:
    """返回庄园内当前实际存储的护院总数。"""

    total = PlayerTroop.objects.filter(manor_id=manor.pk).aggregate(total=Sum("count")).get("total") or 0
    return max(0, int(total))


def get_pending_troop_recruitment_space(
    manor: Manor,
    *,
    exclude_recruitment_id: int | None = None,
) -> int:
    """返回已经由进行中募兵队列预留的庄园内容量。"""

    query = TroopRecruitment.objects.filter(
        manor_id=manor.pk,
        status=TroopRecruitment.Status.RECRUITING,
    )
    if exclude_recruitment_id is not None:
        query = query.exclude(pk=int(exclude_recruitment_id))
    reserved = query.aggregate(total=Sum("quantity")).get("total") or 0
    return max(0, int(reserved))


def get_manor_troop_remaining_space(
    manor: Manor,
    *,
    include_pending_recruitment: bool = True,
    exclude_recruitment_id: int | None = None,
) -> int:
    """返回在不突破庄园内 5000 上限前还可增加的护院数量。"""

    reserved = (
        get_pending_troop_recruitment_space(
            manor,
            exclude_recruitment_id=exclude_recruitment_id,
        )
        if include_pending_recruitment
        else 0
    )
    return max(0, MANOR_TROOP_CAPACITY - get_manor_troop_used_space(manor) - reserved)


def ensure_manor_troop_capacity_locked(
    manor: Manor,
    quantity: int,
    *,
    exclude_recruitment_id: int | None = None,
) -> int:
    """在调用方已锁定 Manor 的事务内校验新增护院容量。

    返回校验后的剩余空间。Manor 锁是跨兵种行聚合容量检查的并发边界；
    调用方不得在未锁定 Manor 时把本函数当作写入保护使用。
    """

    normalized_quantity = int(quantity)
    if normalized_quantity <= 0:
        return get_manor_troop_remaining_space(
            manor,
            exclude_recruitment_id=exclude_recruitment_id,
        )

    remaining = get_manor_troop_remaining_space(
        manor,
        exclude_recruitment_id=exclude_recruitment_id,
    )
    if normalized_quantity > remaining:
        raise TroopCapacityFullError(
            required=normalized_quantity,
            available=remaining,
            capacity=MANOR_TROOP_CAPACITY,
        )
    return remaining - normalized_quantity


__all__ = [
    "MANOR_TROOP_CAPACITY",
    "TROOP_BANK_CAPACITY",
    "ensure_manor_troop_capacity_locked",
    "get_manor_troop_remaining_space",
    "get_manor_troop_used_space",
    "get_pending_troop_recruitment_space",
]
