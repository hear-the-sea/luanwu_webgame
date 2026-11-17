"""报名与声望平衡分组，不要求预先选择出战门客。"""

from __future__ import annotations

from datetime import datetime, timedelta
from fractions import Fraction
from typing import Any

from django.db import transaction

from core.exceptions import GameError
from gameplay.models import Manor
from gameplay.models.season import Season, SeasonAction, SeasonMember
from guests.guest_combat_stats import apply_custom_guest_low_loyalty_penalty, resolve_guest_combat_stats
from guests.models import Guest, GuestStatus

from .campaign_rules import campaign_rules
from .economy import ensure_factions_locked
from .playability_rules import playability_rules
from .population import effective_activity_filter


def registration_ends_at(season: Season) -> datetime:
    return season.starts_at + timedelta(days=campaign_rules(season)["registration"]["days"])


def _require_transaction() -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("赛季报名须在持有赛季锁的事务内执行")


def _weaker(totals: dict[str, tuple[int, int]], preference: str = "random") -> str:
    lowest = min(totals.values())
    choices = [faction for faction, value in totals.items() if value == lowest]
    return preference if preference in choices else choices[0]


def _balance_profiles(members: list[SeasonMember], now: datetime, config: dict[str, Any]) -> dict[int, dict[str, Any]]:
    manor_ids = [member.manor_id for member in members]
    powers: dict[int, list[int]] = {key: [] for key in manor_ids}
    # 出征中的健康门客仍计入实力，避免临时派去个人任务降低分组评分。
    for guest in (
        Guest.objects.filter(manor_id__in=manor_ids)
        .exclude(status=GuestStatus.INJURED)
        .select_related("template")
        .prefetch_related("skills")
    ):
        stats = resolve_guest_combat_stats(guest)
        _, attack, defense = apply_custom_guest_low_loyalty_penalty(guest, stats)
        powers[guest.manor_id].append(attack + defense + stats.max_hp)
    active: dict[int, set[Any]] = {key: set() for key in manor_ids}
    since = now - timedelta(days=config["active_days"])
    for manor_id, day in SeasonAction.objects.filter(
        effective_activity_filter(),
        member__manor_id__in=manor_ids,
        created_at__gte=since,
        created_at__lte=now,
    ).values_list("member__manor_id", "date"):
        active[manor_id].add(day)
    profiles = {}
    for member in members:
        metadata = member.lineup.get("registration", {})
        playtime = metadata.get("playtime", "any") if isinstance(metadata, dict) else "any"
        profiles[member.manor_id] = {
            "power": sum(sorted(powers[member.manor_id], reverse=True)[:3]),
            "activity_days": min(config["active_days"], len(active[member.manor_id])),
            "prestige": member.prestige_snapshot,
            "playtime": playtime if playtime in config["playtime_options"] else "any",
            "scored_at": now.isoformat(),
        }
    return profiles


def _assign_balanced(
    waiting: list[SeasonMember], existing: list[SeasonMember], now: datetime, config: dict[str, Any]
) -> None:
    profiles = _balance_profiles([*existing, *waiting], now, config)
    weights = {
        "activity_days": config["activity_weight"],
        "power": config["power_weight"],
        "prestige": config["prestige_weight"],
    }
    denominators = {key: max(1, sum(row[key] for row in profiles.values())) for key in weights}
    size = max(1, len(profiles))
    total_weight = sum(weights.values())
    groups: dict[str, list[dict[str, Any]]] = {faction: [] for faction in ("fiction", "history")}
    for member in existing:
        groups[member.faction].append(profiles[member.manor_id])

    def burden(faction: str, candidate: dict[str, Any]) -> Fraction:
        teams = {key: [*rows, *([candidate] if key == faction else [])] for key, rows in groups.items()}
        left, right = teams["fiction"], teams["history"]
        score = Fraction(total_weight * abs(len(left) - len(right)), size)
        for key, weight in weights.items():
            score += Fraction(
                weight * abs(sum(row[key] for row in left) - sum(row[key] for row in right)), denominators[key]
            )
        # 不同时段各自均衡；不把填“随时”的玩家当作已承诺全时在线。
        for slot in config["playtime_options"]:
            if slot != "any":
                difference = abs(
                    sum(row["playtime"] == slot for row in left) - sum(row["playtime"] == slot for row in right)
                )
                score += Fraction(config["activity_weight"] * difference, 2 * size)
        return score

    def individual(member: SeasonMember) -> Fraction:
        profile = profiles[member.manor_id]
        return sum((Fraction(weight * profile[key], denominators[key]) for key, weight in weights.items()), Fraction())

    ordered = sorted(waiting, key=lambda member: (-individual(member), -member.prestige_snapshot, member.manor_id))
    # 开季最高声望两名优先分属两边；其余按综合评分平衡。
    if not existing:
        leaders = sorted(waiting, key=lambda member: (-member.prestige_snapshot, member.manor_id))[:2]
        ordered = [*leaders, *(member for member in ordered if member not in leaders)]
    for index, member in enumerate(ordered):
        profile = profiles[member.manor_id]
        if not existing and index < 2:
            faction = ("fiction", "history")[index]
        else:
            scores = {key: burden(key, profile) for key in groups}
            choices = [key for key, score in scores.items() if score == min(scores.values())]
            if existing and len(choices) > 1:
                # 新玩家尚无参与记录时，加入哪边都不改变既有活跃差值；此时补实力较弱方。
                existing_strength = {
                    faction: sum(
                        (
                            Fraction(weight * sum(row[key] for row in groups[faction]), denominators[key])
                            for key, weight in weights.items()
                        ),
                        Fraction(),
                    )
                    for faction in choices
                }
                lowest = min(existing_strength.values())
                choices = [faction for faction in choices if existing_strength[faction] == lowest]
            faction = member.preference if member.preference in choices else choices[0]
        member.faction = faction
        member.lineup = {**member.lineup, "registration": profile}
        groups[faction].append(profile)


def assign_factions_locked(season: Season, now: datetime) -> int:
    _require_transaction()
    if now < registration_ends_at(season):
        return 0
    ensure_factions_locked(season, now)
    waiting = list(season.members.filter(faction="").select_related("manor"))
    if not waiting:
        return 0
    # 使用报名时的声望快照；报名期间的后续变化不追溯改变分组依据。
    waiting.sort(key=lambda member: (-member.prestige_snapshot, member.joined_at, member.pk))
    totals = {faction: (0, 0) for faction in ("fiction", "history")}
    for member in season.members.exclude(faction="").only("faction", "prestige_snapshot"):
        count, prestige = totals[member.faction]
        totals[member.faction] = (count + 1, prestige + member.prestige_snapshot)
    initially_empty = all(count == 0 for count, _ in totals.values())
    pattern = ("fiction", "history", "history", "fiction")
    playable = playability_rules(season)
    if playable["enabled"]:
        _assign_balanced(
            waiting, list(season.members.exclude(faction="").select_related("manor")), now, playable["balance"]
        )
    else:
        for index, member in enumerate(waiting):
            faction = pattern[index % len(pattern)] if initially_empty else _weaker(totals, member.preference)
            member.faction = faction
            count, prestige = totals[faction]
            totals[faction] = (count + 1, prestige + member.prestige_snapshot)
    SeasonMember.objects.bulk_update(waiting, ["faction", "prestige_snapshot", "lineup"])
    from .economy import grant_activity_resources_locked, grant_construction_amount_locked
    from .population import refresh_population_locked

    refresh_population_locked(season, now, force=True)
    for member in waiting:
        member.season = season
        if member.pending_resources:
            grant_activity_resources_locked(member, member.pending_resources, now, refund=True)
        if member.pending_construction:
            grant_construction_amount_locked(member, member.pending_construction, now)
        member.intel_records.filter(faction="").update(faction=member.faction)
        member.pending_resources, member.pending_construction = {}, 0
        member.save(update_fields=["pending_resources", "pending_construction"])
    return len(waiting)


def register_locked(
    season: Season, manor: Manor, preference: str, now: datetime, registration_playtime: str = "any"
) -> SeasonMember:
    _require_transaction()
    rules = campaign_rules(season)
    if (
        season.closed
        or now < season.starts_at
        or now >= min(season.ends_at, season.starts_at + timedelta(days=rules["expeditions"]["combat_days"]))
    ):
        raise GameError("当前不在赛季报名期")
    if preference not in {"fiction", "history", "random"}:
        raise GameError("请选择有效的阵营志愿")
    playable = playability_rules(season)
    if registration_playtime not in playable["balance"]["playtime_options"]:
        raise GameError("请选择有效的常用上线时段")
    if season.members.filter(manor=manor).exists():
        raise GameError("本赛季已报名，不能重复报名或更换阵营")
    assign_factions_locked(season, now)
    faction = ""
    if now >= registration_ends_at(season):
        totals = {key: (0, 0) for key in ("fiction", "history")}
        for member in season.members.exclude(faction="").only("faction", "prestige_snapshot"):
            count, prestige = totals[member.faction]
            totals[member.faction] = (count + 1, prestige + member.prestige_snapshot)
        faction = _weaker(totals, preference)
    member = SeasonMember(
        season=season,
        manor=manor,
        preference=preference,
        prestige_snapshot=manor.prestige,
        faction=faction,
        lineup={"registration": {"playtime": registration_playtime}} if playable["enabled"] else {},
    )
    if playable["enabled"] and now >= registration_ends_at(season):
        _assign_balanced(
            [member], list(season.members.exclude(faction="").select_related("manor")), now, playable["balance"]
        )
    member.save()
    from .population import refresh_population_locked

    refresh_population_locked(season, now)
    return member
