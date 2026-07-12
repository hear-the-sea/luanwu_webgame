from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

from django.utils import timezone

import gameplay.services.arena.coop_core as arena_coop_core
import gameplay.services.arena.core as arena_core
from common.constants.resources import ResourceType
from core.utils.time_scale import scale_duration
from gameplay.models import ArenaCoopEntry, ArenaEntry, ArenaTournament, Manor
from gameplay.services.arena.rewards import load_arena_reward_catalog, select_weekly_blueprint_key
from gameplay.utils.template_loader import get_item_template_names_by_keys

ARENA_PRIMARY_EVENT_BASE = {
    "key": "tianxia_buwu",
    "name": "天下布武",
    "description": (
        "江湖传闻，这场惊动诸侯、令诸子百家连夜卷铺盖参赛的“天下布武”大乱斗，是为了争夺失传的绝世兵法？ "
        "纯属扯淡！真相其实是：几位隐世宗师在华山之巅论剑时，因为“豆腐脑到底该吃甜的还是咸的”吵急了眼！最后大家干脆掀了桌子，决定用拳头定夺天下口味的最终霸权！ "
        "在这里，没有什么家国大义和血海深仇，纯粹是各路诸侯和武林高手吃饱了撑的！为了赢取那口传说中“煮肉永远不塞牙的至尊青铜鼎”，所有人已经打得六亲不认。 "
        "别管什么江湖规矩了，带上你的门客上擂台吧！打赢了名垂青史，打输了，就罚去给对方洗半年夜壶！"
    ),
}


def build_summary_metrics(*rows: tuple[str, str]) -> list[dict[str, str]]:
    return [{"label": label, "value": value} for label, value in rows]


def build_reward_rows(manor: Manor) -> list[dict]:
    catalog = load_arena_reward_catalog()
    resource_labels = dict(ResourceType.choices)
    all_item_keys: set[str] = set()
    for reward in catalog.values():
        all_item_keys.update(reward.items.keys())
        all_item_keys.update(option.item_key for option in reward.random_items)
        if reward.rotating_blueprint_pool is not None:
            all_item_keys.add(select_weekly_blueprint_key(reward.rotating_blueprint_pool))
    item_labels = get_item_template_names_by_keys(all_item_keys)

    rows: list[dict] = []
    for reward in catalog.values():
        rotating_blueprint_row = None
        if reward.rotating_blueprint_pool is not None:
            blueprint_key = select_weekly_blueprint_key(reward.rotating_blueprint_pool)
            rotating_blueprint_row = {
                "key": blueprint_key,
                "label": item_labels.get(blueprint_key, blueprint_key),
                "amount": 1,
            }
        resource_rows = [
            {
                "key": key,
                "label": resource_labels.get(key, key),
                "amount": amount,
            }
            for key, amount in reward.resources.items()
        ]
        item_rows = [
            {
                "key": key,
                "label": item_labels.get(key, key),
                "amount": amount,
            }
            for key, amount in reward.items.items()
        ]
        total_random_weight = sum(option.weight for option in reward.random_items)
        random_item_rows = []
        for option in reward.random_items:
            chance = (option.weight * 100 / total_random_weight) if total_random_weight > 0 else 0
            chance_float = float(chance)
            chance_text = f"{int(chance_float)}%" if chance_float.is_integer() else f"{chance_float:.2f}%"
            random_item_rows.append(
                {
                    "key": option.item_key,
                    "label": item_labels.get(option.item_key, option.item_key),
                    "amount": option.amount,
                    "weight": option.weight,
                    "chance_text": chance_text,
                }
            )
        rows.append(
            {
                "key": reward.key,
                "name": reward.name,
                "description": reward.description,
                "cost_coins": reward.cost_coins,
                "daily_limit": reward.daily_limit,
                "resources": reward.resources,
                "items": reward.items,
                "resource_rows": resource_rows,
                "item_rows": item_rows,
                "random_item_rows": random_item_rows,
                "rotating_blueprint_row": rotating_blueprint_row,
                "can_afford": manor.arena_coins >= reward.cost_coins,
            }
        )
    rows.sort(key=lambda item: (item["cost_coins"], item["key"]))
    return rows


def running_row_sort_key(row: dict[str, Any]) -> tuple[int, Any, int]:
    tournament = cast(ArenaTournament, row["tournament"])
    return (
        0 if row["is_mine"] else 1,
        tournament.next_round_at or timezone.now(),
        tournament.id,
    )


def today_participation_stats(manor: Manor) -> tuple[int, int]:
    today = timezone.localdate()
    if manor.arena_participation_date == today:
        today_participations = max(0, int(manor.arena_participations_today or 0))
    else:
        current_time = timezone.localtime(timezone.now())
        day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        today_participations = ArenaEntry.objects.filter(
            manor=manor,
            joined_at__gte=day_start,
            joined_at__lt=day_end,
        ).count()
    remaining_daily = max(0, arena_core.ARENA_DAILY_PARTICIPATION_LIMIT - today_participations)
    return today_participations, remaining_daily


def build_common_context(manor: Manor) -> dict:
    today_participations, remaining_daily = today_participation_stats(manor)
    round_interval_seconds = max(1, scale_duration(arena_core.ARENA_ROUND_INTERVAL_SECONDS, minimum=1))
    if round_interval_seconds % 60 == 0:
        round_interval_label = f"{round_interval_seconds // 60} 分钟"
    else:
        round_interval_label = f"{round_interval_seconds} 秒"

    return {
        "manor": manor,
        "today_participations": today_participations,
        "remaining_daily": remaining_daily,
        "daily_limit": arena_core.ARENA_DAILY_PARTICIPATION_LIMIT,
        "max_guests_per_entry": arena_core.ARENA_MAX_GUESTS_PER_ENTRY,
        "arena_event": {
            **ARENA_PRIMARY_EVENT_BASE,
            "subtitle": f"{arena_core.ARENA_TOURNAMENT_PLAYER_LIMIT} 人门客淘汰赛",
            "player_limit": arena_core.ARENA_TOURNAMENT_PLAYER_LIMIT,
            "round_interval_seconds": round_interval_seconds,
            "round_interval_label": round_interval_label,
            "summary_metrics": build_summary_metrics(
                ("报名人数", f"{arena_core.ARENA_TOURNAMENT_PLAYER_LIMIT} 人满员开赛"),
                ("回合频率", f"每 {round_interval_label} 1 轮"),
                ("每日次数", f"{arena_core.ARENA_DAILY_PARTICIPATION_LIMIT} 次"),
            ),
        },
        "registration_silver_cost": arena_core.ARENA_REGISTRATION_SILVER_COST,
        "can_afford_registration": manor.silver >= arena_core.ARENA_REGISTRATION_SILVER_COST,
    }


def today_coop_participation_stats(manor: Manor) -> tuple[int, int]:
    today = timezone.localdate()
    if manor.arena_coop_participation_date == today:
        today_participations = max(0, int(manor.arena_coop_participations_today or 0))
    else:
        current_time = timezone.localtime(timezone.now())
        day_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        today_participations = (
            ArenaCoopEntry.objects.filter(
                manor=manor,
                joined_at__gte=day_start,
                joined_at__lt=day_end,
            )
            .exclude(status=ArenaCoopEntry.Status.CANCELLED)
            .count()
        )
    remaining_daily = max(0, arena_coop_core.ARENA_COOP_DAILY_PARTICIPATION_LIMIT - today_participations)
    return today_participations, remaining_daily
