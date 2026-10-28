"""限额集结与整波车轮战；所有状态写入由赛季锁串行。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.exceptions import GameError
from gameplay.models import Manor, Season, SeasonAction, SeasonExpedition, SeasonMember, SeasonRally

from .campaign_rules import campaign_rules, coalition_rules
from .coalition_combat import fight_coalitions_locked
from .combat import npc_opening_profile, pve_lineup
from .development_rules import feature_rules
from .merit import award_capture_locked, battle_merit_shares, split_integer
from .operation_costs import (
    charge_attack_battle_locked,
    charge_demolition_locked,
    mark_outbound_arrival_locked,
    reprice_march_locked,
)
from .rules import season_state
from .simplification import simplification_rules
from .warfare import (
    campaign_open,
    capture_locked,
    check_ownership,
    ensure_sites_locked,
    score_sites_locked,
    validate_destination,
)

logger = logging.getLogger(__name__)


def alive_guests(run: SeasonExpedition, limit: int = 3) -> list[dict[str, Any]]:
    defeated = set(run.result.get("defeated_ids", []))
    snapshots = [
        s for s in run.lineup.get("guests", []) if s.get("guest_id") not in defeated and s.get("current_hp", 0) > 0
    ]
    return sorted(snapshots, key=lambda s: (-(s["attack"] + s["defense"] + s["current_hp"]), s["guest_id"]))[:limit]


def _allocations(runs: list[SeasonExpedition], cap: int, limit: int) -> dict[int, dict[str, int]]:
    pools: dict[tuple[int, str], int] = {}
    for run in runs:
        capacity = sum(s.get("troop_capacity", 0) for s in alive_guests(run, limit))
        total = sum(run.troops.values())
        available = min(capacity, total)
        used = 0
        for key, count in sorted(run.troops.items()):
            amount = count * available // total if total else 0
            pools[(run.pk, key)] = amount
            used += amount
        for key in sorted(run.troops):
            if used >= available:
                break
            if pools[(run.pk, key)] < run.troops[key]:
                pools[(run.pk, key)] += 1
                used += 1
    total = sum(pools.values())
    allowed = min(cap, total)
    counts = {key: count * allowed // total if total else 0 for key, count in pools.items()}
    remainder = allowed - sum(counts.values())
    for key in sorted(pools, key=lambda key: (-(pools[key] * allowed % total) if total else 0, key)):
        if not remainder:
            break
        if counts[key] < pools[key]:
            counts[key] += 1
            remainder -= 1
    return {run.pk: {key: counts.get((run.pk, key), 0) for key in run.troops} for run in runs}


def create_rally_locked(member: SeasonMember, manor: Manor, payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    from .expeditions import dispatch_locked

    config = coalition_rules(member.season)
    if not config["enabled"] or payload.get("mode") not in {"attack", "siege"}:
        raise GameError("该赛季或行动不支持集结")
    target_members = payload.get("rally_target_members", config["max_members"])
    if (
        isinstance(target_members, bool)
        or not isinstance(target_members, int)
        or not config["min_members"] <= target_members <= config["max_members"]
    ):
        raise GameError(f"集结出发人数须为{config['min_members']}—{config['max_members']}人")
    rally = SeasonRally.objects.create(
        season=member.season,
        leader=member,
        node_key=payload.get("node_key", ""),
        kind=payload["mode"],
        gathers_until=now + timedelta(seconds=config["rally_seconds"]),
    )
    rally.result = {
        "unified_siege": bool(simplification_rules(member.season)["enabled"]),
        "target_members": target_members,
    }
    rally.save(update_fields=["result"])
    result = dispatch_locked(member, manor, payload, now, rally=rally)
    if target_members == 1:
        _launch_locked(rally, now)
    if rally.status == "gathering":
        from .reservations import consume_reservations_locked

        consume_reservations_locked(rally, now)
        rally.refresh_from_db()
    message = (
        rally.result["message"]
        if rally.status != "gathering"
        else f"集结已发起，达到{target_members}人提前出发；未达人数则倒计时结束按实际人数出发"
    )
    return {**result, "rally_id": rally.pk, "message": message}


def join_rally_locked(member: SeasonMember, manor: Manor, payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    from .expeditions import dispatch_locked

    rally = SeasonRally.objects.select_for_update().filter(pk=payload.get("rally_id", 0), season=member.season).first()
    if not rally or rally.leader.faction != member.faction or rally.status != "gathering" or now >= rally.gathers_until:
        raise GameError("集结不存在、不属于己方或已经出发")
    config = coalition_rules(member.season)
    if rally.expeditions.filter(status="gathering").count() >= config["max_members"]:
        raise GameError("集结已满员")
    if rally.expeditions.filter(member=member).exists():
        raise GameError("每名玩家只能加入同一集结一次")
    result = dispatch_locked(
        member, manor, {**payload, "node_key": rally.node_key, "mode": rally.kind}, now, rally=rally
    )
    if rally.expeditions.filter(status="gathering").count() >= rally.result.get(
        "target_members", config["max_members"]
    ):
        _launch_locked(rally, now)
    message = (
        rally.result["message"]
        if rally.status != "gathering"
        else f"已加入集结，达到{rally.result.get('target_members', config['max_members'])}人提前出发；未达人数则倒计时结束按实际人数出发"
    )
    return {**result, "rally_id": rally.pk, "message": message}


def _refund_unlaunched(run: SeasonExpedition, now: datetime, message: str) -> None:
    from .expeditions import _finish_return

    Manor.objects.select_for_update().get(pk=run.member.manor_id)
    member = run.member
    member.refresh_from_db()
    from datetime import date

    launched_date = (
        date.fromisoformat(run.result["quota_date"])
        if run.result.get("quota_date")
        else timezone.localdate(run.launched_at)
    )
    if member.troop_date == launched_date:
        member.troops_drawn = max(0, member.troops_drawn - sum(run.initial_troops.values()))
        member.save(update_fields=["troops_drawn"])
    SeasonAction.objects.create(
        member=member,
        token=uuid4(),
        request_hash="unlaunched-rally-refund",
        kind="dispatch_refund",
        date=launched_date,
        week=season_state(member.season, now)["week"],
        node_key=run.node_key,
        result={"run_id": run.pk, "message": message},
    )
    run.result = {**run.result, "message": message, "quota_refunded": True}
    _finish_return(run, now)


def cancel_rally_locked(member: SeasonMember, rally_id: int, now: datetime) -> dict[str, Any]:
    rally = SeasonRally.objects.select_for_update().filter(pk=rally_id, season=member.season, leader=member).first()
    if not rally or rally.status != "gathering":
        raise GameError("只能由发起人取消尚未出发的集结")
    _cancel_locked(rally, now, "集结取消，未出发队伍已归队，领兵额度与出征次数已退回")
    return {"rally_id": rally.pk, "message": rally.result["message"]}


def _cancel_locked(rally: SeasonRally, now: datetime, message: str) -> None:
    for run in rally.expeditions.filter(status="gathering").order_by("member__manor_id", "pk"):
        _refund_unlaunched(run, now, message)
    rally.status = "cancelled"
    rally.result = {**rally.result, "message": message}
    rally.save(update_fields=["status", "result"])


def _launch_locked(rally: SeasonRally, now: datetime) -> None:
    from .expeditions import _schedule

    runs = list(rally.expeditions.filter(status="gathering").order_by("pk"))
    if len(runs) < coalition_rules(rally.season)["min_members"] or not campaign_open(rally.season, now):
        _cancel_locked(rally, now, "集结已取消：行动期已结束或成员全部离开，额度已退回")
        return
    travel = max(run.travel_seconds for run in runs)
    scheduled_arrival = min(now, rally.gathers_until) + timedelta(seconds=travel)
    try:
        validate_destination(
            rally.season,
            rally.node_key,
            rally.kind,
            now,
            legacy_modes=not rally.result.get("unified_siege"),
            city_arrival_at=scheduled_arrival,
        )
        site = ensure_sites_locked(rally.season, now)[rally.node_key]
        check_ownership(site, rally.leader.faction, rally.kind, now)
    except GameError as exc:
        _cancel_locked(rally, now, f"目标状态变化，未出发集结取消：{exc}")
        return
    cutoff = rally.season.starts_at + timedelta(days=campaign_rules(rally.season)["expeditions"]["combat_days"])
    if now + timedelta(seconds=travel) >= min(cutoff, rally.season.ends_at):
        _cancel_locked(rally, now, "集结无法在行动期内抵达，未出发额度已退回")
        return
    try:
        with transaction.atomic():
            for run in runs:
                reprice_march_locked(run, travel, now)
    except GameError as exc:
        for run in runs:
            run.refresh_from_db()
        _cancel_locked(rally, now, f"集结补给不足，未出发队伍已取消：{exc}")
        return
    rally.status = "marching"
    rally.launched_at = now
    rally.due_at = now + timedelta(seconds=travel)
    rally.result = {
        **rally.result,
        "wave": 0,
        "player_count": len(runs),
        "message": "集结军已共同出发",
        "scheduled_arrival_at": scheduled_arrival.isoformat(),
    }
    if all(run.result.get("npc_opening") for run in runs):
        source = runs[0].result["npc_opening"]
        rally.result["npc_opening"] = npc_opening_profile(
            rally.season,
            [guest for run in runs for guest in alive_guests(run)],
            len(runs),
            source["base_level"],
            rules_override=source["rules"],
        )
    rally.save(update_fields=["status", "launched_at", "due_at", "result"])
    for run in runs:
        run.status = "outbound"
        run.launched_at = now
        run.travel_seconds = travel
        run.due_at = rally.due_at
        run.result = {**run.result, "message": "集结军共同出征，到达后进入整波车轮战"}
        run.save(update_fields=["status", "launched_at", "travel_seconds", "due_at", "result"])
        _schedule(run)


def _resolve_locked(rally: SeasonRally, runs: list[SeasonExpedition], now: datetime, message: str) -> None:
    from .expeditions import _start_return

    for run in runs:
        if run.status == "outbound":
            _start_return(run, now, message)
    rally.status = "resolved"
    rally.result = {**rally.result, "message": message}
    rally.save(update_fields=["status", "result"])


def _next_wave(rally: SeasonRally, runs: list[SeasonExpedition], now: datetime, message: str) -> None:
    from .expeditions import _schedule

    rally.due_at = now + timedelta(seconds=coalition_rules(rally.season)["wave_interval_seconds"])
    rally.result = {**rally.result, "message": message}
    rally.save(update_fields=["due_at", "result"])
    for run in runs:
        if run.status == "outbound":
            run.due_at = rally.due_at
            run.result = {**run.result, "message": message}
            run.save(update_fields=["due_at", "result"])
            _schedule(run)


def _record_participants(
    runs: list[SeasonExpedition],
    report: Any,
    won: bool,
    now: datetime,
    *,
    defending: bool,
    opponents: list[SeasonExpedition] | None = None,
) -> None:
    from .expeditions import _record_combat

    exp = campaign_rules(runs[0].member.season)["expeditions"]
    enhanced = feature_rules(runs[0].member.season, "contribution")["enabled"]
    shares = (
        battle_merit_shares(
            runs, report, "defender" if defending else "attacker", won, npc=not opponents and not defending
        )
        if enhanced
        else {}
    )
    for run in runs:
        merit = (
            exp["defend_merit"]
            if defending and won
            else 0 if defending else exp["attack_merit" if won else "failure_merit"]
        )
        activity = ("defense_win" if won else None) if defending else ("attack_win" if won else "attack_loss")
        if enhanced:
            merit = shares[run.pk]["merit"]
            if not shares[run.pk]["effective"]:
                activity = None
        _record_combat(
            run.member,
            run,
            report,
            merit,
            now,
            "联合驻防交战" if defending else "集结军车轮战",
            activity_key=activity,
            opponent=opponents[0].member if opponents else None,
            metrics=shares[run.pk]["metrics"] if enhanced else {},
        )


def _wave_locked(rally: SeasonRally, now: datetime) -> None:
    from .recovery import settle_auto_repairs_locked

    settle_auto_repairs_locked(rally.season, now)
    from .expeditions import _record_progress, _start_return

    config = coalition_rules(rally.season)
    runs = list(rally.expeditions.filter(status="outbound").select_related("member__season").order_by("pk"))
    active = []
    for run in runs:
        if alive_guests(run, config["commander_guest_limit"]):
            active.append(run)
        else:
            _start_return(run, now, "门客均已战败，退出集结返程")
    if not active:
        _resolve_locked(rally, [], now, "集结军已全部撤回或战败")
        return
    qualification_at = rally.due_at
    if not rally.result.get("wave", 0):
        qualification_at = parse_datetime(rally.result.get("scheduled_arrival_at", "")) or qualification_at
    try:
        validate_destination(
            rally.season,
            rally.node_key,
            rally.kind,
            now,
            legacy_modes=not rally.result.get("unified_siege"),
            city_arrival_at=qualification_at,
        )
        site = ensure_sites_locked(rally.season, now)[rally.node_key]
        check_ownership(site, rally.leader.faction, rally.kind, now)
    except GameError as exc:
        _resolve_locked(rally, active, now, f"目标状态变化，集结军返程：{exc}")
        return
    wave = int(rally.result.get("wave", 0)) + 1
    if wave > config["max_waves"]:
        _resolve_locked(rally, active, now, "本次车轮战达到波次上限，队伍返程")
        return
    rally.result = {**rally.result, "wave": wave, "player_count": len(active)}
    from .supply import settle_supplies_locked

    settle_supplies_locked(rally.season, now)
    site.refresh_from_db()
    for run in active:
        mark_outbound_arrival_locked(run, now)
        run.result = {**run.result, "capture_cycle": site.capture_cycle}
    score_sites_locked(rally.season, now)
    from .hitpoints import hitpoint_rules

    if hitpoint_rules(rally.season)["enabled"] and not site.durability:
        from .hitpoint_waves import capture_hp_site_locked

        capture_hp_site_locked(rally, site, active, now)
        return
    defending = []
    seen = set()
    for run in (
        SeasonExpedition.objects.filter(
            member__season=rally.season,
            member__faction=site.owner,
            node_key=rally.node_key,
            kind="garrison",
            status="stationed",
            due_at__gt=now,
        )
        .select_related("member__season")
        .order_by("launched_at", "pk")
    ):
        if run.member_id not in seen and alive_guests(run, config["commander_guest_limit"]):
            seen.add(run.member_id)
            defending.append(run)
        if len(defending) >= len(active):
            break
    from .supply import prepare_defense_locked

    att_alloc = _allocations(active, config["frontline_capacity"], config["commander_guest_limit"])
    def_alloc = _allocations(defending, config["frontline_capacity"], config["commander_guest_limit"])
    # 守方只支付实际抽中的前线；全体缺粮撤回时不会虚构一场交战。
    defending = prepare_defense_locked(site, defending, now, allocations=def_alloc)
    def_alloc = {run.pk: def_alloc[run.pk] for run in defending}
    site.front_allocations = {str(pk): amounts for pk, amounts in def_alloc.items()}
    site.front_line = {}
    for amounts in def_alloc.values():
        for key, count in amounts.items():
            site.front_line[key] = site.front_line.get(key, 0) + count
    site.reinforcement_at = now + timedelta(seconds=config["wave_interval_seconds"])
    from .fortifications import breach_capture, tower_state
    from .hitpoints import hitpoint_rules

    if hitpoint_rules(rally.season)["enabled"]:
        from .hitpoint_waves import hitpoint_wave_locked

        hitpoint_wave_locked(rally, site, active, defending, att_alloc, def_alloc, now, wave, config)
        return
    if defending or site.guards or tower_state(site)["hp"]:
        charge_attack_battle_locked(active, att_alloc, now)
        npc = None
        if not defending and site.guards:
            level = site.npc_profile.get(
                "level", campaign_rules(rally.season)["expeditions"]["enemy_levels"][rally.node_key]
            )
            fallback = "mountain:ruins" if rally.node_key.startswith("mountain:") else "forest:lodge"
            npc = pve_lineup(
                fallback,
                "据点守军",
                0,
                active[0].seed + wave,
                level_override=level,
                opening_profile=rally.result.get("npc_opening"),
            )
        if not defending and not site.guards:
            npc = {"guests": []}
        report, _, _ = fight_coalitions_locked(
            active,
            defending,
            now,
            attacker_allocations=att_alloc,
            defender_allocations=def_alloc,
            commander_guest_limit=config["commander_guest_limit"],
            npc_lineup=npc,
            site=site,
        )
        won = report.winner == "attacker"
        _record_participants(active, report, won, now, defending=False, opponents=defending)
        if defending:
            _record_participants(defending, report, report.winner == "defender", now, defending=True, opponents=active)
            for run in defending:
                if won or not alive_guests(run, config["commander_guest_limit"]):
                    _start_return(run, now, "本波守军败退，保留实际战损返程")
        elif won and site.guards:
            site.guards -= 1
        for run in active:
            if not alive_guests(run, config["commander_guest_limit"]):
                _start_return(run, now, "门客均已战败，退出车轮战返程")
        site.save(update_fields=["guards", "front_allocations", "front_line", "reinforcement_at"])
        if not won:
            _resolve_locked(rally, active, now, "本波未获胜，集结军保留实际伤势与战损返程")
            return
        _next_wave(rally, active, now, f"第 {wave} 波获胜，幸存门客保留血量与护院余量，准备下一波")
        return
    if rally.kind == "attack" and site.durability:
        _resolve_locked(rally, active, now, "守军已清理，请另行发起破城集结")
        return
    if site.attacker and site.attacker != rally.leader.faction:
        site.control = 0
    site.attacker = rally.leader.faction
    exp = campaign_rules(rally.season)["expeditions"]
    wall_before, control_before = site.durability, site.control
    cycle = site.capture_cycle
    if site.durability:
        count = sum(sum(row.values()) for row in att_alloc.values())
        if not count:
            _resolve_locked(rally, active, now, "护院不足以破城，集结军返程")
            return
        amount = min(site.durability, exp["demolition_amount"], count)
        charge_demolition_locked(active, att_alloc, amount, now)
        site.durability -= amount
    elif not breach_capture(rally.season):
        site.control += exp["control_amount"]
    actual_wall_damage = max(0, wall_before - site.durability)
    captured = capture_locked(site, rally.leader.faction, now)
    site.save(
        update_fields=[
            "owner",
            "guards",
            "durability",
            "attacker",
            "control",
            "protected_until",
            "last_score_at",
            "front_line",
            "front_allocations",
            "reinforcement_at",
            "npc_profile",
            "defenses",
            "capture_cycle",
            "supplies",
            "supply_updated_at",
        ]
    )
    contribution = feature_rules(rally.season, "contribution")
    delta = (
        actual_wall_damage
        if wall_before
        else max(
            0,
            (campaign_rules(rally.season)["sites"][site.key]["control_required"] if captured else site.control)
            - control_before,
        )
    )
    if breach_capture(rally.season) and not wall_before:
        delta = 0
    pool = max(1, delta * contribution["demolition_points_per_100"] // 100) if contribution["enabled"] and delta else 0
    shares = split_integer(pool, {run.pk: sum(att_alloc[run.pk].values()) or 1 for run in active})
    for run in active:
        run.result = {**run.result, "progress_step": wave, "capture_cycle": cycle}
        _record_progress(run, shares[run.pk] if contribution["enabled"] else exp["attack_merit"], now, "集结军推进城防")
    if captured:
        award_capture_locked(rally.season, site.key, rally.leader.faction, cycle, now)
        for old in SeasonExpedition.objects.filter(
            member__season=rally.season, node_key=site.key, status="stationed"
        ).exclude(member__faction=site.owner):
            _start_return(old, now, "城池易主，驻军撤回")
        _resolve_locked(rally, active, now, "集结军成功占领目标，幸存队伍返程")
    else:
        _next_wave(rally, active, now, "集结军继续推进城防")


def advance_rally_locked(rally: SeasonRally, now: datetime) -> bool:
    rally = SeasonRally.objects.select_for_update().get(pk=rally.pk)
    if rally.status == "gathering" and now >= rally.gathers_until:
        _launch_locked(rally, now)
        return True
    if rally.status == "marching" and rally.due_at and now >= rally.due_at:
        try:
            with transaction.atomic():
                _wave_locked(rally, now)
        except GameError as exc:
            rally.refresh_from_db()
            active = list(rally.expeditions.filter(status="outbound"))
            _resolve_locked(rally, active, now, f"行动条件变化，集结军安全返程：{exc}")
        return True
    return False


def advance_due_rallies(now: datetime, limit: int = 100) -> dict[str, int]:
    result = {"rallies": 0, "rally_errors": 0}
    ids = list(
        SeasonRally.objects.filter(
            Q(status="gathering", gathers_until__lte=now) | Q(status="marching", due_at__lte=now)
        )
        .order_by("pk")
        .values_list("pk", flat=True)[:limit]
    )
    for pk in ids:
        try:
            with transaction.atomic():
                hint = SeasonRally.objects.get(pk=pk)
                Season.objects.select_for_update().get(pk=hint.season_id)
                result["rallies"] += int(advance_rally_locked(hint, now))
        except Exception:
            logger.exception("season rally settlement failed: rally=%s", pk)
            # 扫描批次不被同一批异常集结永久占满；不改变波次或提交战损。
            retry_at = now + timedelta(seconds=60)
            with transaction.atomic():
                changed = SeasonRally.objects.filter(pk=pk, status="marching", due_at__lte=now).update(due_at=retry_at)
                if changed:
                    SeasonExpedition.objects.filter(rally_id=pk, status="outbound", due_at__lte=now).update(
                        due_at=retry_at
                    )
                changed = SeasonRally.objects.filter(pk=pk, status="gathering", gathers_until__lte=now).update(
                    gathers_until=retry_at
                )
                if changed:
                    SeasonExpedition.objects.filter(rally_id=pk, status="gathering", due_at__lte=now).update(
                        due_at=retry_at
                    )
            result["rally_errors"] += 1
    return result
