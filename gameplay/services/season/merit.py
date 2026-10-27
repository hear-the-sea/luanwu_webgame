"""按有效事件发放贡献；统一每日上限、分项账本及确定性分配。"""

from __future__ import annotations

import hashlib
from datetime import datetime
from fractions import Fraction
from typing import Any
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from core.exceptions import GameError
from gameplay.models import SeasonAction, SeasonClaim

from .development_rules import feature_rules
from .rules import season_state


def daily_merit_summary(member: Any, now: datetime) -> dict[str, Any]:
    config = feature_rules(member.season, "contribution")
    totals = {"combat": 0, "logistics": 0}
    for action in member.actions.filter(
        date=timezone.localdate(now), kind__in=("merit_credit", "combat", "logistics", "exploration", "upgrade")
    ).only("result", "kind"):
        if action.kind != "merit_credit" and "merit_credit_id" in action.result:
            continue
        category = action.result.get("category", "combat" if action.kind == "combat" else "logistics")
        if category in totals:
            totals[category] += int(action.result.get("merit", 0))
    cap = config["daily_cap"] if config["enabled"] else None
    earned = sum(totals.values())
    return {**totals, "earned": earned, "cap": cap, "remaining": max(0, cap - earned) if cap is not None else None}


def grant_merit_locked(
    member: Any, amount: int, category: str, now: datetime, *, event_key: str, reason: str = ""
) -> dict[str, Any]:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("贡献结算必须在赛季事务内执行")
    if category not in {"combat", "logistics"} or isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        raise GameError("贡献结算参数无效")
    key = "merit:" + hashlib.sha256(event_key.encode()).hexdigest()[:48]
    summary = daily_merit_summary(member, now)
    if member.claims.filter(key=key).exists():
        return {
            "merit": 0,
            "requested_merit": amount,
            "category": category,
            "daily_remaining": summary["remaining"],
            "duplicate": True,
            "reason": reason,
        }
    credited = min(amount, summary["remaining"]) if summary["remaining"] is not None else amount
    member.refresh_from_db(fields=["merit", "coins", "combat_merit", "logistics_merit"])
    member.merit += credited
    member.coins += credited
    field = "combat_merit" if category == "combat" else "logistics_merit"
    setattr(member, field, getattr(member, field) + credited)
    member.save(update_fields=["merit", "coins", field])
    receipt = {
        "occurred_at": now.isoformat(),
        "merit": credited,
        "requested_merit": amount,
        "category": category,
        "daily_remaining": max(0, summary["remaining"] - credited) if summary["remaining"] is not None else None,
        "duplicate": False,
        "reason": reason,
    }
    SeasonClaim.objects.create(member=member, key=key)
    from .playability_rules import playability_rules
    from .weekly_campaign import week_number

    ledger = SeasonAction.objects.create(
        member=member,
        token=uuid4(),
        request_hash="merit-event",
        kind="merit_credit",
        date=timezone.localdate(now),
        week=(
            week_number(member.season, now)
            if playability_rules(member.season)["enabled"]
            else season_state(member.season, now)["week"]
        ),
        result=receipt,
    )
    receipt["merit_credit_id"] = ledger.pk
    if credited:
        from .weekly_campaign import freeze_current_targets_locked

        freeze_current_targets_locked(member.season, now)
    return receipt


def split_integer(total: int, weights: dict[int, Any]) -> dict[int, int]:
    weights = {key: Fraction(max(0, value)) for key, value in weights.items()}
    size = sum(weights.values())
    if not size:
        return {key: 0 for key in weights}
    exact = {key: Fraction(total) * weight / size for key, weight in weights.items()}
    result = {key: int(value) for key, value in exact.items()}
    for key in sorted(exact, key=lambda k: (-(exact[k] - result[k]), k))[: total - sum(result.values())]:
        result[key] += 1
    return result


def battle_merit_shares(
    runs: list[Any], report: Any, side: str, won: bool, *, npc: bool = False
) -> dict[int, dict[str, Any]]:
    config = feature_rules(runs[0].member.season, "contribution")
    scores = {run.pk: {"damage": Fraction(0), "tank": Fraction(0), "support": Fraction(0)} for run in runs}
    guest_owner = {gid: run.pk for run in runs for gid in run.result.get("coalition_guest_ids", run.guest_ids)}
    fronts = {run.pk: sum(run.result.get("coalition_front_troops", run.troops).values()) for run in runs}
    total_front = sum(fronts.values())

    def add(metric: str, guest_id: Any, amount: int, *, troop: bool = False) -> None:
        if amount <= 0:
            return
        owner = guest_owner.get(guest_id)
        if owner is not None:
            scores[owner][metric] += amount
        elif troop and total_front:
            for run_id, count in fronts.items():
                scores[run_id][metric] += Fraction(amount * count, total_front)

    def events_in(items: Any) -> Any:
        for event in items:
            yield event
            for key in ("additional_targets", "additional_attacks", "passive_events_before", "passive_events_after"):
                yield from events_in(event.get(key, []))

    for round_data in report.rounds:
        for event in events_in(round_data.get("events", [])):
            event_side = event.get("side")
            applied = max(0, int(event.get("applied_damage", 0) or 0))
            if applied:
                if event_side == side:
                    add("damage", event.get("actor_guest_id"), applied, troop=event.get("kind") == "troop")
                elif event_side in {"attacker", "defender"}:
                    add(
                        "tank",
                        event.get("target_guest_id"),
                        applied,
                        troop=event.get("target_state", {}).get("kind") == "troop",
                    )
            for field in ("reflect_applied_damage", "counter_applied_damage"):
                secondary = max(0, int(event.get(field, 0) or 0))
                if event_side != side:
                    add(
                        "damage",
                        event.get("target_guest_id"),
                        secondary,
                        troop=event.get("target_state", {}).get("kind") == "troop",
                    )
                else:
                    add("tank", event.get("actor_guest_id"), secondary, troop=event.get("kind") == "troop")
            healed = max(0, int(event.get("effective_healed", event.get("healed", 0)) or 0))
            if healed and event_side == side:
                add(
                    "support",
                    event.get("source_guest_id", event.get("actor_guest_id")),
                    healed,
                    troop=event.get("unit_state", {}).get("kind") == "troop",
                )
    # 老战报缺事件时只能按实际生命损失识别承伤，不根据战力或过量伤害虚构表现。
    if not any(any(row.values()) for row in scores.values()):
        entries = report.attacker_team if side == "attacker" else report.defender_team
        for entry in entries:
            add(
                "tank",
                entry.get("guest_id"),
                max(0, int(entry.get("initial_hp", 0)) - max(0, int(entry.get("remaining_hp", 0)))),
            )
    effective = {pk: row for pk, row in scores.items() if sum(row.values()) > 0}
    if not effective or sum(sum(row.values()) for row in scores.values()) < config.get("minimum_battle_value", 100):
        return {
            run.pk: {"merit": 0, "effective": False, "metrics": {k: int(v) for k, v in scores[run.pk].items()}}
            for run in runs
        }
    pool = (config["npc_pool_per_player"] if npc else config["battle_pool_per_player"]) * len(runs)
    if not won:
        pool = pool * config["loss_percent"] // 100
    base = split_integer(pool * config["participation_percent"] // 100, {run.pk: 1 for run in runs})
    weighted = {pk: Fraction(0) for pk in effective}
    for metric in ("damage", "tank", "support"):
        total = sum(row[metric] for row in effective.values())
        if total:
            for pk, row in effective.items():
                weighted[pk] += Fraction(config[metric + "_weight"]) * row[metric] / total
    bonus = split_integer(pool - sum(base.values()), weighted)
    return {
        run.pk: {
            "merit": base.get(run.pk, 0) + bonus.get(run.pk, 0),
            "effective": True,
            "metrics": {k: int(v) for k, v in scores[run.pk].items()},
        }
        for run in runs
    }


def progress_merit_locked(run: Any, now: datetime, amount: int) -> int:
    """每阵营每城每日有固定拆城预算，维修不能重置预算。"""
    config = feature_rules(run.member.season, "contribution")
    if not config["enabled"]:
        return amount
    day = timezone.localdate(now)
    used = sum(
        int(a.result.get("requested_merit", a.result.get("merit", 0)))
        for a in SeasonAction.objects.filter(
            member__season=run.member.season,
            member__faction=run.member.faction,
            node_key=run.node_key,
            date=day,
            request_hash="site-progress",
        )
    )
    return min(max(0, config["demolition_daily_budget"] - used), amount)


def award_capture_locked(season: Any, node_key: str, faction: str, cycle: int, now: datetime) -> None:
    config = feature_rules(season, "contribution")
    if not config["enabled"]:
        return
    day = timezone.localdate(now)
    if SeasonAction.objects.filter(
        member__season=season, member__faction=faction, node_key=node_key, kind="capture_bonus", date=day
    ).exists():
        return
    weights: dict[int, int] = {}
    members = {}
    for action in SeasonAction.objects.filter(
        member__season=season, member__faction=faction, node_key=node_key, kind="combat"
    ).select_related("member"):
        if action.result.get("capture_cycle") != cycle or action.result.get("combat_side") != "attacker":
            continue
        effect = int(action.result.get("requested_merit", action.result.get("merit", 0)))
        if effect:
            weights[action.member_id] = weights.get(action.member_id, 0) + effect
            members[action.member_id] = action.member
    for member_id, share in split_integer(config["capture_bonus_pool"], weights).items():
        member = members[member_id]
        receipt = grant_merit_locked(
            member,
            share,
            "combat",
            now,
            event_key=f"capture:{node_key}:{day}:{faction}:{member_id}",
            reason="本轮清军与破城共同占领奖励",
        )
        SeasonAction.objects.create(
            member=member,
            token=uuid4(),
            request_hash="capture-shared-bonus",
            kind="capture_bonus",
            node_key=node_key,
            date=day,
            result={**receipt, "message": "参与本轮清军与破城，共享占领奖励", "capture_cycle": cycle},
        )
