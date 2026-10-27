"""赛季结束时固定阵营内排名及奖励，领取不重新计算名次。"""

from __future__ import annotations

import math
from typing import Any

from django.db import transaction

from core.exceptions import GameError
from gameplay.models import Manor
from gameplay.models.season import Season, SeasonClaim, SeasonMember

from .campaign_rules import campaign_rules
from .playability_rules import playability_rules


def freeze_rankings_locked(season: Season) -> None:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("排名结算须在持有赛季锁的事务内执行")
    config = campaign_rules(season)["rewards"]
    for faction in ("fiction", "history"):
        eligible = list(
            season.members.filter(faction=faction, merit__gte=config["minimum_merit"]).order_by(
                "-merit", "joined_at", "id"
            )
        )
        for rank, member in enumerate(eligible, start=1):
            if member.final_tier:
                continue
            tier = next(
                tier for tier in config["tiers"] if rank <= max(1, math.ceil(len(eligible) * tier["percentile"] / 100))
            )
            items = dict(tier["items"])
            playability = playability_rules(season)
            if playability["enabled"]:
                weekly = playability["weekly"]
                bonus = weekly["ranking_bonus_percent"].get(tier["key"], 0)
                items = {key: amount + amount * bonus // 100 for key, amount in weekly["ranking_base_items"].items()}
            winner_bonus = faction == season.winner
            if winner_bonus:
                for key, quantity in config["winner_items"].items():
                    items[key] = items.get(key, 0) + quantity
            member.final_rank = rank
            member.final_tier = tier["key"]
            member.final_reward = {
                "items": items,
                "rank": rank,
                "tier": tier["key"],
                "tier_name": tier["name"],
                "winner_bonus": winner_bonus,
            }
            member.save(update_fields=["final_rank", "final_tier", "final_reward"])
    season.members.filter(final_tier="").update(final_tier="ineligible", final_reward={})


def claim_final_locked(member: SeasonMember, manor: Manor) -> dict[str, Any]:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("排名奖励领取须在持有赛季锁的事务内执行")
    if not member.season.closed:
        raise GameError("赛季尚未结束，排名奖励暂不可领取")
    items = member.final_reward.get("items", {})
    if not member.final_rank or not items:
        raise GameError("本赛季贡献未达到排名奖励要求")
    key = "campaign:final"
    if member.claims.filter(key=key).exists():
        raise GameError("本赛季排名与胜利奖励已经领取")
    from .actions import _grant

    _grant(manor, items)
    SeasonClaim.objects.create(member=member, key=key)
    return {
        "message": f"已领取阵营第 {member.final_rank} 名奖励"
        + ("与阵营胜利额外奖励" if member.final_reward.get("winner_bonus") else "")
    }
