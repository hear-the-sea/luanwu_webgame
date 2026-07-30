from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db.models import Exists, OuterRef, Q

from common.constants.virtual_players import VIRTUAL_PLAYER_MANAGED_STOCK_EFFECT_TYPES
from gameplay.constants import VIRTUAL_PLAYER_REGION_KEYS
from gameplay.models import BotExternalStrengthReconciliation, BotProfile, ItemTemplate, Manor
from gameplay.services.technology_catalog import build_technology_index
from gameplay.services.virtual_player_core.config import load_virtual_player_config
from gameplay.services.virtual_player_state_policy import VIRTUAL_PROFILE_MAINTAINED_STATES

ALL_TEMPLATE_SENTINEL = "__all__"
ALL_TRADEABLE_TEMPLATE_SENTINEL = "__all_tradeable__"


def configured_keys(config: dict[str, Any], field: str) -> list[str]:
    projection = config.get("projection") or {}
    raw = projection.get(field) or []
    if isinstance(raw, str):
        return [raw] if raw else []
    return [str(item) for item in raw if item]


def configured_model_keys(
    config: dict[str, Any],
    field: str,
    model,
) -> list[str]:
    keys = configured_keys(config, field)
    if ALL_TEMPLATE_SENTINEL not in keys:
        return keys
    queryset = model.objects.all()
    return list(queryset.order_by("key").values_list("key", flat=True))


def configured_item_keys(config: dict[str, Any], field: str) -> list[str]:
    keys = configured_keys(config, field)
    if ALL_TEMPLATE_SENTINEL not in keys and ALL_TRADEABLE_TEMPLATE_SENTINEL not in keys:
        return keys
    return list(ItemTemplate.objects.filter(tradeable=True).order_by("key").values_list("key", flat=True))


def configured_technology_keys(config: dict[str, Any]) -> list[str]:
    keys = configured_keys(config, "technology_keys")
    if ALL_TEMPLATE_SENTINEL not in keys:
        return keys
    return sorted(build_technology_index().keys())


def regions() -> list[str]:
    return list(VIRTUAL_PLAYER_REGION_KEYS)


def prestige_bands(config: dict[str, Any]) -> dict[str, tuple[int, int | None]]:
    bands: dict[str, tuple[int, int | None]] = {}
    for key, raw_range in (config.get("prestige_bands") or {}).items():
        if not isinstance(raw_range, (list, tuple)) or len(raw_range) != 2:
            continue
        low = max(0, int(raw_range[0] or 0))
        high = None if raw_range[1] is None else max(low, int(raw_range[1]))
        bands[str(key)] = (low, high)
    return bands


def band_filter_kwargs(low: int, high: int | None, *, prefix: str = "") -> dict[str, Any]:
    kwargs: dict[str, Any] = {f"{prefix}prestige__gte": low}
    if high is not None:
        kwargs[f"{prefix}prestige__lt"] = high
    return kwargs


def prestige_band_for_value(prestige: int, config: dict[str, Any]) -> str | None:
    for band_name, (low, high) in prestige_bands(config).items():
        if int(prestige) >= low and (high is None or int(prestige) < high):
            return band_name
    return None


def profile_target_prestige_band(profile: BotProfile) -> str:
    return str(profile.target_prestige_band or profile.prestige_band)


def target_band_filter(prestige_band: str) -> Q:
    return Q(target_prestige_band=str(prestige_band)) | Q(
        target_prestige_band="",
        prestige_band=str(prestige_band),
    )


def population_cell_membership_filter(
    prestige_band: str,
    *,
    config: dict[str, Any],
    target_based: bool,
) -> Q:
    if target_based:
        return target_band_filter(prestige_band)
    low, high = prestige_bands(config)[prestige_band]
    return Q(**band_filter_kwargs(low, high, prefix="manor__"))


def active_real_player_count(now) -> int:
    config = load_virtual_player_config()
    active_days = max(1, int((config.get("population") or {}).get("active_window_days") or 7))
    active_after = now - timedelta(days=active_days)
    return Manor.objects.filter(
        bot_profile__isnull=True,
        user__is_staff=False,
        user__is_superuser=False,
        last_active_at__gte=active_after,
    ).count()


def maintained_bot_queryset():
    return BotProfile.objects.filter(state__in=VIRTUAL_PROFILE_MAINTAINED_STATES)


def maintained_bot_count() -> int:
    return maintained_bot_queryset().count()


def without_unresolved_external_reconciliations(queryset):
    """Keep this read path side-effect free while excluding unsafe Bot profiles."""
    unresolved = BotExternalStrengthReconciliation.objects.filter(profile_id=OuterRef("pk")).exclude(
        status=BotExternalStrengthReconciliation.Status.APPLIED
    )
    return queryset.alias(_has_unresolved_external_reconciliation=Exists(unresolved)).filter(
        _has_unresolved_external_reconciliation=False
    )


def unresolved_external_reconciliation_profile_ids(
    profile_ids: list[int] | tuple[int, ...] | set[int],
) -> set[int]:
    normalized_ids = {int(profile_id) for profile_id in profile_ids}
    if not normalized_ids:
        return set()
    return set(
        BotExternalStrengthReconciliation.objects.filter(profile_id__in=normalized_ids)
        .exclude(status=BotExternalStrengthReconciliation.Status.APPLIED)
        .values_list("profile_id", flat=True)
        .distinct()
    )


def configured_population_value(
    population: dict[str, Any],
    field: str,
    *,
    legacy_field: str,
    default: int,
) -> int:
    runtime = getattr(settings, "VIRTUAL_PLAYER_CONFIG", None) or {}
    runtime_population = runtime.get("population") if isinstance(runtime, dict) else None
    if isinstance(runtime_population, dict):
        if field in runtime_population:
            return int(runtime_population[field] or 0)
        if legacy_field in runtime_population:
            return int(runtime_population[legacy_field] or 0)
    return int(population.get(field, population.get(legacy_field, default)) or 0)


def uses_regional_population_planning() -> bool:
    runtime = getattr(settings, "VIRTUAL_PLAYER_CONFIG", None) or {}
    runtime_population = runtime.get("population") if isinstance(runtime, dict) else None
    if not isinstance(runtime_population, dict) or not runtime_population:
        return True
    regional_fields = {
        "region_floor",
        "region_active_multiplier",
        "global_floor",
        "global_active_multiplier",
    }
    if regional_fields.intersection(runtime_population):
        return True
    legacy_planning_fields = {
        "active_player_multiplier",
        "cell_floor",
        "cell_active_multiplier",
        "min_per_region",
    }
    return not bool(legacy_planning_fields.intersection(runtime_population))


def population_config_int(population: dict[str, Any], field: str, default: int) -> int:
    value = population.get(field, default)
    return int(default if value is None else value)


def is_bot_manor(manor: Manor) -> bool:
    return hasattr(manor, "bot_profile")


def managed_stock_effect_types() -> frozenset[str]:
    return frozenset(VIRTUAL_PLAYER_MANAGED_STOCK_EFFECT_TYPES)


__all__ = [
    "active_real_player_count",
    "band_filter_kwargs",
    "configured_item_keys",
    "configured_keys",
    "configured_model_keys",
    "configured_population_value",
    "configured_technology_keys",
    "is_bot_manor",
    "maintained_bot_count",
    "maintained_bot_queryset",
    "managed_stock_effect_types",
    "population_cell_membership_filter",
    "population_config_int",
    "prestige_band_for_value",
    "prestige_bands",
    "profile_target_prestige_band",
    "regions",
    "target_band_filter",
    "uses_regional_population_planning",
    "unresolved_external_reconciliation_profile_ids",
    "without_unresolved_external_reconciliations",
]
