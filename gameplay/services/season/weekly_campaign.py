"""按周保存战果与有效贡献；领奖不改变地图、兵力或个人贡献。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils.dateparse import parse_datetime

from core.exceptions import GameError
from gameplay.models import Manor
from gameplay.models.season import Season, SeasonAction, SeasonClaim, SeasonFaction, SeasonMember, SeasonWeek

from .playability_rules import playability_rules

FACTIONS = ("fiction", "history")
WEEK_SECONDS = 7 * 86400


def week_number(season: Season, now: datetime) -> int:
    return max(1, min(13, int((now - season.starts_at).total_seconds()) // WEEK_SECONDS + 1))


def week_bounds(season: Season, number: int) -> tuple[datetime, datetime]:
    return season.starts_at + timedelta(weeks=number - 1), min(
        season.ends_at, season.starts_at + timedelta(weeks=number)
    )


def _book(state: SeasonFaction) -> dict[str, Any]:
    return state.economy_state.setdefault("weekly_campaign", {"weeks": {}})


def activate_weekly_locked(season: Season, factions: dict[str, SeasonFaction], now: datetime) -> datetime:
    """独立账本记录启用时刻，保留旧的累计分且不追溯启用前战果。"""
    found = [parse_datetime(_book(state).get("activated_at", "")) for state in factions.values()]
    activated = next((value for value in found if value is not None), min(max(now, season.starts_at), season.ends_at))
    for state in factions.values():
        book = _book(state)
        if not book.get("activated_at"):
            book["activated_at"] = activated.isoformat()
            book["legacy_score"] = state.score
            state.save(update_fields=["economy_state"])
    return activated


def record_site_ticks_locked(
    season: Season,
    state: SeasonFaction,
    first_tick: datetime,
    ticks: int,
    interval: int,
    points: int,
    activated: datetime,
) -> None:
    """按每个区间的结算时刻归周；边界时刻归前一周，不逐个 tick 循环。"""
    end_tick = first_tick + timedelta(seconds=(ticks - 1) * interval)
    for number in range(
        week_number(season, first_tick - timedelta(microseconds=1)),
        week_number(season, end_tick - timedelta(microseconds=1)) + 1,
    ):
        start, end = week_bounds(season, number)
        # 周区间 (start, end]，同一建筑的计分游标保证重试不重复入账。
        lower = max(start, activated)
        first = max(0, int((lower - first_tick).total_seconds()) // interval + 1)
        last = min(ticks - 1, int((end - first_tick).total_seconds()) // interval)
        count = max(0, last - first + 1)
        if count:
            row = _book(state)["weeks"].setdefault(str(number), {})
            row["score"] = row.get("score", 0) + count * points
    state.save(update_fields=["economy_state"])


def _week_filter(season: Season, number: int) -> Q:
    start, end = week_bounds(season, number)
    dates = Q(date__gte=start.date() - timedelta(days=1), date__lte=end.date() + timedelta(days=1))
    query = Q(week=number) | (Q(week=0) & dates)
    # 旧战报周数上限为12；尾声第13周仍通过真实发生时间精确过滤。
    if number == 13:
        query |= Q(week=12) & dates
    return query


def _participation(season: Season, number: int, activated: datetime) -> dict[str, dict[int, int]]:
    start, end = week_bounds(season, number)
    start = max(start, activated)
    totals: dict[str, dict[int, int]] = {faction: {} for faction in FACTIONS}
    # 只读取唯一贡献入账凭证，不能把展示战报与入账记录重复相加。
    rows = SeasonAction.objects.filter(
        _week_filter(season, number), member__season=season, kind="merit_credit"
    ).select_related("member")
    for action in rows:
        stamp = parse_datetime(action.result.get("occurred_at", "")) or action.created_at
        if not start <= stamp < end or action.result.get("category") not in {"combat", "logistics", "exploration"}:
            continue
        amount = max(0, int(action.result.get("merit", 0)))
        faction = action.member.faction
        if amount and faction in totals:
            totals[faction][action.member_id] = totals[faction].get(action.member_id, 0) + amount
    return totals


def _contested_days(season: Season, number: int, activated: datetime) -> int:
    from .contests import effective_contested_actions

    start, end = week_bounds(season, number)
    start = max(start, activated)
    dates = set()
    for action in (
        effective_contested_actions(season).filter(_week_filter(season, number)).select_related("member", "opponent")
    ):
        stamp = parse_datetime(action.result.get("occurred_at", "")) or action.created_at
        if start <= stamp < end and action.opponent and action.member.faction != action.opponent.faction:
            dates.add(action.date)
    return len(dates)


def _target(config: dict[str, Any], active: int) -> int:
    return min(config["goal_maximum"], max(config["goal_minimum"], active * config["goal_per_active"]))


def settle_campaign_weeks_locked(season: Season, now: datetime) -> int:
    if not playability_rules(season)["enabled"]:
        return 0
    from .economy import ensure_factions_locked

    factions = ensure_factions_locked(season, now)
    activated = activate_weekly_locked(season, factions, now)
    config = playability_rules(season)["weekly"]
    settled = 0
    for number in range(week_number(season, activated), 14):
        _, end = week_bounds(season, number)
        if now < end:
            break
        if season.weeks.filter(number=number).exists():
            continue
        from .settlement_pending import has_pending_contributions_locked

        if has_pending_contributions_locked(season, end):
            # 驻军与采集结束时会补记历史贡献；先等队伍结算，避免冻结不完整战果。
            break
        participation = _participation(season, number, activated)
        contested = _contested_days(season, number, activated)
        qualified = all(participation.values()) and contested >= config["minimum_contested_days"]
        scores = {}
        for faction, state in factions.items():
            row = _book(state)["weeks"].setdefault(str(number), {})
            row.setdefault("target", _target(config, len(participation[faction])))
            row.update(
                {
                    "merit": sum(participation[faction].values()),
                    "active_members": len(participation[faction]),
                    "contested_days": contested,
                    "qualified": qualified,
                }
            )
            scores[faction] = int(row.get("score", 0))
            state.save(update_fields=["economy_state"])
        winner = ""
        if qualified and scores["fiction"] != scores["history"]:
            winner = max(scores, key=lambda faction: scores[faction])
        SeasonWeek.objects.create(
            season=season,
            number=number,
            node_key="campaign",
            fiction_points=scores["fiction"],
            history_points=scores["history"],
            matched_members=min(map(len, participation.values())),
            winner=winner,
            settled_at=now,
        )
        for faction, state in factions.items():
            row = _book(state)["weeks"][str(number)]
            row["victory_points"] = (
                config["win_points"] if winner == faction else config["draw_points"] if qualified and not winner else 0
            )
            state.save(update_fields=["economy_state"])
        season.settled_weeks = max(season.settled_weeks, number)
        settled += 1
    season.save(update_fields=["settled_weeks"])
    return settled


def freeze_current_targets_locked(season: Season, now: datetime) -> None:
    """在后台推进时首次见到有效参与就冻结当周目标，之后人数变化不抬高门槛。"""
    if not playability_rules(season)["enabled"] or now >= season.ends_at:
        return
    from .economy import ensure_factions_locked

    factions = ensure_factions_locked(season, now)
    activated = activate_weekly_locked(season, factions, now)
    number = week_number(season, now)
    if all("target" in _book(state)["weeks"].get(str(number), {}) for state in factions.values()):
        return
    totals = _participation(season, number, activated)
    for faction, state in factions.items():
        if totals[faction]:
            row = _book(state)["weeks"].setdefault(str(number), {})
            if "target" not in row:
                recent = season.population.get("factions", {}).get(faction, {}).get("count", 0)
                row["target"] = _target(playability_rules(season)["weekly"], max(len(totals[faction]), int(recent)))
                state.save(update_fields=["economy_state"])


def weekly_victory_scores(season: Season) -> dict[str, int]:
    return {
        state.faction: sum(
            int(row.get("victory_points", 0))
            for row in state.economy_state.get("weekly_campaign", {}).get("weeks", {}).values()
        )
        for state in season.faction_states.all()
    }


def weekly_campaign_context(season: Season, member: SeasonMember | None, now: datetime) -> dict[str, Any]:
    """纯读视图：不会创建周结算、冻结目标或发奖。"""
    if not playability_rules(season)["enabled"]:
        return {"enabled": False}
    config = playability_rules(season)["weekly"]
    states = {state.faction: state for state in season.faction_states.all()}
    state = states.get(member.faction) if member else None
    book = state.economy_state.get("weekly_campaign", {}) if state else {}
    activated = parse_datetime(book.get("activated_at", "")) or now
    claims = set(member.claims.values_list("key", flat=True)) if member else set()
    rows = []
    current = week_number(season, min(now, season.ends_at - timedelta(microseconds=1)))
    for number in range(week_number(season, activated), current + 1):
        _, end = week_bounds(season, number)
        totals = _participation(season, number, activated).get(member.faction, {}) if member else {}
        saved = book.get("weeks", {}).get(str(number), {})
        merit = totals.get(member.pk, 0) if member else 0
        target = saved.get("target", _target(config, len(totals)))
        complete = end <= now and season.weeks.filter(number=number).exists()
        eligible = merit >= config["participation_merit"]
        team_met = sum(totals.values()) >= target
        rows.append(
            {
                "number": number,
                "ends_at": end.isoformat(),
                "settled": complete,
                "merit": merit,
                "participation_merit": config["participation_merit"],
                "team_merit": sum(totals.values()),
                "target": target,
                "team_met": team_met,
                "personal_claimed": f"campaign:weekly:{number}:personal" in claims,
                "team_claimed": f"campaign:weekly:{number}:team" in claims,
                "personal_claimable": complete and eligible and f"campaign:weekly:{number}:personal" not in claims,
                "team_claimable": complete and eligible and team_met and f"campaign:weekly:{number}:team" not in claims,
                "personal_items": config["personal_items"],
                "team_items": config["team_items"],
                "score": saved.get("score", 0),
                "victory_points": saved.get("victory_points", 0),
            }
        )
    from .warfare import campaign_open

    return {
        "enabled": True,
        "current_week": current,
        "weeks": rows,
        "victory_points": weekly_victory_scores(season),
        "reward_only": not campaign_open(season, now),
    }


def claim_weekly_locked(member: SeasonMember, manor: Manor, number: int) -> dict[str, Any]:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("周奖励领取须在持有赛季锁的事务内执行")
    from django.utils import timezone

    from .actions import _grant

    now = timezone.now()
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 13:
        raise GameError("周次无效")
    context = weekly_campaign_context(member.season, member, now)
    row = next((row for row in context.get("weeks", []) if row["number"] == number), None)
    if not row or not row["settled"]:
        raise GameError("该周尚未结算")
    items: dict[str, int] = {}
    granted = []
    for kind in ("personal", "team"):
        if not row[f"{kind}_claimable"]:
            continue
        key = f"campaign:weekly:{number}:{kind}"
        # Season 行锁串行化领奖；唯一键额外防止重复发放。
        _, created = SeasonClaim.objects.get_or_create(member=member, key=key)
        if created:
            for item, quantity in row[f"{kind}_items"].items():
                items[item] = items.get(item, 0) + quantity
            granted.append(kind)
    if not granted:
        raise GameError("该周贡献不足、共同目标未达成或奖励已领取")
    _grant(manor, items)
    return {"message": f"已领取第 {number} 周参与奖励", "items": items, "rewards": granted}


def record_capture_points_locked(
    season: Season, state: SeasonFaction, now: datetime, points: int, activated: datetime
) -> None:
    """即时破城分属于实际发生的一周，区别于跨周持有区间的结算点。"""
    if now < activated or now >= season.ends_at:
        return
    row = _book(state)["weeks"].setdefault(str(week_number(season, now)), {})
    row["score"] = row.get("score", 0) + points
    row["capture_points"] = row.get("capture_points", 0) + points
