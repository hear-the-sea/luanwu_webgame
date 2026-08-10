from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Iterable

from django.db.models import Count, QuerySet
from django.utils import timezone

from core.config import RECRUITMENT
from core.utils.time_scale import scale_duration

from ..models import Guest, GuestRarity, GuestRecruitment, RecruitmentCandidate, RecruitmentPool
from ..query_utils import guest_template_rarity_rank_case
from .recruitment_shared import CORE_POOL_TIERS, NON_REPEATABLE_RARITIES

if TYPE_CHECKING:
    from gameplay.models import Manor


def get_excluded_template_ids(manor: Manor) -> set[int]:
    """获取玩家不能再招募的门客模板ID。"""
    owned_templates = manor.guests.values_list("template_id", "template__rarity", "template__is_hermit")

    excluded = set()
    for template_id, rarity, is_hermit in owned_templates:
        if rarity in NON_REPEATABLE_RARITIES:
            excluded.add(template_id)
        elif rarity == GuestRarity.BLACK and is_hermit:
            excluded.add(template_id)

    return excluded


def list_pools(core_only: bool = False, *, include_entries: bool = True) -> Iterable[RecruitmentPool]:
    """列出所有招募卡池，按级别从高到低排序（殿试->村募）。"""
    qs = RecruitmentPool.objects.all()
    if include_entries:
        qs = qs.prefetch_related("entries__template")
    if core_only:
        qs = qs.filter(tier__in=CORE_POOL_TIERS)

    tier_priority = {
        RecruitmentPool.Tier.DIANSHI: 0,
        RecruitmentPool.Tier.HUISHI: 1,
        RecruitmentPool.Tier.XIANGSHI: 2,
        RecruitmentPool.Tier.CUNMU: 3,
    }

    pools = list(qs)
    pools.sort(key=lambda pool: tier_priority.get(pool.tier, 99))  # type: ignore[call-overload]
    return pools


def get_pool_recruitment_duration_seconds(pool: RecruitmentPool) -> int:
    """获取卡池招募倒计时秒数（仅使用 YAML/数据库配置并应用全局时间倍率）。"""
    raw_seconds = getattr(pool, "cooldown_seconds", None)
    if raw_seconds is None or isinstance(raw_seconds, bool):
        raise AssertionError(f"invalid recruitment cooldown: {raw_seconds!r}")
    try:
        base_seconds = int(raw_seconds)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid recruitment cooldown: {raw_seconds!r}") from exc
    if base_seconds <= 0:
        raise AssertionError(f"invalid recruitment cooldown: {raw_seconds!r}")
    return scale_duration(base_seconds, minimum=1)


def _get_pool_daily_draw_limit() -> int:
    """获取单卡池每日招募上限。"""
    raw_value = getattr(RECRUITMENT, "DAILY_POOL_DRAW_LIMIT", None)
    if raw_value is None or isinstance(raw_value, bool):
        raise AssertionError(f"invalid recruitment daily limit: {raw_value!r}")
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid recruitment daily limit: {raw_value!r}") from exc
    if value <= 0:
        raise AssertionError(f"invalid recruitment daily limit: {raw_value!r}")
    return value


def _count_pool_draws_today(manor_id: int, pool_id: int, *, now: datetime | None = None) -> int:
    """统计指定庄园今日对指定卡池已发起的招募次数。"""
    current_time = now or timezone.now()
    local_now = timezone.localtime(current_time)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    valid_statuses = (GuestRecruitment.Status.PENDING, GuestRecruitment.Status.COMPLETED)
    return GuestRecruitment.objects.filter(
        manor_id=manor_id,
        pool_id=pool_id,
        status__in=valid_statuses,
        started_at__gte=day_start,
        started_at__lt=day_end,
    ).count()


def bulk_count_pool_draws_today(
    manor_id: int,
    pool_ids: Iterable[int],
    *,
    now: datetime | None = None,
) -> dict[int, int]:
    """批量统计庄园今日各卡池已发起的招募次数。"""
    normalized_pool_ids = [int(pool_id) for pool_id in pool_ids]
    result = {pool_id: 0 for pool_id in normalized_pool_ids}
    if not normalized_pool_ids:
        return result

    current_time = now or timezone.now()
    local_now = timezone.localtime(current_time)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    valid_statuses = (GuestRecruitment.Status.PENDING, GuestRecruitment.Status.COMPLETED)
    rows = (
        GuestRecruitment.objects.filter(
            manor_id=manor_id,
            pool_id__in=normalized_pool_ids,
            status__in=valid_statuses,
            started_at__gte=day_start,
            started_at__lt=day_end,
        )
        .values("pool_id")
        .annotate(count=Count("id"))
    )
    for row in rows:
        result[int(row["pool_id"])] = int(row["count"])
    return result


def has_active_guest_recruitment(manor: Manor) -> bool:
    """是否存在进行中的门客招募。"""
    return manor.guest_recruitments.filter(status=GuestRecruitment.Status.PENDING).exists()


def get_active_guest_recruitment(manor: Manor) -> GuestRecruitment | None:
    """获取最早完成的一条进行中门客招募。"""
    return (
        manor.guest_recruitments.filter(status=GuestRecruitment.Status.PENDING)
        .select_related("pool")
        .order_by("complete_at")
        .first()
    )


def available_guests(manor: Manor) -> QuerySet[Guest]:
    """获取庄园所有可用门客。"""
    return (
        manor.guests.select_related("template")
        .prefetch_related("gear_items__template")
        .annotate(_template_rarity_rank=guest_template_rarity_rank_case("template__rarity"))
        .order_by("-_template_rarity_rank", "-level")
    )


def list_candidates(manor: Manor) -> QuerySet[RecruitmentCandidate]:
    """列出庄园的招募候选门客。"""
    return manor.candidates.only("id", "display_name", "rarity", "rarity_revealed", "created_at").order_by("created_at")


__all__ = [
    "_count_pool_draws_today",
    "_get_pool_daily_draw_limit",
    "available_guests",
    "bulk_count_pool_draws_today",
    "get_active_guest_recruitment",
    "get_excluded_template_ids",
    "get_pool_recruitment_duration_seconds",
    "has_active_guest_recruitment",
    "list_candidates",
    "list_pools",
]
