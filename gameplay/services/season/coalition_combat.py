"""集结前线与联合守军的单场战斗；调用方持有赛季锁并负责贡献流水。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from django.db import transaction

from battle.models import BattleReport
from battle.season_outcome import cleared_guard_wave
from core.exceptions import GameError
from gameplay.models import Manor, SeasonExpedition
from guests.models import Guest, GuestStatus

from .combat import prepare_combat, simulate_combat
from .fatigue import award_fatigue_locked, fatigue_report_effects, fatigue_snapshot
from .fortifications import tower_state
from .hitpoints import prepare_gate, settle_gate_locked
from .tower_support import prepare_tower_support, settle_tower_support_locked


def choose_commander(runs: list[SeasonExpedition], limit: int) -> SeasonExpedition | None:
    """只读展示主将候选；实际交战仍会锁定真实门客并复查生命和归属。"""
    if limit < 1:
        return None
    candidates = []
    for run in runs:
        defeated = set(run.result.get("defeated_ids", []))
        strengths = sorted(
            (
                row["attack"] + row["defense"] + row["current_hp"]
                for row in run.lineup.get("guests", [])
                if row.get("current_hp", 0) > 0 and row.get("guest_id") not in defeated
            ),
            reverse=True,
        )[:limit]
        if strengths:
            candidates.append((sum(strengths), run))
    return max(candidates, key=lambda row: (row[0], -row[1].pk))[1] if candidates else None


def _validate_allocations(
    runs: list[SeasonExpedition], allocations: dict[int, dict[str, int]]
) -> dict[int, dict[str, int]]:
    by_id = {run.pk: run for run in runs}
    if len(by_id) != len(runs) or not isinstance(allocations, dict) or not set(allocations) <= set(by_id):
        raise GameError("集结前线队伍配置无效")
    result = {}
    for run_id, loadout in allocations.items():
        if not isinstance(loadout, dict) or any(
            key not in by_id[run_id].troops
            or isinstance(amount, bool)
            or not isinstance(amount, int)
            or amount < 0
            or amount > by_id[run_id].troops[key]
            for key, amount in loadout.items()
        ):
            raise GameError("集结前线兵力超过该队伍实际兵力")
        result[run_id] = {key: amount for key, amount in loadout.items() if amount}
    return result


def _commander(
    runs: list[SeasonExpedition], guests: dict[int, Guest], limit: int
) -> tuple[SeasonExpedition, dict[int, list[dict[str, Any]]]]:
    candidates = []
    selected_by_run = {}
    for run in runs:
        snapshots = []
        seen = set()
        for original in run.lineup.get("guests", []):
            guest_id = original.get("guest_id")
            guest = guests.get(guest_id)
            if guest_id in seen or guest_id not in run.guest_ids:
                raise GameError("集结门客快照与派遣记录不一致")
            seen.add(guest_id)
            if guest is None or guest.manor_id != run.member.manor_id or guest.status != GuestStatus.DEPLOYED:
                raise GameError("集结门客归属或出征状态发生变化")
            hp = min(original["current_hp"], guest.current_hp, original["max_hp"])
            if hp <= 0 or guest_id in run.result.get("defeated_ids", []):
                continue
            snapshot = deepcopy(original)
            snapshot["current_hp"] = hp
            snapshots.append(snapshot)
        if seen != set(run.guest_ids):
            raise GameError("集结门客快照与派遣记录不一致")
        snapshots.sort(key=lambda row: (-(row["attack"] + row["defense"] + row["current_hp"]), row["guest_id"]))
        selected = snapshots[:limit]
        if selected:
            selected_by_run[run.pk] = selected
            strength = sum(row["attack"] + row["defense"] + row["current_hp"] for row in selected)
            candidates.append((strength, run, selected))
    if not candidates:
        raise GameError("集结军没有可出战的主将门客")
    _strength, leader, _selected = max(candidates, key=lambda row: (row[0], -row[1].pk))
    return leader, selected_by_run


def _front_lineup(
    leader: SeasonExpedition,
    selected_by_run: dict[int, list[dict[str, Any]]],
    allocations: dict[int, dict[str, int]],
) -> dict[str, Any]:
    troops: dict[str, int] = {}
    for run_id, loadout in allocations.items():
        if any(loadout.values()) and run_id not in selected_by_run:
            raise GameError("没有存活门客的队伍不能继续投入前线护院")
        for key, amount in loadout.items():
            troops[key] = troops.get(key, 0) + amount
    selected = [snapshot for run_id in sorted(selected_by_run) for snapshot in selected_by_run[run_id]]
    if sum(troops.values()) > sum(snapshot.get("troop_capacity", 0) for snapshot in selected):
        raise GameError("前线护院超过本波存活门客的实际带兵容量")
    return {"guests": selected, "troops": troops, "technology": deepcopy(leader.technology)}


def _split_losses(allocations: dict[int, dict[str, int]], report: BattleReport, side: str) -> dict[int, dict[str, int]]:
    reported: dict[str, int] = {}
    for casualty in report.losses.get(side, {}).get("casualties", []):
        key = casualty.get("key")
        reported[key] = reported.get(key, 0) + max(0, int(casualty.get("lost", 0)))
    result: dict[int, dict[str, int]] = {run_id: {} for run_id in allocations}
    keys = {key for loadout in allocations.values() for key in loadout}
    for key in sorted(keys):
        weights = {run_id: loadout[key] for run_id, loadout in allocations.items() if loadout.get(key, 0)}
        total = sum(weights.values())
        loss = min(total, reported.get(key, 0))
        shares = {run_id: loss * amount // total for run_id, amount in weights.items()}
        remainder = loss - sum(shares.values())
        order = sorted(weights, key=lambda run_id: (-(loss * weights[run_id] % total), run_id))
        for run_id in order[:remainder]:
            shares[run_id] += 1
        for run_id, amount in shares.items():
            result[run_id][key] = amount
    return result


def _apply_coalition(
    runs: list[SeasonExpedition],
    leader: SeasonExpedition,
    selected_by_run: dict[int, list[dict[str, Any]]],
    allocations: dict[int, dict[str, int]],
    report: BattleReport,
    side: str,
    now: datetime,
) -> None:
    losses = _split_losses(allocations, report, side)
    entries = report.attacker_team if side == "attacker" else report.defender_team
    hp_states = {int(row["guest_id"]): int(row["remaining_hp"]) for row in entries if row.get("guest_id") is not None}
    selected_ids = {snapshot["guest_id"] for snapshots in selected_by_run.values() for snapshot in snapshots}
    if not selected_ids <= set(hp_states):
        raise GameError("集结战报缺少实际参战门客生命结算")
    for run in runs:
        front = allocations.get(run.pk, {})
        if run.pk not in selected_by_run:
            continue
        defeated = set(run.result.get("defeated_ids", []))
        own_selected_ids = {snapshot["guest_id"] for snapshot in selected_by_run[run.pk]}
        for snapshot in run.lineup["guests"]:
            guest_id = snapshot["guest_id"]
            if guest_id not in own_selected_ids:
                continue
            hp = hp_states[guest_id]
            if hp <= 0:
                defeated.add(guest_id)
            snapshot["current_hp"] = max(1, min(snapshot["max_hp"], hp))
            Guest.objects.filter(pk=guest_id, manor_id=run.member.manor_id).update(
                current_hp=snapshot["current_hp"], last_hp_recovery_at=now
            )
        own_losses = losses.get(run.pk, {})
        run.troops = {key: amount - own_losses.get(key, 0) for key, amount in run.troops.items()}
        run.battles += 1
        run.report = report
        run.result = {
            **run.result,
            "defeated_ids": sorted(defeated),
            "coalition_front_troops": dict(front),
            "coalition_losses": own_losses,
            "coalition_leader": run.pk == leader.pk,
            "coalition_side": side,
            "coalition_guest_ids": sorted(own_selected_ids),
        }
        award_fatigue_locked(
            run,
            won=report.winner == side,
            survivor_ids={guest_id for guest_id in own_selected_ids if hp_states[guest_id] > 0},
        )
        run.save(update_fields=["lineup", "troops", "result", "battles", "report"])


def fight_coalitions_locked(
    attackers: list[SeasonExpedition],
    defenders: list[SeasonExpedition],
    now: datetime,
    *,
    attacker_allocations: dict[int, dict[str, int]],
    defender_allocations: dict[int, dict[str, int]],
    commander_guest_limit: int,
    npc_lineup: dict[str, Any] | None = None,
    site: Any = None,
    include_gate: bool = False,
    guard_battle: bool = False,
) -> tuple[BattleReport, int, int | None]:
    """整波合并对战，返回战报和双方旗帜主将队伍ID；NPC守军主将ID为空。

    commander_guest_limit表示每名玩家本波最多入场门客数（上限3），每边最多5名玩家。
    主将仅提供旗帜和冻结科技，每支选中队伍的存活门客都会入战、受到真实伤害。
    参与流水由调用方按实际入场队伍写入，纯门客零护院的队伍也属于参战者。
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("集结战斗必须在持有赛季锁的事务中结算")
    if (
        isinstance(commander_guest_limit, bool)
        or not isinstance(commander_guest_limit, int)
        or not 1 <= commander_guest_limit <= 3
    ):
        raise GameError("主将出战人数配置无效")
    has_tower = site is not None and (tower_state(site)["hp"] > 0 or (include_gate and site.durability > 0))
    if not attackers or (not defenders and not npc_lineup and not has_tower):
        raise GameError("集结战斗缺少进攻队伍或守军")
    if len(attackers) > 5 or len(defenders) > 5:
        raise GameError("每波每方最多5名玩家参与集结")
    if any(len({run.member.manor_id for run in side}) != len(side) for side in (attackers, defenders)):
        raise GameError("每名玩家每波只能派遣一支最多3名门客的队伍")
    all_runs = attackers + defenders
    if len({run.pk for run in all_runs}) != len(all_runs) or len({run.member.season_id for run in all_runs}) != 1:
        raise GameError("集结队伍重复或不属于同一赛季")
    if len({run.member.faction for run in attackers}) != 1 or (
        defenders
        and (
            len({run.member.faction for run in defenders}) != 1
            or attackers[0].member.faction == defenders[0].member.faction
        )
    ):
        raise GameError("集结军与联合守军的阵营配置无效")
    all_guest_ids = [guest_id for run in all_runs for guest_id in run.guest_ids]
    if len(set(all_guest_ids)) != len(all_guest_ids):
        raise GameError("同一门客不能同时属于两支集结队伍")
    if any(run.status == "completed" for run in all_runs):
        raise GameError("已归队的队伍不能参与集结")
    attack_front = _validate_allocations(attackers, attacker_allocations)
    defend_front = _validate_allocations(defenders, defender_allocations)
    manor_ids = sorted({run.member.manor_id for run in all_runs})
    list(Manor.objects.select_for_update().filter(pk__in=manor_ids).order_by("pk"))
    guest_ids = {guest_id for run in all_runs for guest_id in run.guest_ids}
    guests = {guest.pk: guest for guest in Guest.objects.select_for_update().filter(pk__in=guest_ids).order_by("pk")}
    attacker_leader, attacker_guests = _commander(attackers, guests, commander_guest_limit)
    attack_battle_guests = {
        run.pk: [fatigue_snapshot(run, snapshot) for snapshot in attacker_guests.get(run.pk, [])]
        for run in attackers
        if run.pk in attacker_guests
    }
    attack_lineup = _front_lineup(attacker_leader, attack_battle_guests, attack_front)
    defender_leader = None
    defender_guests: dict[int, list[dict[str, Any]]] = {}
    if defenders:
        defender_leader, defender_guests = _commander(defenders, guests, commander_guest_limit)
        defense_battle_guests = {
            run.pk: [fatigue_snapshot(run, snapshot) for snapshot in defender_guests.get(run.pk, [])]
            for run in defenders
            if run.pk in defender_guests
        }
        defending = _front_lineup(defender_leader, defense_battle_guests, defend_front)
        opponent = defender_leader.member.manor.display_name
    else:
        defending = deepcopy(npc_lineup) if npc_lineup is not None else {"guests": []}
        opponent = "据点联合守军"
    seed = attacker_leader.seed + attacker_leader.battles
    prepared = prepare_combat(
        attacker_leader.member.manor,
        attack_lineup,
        defending,
        opponent,
        seed,
        preserve_state=True,
        allow_empty_defenders=has_tower,
    )
    tower = (
        prepare_tower_support(prepared, site, defender_leader.member.faction if defender_leader else site.owner)
        if site is not None
        else {}
    )
    gate = prepare_gate(prepared, site) if include_gate and site is not None else {}
    report = simulate_combat(attacker_leader.member.manor, prepared, seed)
    if gate and any(row["key"] == "wall" and row["hp"] <= 0 for row in report.defender_city_defenses):
        report.winner = "attacker"
    effects: dict[str, Any] = {
        "attacker": fatigue_report_effects(attackers),
        "defender": fatigue_report_effects(defenders),
    }
    # 守军战的目标是击退本波门客；幸存箭塔留到后续战斗，不应将清军判成失败。
    # 城门战、双方同归于尽及仍有守军存活时沿用战斗引擎原结果。
    if guard_battle and not include_gate and cleared_guard_wave(report):
        report.winner = "attacker"
        effects["guard_wave_cleared"] = True
    if any(effects.values()):
        report.losses = {**report.losses, "season_effects": {**report.losses.get("season_effects", {}), **effects}}
    report.battle_type = "season"
    report.save(force_insert=True)
    if tower:
        tower_result = settle_tower_support_locked(site, report, tower)
        report.losses = {
            **report.losses,
            "season_effects": {**report.losses.get("season_effects", {}), "tower": tower_result},
        }
        report.save(update_fields=["losses"])
    if gate:
        gate_result = settle_gate_locked(site, report, gate)
        report.losses = {
            **report.losses,
            "season_effects": {**report.losses.get("season_effects", {}), "gate": gate_result},
        }
        report.save(update_fields=["losses"])
    _apply_coalition(attackers, attacker_leader, attacker_guests, attack_front, report, "attacker", now)
    if defender_leader:
        _apply_coalition(defenders, defender_leader, defender_guests, defend_front, report, "defender", now)
    return report, attacker_leader.pk, defender_leader.pk if defender_leader else None
