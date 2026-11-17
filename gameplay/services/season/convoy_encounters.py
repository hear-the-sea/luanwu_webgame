"""运输窗口末统一多人遭遇战，不能用首个弱队提前消耗拦截机会。"""

from datetime import datetime
from typing import Any

from django.utils.dateparse import parse_datetime

from core.exceptions import GameError
from gameplay.models import Manor, SeasonExpedition
from guests.models import Guest, GuestStatus


def is_encounter(convoy: Any) -> bool:
    return convoy.kind == "transport" and convoy.result.get("activity", {}).get("encounter_version") == 1


def target_id(run: Any) -> int | None:
    return run.result.get("activity", {}).get("target_run_id")


def helpers(convoy: Any):
    return (
        SeasonExpedition.objects.filter(
            member__season_id=convoy.member.season_id,
            kind__in=("intercept", "escort"),
            status__in=("outbound", "transporting"),
            result__activity__target_run_id=convoy.pk,
        )
        .select_related("member__manor", "member__season")
        .order_by("pk")
    )


def validate_join(member: Any, convoy: Any, mode: str) -> None:
    if not is_encounter(convoy) or convoy.status != "transporting":
        raise GameError("请选择仍在运输中的车队")
    if helpers(convoy).filter(member=member).exists():
        raise GameError("你已有队伍参与本趟运输，请勿重复报名")
    same = member.faction == convoy.member.faction
    if mode == "escort" and (not same or convoy.member_id == member.pk):
        raise GameError("仅可护送本阵营其他玩家的运输队，发起人已自动护送")
    if mode == "intercept" and (same or not member.faction):
        raise GameError("只能拦截敌方运输队")


def arrive_locked(run: Any, now: datetime) -> bool:
    from .expeditions import _schedule, _start_return

    convoy_id = target_id(run)
    if convoy_id is None:
        return False
    convoy = SeasonExpedition.objects.select_for_update().filter(pk=convoy_id).select_related("member__season").first()
    if convoy is None or not is_encounter(convoy):
        return False
    if convoy.status != "transporting" or run.due_at >= convoy.due_at:
        run.result = {**run.result, "refund_quota": not bool(run.report_id)}
        _start_return(run, now, "车队已结束或未能及时抵达，未参战额度归队后退回")
        return True
    run.result = {
        **run.result,
        "convoy_ready_at": run.due_at.isoformat(),
        "message": "已抵达，等待运输窗口结束统一遭遇战",
    }
    run.status = "transporting"
    run.due_at = convoy.due_at
    run.save(update_fields=["result", "status", "due_at"])
    _schedule(run)
    return True


def release_helpers_locked(convoy: Any, now: datetime, message: str) -> None:
    from .expeditions import _start_return

    if not is_encounter(convoy):
        return
    for run in helpers(convoy).select_for_update():
        run.result = {**run.result, "refund_quota": not bool(run.report_id)}
        if run.status == "outbound":
            run.travel_seconds = min(run.travel_seconds, max(1, int((now - run.launched_at).total_seconds())))
        _start_return(run, now, message)


def _rank(runs: list[Any]) -> list[Any]:
    ids = {gid for run in runs for gid in run.guest_ids}
    guests = {guest.pk: guest for guest in Guest.objects.select_for_update().filter(pk__in=ids).order_by("pk")}
    ranked = []
    for run in runs:
        snapshots = run.lineup.get("guests", [])
        if not snapshots or any(
            row.get("guest_id") not in guests
            or guests[row["guest_id"]].manor_id != run.member.manor_id
            or guests[row["guest_id"]].status != GuestStatus.DEPLOYED
            for row in snapshots
        ):
            continue
        alive = [
            row
            for row in snapshots
            if row.get("guest_id") not in run.result.get("defeated_ids", []) and row.get("current_hp", 0) > 0
        ]
        if not alive:
            continue
        from .faction_bonus import battle_hp

        strength = sum(
            row["attack"] + row["defense"] + min(row["current_hp"], battle_hp(row, guests[row["guest_id"]].current_hp))
            for row in alive
        )
        ranked.append((strength + 10 * sum(run.troops.values()), run))
    return [run for _, run in sorted(ranked, key=lambda row: (-row[0], row[1].pk))]


def settle_locked(convoy: Any, now: datetime) -> None:
    """调用方持有赛季锁；只处理一场合并战，随后仍由原运输逻辑交付余货。"""
    if not is_encounter(convoy) or convoy.result.get("convoy_encounter_resolved"):
        return
    from .activities import transfer_intercept_locked
    from .coalition_combat import fight_coalitions_locked
    from .expeditions import _record_combat, _settle_activity_reward, _start_return
    from .merit import battle_merit_shares, split_integer
    from .operation_costs import charge_convoy_battle_locked, mark_outbound_arrival_locked
    from .report_mail import send_report_mail_locked

    candidates = list(helpers(convoy).select_for_update())
    for run in candidates:
        if run.status == "outbound" and run.due_at < convoy.due_at and run.due_at <= now:
            mark_outbound_arrival_locked(run, run.due_at)
            arrive_locked(run, now)
    manor_ids = sorted({convoy.member.manor_id, *(run.member.manor_id for run in candidates)})
    list(Manor.objects.select_for_update().filter(pk__in=manor_ids).order_by("pk"))
    ready = [
        run
        for run in candidates
        if run.status == "transporting" and parse_datetime(run.result.get("convoy_ready_at", "")) is not None
    ]
    limit = convoy.result["activity"].get("encounter_players", 5)
    attackers = _rank(
        [run for run in ready if run.kind == "intercept" and run.member.faction != convoy.member.faction]
    )[:limit]
    guards = _rank([run for run in ready if run.kind == "escort" and run.member.faction == convoy.member.faction])
    defenders = ([convoy] + guards[: max(0, limit - 1)]) if _rank([convoy]) else guards[:limit]
    selected = {run.pk for run in attackers + defenders}
    if attackers and defenders:
        allocations = {run.pk: dict(run.troops) for run in attackers + defenders}
        charge_convoy_battle_locked(attackers + defenders, now)
        report, attacker_leader, _ = fight_coalitions_locked(
            attackers,
            defenders,
            now,
            attacker_allocations={run.pk: allocations[run.pk] for run in attackers},
            defender_allocations={run.pk: allocations[run.pk] for run in defenders},
            commander_guest_limit=3,
        )
        won = report.winner == "attacker"
        attack_shares = battle_merit_shares(attackers, report, "attacker", won)
        defense_shares = battle_merit_shares(defenders, report, "defender", not won)
        lead = next(run for run in attackers if run.pk == attacker_leader)
        loot = transfer_intercept_locked(lead, convoy, won, now)
        weights = {run.pk: sum(attack_shares[run.pk]["metrics"].values()) for run in attackers}
        loot_shares = split_integer(
            loot["merit"], weights if any(weights.values()) else {run.pk: 1 for run in attackers}
        )
        for side, shares, opposing in ((attackers, attack_shares, defenders), (defenders, defense_shares, attackers)):
            for run in side:
                credit = _record_combat(
                    run.member,
                    run,
                    report,
                    shares[run.pk]["merit"],
                    now,
                    "运输多人遭遇战",
                    metrics=shares[run.pk]["metrics"],
                    opponent=opposing[0].member,
                    send_mail=False,
                )
                extra = 0
                if run in attackers:
                    reward = _settle_activity_reward(
                        run,
                        {
                            "merit": loot_shares[run.pk],
                            "construction": loot_shares[run.pk],
                            "message": "运输遭遇战按实际掠夺分配贡献",
                        },
                        now,
                        "combat",
                    )
                    extra = reward["merit"]
                run.result = {**run.result, "convoy_battle_merit": credit["merit"], "convoy_loot_merit": extra}
                run.save(update_fields=["result"])
                send_report_mail_locked(
                    run,
                    report,
                    now,
                    credit["merit"] + extra,
                    reward={"loot": loot["loot"], "resource_label": "阵营夺取物资合计"} if run in attackers else None,
                )
        convoy.result = {**convoy.result, "convoy_loot": loot["loot"], "convoy_report_id": report.pk}
    else:
        selected = set()
    convoy.result = {**convoy.result, "convoy_encounter_resolved": True}
    convoy.save(update_fields=["result", "cargo", "intercepted"])
    for run in candidates:
        if run.status not in {"outbound", "transporting"}:
            continue
        if run.pk not in selected:
            run.result = {**run.result, "refund_quota": not bool(run.report_id)}
        _start_return(
            run, now, "运输遭遇战结束，队伍返程" if run.pk in selected else "未入场交战，队伍返程并退回出征额度"
        )
