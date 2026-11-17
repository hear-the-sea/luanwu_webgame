"""赛季开季、战场推进与周期归档；只由显式命令和后台任务调用。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.exceptions import GameError
from gameplay.models.season import Season, SeasonAction, SeasonWeek

from .rules import front_node

SEASON_DAYS = 90
COMBAT_DAYS = 83
WEEK_DAYS = 7


def create_season(name: str, starts_at: datetime | None = None) -> Season:
    """开季命令使用单一入口，固定规则快照并拒绝时间重叠。"""
    from .catalog import build_faction_roster_snapshot, load_rules

    name = str(name).strip()
    if not name or len(name) > 64:
        raise GameError("赛季名称须为 1 至 64 个字符")
    starts_at = starts_at or timezone.now()
    if not isinstance(starts_at, datetime) or timezone.is_naive(starts_at):
        raise GameError("赛季开始时间必须包含时区")
    ends_at = starts_at + timedelta(days=SEASON_DAYS)
    rules = deepcopy(load_rules())
    rules["faction_roster_keys"] = build_faction_roster_snapshot(rules)
    from .campaign_rules import load_campaign_rules

    rules["campaign"] = load_campaign_rules()
    with transaction.atomic():
        # 已有赛季行构成低频开季入口的串行点；首季应由单个运营命令创建。
        list(Season.objects.select_for_update().order_by("id").values_list("id", flat=True))
        if Season.objects.filter(starts_at__lt=ends_at, ends_at__gt=starts_at).exists():
            raise GameError("该时间段已有赛季，请在已有赛季结束后开启新赛季")
        season = Season.objects.create(name=name, starts_at=starts_at, ends_at=ends_at, rules=rules)
        from .economy import ensure_factions_locked
        from .warfare import ensure_sites_locked

        ensure_factions_locked(season, starts_at)
        ensure_sites_locked(season, starts_at)
        return season


def week_ends_at(season: Season, number: int) -> datetime:
    return min(season.ends_at, season.starts_at + timedelta(days=min(COMBAT_DAYS, WEEK_DAYS * number)))


def calculate_week_scores(season: Season, number: int) -> dict[str, int | str]:
    """双方各取等量有效贡献者，减少阵营人数差异对周分的影响。"""
    totals = (
        SeasonAction.objects.filter(member__season=season, week=number, war_points__gt=0)
        .order_by()
        .values("member_id", "member__faction")
        .annotate(points=Sum("war_points"))
    )
    contributions: dict[str, list[int]] = {"fiction": [], "history": []}
    for row in totals:
        faction = row["member__faction"]
        if faction in contributions:
            contributions[faction].append(int(row["points"] or 0))
    matched = min(len(contributions["fiction"]), len(contributions["history"]))
    fiction = sum(sorted(contributions["fiction"], reverse=True)[:matched])
    history = sum(sorted(contributions["history"], reverse=True)[:matched])
    return {
        "fiction_points": fiction,
        "history_points": history,
        "matched_members": matched,
        "winner": "fiction" if fiction > history else "history" if history > fiction else "",
    }


def settle_season(season_id: int, now: datetime | None = None) -> dict[str, int]:
    """幂等推进到期周；挑战入账须共享 Season 行锁与时间校验。"""
    now = now or timezone.now()
    if timezone.is_naive(now):
        raise ValueError("now must be timezone-aware")
    settled = 0
    closed = 0
    with transaction.atomic():
        season = Season.objects.select_for_update().get(pk=season_id)
        if season.closed or now < season.starts_at:
            return {"weeks": 0, "closed": 0}
        if season.rules.get("campaign"):
            from .campaign_rewards import freeze_rankings_locked
            from .contests import victory_contested_days
            from .economy import produce_locked
            from .population import refresh_population_locked, victory_qualified
            from .registration import assign_factions_locked
            from .warfare import score_sites_locked

            assign_factions_locked(season, now)
            refresh_population_locked(season, now)
            produce_locked(season, now)
            score_sites_locked(season, now)
            from .playability_rules import playability_rules
            from .weekly_campaign import (
                freeze_current_targets_locked,
                settle_campaign_weeks_locked,
                weekly_victory_scores,
            )

            weekly = playability_rules(season)["enabled"]
            if weekly:
                freeze_current_targets_locked(season, now)
                settled = settle_campaign_weeks_locked(season, now)
            if now >= season.ends_at:
                # 驻军和采集均可能在返程时补记历史贡献，排名冻结前必须完成入账。
                from .settlement_pending import has_pending_contributions_locked

                if has_pending_contributions_locked(season, season.ends_at):
                    return {"weeks": settled, "closed": 0}
                season.closed = True
                season.winner = (
                    "fiction"
                    if season.fiction_score > season.history_score
                    else "history" if season.history_score > season.fiction_score else ""
                )
                if weekly:
                    points = weekly_victory_scores(season)
                    fiction, history = points.get("fiction", 0), points.get("history", 0)
                    season.winner = "fiction" if fiction > history else "history" if history > fiction else ""
                qualified = victory_qualified(season)
                if not qualified:
                    season.winner = ""
                season.population = {
                    **season.population,
                    "victory_result": {"qualified": qualified, "days": victory_contested_days(season)},
                }
                season.save(update_fields=["closed", "winner", "population"])
                freeze_rankings_locked(season)
                return {"weeks": settled, "closed": 1}
            return {"weeks": settled, "closed": 0}
        # 第十二周仅六天，计分在第84天开始时截止。
        final_week = (COMBAT_DAYS + WEEK_DAYS - 1) // WEEK_DAYS
        for number in range(season.settled_weeks + 1, final_week + 1):
            if now < week_ends_at(season, number):
                break
            values = calculate_week_scores(season, number)
            week, created = SeasonWeek.objects.get_or_create(
                season=season,
                number=number,
                defaults={"node_key": front_node(number), "settled_at": now, **values},
            )
            if created:
                if week.winner == "fiction":
                    season.fiction_score += 1
                elif week.winner == "history":
                    season.history_score += 1
                settled += 1
            season.settled_weeks = number
        if now >= season.ends_at:
            season.closed = True
            season.winner = (
                "fiction"
                if season.fiction_score > season.history_score
                else "history" if season.history_score > season.fiction_score else ""
            )
            closed = 1
        season.save(update_fields=["settled_weeks", "fiction_score", "history_score", "closed", "winner"])
    return {"weeks": settled, "closed": closed}


def settle_seasons(now: datetime | None = None) -> dict[str, int]:
    """后台定时推进，保留历史赛季与玩家奖励领取记录。"""
    now = now or timezone.now()
    result = {"seasons": 0, "weeks": 0, "closed": 0}
    ids = Season.objects.filter(closed=False, starts_at__lte=now).order_by("id").values_list("id", flat=True)
    for season_id in ids.iterator():
        changed = settle_season(season_id, now=now)
        result["seasons"] += 1
        result["weeks"] += changed["weeks"]
        result["closed"] += changed["closed"]
    return result


def advance_seasons(now: datetime | None = None) -> dict[str, int]:
    """结算并衔接下一季；从上一季结束时间开始新周期，保留历史成员。"""
    now = now or timezone.now()
    result = settle_seasons(now)
    result["started"] = 0
    with transaction.atomic():
        # 与手动开季保持相同加锁顺序，避免跨季运营命令反向持锁。
        rows = list(Season.objects.select_for_update().order_by("id"))
        latest = max(rows, key=lambda row: (row.starts_at, row.pk), default=None)
        if latest and latest.closed and latest.ends_at <= now:
            next_season = create_season(f"九州争衡·第 {Season.objects.count() + 1} 季", latest.ends_at)
            result["started"] = int(next_season is not None)
    return result
