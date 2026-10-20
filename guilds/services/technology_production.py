from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from guests.rarity import GUEST_RARITY_ORDER

from .. import constants as guild_constants
from ..models import Guild, GuildResourceLog, GuildTechnology, GuildWarehouse
from ..technology_tree import PRODUCTION_TECH_KEYS, TECHNOLOGY_NODE_BY_KEY
from .warehouse import add_item_to_warehouse, get_item_rarity_map
from .warehouse_config import ProductionItem, get_technology_production_items

RARITY_RANK = {rarity: rank for rank, rarity in enumerate(GUEST_RARITY_ORDER)}


@dataclass(frozen=True)
class GuildProductionResult:
    due_tech_keys: tuple[str, ...]
    produced_item_count: int
    silver_spent: int
    grain_spent: int
    stopped_item_key: str = ""


@dataclass(frozen=True)
class _ProductionCandidate:
    item_key: str
    quantity: int
    contribution_cost: int
    rarity_rank: int


def _is_technology_production_due(tech: GuildTechnology, now) -> bool:
    last_production_at = tech.last_production_at
    if last_production_at is None:
        return True

    interval_days = max(
        1,
        int(TECHNOLOGY_NODE_BY_KEY.get(tech.tech_key, {}).get("production_interval_days", 1) or 1),
    )
    if interval_days == 1:
        return timezone.localdate(last_production_at) < timezone.localdate(now)
    return last_production_at + timedelta(days=interval_days) <= now


def _collect_due_technology_candidates(
    due_techs: list[GuildTechnology],
) -> list[_ProductionCandidate]:
    configured_candidates: list[tuple[str, ProductionItem]] = []
    for tech in due_techs:
        for item in get_technology_production_items(tech.tech_key, tech.level):
            quantity = max(0, int(item.quantity or 0))
            if quantity <= 0:
                continue
            configured_candidates.append((str(item.item_key), item))

    rarities = get_item_rarity_map(item_key for item_key, _item in configured_candidates)
    candidates = [
        _ProductionCandidate(
            item_key=item_key,
            quantity=max(0, int(item.quantity or 0)),
            contribution_cost=max(0, int(item.contribution_cost or 0)),
            rarity_rank=RARITY_RANK.get(rarities.get(item_key, "gray"), -1),
        )
        for item_key, item in configured_candidates
    ]

    # Shuffle first so candidates with equal rarity and exchange cost are randomized.
    random.shuffle(candidates)
    candidates.sort(
        key=lambda candidate: (candidate.rarity_rank, candidate.contribution_cost),
        reverse=True,
    )
    return candidates


def process_guild_technology_production(guild_id: int, *, now=None) -> GuildProductionResult:
    """按全帮会统一优先级结算当期科技产出和维护资源。"""
    now = now or timezone.now()
    with transaction.atomic():
        guild = Guild.objects.select_for_update().get(pk=guild_id)
        if not guild.is_active:
            return GuildProductionResult((), 0, 0, 0)

        tech_by_key = {
            tech.tech_key: tech
            for tech in GuildTechnology.objects.select_for_update().filter(
                guild=guild, tech_key__in=PRODUCTION_TECH_KEYS, level__gt=0
            )
        }
        due_techs = [
            tech_by_key[tech_key]
            for tech_key in PRODUCTION_TECH_KEYS
            if tech_key in tech_by_key and _is_technology_production_due(tech_by_key[tech_key], now)
        ]
        if not due_techs:
            return GuildProductionResult((), 0, 0, 0)

        candidates = _collect_due_technology_candidates(due_techs)
        grain_row = GuildWarehouse.objects.select_for_update().filter(guild=guild, item_key="grain").first()
        remaining_silver = max(0, int(guild.silver or 0))
        remaining_grain = max(0, int(grain_row.quantity or 0)) if grain_row else 0
        silver_per_contribution = max(
            0,
            int(guild_constants.TECH_PRODUCTION_UPKEEP.get("silver_per_contribution", 0) or 0),
        )
        grain_per_contribution = max(
            0,
            int(guild_constants.TECH_PRODUCTION_UPKEEP.get("grain_per_contribution", 0) or 0),
        )

        selected_items: dict[str, tuple[int, int]] = {}
        silver_spent = 0
        grain_spent = 0
        stopped_item_key = ""

        for candidate in candidates:
            item_silver_cost = candidate.contribution_cost * silver_per_contribution
            item_grain_cost = candidate.contribution_cost * grain_per_contribution
            for _ in range(candidate.quantity):
                if remaining_silver < item_silver_cost or remaining_grain < item_grain_cost:
                    stopped_item_key = candidate.item_key
                    break

                remaining_silver -= item_silver_cost
                remaining_grain -= item_grain_cost
                silver_spent += item_silver_cost
                grain_spent += item_grain_cost
                previous_quantity, previous_cost = selected_items.get(candidate.item_key, (0, 0))
                selected_items[candidate.item_key] = (
                    previous_quantity + 1,
                    max(previous_cost, candidate.contribution_cost),
                )
            if stopped_item_key:
                break

        if silver_spent:
            updated = Guild.objects.filter(pk=guild.pk, silver__gte=silver_spent).update(
                silver=F("silver") - silver_spent
            )
            if not updated:
                raise RuntimeError("Guild silver changed during locked technology production")

        if grain_spent:
            if grain_row is None:
                raise RuntimeError("Guild grain row disappeared during locked technology production")
            updated = GuildWarehouse.objects.filter(pk=grain_row.pk, quantity__gte=grain_spent).update(
                quantity=F("quantity") - grain_spent
            )
            if not updated:
                raise RuntimeError("Guild grain changed during locked technology production")
            GuildWarehouse.objects.filter(pk=grain_row.pk, quantity=0).delete()

        for item_key, (quantity, contribution_cost) in selected_items.items():
            add_item_to_warehouse(guild, item_key, quantity, contribution_cost)

        produced_item_count = sum(quantity for quantity, _cost in selected_items.values())
        stopped_note = f"；资源不足，停止于{stopped_item_key}" if stopped_item_key else ""
        if produced_item_count:
            note = (
                f"科技产出维护消耗银两{silver_spent}、粮食{grain_spent}，产出{produced_item_count}件" f"{stopped_note}"
            )
        else:
            note = f"科技产出维护资源不足，今日未产出{stopped_note}"
        GuildResourceLog.objects.create(
            guild=guild,
            action="production",
            silver_change=-silver_spent,
            grain_change=-grain_spent,
            note=note[:200],
        )

        for tech in due_techs:
            tech.last_production_at = now
        GuildTechnology.objects.bulk_update(due_techs, ["last_production_at"])

        return GuildProductionResult(
            due_tech_keys=tuple(tech.tech_key for tech in due_techs),
            produced_item_count=produced_item_count,
            silver_spent=silver_spent,
            grain_spent=grain_spent,
            stopped_item_key=stopped_item_key,
        )
