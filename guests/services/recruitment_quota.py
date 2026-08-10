from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any, Iterable

from django.db import IntegrityError, transaction
from django.utils import timezone

from gameplay.services.inventory import core as inventory_core

from ..models import RecruitmentExtraAttempt, RecruitmentPool

if TYPE_CHECKING:
    from gameplay.models import Manor


RECRUITMENT_CARD_KEY = "recruitment_card"


def _resolve_non_negative_int(raw_value: Any, *, field_name: str) -> int:
    if raw_value is None or isinstance(raw_value, bool):
        raise AssertionError(f"invalid recruitment {field_name}: {raw_value!r}")
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid recruitment {field_name}: {raw_value!r}") from exc
    if value < 0:
        raise AssertionError(f"invalid recruitment {field_name}: {raw_value!r}")
    return value


def _resolve_positive_int(raw_value: object, *, field_name: str) -> int:
    value = _resolve_non_negative_int(raw_value, field_name=field_name)
    if value <= 0:
        raise AssertionError(f"invalid recruitment {field_name}: {raw_value!r}")
    return value


def _next_extra_count(current: object, increment: int) -> int:
    current_count = _resolve_non_negative_int(current, field_name="extra attempts")
    return current_count + increment


def get_recruitment_extra_attempts(
    manor: Manor,
    pool: RecruitmentPool,
    *,
    target_date: date | None = None,
) -> int:
    current_date = target_date or timezone.localdate()
    extra = RecruitmentExtraAttempt.objects.filter(manor=manor, pool=pool, date=current_date).first()
    return _resolve_non_negative_int(extra.extra_count if extra else 0, field_name="extra attempts")


def bulk_get_recruitment_extra_attempts(
    manor: Manor,
    pools: Iterable[RecruitmentPool],
    *,
    target_date: date | None = None,
) -> dict[int, int]:
    pool_list = list(pools)
    pool_ids = [int(pool.pk) for pool in pool_list if pool.pk]
    result = {pool_id: 0 for pool_id in pool_ids}
    if not pool_ids:
        return result

    current_date = target_date or timezone.localdate()
    extras = RecruitmentExtraAttempt.objects.filter(manor=manor, pool_id__in=pool_ids, date=current_date).values(
        "pool_id", "extra_count"
    )
    for extra in extras:
        result[int(extra["pool_id"])] = _resolve_non_negative_int(extra["extra_count"], field_name="extra attempts")
    return result


def add_recruitment_extra_attempt(
    manor: Manor,
    pool: RecruitmentPool,
    *,
    count: int = 1,
    target_date: date | None = None,
) -> int:
    increment = _resolve_positive_int(count, field_name="extra attempt count")
    current_date = target_date or timezone.localdate()

    with transaction.atomic():
        extra = (
            RecruitmentExtraAttempt.objects.select_for_update()
            .filter(manor=manor, pool=pool, date=current_date)
            .first()
        )
        if extra:
            extra.extra_count = _next_extra_count(extra.extra_count, increment)
            extra.save(update_fields=["extra_count", "updated_at"])
            return extra.extra_count

    try:
        with transaction.atomic():
            extra = RecruitmentExtraAttempt.objects.create(
                manor=manor,
                pool=pool,
                date=current_date,
                extra_count=increment,
            )
            return extra.extra_count
    except IntegrityError:
        with transaction.atomic():
            extra = RecruitmentExtraAttempt.objects.select_for_update().get(
                manor=manor,
                pool=pool,
                date=current_date,
            )
            extra.extra_count = _next_extra_count(extra.extra_count, increment)
            extra.save(update_fields=["extra_count", "updated_at"])
            return extra.extra_count


def add_recruitment_extra_attempt_with_item_cost(
    manor: Manor,
    pool: RecruitmentPool,
    *,
    count: int = 1,
    target_date: date | None = None,
) -> int:
    increment = _resolve_positive_int(count, field_name="extra attempt count")
    with transaction.atomic():
        inventory_core.consume_inventory_item_for_manor_locked(manor, RECRUITMENT_CARD_KEY, increment)
        return add_recruitment_extra_attempt(
            manor,
            pool,
            count=increment,
            target_date=target_date,
        )


__all__ = [
    "RECRUITMENT_CARD_KEY",
    "add_recruitment_extra_attempt",
    "add_recruitment_extra_attempt_with_item_cost",
    "bulk_get_recruitment_extra_attempts",
    "get_recruitment_extra_attempts",
]
