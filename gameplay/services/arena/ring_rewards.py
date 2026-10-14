from datetime import date, datetime, time, timedelta

from django.db.models import Exists, F, IntegerField, OuterRef, Sum
from django.db.models.functions import Cast
from django.utils import timezone

from gameplay.models import ArenaRingAction, ArenaRingDaily, ArenaRingMatch, ArenaRingTenure, Manor, ResourceEvent
from gameplay.services.inventory.core import add_item_to_inventory_locked
from gameplay.services.resources import log_resource_gain

from .ring_rules import load_ring_rules


def daily_for(manor: Manor, day: date) -> ArenaRingDaily:
    return ArenaRingDaily.objects.get_or_create(manor=manor, date=day)[0]


def credit_coins(manor: Manor, amount: int, note: str) -> None:
    if amount:
        manor.arena_coins += amount
        manor.save(update_fields=["arena_coins"])
        log_resource_gain(manor, {"arena_coins": amount}, ResourceEvent.Reason.BATTLE_REWARD, note)


def settle_time(tenure: ArenaRingTenure, manor: Manor, now: datetime) -> None:
    until = min(now, tenure.ends_at, tenure.ended_at or now)
    cursor = tenure.settled_at
    while cursor < until:
        local = timezone.localtime(cursor)
        boundary = timezone.make_aware(
            datetime.combine(local.date() + timedelta(days=1), time.min),
            timezone.get_current_timezone(),
        )
        end = min(until, boundary)
        seconds = int((end - cursor).total_seconds())
        daily = daily_for(manor, local.date())
        daily.seconds += seconds
        rules = load_ring_rules()
        coins = max(
            daily.time_coins,
            min(
                rules["daily_time_coins"],
                daily.seconds // rules["time_interval_seconds"] * rules["time_interval_coins"],
            ),
        )
        credit_coins(manor, coins - daily.time_coins, "擂台守擂时间奖励")
        daily.time_coins = coins
        daily.save()
        # Preserve subsecond remainders between refreshes.
        cursor += timedelta(seconds=seconds)
        if cursor < end:
            if end == boundary:
                cursor = boundary
            else:
                break
    tenure.settled_at = cursor
    tenure.save(update_fields=["settled_at"])


def effective_streak(tenure: ArenaRingTenure) -> int:
    # 同一对玩家当天只有首场对战可计入奖励连胜，跨擂台也不重复计算。
    earlier_pair_matches = ArenaRingMatch.objects.filter(
        pair_low=OuterRef("pair_low"),
        pair_high=OuterRef("pair_high"),
        reward_date=OuterRef("reward_date"),
        pk__lt=OuterRef("pk"),
    )
    return (
        tenure.matches.filter(winner_id=F("defender_id"))
        .alias(repeated_pair=Exists(earlier_pair_matches))
        .filter(repeated_pair=False)
        .count()
    )


def credit_streak_reward(manor: Manor, amount: int, day: date, *, breaking: bool = False) -> int:
    if amount <= 0:
        return 0
    rules = load_ring_rules()
    note = "演武台终结连胜奖励" if breaking else "演武台守擂连胜奖励"
    limit = rules["daily_break_streak_coins" if breaking else "daily_streak_coins"]
    received = (
        ResourceEvent.objects.filter(
            manor=manor,
            resource_type="arena_coins",
            reason=ResourceEvent.Reason.BATTLE_REWARD,
            note=note,
            created_at__date=day,
        ).aggregate(total=Sum("delta"))["total"]
        or 0
    )
    granted = min(amount, max(0, limit - received))
    credit_coins(manor, granted, note)
    return granted


def credit_streak_chunqiu_reward(manor: Manor, streak: int, day: date) -> int:
    """沿用有效连胜资格，以事务内的操作凭证统计跨任期每日奖励。"""
    rules = load_ring_rules()
    amount = rules.get(f"streak_{streak}_chunqiu_coins", 0)
    if not amount:
        return 0
    received = (
        ArenaRingAction.objects.filter(
            kind="challenge",
            result__streak_reward_manor_id=manor.pk,
            result__streak_reward_date=day.isoformat(),
        ).aggregate(total=Sum(Cast("result__streak_chunqiu_coins", IntegerField())))["total"]
        or 0
    )
    granted = min(amount, max(0, rules["daily_streak_chunqiu_coins"] - received))
    if granted:
        add_item_to_inventory_locked(manor, "chunqiu_coin", granted)
    return granted
