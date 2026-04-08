from __future__ import annotations

import logging

from django.core.cache import cache

from common.constants.resources import ResourceType
from guests.services.recruitment_queries import (
    get_active_guest_recruitment,
    get_pool_recruitment_duration_seconds,
    list_candidates,
    list_pools,
)

from ..models import InventoryItem
from ..services.utils.cache import CACHE_TIMEOUT_SHORT, recruitment_hall_context_cache_key
from ..services.utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS

logger = logging.getLogger(__name__)


def _recruitment_hall_cache_key(manor_id: int) -> str:
    return recruitment_hall_context_cache_key(manor_id)


def _format_duration_cn(seconds: int) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, sec = divmod(rem, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分钟")
    if sec or not parts:
        parts.append(f"{sec}秒")
    return "".join(parts)


def _serialize_recruit_records(records) -> list[dict]:
    payload: list[dict] = []
    for record in records:
        if not record.guest_id:
            continue
        payload.append(
            {
                "created_at": record.created_at,
                "guest_display_name": record.guest.display_name,
                "guest_rarity": record.guest.rarity,
            }
        )
    return payload


def _serialize_cached_payload(manor) -> dict:
    candidates_payload = list(
        list_candidates(manor).values(
            "id",
            "display_name",
            "rarity",
            "rarity_revealed",
        )
    )

    magnifying_glass_items = (
        manor.inventory_items.filter(
            template__key="fangdajing",
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
        )
        .select_related("template")
        .order_by("id")
    )
    magnifying_payload = [
        {
            "id": item.id,
            "quantity": item.quantity,
            "template_name": item.template.name,
        }
        for item in magnifying_glass_items
    ]

    return {
        "candidates_payload": candidates_payload,
        "candidate_count": len(candidates_payload),
        "magnifying_glass_items": magnifying_payload,
    }


def _load_recruitment_records_payload(manor, records_limit: int) -> list[dict]:
    records = list(manor.recruit_records.select_related("guest__template").order_by("-created_at")[:records_limit])
    return _serialize_recruit_records(records)


def _safe_cache_get(key: str):
    try:
        return cache.get(key)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("Recruitment hall cache.get failed: key=%s error=%s", key, exc, exc_info=True)
        return None


def _safe_cache_set(key: str, value: dict, timeout: int) -> None:
    try:
        cache.set(key, value, timeout=timeout)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("Recruitment hall cache.set failed: key=%s error=%s", key, exc, exc_info=True)


def get_recruitment_hall_context(manor, records_limit: int, *, use_cache: bool = True) -> dict:
    pools = list(list_pools(core_only=True, include_entries=False))
    for pool in pools:
        duration_seconds = get_pool_recruitment_duration_seconds(pool)
        setattr(pool, "recruit_duration_seconds", duration_seconds)
        setattr(pool, "recruit_duration_display", _format_duration_cn(duration_seconds))
    active_recruitment = get_active_guest_recruitment(manor)

    cache_key = _recruitment_hall_cache_key(int(manor.id))
    cached_payload = _safe_cache_get(cache_key) if use_cache else None
    if cached_payload is None:
        cached_payload = _serialize_cached_payload(manor)
        if use_cache:
            _safe_cache_set(cache_key, cached_payload, timeout=CACHE_TIMEOUT_SHORT)
    records_payload = _load_recruitment_records_payload(manor, records_limit)

    return {
        "manor": manor,
        "resource_labels": dict(ResourceType.choices),
        "pools": pools,
        "candidates": cached_payload["candidates_payload"],
        "candidates_payload": cached_payload["candidates_payload"],
        "candidate_count": cached_payload["candidate_count"],
        "active_recruitment": active_recruitment,
        "records": records_payload,
        "magnifying_glass_items": cached_payload["magnifying_glass_items"],
    }
