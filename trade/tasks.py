"""
商铺和拍卖行定时任务
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from common.utils.celery import safe_apply_async
from core.utils.infrastructure import DATABASE_INFRASTRUCTURE_EXCEPTIONS
from core.utils.task_monitoring import increment_degraded_counter
from trade.models import ShopStock
from trade.services.shop_config import get_shop_config, reload_shop_config

logger = logging.getLogger(__name__)


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_non_negative_int(value, default: int = 0) -> int:
    parsed = _safe_int(value, default)
    return parsed if parsed >= 0 else default


def _normalize_settlement_stats(stats) -> tuple[int, int, int, int]:
    if not isinstance(stats, dict):
        logger.warning("settle_auction_round returned non-dict stats: %r", stats)
        return 0, 0, 0, 0

    settled = _coerce_non_negative_int(stats.get("settled", 0), 0)
    sold = _coerce_non_negative_int(stats.get("sold", 0), 0)
    unsold = _coerce_non_negative_int(stats.get("unsold", 0), 0)
    total_gold_bars = _coerce_non_negative_int(stats.get("total_gold_bars", 0), 0)
    return settled, sold, unsold, total_gold_bars


@shared_task(name="trade.refresh_shop_stock", bind=True, max_retries=2, default_retry_delay=60)
def refresh_shop_stock(self):
    """
    每日刷新商铺库存

    对于配置了 daily_refresh: true 的商品，
    将其库存重置为 YAML 配置的初始值。
    """
    try:
        # 重新加载配置（清除缓存）
        reload_shop_config()
        config_list = get_shop_config()

        today = timezone.now().date()
        refreshed_count = 0
        failed_items = []

        for item in config_list:
            item_key = str(getattr(item, "item_key", "") or "").strip()
            daily_refresh = bool(getattr(item, "daily_refresh", False))
            stock = _coerce_non_negative_int(getattr(item, "stock", 0), 0)

            # 只刷新设置了 daily_refresh 且有限库存的商品
            if not item_key:
                logger.warning("Skip refresh shop stock for invalid item config: %r", item)
                continue
            if daily_refresh and stock > 0:
                try:
                    ShopStock.objects.update_or_create(
                        item_key=item_key,
                        defaults={"current_stock": stock, "last_refresh": today},
                    )
                    refreshed_count += 1
                except DATABASE_INFRASTRUCTURE_EXCEPTIONS as exc:
                    # DB/connection errors on a single item should not abort the
                    # entire refresh; log and continue with the remaining items.
                    logger.exception("Failed to refresh stock for item %s: %s", item_key, exc)
                    failed_items.append({"item_key": item_key, "error": str(exc)})

        # 记录失败统计，便于监控告警
        if failed_items:
            failed_item_keys = [failed_item["item_key"] for failed_item in failed_items]
            logger.error(
                "batch partial failure",
                extra={
                    "task": "trade.refresh_shop_stock",
                    "failed_ids": failed_item_keys,
                    "degraded": True,
                },
            )
            increment_degraded_counter("shop_stock")
            logger.error(
                "Shop stock refresh completed with %s failures: %s",
                len(failed_items),
                failed_item_keys,
            )
            return (
                f"refreshed {refreshed_count} items, {len(failed_items)} failed, "
                f"failed_item_keys={failed_item_keys}"
            )

        return f"refreshed {refreshed_count} items"
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.exception("Failed to refresh shop stock: %s", exc)
        raise self.retry(exc=exc)


@shared_task(name="trade.process_expired_listings", bind=True, max_retries=2, default_retry_delay=60)
def process_expired_listings(self):
    """
    Scan-fallback: process expired market listings.

    Primary path: listings are expired individually when their expiry time is
    reached via scheduled per-listing Celery tasks.

    This periodic scan (recommended every 10 minutes) acts as a fallback to
    catch listings whose individual expiry tasks were lost, delayed, or failed
    due to broker restarts, worker crashes, or transient infrastructure errors.
    It compensates for the unreliable at-most-once delivery guarantee of the
    Celery broker by sweeping up any missed expirations in bulk.
    """
    try:
        from trade.services.market_service import expire_listings

        count = _coerce_non_negative_int(expire_listings(), 0)
        return f"处理了 {count} 个过期挂单"
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.exception("Failed to process expired listings: %s", exc)
        raise self.retry(exc=exc)


# ============ 拍卖行定时任务 ============


@shared_task(name="trade.settle_auction_round", bind=True, max_retries=2, default_retry_delay=60)
def settle_auction_round_task(self):
    """
    结算到期的拍卖轮次

    由 Celery Beat 每 5 分钟检查一次，如果有到期的轮次则进行结算：
    - 中标者：消耗冻结金条，默认通过站内信附件发放物品
      - 若消息创建失败且属于受控消息/数据库基础设施异常，则降级为直接入仓库
    - 落选者：金条已在出价时即时退还
    - 流拍商品：标记状态，不重新上架

    结算完成后自动触发创建新轮次任务。
    """
    try:
        from trade.services.auction_service import create_auction_round, settle_auction_round

        settled, sold, unsold, total_gold_bars = _normalize_settlement_stats(settle_auction_round())

        if settled > 0:
            # 结算完成后触发创建新轮次
            dispatched = safe_apply_async(
                create_auction_round_task,
                logger=logger,
                log_message="拍卖结算后触发新轮次任务失败，立即切换为同步创建",
            )
            if not dispatched:
                try:
                    create_auction_round()
                except DATABASE_INFRASTRUCTURE_EXCEPTIONS as fallback_exc:
                    logger.warning("拍卖结算后同步创建新轮次失败: %s", fallback_exc, exc_info=True)
            logger.info(f"拍卖轮次结算完成：售出 {sold} 件，" f"流拍 {unsold} 件，" f"共收取 {total_gold_bars} 金条")
            return f"结算完成：售出 {sold} 件，流拍 {unsold} 件，" f"共 {total_gold_bars} 金条"
        else:
            return "没有需要结算的拍卖轮次"
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.exception("结算拍卖轮次失败: %s", exc)
        raise self.retry(exc=exc)


@shared_task(name="trade.create_auction_round", bind=True, max_retries=2, default_retry_delay=60)
def create_auction_round_task(self):
    """
    创建新的拍卖轮次

    在以下情况触发：
    1. 结算完成后自动触发
    2. 每天0:10的定时检查（防止遗漏）

    如果已存在进行中的轮次，则不会创建新轮次。
    """
    try:
        from .services.auction_config import reload_auction_config
        from .services.auction_service import create_auction_round

        # 重新加载配置（清除缓存，确保获取最新配置）
        reload_auction_config()

        auction_round = create_auction_round()

        if auction_round:
            round_number = _safe_int(getattr(auction_round, "round_number", 0), 0)
            try:
                slot_count = _coerce_non_negative_int(auction_round.slots.count(), 0)
            except DATABASE_INFRASTRUCTURE_EXCEPTIONS as exc:
                # Slot count is informational; degrade to 0 on infra errors.
                logger.warning(
                    "读取拍卖轮次槽位数失败: round=%s error=%s",
                    round_number,
                    exc,
                    exc_info=True,
                )
                slot_count = 0
            logger.info(f"创建拍卖轮次 #{round_number}，" f"共 {slot_count} 个拍卖位")
            return f"创建拍卖轮次 #{round_number}，" f"拍卖位数量: {slot_count}"
        else:
            return "已有进行中的拍卖轮次或无可用商品，跳过创建"
    except DATABASE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.exception("创建拍卖轮次失败: %s", exc)
        raise self.retry(exc=exc)
