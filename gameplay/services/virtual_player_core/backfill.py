from __future__ import annotations

from typing import Any

from django.db import transaction

from gameplay.models import BotBackfillDemand, Manor
from gameplay.services.runtime_configs import read_virtual_player_routing

from .config import BootstrapMode, load_virtual_player_config, load_virtual_player_v2_config
from .selectors import prestige_band_for_value, regions


def _population_config_for_bootstrap_mode(
    bootstrap_mode: BootstrapMode,
) -> dict[str, Any] | None:
    if bootstrap_mode is BootstrapMode.V2_PAUSED:
        return None
    config = dict(load_virtual_player_config())
    if bootstrap_mode is BootstrapMode.V2_ACTIVE:
        v2_config = load_virtual_player_v2_config()
        if v2_config is None:
            return None
        config["prestige_bands"] = {band.name: [band.lower_inclusive, band.upper_exclusive] for band in v2_config.bands}
    return config


def record_virtual_player_backfill_demand(*, region: str, prestige_band: str, needed: int) -> None:
    """Reconcile the current async bot backfill shortage for one population cell."""
    needed = max(0, int(needed or 0))
    if not region or not prestige_band:
        return
    normalized_region = str(region)
    normalized_band = str(prestige_band)
    with transaction.atomic():
        demand = (
            BotBackfillDemand.objects.select_for_update()
            .filter(
                region=normalized_region,
                prestige_band=normalized_band,
            )
            .first()
        )
        if needed <= 0:
            if demand is not None:
                demand.delete()
            return
        if demand is None:
            BotBackfillDemand.objects.select_for_update().update_or_create(
                region=normalized_region,
                prestige_band=normalized_band,
                defaults={"needed": needed},
            )
        elif needed != int(demand.needed or 0):
            demand.needed = needed
            demand.save(update_fields=["needed", "updated_at"])


def consume_virtual_player_backfill_demands(*, limit: int | None = None) -> list[dict[str, Any]]:
    """Pop recorded backfill demands in deterministic order."""
    queryset = BotBackfillDemand.objects.select_for_update().order_by("region", "prestige_band", "id")
    if limit is not None:
        queryset = queryset[: max(0, int(limit))]
    with transaction.atomic():
        rows = list(queryset)
        if not rows:
            return []
        consumed = [
            {
                "region": row.region,
                "prestige_band": row.prestige_band,
                "needed": int(row.needed or 0),
            }
            for row in rows
            if int(row.needed or 0) > 0
        ]
        BotBackfillDemand.objects.filter(id__in=[row.id for row in rows]).delete()
        return consumed


def record_virtual_player_backfill_demand_for_search(
    *,
    searcher: Manor,
    region: str,
    candidate_count: int,
) -> None:
    bootstrap_mode = read_virtual_player_routing().bootstrap_mode
    config = _population_config_for_bootstrap_mode(bootstrap_mode)
    if config is None:
        return
    if not bool(config.get("enabled", True)):
        return
    population = config.get("population") or {}
    min_per_band = max(0, int(population.get("min_attackable_per_band", 0) or 0))
    if min_per_band <= 0:
        return
    prestige_band = prestige_band_for_value(int(searcher.prestige or 0), config)
    if prestige_band is None:
        return
    deficit = max(0, min_per_band - max(0, int(candidate_count or 0)))
    record_virtual_player_backfill_demand(region=region, prestige_band=prestige_band, needed=deficit)
    if bootstrap_mode is BootstrapMode.V2_ACTIVE:
        from .population_runtime import merge_population_recompute_demand

        merge_population_recompute_demand(
            region=region,
            prestige_band=prestige_band,
        )


def get_virtual_player_backfill_search_limit() -> int:
    """Return the attackable-target threshold needed by a region search."""
    bootstrap_mode = read_virtual_player_routing().bootstrap_mode
    config = _population_config_for_bootstrap_mode(bootstrap_mode)
    if config is None:
        return 0
    if not bool(config.get("enabled", True)):
        return 0
    population = config.get("population") or {}
    return max(0, int(population.get("min_attackable_per_band", 0) or 0))


def request_virtual_player_backfill_for_region_search(*, searcher: Manor, region: str) -> bool:
    """Record an explicit region-search shortage for a later population roll."""
    if region not in regions():
        return False
    candidate_limit = get_virtual_player_backfill_search_limit()
    if candidate_limit <= 0:
        return False
    if searcher.is_under_newbie_protection or searcher.is_under_peace_shield:
        return False

    from gameplay.services.raid.map_search import count_attackable_manors_by_region

    candidate_count = count_attackable_manors_by_region(
        searcher,
        region,
        limit=candidate_limit,
    )
    record_virtual_player_backfill_demand_for_search(
        searcher=searcher,
        region=region,
        candidate_count=candidate_count,
    )
    return True


__all__ = [
    "consume_virtual_player_backfill_demands",
    "get_virtual_player_backfill_search_limit",
    "record_virtual_player_backfill_demand",
    "record_virtual_player_backfill_demand_for_search",
    "request_virtual_player_backfill_for_region_search",
]
