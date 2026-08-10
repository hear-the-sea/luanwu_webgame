"""
门客招募系统服务。

保留历史导入入口，同时将查询/门客创建与结算逻辑拆分到专门模块，
降低单文件复杂度并保持现有 monkeypatch/导入兼容性。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, List

from django.db import transaction
from django.utils import timezone

from core.exceptions import InsufficientStockError, RecruitmentError, RecruitmentItemOwnershipError

if TYPE_CHECKING:
    from gameplay.models import Manor

from gameplay.services.action_points import consume_action_points_for_expedition
from gameplay.services.inventory import core as inventory_core
from gameplay.services.resources import spend_resources

from ..models import GuestRecruitment, RecruitmentCandidate, RecruitmentPool
from ..utils.name_generator import generate_random_name
from . import recruitment_candidates as _recruitment_candidates
from . import recruitment_flow as _recruitment_flow
from . import recruitment_followups as _recruitment_followups
from . import recruitment_queries as _recruitment_queries
from . import recruitment_quota as _recruitment_quota
from . import recruitment_shared as _recruitment_shared
from . import recruitment_templates as _recruitment_templates

logger = logging.getLogger(__name__)


def reveal_candidate_rarity(manor: Manor) -> int:
    """使用放大镜显示所有候选门客的稀有度。"""
    candidates = manor.candidates.filter(rarity_revealed=False)
    count = candidates.update(rarity_revealed=True)
    if count > 0:
        _recruitment_shared.invalidate_recruitment_hall_cache(getattr(manor, "id", None))
    return count


@transaction.atomic
def use_magnifying_glass_for_candidates(manor: Manor, item_id: int) -> int:
    """
    使用放大镜显示所有未显现候选门客稀有度（原子化版本）。

    关键保证：
    - 稀有度显现与道具扣减在同一事务中完成
    - 任一步失败都会整体回滚，避免“显现成功但扣道具失败”导致可重复白嫖
    - 锁顺序统一为 Manor -> InventoryItem -> RecruitmentCandidate
    """
    from gameplay.models import InventoryItem
    from gameplay.models import Manor as ManorModel

    ManorModel.objects.select_for_update().get(pk=manor.pk)

    locked_item = (
        InventoryItem.objects.select_for_update()
        .select_related("template")
        .filter(
            pk=item_id,
            manor=manor,
            template__key="fangdajing",
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .first()
    )
    if not locked_item:
        raise RecruitmentItemOwnershipError()
    if locked_item.quantity <= 0:
        raise InsufficientStockError(locked_item.template.name, 1, locked_item.quantity)

    count = manor.candidates.filter(rarity_revealed=False).update(rarity_revealed=True)
    if count <= 0:
        return 0

    inventory_core.consume_inventory_item_locked(locked_item, 1)
    _recruitment_shared.invalidate_recruitment_hall_cache(getattr(manor, "id", None))
    return int(count)


@transaction.atomic
def recruit_guest(manor: Manor, pool: RecruitmentPool, seed: int | None = None) -> List[RecruitmentCandidate]:
    """
    从指定卡池招募门客。

    如果卡池未配置 entries，会从所有 recruitable=True 的模板中按稀有度随机选择。
    """
    cost = _recruitment_flow.resolve_recruitment_cost(pool)
    if cost:
        from gameplay.models import ResourceEvent

        spend_resources(
            manor,
            cost,
            note=f"卡池：{pool.name}",
            reason=ResourceEvent.Reason.RECRUIT_COST,
        )

    return _build_recruitment_candidates(
        manor,
        pool,
        seed=seed,
        total_draw_count=_recruitment_candidates.resolve_candidate_draw_count(
            pool=pool, manor=manor, total_draw_count=None
        ),
        clear_existing=True,
    )


def _build_recruitment_candidates(
    manor: Manor,
    pool: RecruitmentPool,
    *,
    seed: int | None = None,
    total_draw_count: int | None = None,
    clear_existing: bool = True,
) -> List[RecruitmentCandidate]:
    """生成候选门客（不处理资源扣除）。"""
    if clear_existing:
        _recruitment_flow.clear_manor_candidates(manor)

    context = _recruitment_candidates.load_candidate_generation_context(
        manor=manor,
        pool=pool,
        seed=seed,
        total_draw_count=total_draw_count,
        get_recruitable_templates_by_rarity=_recruitment_templates._get_recruitable_templates_by_rarity,
        get_hermit_templates=_recruitment_templates._get_hermit_templates,
        get_excluded_template_ids=_recruitment_queries.get_excluded_template_ids,
    )
    candidates_to_create = _recruitment_candidates.build_candidate_batch(
        manor=manor,
        pool=pool,
        pool_entries=context["pool_entries"],
        resolved_draw_count=context["resolved_draw_count"],
        excluded_ids=context["excluded_ids"],
        rng=context["rng"],
        choose_template_from_entries=_recruitment_templates.choose_template_from_entries,
        templates_by_rarity=context["templates_by_rarity"],
        hermit_templates=context["hermit_templates"],
        generate_random_name=generate_random_name,
        non_repeatable_rarities=_recruitment_shared.NON_REPEATABLE_RARITIES,
    )

    return _recruitment_candidates.persist_candidate_batch(
        recruitment_candidate_model=RecruitmentCandidate,
        manor=manor,
        candidates_to_create=candidates_to_create,
        invalidate_cache=_recruitment_shared.invalidate_recruitment_hall_cache,
    )


def _schedule_guest_recruitment_completion(recruitment: GuestRecruitment, eta_seconds: int) -> None:
    """调度门客招募完成任务。"""
    _recruitment_followups.schedule_guest_recruitment_completion(recruitment, eta_seconds, logger=logger)


def _mark_recruitment_failed_locked(recruitment: GuestRecruitment, *, current_time: datetime, reason: str) -> None:
    _recruitment_flow.mark_recruitment_failed_locked(
        recruitment,
        current_time=current_time,
        reason=reason,
        invalidate_cache=_recruitment_shared.invalidate_recruitment_hall_cache,
    )


def _mark_recruitment_completed_locked(
    recruitment: GuestRecruitment,
    *,
    current_time: datetime,
    result_count: int,
) -> None:
    _recruitment_flow.mark_recruitment_completed_locked(
        recruitment,
        current_time=current_time,
        result_count=result_count,
        invalidate_cache=_recruitment_shared.invalidate_recruitment_hall_cache,
    )


def _send_recruitment_completion_notification(
    *,
    manor: Manor,
    pool: RecruitmentPool,
    candidate_count: int,
    recruitment_id: int | None = None,
) -> None:
    _recruitment_followups.send_recruitment_completion_notification(
        manor=manor,
        pool=pool,
        candidate_count=candidate_count,
        logger=logger,
        recruitment_id=recruitment_id,
    )


@transaction.atomic
def start_guest_recruitment(manor: Manor, pool: RecruitmentPool, seed: int | None = None) -> GuestRecruitment:
    """启动异步门客招募：立即扣资源，进入倒计时，完成后生成候选。"""
    from gameplay.models import Manor as ManorModel
    from gameplay.models import ResourceEvent

    locked_manor = ManorModel.objects.select_for_update().get(pk=manor.pk)

    current_time = timezone.now()
    _recruitment_flow.validate_recruitment_start_allowed(
        locked_manor=locked_manor,
        pool=pool,
        current_time=current_time,
        has_active_guest_recruitment=_recruitment_queries.has_active_guest_recruitment,
        daily_limit=(
            _recruitment_queries._get_pool_daily_draw_limit()
            + _recruitment_quota.get_recruitment_extra_attempts(locked_manor, pool)
        ),
        count_pool_draws_today=_recruitment_queries._count_pool_draws_today,
    )

    resolved_seed = _recruitment_flow.resolve_recruitment_seed(seed)
    draw_count = _recruitment_candidates.resolve_candidate_draw_count(
        pool=pool, manor=locked_manor, total_draw_count=None
    )
    duration_seconds = _recruitment_queries.get_pool_recruitment_duration_seconds(pool)
    cost = _recruitment_flow.resolve_recruitment_cost(pool)

    consume_action_points_for_expedition(locked_manor)

    _recruitment_flow.spend_recruitment_cost_if_needed(
        manor=locked_manor,
        cost=cost,
        pool_name=pool.name,
        spend_resources=spend_resources,
        recruit_cost_reason=ResourceEvent.Reason.RECRUIT_COST,
    )

    _recruitment_flow.clear_manor_candidates(locked_manor)

    recruitment = _recruitment_flow.create_pending_recruitment(
        recruitment_model=GuestRecruitment,
        manor=locked_manor,
        pool=pool,
        current_time=current_time,
        cost=cost,
        draw_count=draw_count,
        duration_seconds=duration_seconds,
        seed=resolved_seed,
    )
    _schedule_guest_recruitment_completion(recruitment, duration_seconds)
    _recruitment_shared.invalidate_recruitment_hall_cache(getattr(locked_manor, "id", None))
    return recruitment


def finalize_guest_recruitment(
    recruitment: GuestRecruitment,
    *,
    now: datetime | None = None,
    send_notification: bool = False,
) -> bool:
    """完成门客招募：生成候选并更新队列状态。"""
    recruitment_id = getattr(recruitment, "pk", None)
    if recruitment_id is None or isinstance(recruitment_id, bool):
        raise AssertionError("finalize_guest_recruitment requires a persisted recruitment")
    try:
        recruitment_id = int(recruitment_id)
    except (TypeError, ValueError) as exc:
        raise AssertionError("finalize_guest_recruitment requires a persisted recruitment") from exc
    if recruitment_id <= 0:
        raise AssertionError("finalize_guest_recruitment requires a persisted recruitment")

    current_time = now or timezone.now()
    if getattr(recruitment, "source", GuestRecruitment.Source.PLAYER) == GuestRecruitment.Source.VIRTUAL:
        from gameplay.services.virtual_player_core.recruitment import finalize_virtual_guest_recruitment

        return finalize_virtual_guest_recruitment(recruitment_id, now=current_time)

    manor: Manor | None = None
    pool: RecruitmentPool | None = None
    candidate_count = 0

    with transaction.atomic():
        from gameplay.models import Manor as ManorModel

        locked = (
            GuestRecruitment.objects.select_for_update()
            .select_related("manor", "manor__user", "pool")
            .filter(pk=recruitment_id)
            .first()
        )
        if not locked or locked.status != GuestRecruitment.Status.PENDING:
            return False
        if locked.complete_at > current_time:
            return False
        if not locked.pool_id:
            _mark_recruitment_failed_locked(locked, current_time=current_time, reason="招募卡池不存在")
            return False
        ManorModel.objects.select_for_update().filter(pk=locked.manor_id).exists()
        locked_pool = locked.pool
        if locked_pool is None:
            _mark_recruitment_failed_locked(locked, current_time=current_time, reason="招募卡池不存在")
            return False

        try:
            candidates = _build_recruitment_candidates(
                locked.manor,
                locked_pool,
                seed=locked.seed,
                total_draw_count=locked.draw_count,
                clear_existing=True,
            )
        except RecruitmentError as exc:
            logger.warning(
                "Guest recruitment finalized as FAILED due to known recruitment error: recruitment_id=%s error=%s",
                locked.id,
                exc,
            )
            _mark_recruitment_failed_locked(locked, current_time=current_time, reason=str(exc))
            return False

        _mark_recruitment_completed_locked(locked, current_time=current_time, result_count=len(candidates))
        manor = locked.manor
        pool = locked.pool
        candidate_count = len(candidates)

    if send_notification and pool and manor is not None:
        _send_recruitment_completion_notification(
            manor=manor,
            pool=pool,
            candidate_count=candidate_count,
            recruitment_id=recruitment_id,
        )
    return True


def refresh_guest_recruitments(manor: Manor, limit: int = 20) -> int:
    """兜底刷新门客招募状态（用于 worker 中断场景）。"""
    if limit is None or isinstance(limit, bool):
        raise AssertionError(f"invalid guest recruitment refresh limit: {limit!r}")
    try:
        resolved_limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"invalid guest recruitment refresh limit: {limit!r}") from exc
    if resolved_limit <= 0:
        raise AssertionError(f"invalid guest recruitment refresh limit: {limit!r}")

    now = timezone.now()
    completed = 0
    recruitments = (
        manor.guest_recruitments.filter(status=GuestRecruitment.Status.PENDING, complete_at__lte=now)
        .select_related("pool", "manor", "manor__user")
        .order_by("complete_at")[:resolved_limit]
    )
    for recruitment in recruitments:
        if finalize_guest_recruitment(recruitment, now=now, send_notification=True):
            completed += 1
    return completed


__all__ = [
    "_build_recruitment_candidates",
    "finalize_guest_recruitment",
    "recruit_guest",
    "refresh_guest_recruitments",
    "reveal_candidate_rarity",
    "start_guest_recruitment",
    "use_magnifying_glass_for_candidates",
]
