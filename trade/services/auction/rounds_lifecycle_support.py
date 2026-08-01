"""Auction round lifecycle orchestration helpers."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any, Callable, Optional

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from core.utils.cache_lock import release_cache_key_if_owner
from gameplay.models import ItemTemplate
from trade.models import AuctionDelivery, AuctionRound, AuctionSlot
from trade.services.auction.constants import (
    AUCTION_CREATE_LOCK_KEY,
    AUCTION_CREATE_LOCK_TIMEOUT,
    AUCTION_SETTLE_LOCK_KEY,
    AUCTION_SETTLE_LOCK_TIMEOUT,
)
from trade.services.auction_config import AuctionItemConfig, AuctionSettings


def _release_lock_preserving_active_exception(
    lock_key: str,
    lock_token: str,
    *,
    logger: logging.Logger,
    log_context: str,
    active_exception: BaseException | None,
) -> None:
    if active_exception is None:
        release_cache_key_if_owner(
            lock_key,
            lock_token=lock_token,
            logger=logger,
            log_context=log_context,
        )
        return

    try:
        release_cache_key_if_owner(
            lock_key,
            lock_token=lock_token,
            logger=logger,
            log_context=log_context,
        )
    except Exception:
        logger.exception(
            "%s lock release failed while preserving active exception: %s",
            log_context,
            active_exception,
        )


def create_auction_round_impl(
    *,
    safe_cache_add_func: Callable[[str, object, int], bool],
    logger: logging.Logger,
    get_settings_func: Callable[[], AuctionSettings],
    get_enabled_items_func: Callable[[], list[AuctionItemConfig]],
) -> Optional[AuctionRound]:
    lock_value = str(uuid.uuid4())
    lock_acquired = safe_cache_add_func(AUCTION_CREATE_LOCK_KEY, lock_value, AUCTION_CREATE_LOCK_TIMEOUT)
    if not lock_acquired:
        logger.info("拍卖轮次创建锁未获取，跳过本次创建")
        return None

    active_exception: BaseException | None = None
    try:
        settings = get_settings_func()
        enabled_items = get_enabled_items_func()

        if not enabled_items:
            logger.warning("没有启用的拍卖商品，跳过创建轮次")
            return None

        now = timezone.now()
        end_at = now + timedelta(days=settings.cycle_days)

        with transaction.atomic():
            if (
                AuctionRound.objects.select_for_update()
                .filter(status__in=[AuctionRound.Status.ACTIVE, AuctionRound.Status.SETTLING])
                .exists()
            ):
                logger.info("已有进行中或结算中的拍卖轮次，跳过创建")
                return None

            last_round = AuctionRound.objects.select_for_update().order_by("-round_number").first()
            round_number = (last_round.round_number + 1) if last_round else 1

            item_keys = [item_config.item_key for item_config in enabled_items]
            from core.utils.template_loader import load_templates_by_key

            templates_map = load_templates_by_key(ItemTemplate, keys=item_keys)

            slots_to_create: list[dict[str, object]] = []
            for item_config in enabled_items:
                item_template = templates_map.get(item_config.item_key)
                if not item_template:
                    logger.warning("物品模板不存在: %s，跳过", item_config.item_key)
                    continue

                for slot_index in range(item_config.slots):
                    slots_to_create.append(
                        {
                            "item_template": item_template,
                            "quantity": item_config.quantity_per_slot,
                            "starting_price": item_config.starting_price,
                            "current_price": item_config.starting_price,
                            "min_increment": item_config.min_increment,
                            "status": AuctionSlot.Status.ACTIVE,
                            "config_key": item_config.item_key,
                            "slot_index": slot_index,
                        }
                    )

            if not slots_to_create:
                logger.warning("本轮拍卖没有可创建的拍卖位，跳过创建轮次")
                return None

            try:
                auction_round = AuctionRound.objects.create(
                    round_number=round_number,
                    status=AuctionRound.Status.ACTIVE,
                    start_at=now,
                    end_at=end_at,
                )
            except IntegrityError:
                logger.warning("创建拍卖轮次时发生编号冲突，跳过本次创建")
                return None

            AuctionSlot.objects.bulk_create(
                [AuctionSlot(round=auction_round, **slot_data) for slot_data in slots_to_create]
            )

            logger.info("创建拍卖轮次 #%d，共 %d 个拍卖位", round_number, len(slots_to_create))

        return auction_round
    except BaseException as exc:
        active_exception = exc
        raise
    finally:
        _release_lock_preserving_active_exception(
            AUCTION_CREATE_LOCK_KEY,
            lock_value,
            logger=logger,
            log_context="auction round creation",
            active_exception=active_exception,
        )


def settle_auction_round_impl(
    round_id: int | None = None,
    *,
    settle_slot_func: Callable[[AuctionSlot], dict[str, object]],
    safe_cache_add_func: Callable[[str, object, int], bool],
    safe_int_func: Callable[[object, int], int],
    logger: logging.Logger,
    database_exceptions: tuple[type[BaseException], ...],
) -> dict[str, Any]:
    stats: dict[str, Any] = {"settled": 0, "sold": 0, "unsold": 0, "total_gold_bars": 0}

    lock_value = str(uuid.uuid4())
    lock_acquired = safe_cache_add_func(AUCTION_SETTLE_LOCK_KEY, lock_value, AUCTION_SETTLE_LOCK_TIMEOUT)
    if not lock_acquired:
        logger.info("拍卖结算锁未获取，跳过本次结算")
        return stats

    active_exception: BaseException | None = None
    try:
        with transaction.atomic():
            settlement_cutoff = timezone.now()
            if round_id is not None:
                auction_round = (
                    AuctionRound.objects.select_for_update()
                    .filter(id=round_id, status__in=[AuctionRound.Status.ACTIVE, AuctionRound.Status.SETTLING])
                    .first()
                )
                if auction_round and auction_round.end_at > settlement_cutoff:
                    logger.info("指定拍卖轮次 #%s 尚未结束，跳过结算", auction_round.round_number)
                    return stats
            else:
                auction_round = (
                    AuctionRound.objects.select_for_update()
                    .filter(
                        status__in=[AuctionRound.Status.ACTIVE, AuctionRound.Status.SETTLING],
                        end_at__lte=settlement_cutoff,
                    )
                    .order_by("end_at", "id")
                    .first()
                )

            if not auction_round:
                logger.info("没有需要结算的拍卖轮次")
                return stats

            if auction_round.status != AuctionRound.Status.SETTLING:
                auction_round.status = AuctionRound.Status.SETTLING
                auction_round.save(update_fields=["status"])

        slots = AuctionSlot.objects.filter(round=auction_round, status=AuctionSlot.Status.ACTIVE).select_related(
            "item_template", "highest_bidder"
        )

        for slot in slots:
            try:
                result = settle_slot_func(slot)
                if not isinstance(result, dict):
                    raise AssertionError(f"invalid settle slot result for slot={slot.id}: {result!r}")

                if result.get("skipped"):
                    continue

                if result.get("sold"):
                    stats["sold"] += 1
                    stats["total_gold_bars"] += max(0, safe_int_func(result.get("price", 0), 0))
                else:
                    stats["unsold"] += 1
            except database_exceptions as exc:
                logger.exception("结算拍卖位 %s 时出错: %s", slot.id, exc)
                raise

        with transaction.atomic():
            locked_round = AuctionRound.objects.select_for_update().get(pk=auction_round.pk)

            if AuctionSlot.objects.filter(round=locked_round, status=AuctionSlot.Status.ACTIVE).exists():
                logger.info("拍卖轮次 #%s 仍有未完成拍卖位，保持 SETTLING 状态", locked_round.round_number)
                return stats

            locked_round.status = AuctionRound.Status.COMPLETED
            locked_round.settled_at = timezone.now()
            locked_round.save(update_fields=["status", "settled_at"])

            stats["sold"] = AuctionSlot.objects.filter(
                round=locked_round,
                status=AuctionSlot.Status.SOLD,
            ).count()
            stats["unsold"] = AuctionSlot.objects.filter(
                round=locked_round,
                status=AuctionSlot.Status.UNSOLD,
            ).count()
            stats["total_gold_bars"] = (
                AuctionDelivery.objects.filter(
                    slot__round=locked_round,
                    slot__status=AuctionSlot.Status.SOLD,
                ).aggregate(total=Sum("settlement_price"))["total"]
                or 0
            )

        stats["settled"] = 1
        logger.info(
            "拍卖轮次 #%s 结算完成，售出 %s 件，流拍 %s 件，共收取 %s 金条",
            auction_round.round_number,
            stats["sold"],
            stats["unsold"],
            stats["total_gold_bars"],
        )
        return stats
    except BaseException as exc:
        active_exception = exc
        raise
    finally:
        _release_lock_preserving_active_exception(
            AUCTION_SETTLE_LOCK_KEY,
            lock_value,
            logger=logger,
            log_context="auction round settlement",
            active_exception=active_exception,
        )
