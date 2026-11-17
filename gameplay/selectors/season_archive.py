"""上季待处理奖励提示；只读取当前玩家有权领取的项目。"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from gameplay.models import ItemTemplate, SeasonAction, SeasonMember
from gameplay.services.season.catalog import has_summon_catalog, reward_catalog
from gameplay.services.season.playability_rules import playability_rules
from gameplay.services.season.weekly_campaign import week_bounds, week_number
from gameplay.services.utils.cache import CacheKeys
from gameplay.services.utils.cache_exceptions import CACHE_INFRASTRUCTURE_EXCEPTIONS

logger = logging.getLogger(__name__)


def _safe_cache_get(key: str) -> Any:
    try:
        return cache.get(key)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("Season archive cache.get failed: key=%s error=%s", key, exc, exc_info=True)
        return None


def _safe_cache_set(key: str, value: Any, timeout: int) -> None:
    try:
        cache.set(key, value, timeout=timeout)
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("Season archive cache.set failed: key=%s error=%s", key, exc, exc_info=True)


def _cache_ttl() -> int:
    return max(0, int(getattr(settings, "PREVIOUS_SEASON_REWARD_CACHE_TTL_SECONDS", 60)))


def _cache_version(manor_id: int) -> str:
    """读取庄园级缓存版本；缺失时视为 0，旧版本条目会在 TTL 内过期。"""
    value = _safe_cache_get(CacheKeys.previous_season_reward_version(manor_id))
    return str(value) if isinstance(value, (str, int)) else "0"


def _version_timeout() -> int:
    # 版本号必须比条目活得久，避免版本号先过期后旧条目重新可达。
    return max(_cache_ttl() * 10, 3600)


def invalidate_previous_season_reward(manor_id: int) -> None:
    """写入新版本号，一次失效首页与所有赛季页的往季奖励缓存。"""
    if _cache_ttl() <= 0:
        return
    key = CacheKeys.previous_season_reward_version(manor_id)
    try:
        cache.set(key, str(time.time_ns()), timeout=_version_timeout())
    except CACHE_INFRASTRUCTURE_EXCEPTIONS as exc:
        logger.warning("Season archive cache.invalidate failed: key=%s error=%s", key, exc, exc_info=True)


def _loaded(items: dict[str, int]) -> bool:
    keys = set(items)
    return not keys or ItemTemplate.objects.filter(key__in=keys).values("key").distinct().count() == len(keys)


def _week_totals(rows: Any, season: Any, activated: Any, numbers: set[int]) -> dict[int, dict[int, int]]:
    totals: dict[int, dict[int, int]] = {number: {} for number in numbers}
    for row in rows:
        result = row["result"]
        stamp = parse_datetime(result.get("occurred_at", "")) or row["created_at"]
        number = week_number(season, stamp)
        if number not in numbers:
            continue
        start, end = week_bounds(season, number)
        if not max(start, activated) <= stamp < end:
            continue
        # 与周奖励的 _week_filter 一致，兼容旧周数和最后一周的历史流水。
        if row["week"] != number:
            if row["week"] != 0 and not (number == 13 and row["week"] == 12):
                continue
            if not start.date() - timedelta(days=1) <= row["date"] <= end.date() + timedelta(days=1):
                continue
        if result.get("category") not in {"combat", "logistics", "exploration"}:
            continue
        amount = max(0, int(result.get("merit", 0)))
        if amount:
            members = totals[number]
            members[row["member_id"]] = members.get(row["member_id"], 0) + amount
    return totals


def _weekly_pending(member: Any, claims: set[str], now: Any) -> bool:
    season = member.season
    playability = playability_rules(season)
    if not playability["enabled"]:
        return False
    state = season.faction_states.filter(faction=member.faction).first()
    book = state.economy_state.get("weekly_campaign", {}) if state else {}
    activated = parse_datetime(book.get("activated_at", "")) or now
    config = playability["weekly"]
    settled = {
        number
        for number in season.weeks.values_list("number", flat=True)
        if 1 <= number <= 13 and week_bounds(season, number)[1] <= now
    }
    candidates = {
        number
        for number in settled
        if f"campaign:weekly:{number}:personal" not in claims or f"campaign:weekly:{number}:team" not in claims
    }
    if not candidates:
        return False
    fields = ("member_id", "date", "week", "created_at", "result")
    base = SeasonAction.objects.filter(member__season=season, kind="merit_credit")
    own = _week_totals(base.filter(member=member).values(*fields), season, activated, candidates)
    eligible = {number for number in candidates if own[number].get(member.pk, 0) >= config["participation_merit"]}
    personal_loaded = _loaded(config["personal_items"])
    team_loaded = _loaded(config["team_items"])
    personal_pending = {number for number in eligible if f"campaign:weekly:{number}:personal" not in claims}
    if personal_pending and personal_loaded and team_loaded:
        return True
    if any(f"campaign:weekly:{number}:team" in claims for number in personal_pending) and personal_loaded:
        return True
    remaining = {number for number in eligible if f"campaign:weekly:{number}:team" not in claims}
    if not remaining:
        return False
    teams = _week_totals(base.filter(member__faction=member.faction).values(*fields), season, activated, remaining)
    for number in remaining:
        row = book.get("weeks", {}).get(str(number), {})
        target = row.get(
            "target",
            min(config["goal_maximum"], max(config["goal_minimum"], len(teams[number]) * config["goal_per_active"])),
        )
        personal = number in personal_pending
        team = sum(teams[number].values()) >= target
        if (personal or team) and (not personal or personal_loaded) and (not team or team_loaded):
            return True
    return False


def _member_reward(member: Any, now: Any) -> dict[str, Any]:
    claims = set(member.claims.values_list("key", flat=True))
    destination = "season-rewards"
    kind = ""
    final_items = member.final_reward.get("items", {})
    if member.final_rank and final_items and "campaign:final" not in claims and _loaded(final_items):
        kind = "排名奖励"
    if not kind and member.season.rules.get("rewards"):
        for reward in reward_catalog(member.faction, rules=member.season.rules):
            if (
                member.merit >= reward["threshold"]
                and "reward:" + reward["key"] not in claims
                and _loaded(reward["items"])
            ):
                kind = "功勋奖励"
                break
    if (
        not kind
        and member.scroll_available
        and not member.scroll_used
        and member.season.rules.get("roster")
        and has_summon_catalog(member.faction, member.season.rules)
    ):
        destination, kind = "season-summon", "人物卷轴"
    if not kind and _weekly_pending(member, claims, now):
        kind = "周奖励"
    if not kind:
        return {}
    return {
        "season_id": member.season_id,
        "season_name": member.season.name,
        "kind": kind,
        "url": f"{reverse('gameplay:season')}?season_id={member.season_id}#{destination}",
    }


def _compute_previous_season_reward(manor: Any, season: Any, now: Any) -> dict[str, Any]:
    previous = SeasonMember.objects.filter(manor=manor, season__closed=True, faction__in=("fiction", "history"))
    if season is not None:
        previous = previous.filter(season__starts_at__lt=season.starts_at)
    for member in previous.select_related("season").order_by("-season__starts_at", "-season_id"):
        reward = _member_reward(member, now)
        if reward:
            return reward
    return {}


def previous_season_reward(manor: Any, season: Any = None, now: Any = None) -> dict[str, Any]:
    """返回最近一个有待处理奖励的已结束赛季入口。

    首页和赛季页都会调用；结果按 (庄园, 当前赛季) 缓存短 TTL，领取奖励后由
    ``perform_action`` 主动失效。缓存不可用时直接回退到实时计算。
    """
    now = now or timezone.now()
    ttl = _cache_ttl()
    if ttl <= 0:
        return _compute_previous_season_reward(manor, season, now)
    cache_key = CacheKeys.previous_season_reward(manor.pk, getattr(season, "pk", None), _cache_version(manor.pk))
    cached = _safe_cache_get(cache_key)
    if cached is not None:
        return cached
    reward = _compute_previous_season_reward(manor, season, now)
    _safe_cache_set(cache_key, reward, ttl)
    return reward
