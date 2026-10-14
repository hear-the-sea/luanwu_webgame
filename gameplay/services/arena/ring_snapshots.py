from collections.abc import Iterable, Mapping
from typing import Any

from battle.combatants_pkg.troop_device_bonuses import build_troop_device_bonus_sources_by_guest
from battle.troops import load_troop_templates
from core.exceptions import GameError
from core.game_data.technology import load_technology_templates
from gameplay.models import Manor
from guests.guest_combat_stats import resolve_guest_combat_stats
from guests.models import Guest, GuestStatus

from .ring_rules import load_ring_rules
from .snapshots import ArenaGuestSnapshotProxy, build_entry_guest_snapshot


class ArenaRingError(GameError):
    error_code = "ARENA_RING_ERROR"


def build_ring_technology_payload() -> dict[str, dict[str, int]]:
    """演武台临时满级科技，不修改玩家的实际科技。"""
    return {
        "levels": {
            tech["key"]: int(tech["max_level"])
            for tech in load_technology_templates().get("technologies", [])
            if tech.get("category") == "martial"
        }
    }


def build_ring_lineup(
    manor: Manor, guest_ids: Iterable[int | str], troop_loadout: Mapping[str, int | str]
) -> dict[str, Any]:
    try:
        requested = list(guest_ids)
        ids = [int(value) for value in requested]
        if any(isinstance(value, bool) for value in requested):
            raise ValueError
        troops = {}
        for key, value in troop_loadout.items():
            if isinstance(value, bool) or str(value) != str(int(value)) or int(value) < 0:
                raise ValueError
            if int(value):
                troops[str(key)] = int(value)
    except (ValueError, TypeError, AttributeError):
        raise ArenaRingError("上阵配置无效") from None
    if not ids or len(ids) != len(set(ids)):
        raise ArenaRingError("请至少选择一名门客，且不能重复选择")
    maximum = load_ring_rules()["max_guests"]
    if len(ids) > maximum:
        raise ArenaRingError(f"最多上阵 {maximum} 名门客")
    guests = list(
        Guest.objects.select_for_update()
        .filter(manor=manor, pk__in=ids)
        .select_related("template")
        .prefetch_related("skills")
        .order_by("id")
    )
    if len(guests) != len(ids) or any(g.status == GuestStatus.INJURED for g in guests):
        raise ArenaRingError("门客不存在或重伤，无法上阵")
    if troops.keys() - load_troop_templates().keys():
        raise ArenaRingError("护院兵种不存在")
    capacity = sum(resolve_guest_combat_stats(guest).troop_capacity for guest in guests)
    if sum(troops.values()) > capacity:
        raise ArenaRingError(f"护院数量超过所选门客的带兵上限 {capacity}")
    snapshots = []
    sources = build_troop_device_bonus_sources_by_guest(guests)
    for guest, device_sources in zip(guests, sources):
        snapshot = build_entry_guest_snapshot(guest)
        snapshot.update(
            guest_id=guest.pk,
            current_hp=snapshot["max_hp"],
            troop_capacity=resolve_guest_combat_stats(guest).troop_capacity,
            troop_device_bonus_sources=device_sources,
        )
        snapshots.append(snapshot)
    return {
        "guests": snapshots,
        "troops": troops,
        "technology": build_ring_technology_payload(),
        "guest_count": len(snapshots),
        "troop_count": sum(troops.values()),
        "power": sum(g["attack"] + g["defense"] + g["max_hp"] for g in snapshots),
    }


def ring_guest_proxies(lineup: dict[str, Any]) -> list[Any]:
    proxies: list[Any] = []
    for snapshot in lineup["guests"]:
        proxy: Any = ArenaGuestSnapshotProxy(snapshot)
        proxy.current_hp = proxy.max_hp
        proxy.troop_capacity = snapshot["troop_capacity"]
        proxy.has_troop_device_bonus_sources = True
        proxy.troop_device_bonus_sources = snapshot.get("troop_device_bonus_sources", [])
        proxies.append(proxy)
    return proxies


def ring_battle_troops(lineup: dict[str, Any]) -> dict[str, int]:
    """旧版超额阵容按快照带兵上限同比缩减，避免已登台阵容绕过限制。"""
    troops = {key: count for key, count in lineup.get("troops", {}).items() if count > 0}
    total = sum(troops.values())
    capacity = sum(guest["troop_capacity"] for guest in lineup["guests"])
    if total <= capacity:
        return troops
    scaled = {key: count * capacity // total for key, count in troops.items()}
    remainder = capacity - sum(scaled.values())
    order = sorted(troops, key=lambda key: (-(troops[key] * capacity % total), key))
    for key in order[:remainder]:
        scaled[key] += 1
    return {key: count for key, count in scaled.items() if count > 0}
