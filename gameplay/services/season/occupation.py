"""占领休整、一次性阵营战果及自动维修策略。"""

from copy import deepcopy
from datetime import datetime
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from django.utils.dateparse import parse_datetime

DEFAULT_OCCUPATION = {
    "enabled": True,
    "capture_hp": 1,
    "capture_score_ticks": 2,
    "capture_score_daily_limit": 1,
    "automatic_repair": True,
    "operating_grain_per_active": 100,
    "repair_reserved_cycles": 1,
}


def occupation_rules(season: Any) -> dict[str, Any]:
    from .hitpoints import hitpoint_rules

    value = season.rules.get("campaign", {}).get("occupation", {}) if season else {}
    result = {**DEFAULT_OCCUPATION, **value}
    result["enabled"] = bool(value.get("enabled", False) and hitpoint_rules(season)["enabled"])
    return deepcopy(result)


def validate_occupation(value: Any) -> None:
    if value is None:
        return
    try:
        if not isinstance(value["enabled"], bool) or not isinstance(value["automatic_repair"], bool):
            raise ValueError
        for key, maximum in (
            ("capture_hp", 100),
            ("capture_score_ticks", 24),
            ("capture_score_daily_limit", 10),
            ("operating_grain_per_active", 10000),
            ("repair_reserved_cycles", 4),
        ):
            if type(value[key]) is not int or not 1 <= value[key] <= maximum:
                raise ValueError
        limit = value.get("wall_repair_daily_limit")
        if limit is not None and (type(limit) is not int or not 0 <= limit <= 100000000):
            raise ValueError
        if "wall_repair_daily_effective_at" in value:
            stamp = value["wall_repair_daily_effective_at"]
            parsed = parse_datetime(stamp) if isinstance(stamp, str) else None
            if parsed is None or timezone.is_naive(parsed):
                raise ValueError
    except (KeyError, ValueError, TypeError) as exc:
        raise ImproperlyConfigured("占领休整与维修资源优先级配置无效") from exc


def initialize_occupation(site: Any, now: datetime) -> None:
    from .fortifications import tower_state

    config = occupation_rules(site.season)
    site.durability = min(site.npc_profile["durability"], config["capture_hp"])
    tower = tower_state(site)
    site.defenses = {
        "captured_at": now.isoformat(),
        "auto_repair_at": now.isoformat(),
        **({"wall_repair_daily": site.defenses["wall_repair_daily"]} if "wall_repair_daily" in site.defenses else {}),
        **(
            {"arrow_tower": {"hp": min(tower["max_hp"], config["capture_hp"]), "max_hp": tower["max_hp"]}}
            if tower["max_hp"]
            else {}
        ),
    }
    site.last_score_at = site.protected_until


def award_occupation_score_locked(site: Any, faction: str, now: datetime) -> int:
    from zoneinfo import ZoneInfo

    from django.utils import timezone

    from .campaign_rules import campaign_rules
    from .economy import ensure_factions_locked
    from .playability_rules import playability_rules

    config = occupation_rules(site.season)
    if not config["enabled"]:
        return 0
    states = ensure_factions_locked(site.season, now)
    state = states[faction]
    day = timezone.localdate(now, timezone=ZoneInfo("Asia/Shanghai")).isoformat()
    book = deepcopy(state.economy_state)
    daily = book.setdefault("occupation_scores", {}).setdefault(day, {})
    claimed = int(daily.get(site.key, 0))
    if claimed >= config["capture_score_daily_limit"]:
        return 0
    if playability_rules(site.season)["enabled"]:
        from .weekly_campaign import activate_weekly_locked

        activated = activate_weekly_locked(site.season, states, now)
        state = states[faction]
        # 启用周账本产生的新字段也必须保留。
        book = deepcopy(state.economy_state)
        daily = book.setdefault("occupation_scores", {}).setdefault(day, {})
    else:
        activated = None
    points = campaign_rules(site.season)["sites"][site.key]["score_per_tick"] * config["capture_score_ticks"]
    daily[site.key] = claimed + 1
    state.economy_state = book
    state.score += points
    if activated is not None:
        from .weekly_campaign import record_capture_points_locked

        record_capture_points_locked(site.season, state, now, points, activated)
    state.save(update_fields=["score", "economy_state"])
    site.season.fiction_score = states["fiction"].score
    site.season.history_score = states["history"].score
    site.season.save(update_fields=["fiction_score", "history_score"])
    return points
