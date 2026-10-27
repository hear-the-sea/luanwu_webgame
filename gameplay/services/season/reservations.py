"""单次离线参战授权；等待下一场匹配集结，实际加入时沿用真实派遣校验。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from battle.services import validate_troop_capacity
from core.exceptions import GameError
from gameplay.models import Manor, SeasonAction, SeasonMember, SeasonRally
from guests.models import Guest, GuestStatus

from .campaign_rules import campaign_rules, coalition_rules
from .rules import season_state
from .warfare import campaign_open, validate_destination


def _config(season: Any) -> dict[str, Any]:
    from .playability_rules import playability_rules

    rules = playability_rules(season)
    if not rules["enabled"] or not coalition_rules(season)["enabled"]:
        raise GameError("本季尚未开放一次性参战预约")
    return rules["reservation"]


def _save(member: SeasonMember, reservation: dict[str, Any]) -> None:
    member.lineup = {**member.lineup, "reservation": reservation}
    member.save(update_fields=["lineup"])


def _expiry(reservation: dict[str, Any]) -> datetime | None:
    try:
        value = datetime.fromisoformat(reservation.get("expires_at", ""))
        return value if timezone.is_aware(value) else None
    except (ValueError, TypeError):
        return None


def create_reservation_locked(member: SeasonMember, payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    """只保存一次授权；不预扣公共护院、军粮或出征次数，也不占用门客。"""
    config = _config(member.season)
    if member.faction not in {"fiction", "history"} or not campaign_open(member.season, now):
        raise GameError("请先完成阵营分配，并在赛季行动期内预约")
    previous = member.lineup.get("reservation", {})
    if previous.get("status") == "pending" and (_expiry(previous) or now) > now:
        raise GameError("已有生效中的参战预约，请先取消")
    hours = payload.get("hours", 1)
    if (
        isinstance(hours, bool)
        or not isinstance(hours, int)
        or hours not in {1, 2, 4}
        or hours > config["maximum_hours"]
    ):
        raise GameError("请选择有效的预约时长：1、2或4小时")
    mode = payload.get("mode", "siege")
    if mode not in {"attack", "siege"}:
        raise GameError("参战预约仅支持集结攻城")
    node = payload.get("node_key", "")
    validate_destination(member.season, node, mode, now)
    if node not in campaign_rules(member.season)["sites"]:
        raise GameError("请选择可以攻城的据点")
    ids = payload.get("guest_ids", [])
    limit = min(
        config["guest_limit"], coalition_rules(member.season)["commander_guest_limit"], member.manor.max_squad_size
    )
    if (
        not isinstance(ids, list)
        or not 1 <= len(ids) <= limit
        or any(isinstance(pk, bool) or not isinstance(pk, int) or pk <= 0 for pk in ids)
        or len(set(ids)) != len(ids)
    ):
        raise GameError(f"请选择1—{limit}名不同的空闲门客")
    guests = list(Guest.objects.select_for_update().filter(manor=member.manor, pk__in=ids).select_related("template"))
    if len(guests) != len(ids) or any(guest.status != GuestStatus.IDLE for guest in guests):
        raise GameError("预约门客必须属于你的庄园且处于空闲状态")
    from .quotas import enforce_guest_daily_limit

    enforce_guest_daily_limit(member.season, ids, now)
    economy = campaign_rules(member.season)["economy"]
    troops = payload.get("troops", {})
    allowed = set(economy["initial_troops"]) - set(economy.get("special_troops", {}))
    if not isinstance(troops, dict) or any(
        key not in allowed or isinstance(count, bool) or not isinstance(count, int) or count < 0
        for key, count in troops.items()
    ):
        raise GameError("请明确选择普通护院数量，预约不可调用特殊铁骑")
    troops = {key: count for key, count in troops.items() if count}
    if sum(troops.values()) > economy["daily_troop_quota"]:
        raise GameError("预约兵力不能超过每日领兵上限")
    validate_troop_capacity(guests, troops)
    if mode == "siege" and not troops:
        raise GameError("攻城预约需要明确选择阵营护院")
    expires = min(now + timedelta(hours=hours), member.season.ends_at)
    reservation = {
        "id": str(uuid4()),
        "status": "pending",
        "faction": member.faction,
        "node_key": node,
        "mode": mode,
        "guest_ids": ids,
        "troops": troops,
        "created_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "result": {},
    }
    _save(member, reservation)
    return {
        "message": "已预约一次参战：有效期内下一场匹配集结出现时复验并加入；失败或使用后不再代派",
        "reservation": reservation,
    }


def cancel_reservation_locked(member: SeasonMember, now: datetime) -> dict[str, Any]:
    reservation = member.lineup.get("reservation", {})
    if reservation.get("status") != "pending":
        raise GameError("没有待执行的参战预约")
    reservation = {**reservation, "status": "cancelled", "finished_at": now.isoformat()}
    _save(member, reservation)
    return {"message": "参战预约已取消", "reservation": reservation}


def consume_reservations_locked(rally: SeasonRally, now: datetime) -> None:
    """调用方持有赛季锁；每条实际派遣放入保存点，失败不会遗留兵力或配额扣除。"""
    from .rallies import join_rally_locked

    if rally.status != "gathering" or now >= rally.gathers_until:
        return
    from .playability_rules import playability_rules

    if not playability_rules(rally.season)["enabled"]:
        return
    members = list(
        SeasonMember.objects.select_for_update()
        .filter(season=rally.season, faction=rally.leader.faction, lineup__reservation__status="pending")
        .exclude(pk=rally.leader_id)
        .order_by("lineup__reservation__created_at", "pk")
    )
    for member in members:
        rally.refresh_from_db()
        if rally.status != "gathering":
            break
        reservation = member.lineup["reservation"]
        expiry = _expiry(reservation)
        if not expiry or expiry <= now:
            status, result = "expired", {"message": "参战预约已到期"}
        elif reservation.get("faction") != member.faction:
            status, result = "failed", {"message": "阵营变化，参战预约失效"}
        elif reservation.get("node_key") != rally.node_key or reservation.get("mode") != rally.kind:
            continue
        else:
            try:
                with transaction.atomic():
                    manor = Manor.objects.select_for_update().get(pk=member.manor_id)
                    result = join_rally_locked(
                        member,
                        manor,
                        {"rally_id": rally.pk, "guest_ids": reservation["guest_ids"], "troops": reservation["troops"]},
                        now,
                    )
                    # 普通派遣通过命令入口记录次数；自动加入也必须留下同样的消费记录。
                    SeasonAction.objects.create(
                        member=member,
                        token=uuid4(),
                        request_hash="season-reservation-join",
                        kind="rally_join",
                        node_key=rally.node_key,
                        date=timezone.localdate(now),
                        week=season_state(rally.season, now)["week"],
                        result={"reservation_id": reservation["id"], **result},
                    )
                status = "used"
            except GameError as exc:
                member.refresh_from_db()
                status, result = "failed", {"message": str(exc)}
        _save(member, {**reservation, "status": status, "finished_at": now.isoformat(), "result": result})
        SeasonAction.objects.create(
            member=member,
            token=uuid4(),
            request_hash="season-reservation",
            kind=f"reservation_{status}",
            node_key=reservation.get("node_key", ""),
            date=timezone.localdate(now),
            week=season_state(rally.season, now)["week"],
            result={"reservation_id": reservation["id"], **result},
        )
