"""
门客工资支付服务

性能优化说明：
- 使用 bulk_check_salary_paid() 替代循环调用 check_salary_paid()
- 一次查询获取所有已支付记录，避免 N+1 问题
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Dict, List, Sequence, Set

from django.db import transaction
from django.db.models import prefetch_related_objects
from django.utils import timezone

from core.exceptions import GuestOwnershipError, InsufficientResourceError, NoGuestsError, SalaryAlreadyPaidError
from guests.guest_upkeep_rules import get_guest_salary_for_rarity
from guests.models import Guest, SalaryPayment

if TYPE_CHECKING:
    from gameplay.models import Manor


@dataclass(frozen=True, slots=True)
class SalaryBatchQuote:
    for_date: date
    guest_ids: tuple[int, ...]
    unpaid_guest_ids: tuple[int, ...]
    total_amount: int


def get_guest_salary(guest: Guest) -> int:
    """
    获取门客的工资金额

    Args:
        guest: 门客实例

    Returns:
        工资金额
    """
    return get_guest_salary_for_rarity(guest.rarity)


def check_salary_paid(guest: Guest, for_date: date = None) -> bool:
    """
    检查门客是否已支付今日工资

    注意：此函数会产生一次数据库查询。如果需要批量检查，
    请使用 bulk_check_salary_paid() 以避免 N+1 问题。

    Args:
        guest: 门客实例
        for_date: 检查日期，默认为今天

    Returns:
        是否已支付
    """
    if for_date is None:
        for_date = timezone.localdate()

    return SalaryPayment.objects.filter(guest=guest, for_date=for_date).exists()


def bulk_check_salary_paid(guest_ids: List[int], for_date: date = None) -> Set[int]:
    """
    批量检查门客是否已支付工资（优化 N+1 查询）

    Args:
        guest_ids: 门客ID列表
        for_date: 检查日期，默认为今天

    Returns:
        已支付工资的门客ID集合
    """
    if for_date is None:
        for_date = timezone.localdate()

    if not guest_ids:
        return set()

    paid_guest_ids = SalaryPayment.objects.filter(guest_id__in=guest_ids, for_date=for_date).values_list(
        "guest_id", flat=True
    )

    return set(paid_guest_ids)


def _quote_salary_batch(
    guests: list[Guest],
    *,
    for_date: date,
    paid_guest_ids: Set[int] | None = None,
) -> SalaryBatchQuote:
    guest_ids = tuple(int(guest.id) for guest in guests)
    paid_ids = bulk_check_salary_paid(list(guest_ids), for_date) if paid_guest_ids is None else set(paid_guest_ids)
    unpaid_guests = [guest for guest in guests if guest.id not in paid_ids]
    return SalaryBatchQuote(
        for_date=for_date,
        guest_ids=guest_ids,
        unpaid_guest_ids=tuple(int(guest.id) for guest in unpaid_guests),
        total_amount=sum(get_guest_salary(guest) for guest in unpaid_guests),
    )


def quote_all_salaries(
    manor: Manor,
    for_date: date | None = None,
    *,
    guests: Sequence[Guest] | None = None,
    paid_guest_ids: Set[int] | None = None,
) -> SalaryBatchQuote:
    """只读计算指定日期的整庄园工资。"""
    salary_date = for_date or timezone.localdate()
    resolved_guests = (
        list(guests)
        if guests is not None
        else list(Guest.objects.filter(manor=manor).select_related("template").order_by("id"))
    )
    if any(int(guest.manor_id) != int(manor.id) for guest in resolved_guests):
        raise GuestOwnershipError(message="门客不存在或不属于该庄园")
    return _quote_salary_batch(
        resolved_guests,
        for_date=salary_date,
        paid_guest_ids=paid_guest_ids,
    )


def _load_locked_salary_roster(manor: Manor) -> list[Guest]:
    """在 Manor 父行已锁后，按 ID 锁定当前完整门客名单。

    Manor 锁通过外键校验串行化新增，Guest 行锁串行化已有成员的删除与变更。
    """
    guests = list(Guest.objects.select_for_update().filter(manor_id=manor.pk).order_by("id"))
    prefetch_related_objects(guests, "template")
    return guests


@transaction.atomic
def pay_guest_salary(manor: Manor, guest: Guest, for_date: date = None) -> SalaryPayment:
    """
    支付单个门客的工资

    Args:
        manor: 庄园
        guest: 门客
        for_date: 支付日期，默认为今天

    Returns:
        工资支付记录

    Raises:
        GameError: 业务验证失败时抛出显式异常
    """
    from gameplay.models import Manor

    if for_date is None:
        for_date = timezone.localdate()

    # Concurrency safety:
    # - Lock manor row to serialize with pay_all_salaries()
    # - Lock guest row to serialize per-guest salary payments
    manor_locked = Manor.objects.select_for_update().get(pk=manor.pk)
    guest_locked = Guest.objects.select_for_update().get(pk=guest.pk)

    # 验证门客属于该庄园
    if guest_locked.manor_id != manor_locked.id:
        raise GuestOwnershipError(guest_locked)

    # 检查是否已支付
    if check_salary_paid(guest_locked, for_date):
        raise SalaryAlreadyPaidError(guest_locked)

    # 计算工资
    salary_amount = get_guest_salary(guest_locked)

    if manor_locked.silver < salary_amount:
        raise InsufficientResourceError("silver", salary_amount, manor_locked.silver)

    manor_locked.silver -= salary_amount
    manor_locked.save(update_fields=["silver"])

    # 创建支付记录
    payment = SalaryPayment.objects.create(
        manor=manor_locked, guest=guest_locked, amount=salary_amount, for_date=for_date
    )

    # Keep caller's instance reasonably up to date.
    manor.silver = manor_locked.silver

    return payment


def pay_all_salaries_locked(
    manor: Manor,
    for_date: date = None,
    *,
    _guests: Sequence[Guest] | None = None,
    _quote: SalaryBatchQuote | None = None,
    _locked_guests: Sequence[Guest] | None = None,
) -> Dict:
    """在调用方已锁定 Manor 行的事务内支付全部到期工资。"""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("pay_all_salaries_locked must be called inside transaction.atomic()")

    if for_date is None:
        for_date = timezone.localdate()

    if _guests is not None and _locked_guests is not None:
        raise ValueError("expected guests and locked guests are mutually exclusive")
    locked_guests = list(_locked_guests) if _locked_guests is not None else _load_locked_salary_roster(manor)
    if not locked_guests:
        raise NoGuestsError()

    locked_guest_ids = tuple(int(guest.id) for guest in locked_guests)
    if _guests is not None:
        expected_guests = list(_guests)
        if any(int(guest.manor_id) != int(manor.id) for guest in expected_guests):
            raise GuestOwnershipError(message="门客不存在或不属于该庄园")
        expected_guest_ids = tuple(int(guest.id) for guest in expected_guests)
        if expected_guest_ids != locked_guest_ids:
            raise ValueError("expected salary roster does not match locked roster")

    paid_guest_ids = (
        set(_quote.guest_ids) - set(_quote.unpaid_guest_ids)
        if _locked_guests is not None and _quote is not None
        else None
    )
    quote = _quote_salary_batch(
        locked_guests,
        for_date=for_date,
        paid_guest_ids=paid_guest_ids,
    )
    if _quote is not None and _quote != quote:
        raise ValueError("frozen salary quote does not match locked salary state")
    unpaid_ids = set(quote.unpaid_guest_ids)
    unpaid_guests = [guest for guest in locked_guests if guest.id in unpaid_ids]

    if not unpaid_guests:
        raise SalaryAlreadyPaidError()

    total_salary = quote.total_amount
    available_silver = int(manor.silver or 0)
    if available_silver < total_salary:
        raise InsufficientResourceError("silver", total_salary, available_silver)

    SalaryPayment.objects.bulk_create(
        [
            SalaryPayment(
                manor=manor,
                guest=guest,
                amount=get_guest_salary(guest),
                for_date=for_date,
            )
            for guest in unpaid_guests
        ]
    )
    manor.silver = available_silver - total_salary
    manor.save(update_fields=["silver"])

    return {
        "paid_count": len(unpaid_guests),
        "total_amount": total_salary,
        "guest_names": [guest.display_name for guest in unpaid_guests],
    }


@transaction.atomic
def pay_all_salaries(manor: Manor, for_date: date = None) -> Dict:
    """
    一键支付所有门客工资

    Args:
        manor: 庄园
        for_date: 支付日期，默认为今天

    Returns:
        支付结果字典

    Raises:
        GameError: 业务验证失败时抛出显式异常
    """
    from gameplay.models import Manor

    manor_locked = Manor.objects.select_for_update().get(pk=manor.pk)
    result = pay_all_salaries_locked(manor_locked, for_date=for_date)
    manor.silver = manor_locked.silver
    return result


def get_unpaid_guests(manor: Manor, for_date: date = None) -> List[Guest]:
    """
    获取未支付工资的门客列表

    Args:
        manor: 庄园
        for_date: 检查日期，默认为今天

    Returns:
        未支付工资的门客列表
    """
    if for_date is None:
        for_date = timezone.localdate()

    guests = list(Guest.objects.filter(manor=manor).select_related("template"))

    if not guests:
        return []

    # 批量查询已支付记录（优化 N+1）
    guest_ids = [g.id for g in guests]
    paid_ids = bulk_check_salary_paid(guest_ids, for_date)

    # 筛选未支付工资的门客
    return [g for g in guests if g.id not in paid_ids]
