from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from core.config import WAREHOUSE
from guests.models import Guest, GuestStatus

from ...models import InventoryItem, ItemTemplate
from .guest_items import guest_is_eligible_for_soul_fusion
from .soul_fusion_helpers import (
    SOUL_FUSION_DEFAULT_ALLOWED_RARITIES,
    SOUL_FUSION_DEFAULT_MIN_LEVEL,
    get_soul_fusion_requirements,
)


@dataclass(frozen=True)
class GuestItemSelectionContext:
    guests_for_rebirth: list[Guest]
    guests_for_xisuidan: list[Guest]
    guests_for_xidianka: list[Guest]
    guests_for_soul_fusion: list[Guest]
    guests_for_rarity_upgrade: list[Guest]


def _load_guest_tool_payloads(manor) -> list[object]:
    return list(
        manor.inventory_items.filter(
            storage_location=InventoryItem.StorageLocation.WAREHOUSE,
            quantity__gt=0,
            template__effect_type=ItemTemplate.EffectType.TOOL,
            template__is_usable=True,
        )
        .values_list("template__effect_payload", flat=True)
        .distinct()
    )


def _collect_rarity_upgrade_source_keys(payloads: Iterable[object]) -> set[str]:
    source_keys: set[str] = set(WAREHOUSE.RARITY_UPGRADE_SOURCE_TEMPLATE_KEYS)
    for payload in payloads:
        if not isinstance(payload, dict) or payload.get("action") != "upgrade_guest_rarity":
            continue

        raw_source_keys = payload.get("source_template_keys")
        if isinstance(raw_source_keys, list):
            source_keys.update(str(key).strip() for key in raw_source_keys if str(key).strip())

        target_template_map = payload.get("target_template_map")
        if isinstance(target_template_map, dict):
            source_keys.update(str(key).strip() for key in target_template_map.keys() if str(key).strip())
    return source_keys


def _collect_rarity_upgrade_source_rarities(payloads: Iterable[object]) -> set[str]:
    source_rarities: set[str] = set()
    for payload in payloads:
        if not isinstance(payload, dict) or payload.get("action") != "upgrade_guest_rarity":
            continue

        source_rarity = payload.get("source_rarity")
        if isinstance(source_rarity, str) and source_rarity.strip():
            source_rarities.add(source_rarity.strip())
    return source_rarities


def _collect_soul_fusion_requirements(payloads: Iterable[object]) -> tuple[int, set[str]]:
    min_level: int | None = None
    allowed_rarities: set[str] = set()
    for payload in payloads:
        if not isinstance(payload, dict) or payload.get("action") != "soul_fusion":
            continue

        normalized_min_level, normalized_rarities = get_soul_fusion_requirements(payload)
        min_level = normalized_min_level if min_level is None else min(min_level, normalized_min_level)
        allowed_rarities.update(normalized_rarities)

    return (
        min_level or SOUL_FUSION_DEFAULT_MIN_LEVEL,
        allowed_rarities or set(SOUL_FUSION_DEFAULT_ALLOWED_RARITIES),
    )


def build_guest_item_selection_context(manor, *, eligible_guests: list[Guest]) -> GuestItemSelectionContext:
    payloads = _load_guest_tool_payloads(manor)
    soul_fusion_min_level, soul_fusion_allowed_rarities = _collect_soul_fusion_requirements(payloads)
    rarity_upgrade_source_keys = _collect_rarity_upgrade_source_keys(payloads)
    rarity_upgrade_source_rarities = _collect_rarity_upgrade_source_rarities(payloads)

    guests_for_xisuidan = [guest for guest in eligible_guests if guest.level == 100 and guest.xisuidan_used < 10]
    guests_for_xisuidan.sort(key=lambda guest: (guest.xisuidan_used, guest.template.name, guest.id))

    guests_for_xidianka = [
        guest
        for guest in eligible_guests
        if (
            guest.allocated_force != 0
            or guest.allocated_intellect != 0
            or guest.allocated_defense != 0
            or guest.allocated_agility != 0
        )
    ]

    guests_for_soul_fusion = [
        guest
        for guest in eligible_guests
        if guest_is_eligible_for_soul_fusion(
            guest,
            min_level=soul_fusion_min_level,
            allowed_rarities=soul_fusion_allowed_rarities,
        )
    ]

    guests_for_rarity_upgrade = [
        guest
        for guest in eligible_guests
        if guest.status == GuestStatus.IDLE
        and (
            guest.template.key in rarity_upgrade_source_keys or guest.template.rarity in rarity_upgrade_source_rarities
        )
    ]

    return GuestItemSelectionContext(
        guests_for_rebirth=eligible_guests,
        guests_for_xisuidan=guests_for_xisuidan,
        guests_for_xidianka=guests_for_xidianka,
        guests_for_soul_fusion=guests_for_soul_fusion,
        guests_for_rarity_upgrade=guests_for_rarity_upgrade,
    )
