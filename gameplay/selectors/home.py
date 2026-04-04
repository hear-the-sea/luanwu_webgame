from __future__ import annotations

import logging

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from core.utils import safe_int
from guests.guest_upkeep_rules import get_guest_salary_for_rarity
from guests.models import GuestStatus

from ..models import MissionRun, ResourceType
from ..services.missions import can_retreat
from ..services.technology import get_technology_template
from ..services.utils.cache import CacheKeys
from ..services.utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS
from ..services.utils.query_optimization import optimize_guest_queryset

logger = logging.getLogger(__name__)


def _normalize_hourly_rates(hourly_rates) -> dict[str, int]:
    if not isinstance(hourly_rates, dict):
        return {}

    normalized: dict[str, int] = {}
    for key, value in hourly_rates.items():
        if not isinstance(key, str) or not key:
            continue
        normalized[key] = safe_int(value, default=0, min_val=0) or 0
    return normalized


def _safe_cache_get(key: str):
    try:
        return cache.get(key)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("Home selector cache.get failed: key=%s error=%s", key, exc, exc_info=True)
        return None


def _safe_cache_set(key: str, value, timeout: int) -> None:
    try:
        cache.set(key, value, timeout=timeout)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("Home selector cache.set failed: key=%s error=%s", key, exc, exc_info=True)


def get_home_context(manor) -> dict:
    resources = [
        ("grain", "粮食", manor.grain),
        ("silver", "银两", manor.silver),
        ("retainer", "家丁", f"{manor.retainer_count} / {manor.retainer_capacity}"),
    ]

    guests = list(optimize_guest_queryset(manor.guests.all()).order_by("template__name"))
    guest_status_display = dict(GuestStatus.choices)
    for guest in guests:
        guest.status_display = guest_status_display.get(guest.status, guest.status)

    runs = list(
        manor.mission_runs.select_related("mission")
        .prefetch_related("guests__template")
        .filter(status=MissionRun.Status.ACTIVE, return_at__isnull=False)
    )
    now = timezone.now()
    for run in runs:
        run.can_retreat = can_retreat(run, now=now)

    upgrading_buildings = list(
        manor.buildings.select_related("building_type")
        .filter(is_upgrading=True, upgrade_complete_at__isnull=False)
        .order_by("upgrade_complete_at")
    )

    upgrading_techs = list(
        manor.technologies.filter(is_upgrading=True, upgrade_complete_at__isnull=False).order_by("upgrade_complete_at")
    )
    for tech in upgrading_techs:
        tpl = get_technology_template(tech.tech_key) or {}
        tech.display_name = tpl.get("name", tech.tech_key)

    total_guest_salary = sum(get_guest_salary_for_rarity(g.rarity) for g in guests)

    from ..utils.resource_calculator import get_hourly_rates, get_personnel_grain_cost_per_hour

    cache_key = CacheKeys.home_hourly_rates(manor.pk)
    hourly_rates = _safe_cache_get(cache_key)
    if hourly_rates is None:
        hourly_rates = get_hourly_rates(manor)
        _safe_cache_set(cache_key, hourly_rates, timeout=settings.HOME_STATS_CACHE_TTL_SECONDS)
    hourly_rates = _normalize_hourly_rates(hourly_rates)
    resource_labels = dict(ResourceType.choices)
    building_income = []
    for res_type, rate in hourly_rates.items():
        if rate > 0:
            label = resource_labels.get(res_type, res_type)
            building_income.append({"resource": res_type, "label": label, "rate": rate})

    player_troops = list(
        manor.troops.select_related("troop_template").filter(count__gt=0).order_by("troop_template__priority")
    )

    from ..services.raid import get_active_raids, get_active_scouts, get_incoming_raids

    active_guild_mission = None
    user = getattr(manor, "user", None)
    guild_membership = getattr(user, "guild_membership", None)
    if guild_membership is not None and getattr(guild_membership, "is_active", False):
        from guilds.models import GuildMissionRun

        guild = getattr(guild_membership, "guild", None)
        active_guild_mission = (
            GuildMissionRun.objects.select_related("template")
            .filter(
                guild=guild,
                status=GuildMissionRun.Status.ACTIVE,
            )
            .filter(Q(return_at__isnull=True) | Q(return_at__gt=now))
            .order_by("-started_at")
            .first()
        )

    return {
        "manor": manor,
        "resources": resources,
        "resource_labels": resource_labels,
        "guests": guests,
        "guest_count": len(guests),
        "active_runs": runs,
        "upgrading_buildings": upgrading_buildings,
        "upgrading_technologies": upgrading_techs,
        "total_guest_salary": total_guest_salary,
        "building_income": building_income,
        "grain_production": hourly_rates.get("grain", 0),
        "personnel_grain_cost": get_personnel_grain_cost_per_hour(manor),
        "player_troops": player_troops,
        "active_scouts": get_active_scouts(manor),
        "active_raids": get_active_raids(manor),
        "incoming_raids": get_incoming_raids(manor),
        "active_guild_mission": active_guild_mission,
    }
