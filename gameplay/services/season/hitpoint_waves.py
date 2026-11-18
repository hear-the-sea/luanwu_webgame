"""真实城防车轮战：建筑只按战报扣血，未击破时幸存队伍可继续。"""

from datetime import datetime
from typing import Any

from gameplay.models import SeasonExpedition

from .campaign_rules import campaign_rules
from .coalition_combat import fight_coalitions_locked
from .combat import pve_lineup
from .development_rules import feature_rules
from .expeditions import _record_progress, _start_return
from .hitpoints import hitpoint_rules
from .merit import award_capture_locked, split_integer
from .operation_costs import charge_attack_battle_locked
from .report_mail import send_report_mail_locked
from .warfare import capture_locked


def capture_hp_site_locked(rally: Any, site: Any, active: list[Any], now: datetime) -> bool:
    from .rallies import _resolve_locked

    cycle = site.capture_cycle
    if not capture_locked(site, rally.leader.faction, now):
        return False
    site.save()
    award_capture_locked(rally.season, site.key, rally.leader.faction, cycle, now)
    for old in SeasonExpedition.objects.filter(
        member__season=rally.season, node_key=site.key, kind="garrison", status="stationed"
    ).exclude(member__faction=site.owner):
        _start_return(old, now, "城门被攻破，据点易主，驻军撤回")
    _resolve_locked(rally, active, now, "城门血量归零，集结军成功占领，幸存队伍返程")
    return True


def hitpoint_wave_locked(
    rally: Any,
    site: Any,
    active: list[Any],
    defending: list[Any],
    att_alloc: dict,
    def_alloc: dict,
    now: datetime,
    wave: int,
    config: dict,
) -> None:
    from .rallies import _next_wave, _record_participants, _resolve_locked, alive_guests

    if site.durability == 0 and capture_hp_site_locked(rally, site, active, now):
        return
    guard_battle = bool(defending or site.guards)
    if not guard_battle and rally.kind == "attack":
        _resolve_locked(rally, active, now, "守军已清理，请发起攻城集结攻击城门")
        return
    npc: dict[str, Any] = {"guests": []}
    if not defending and site.guards:
        level = site.npc_profile.get("level", campaign_rules(rally.season)["expeditions"]["enemy_levels"][site.key])
        npc = pve_lineup(
            "mountain:ruins" if site.key.startswith("mountain:") else "forest:lodge",
            "据点守军",
            0,
            active[0].seed + wave,
            level_override=level,
            opening_profile=rally.result.get("npc_opening"),
            encounter=rally.result.get("npc_encounter"),
        )
    charge_attack_battle_locked(active, att_alloc, now)
    report, _, _ = fight_coalitions_locked(
        active,
        defending,
        now,
        attacker_allocations=att_alloc,
        defender_allocations=def_alloc,
        commander_guest_limit=config["commander_guest_limit"],
        npc_lineup=npc,
        site=site,
        include_gate=not guard_battle,
        guard_battle=guard_battle,
    )
    won = report.winner == "attacker"
    enemy_rows = report.defender_team
    cleared = not enemy_rows or all(row.get("remaining_hp", 0) <= 0 for row in enemy_rows)
    if guard_battle:
        _record_participants(active, report, won, now, defending=False, opponents=defending, wave=wave)
        if defending:
            _record_participants(
                defending, report, report.winner == "defender", now, defending=True, opponents=active, wave=wave
            )
            for run in defending:
                if won or not alive_guests(run, config["commander_guest_limit"]):
                    _start_return(run, now, "本波守军败退，保留实际战损返程")
        elif cleared:
            site.guards = max(0, site.guards - 1)
    else:
        effects = report.losses.get("season_effects", {})
        damage = sum(effects.get(key, {}).get("combat_damage", 0) for key in ("gate", "tower"))
        contribution = feature_rules(rally.season, "contribution")
        denominator = 100 * hitpoint_rules(rally.season)["hp_per_work"]
        numerator = rally.result.get("hp_merit_remainder", 0) + damage * contribution["demolition_points_per_100"]
        pool, remainder = divmod(numerator, denominator)
        rally.result = {**rally.result, "hp_merit_remainder": remainder}
        shares = split_integer(pool, {run.pk: sum(att_alloc[run.pk].values()) or 1 for run in active})
        for run in active:
            run.result = {**run.result, "progress_step": wave, "capture_cycle": site.capture_cycle}
            action = _record_progress(
                run,
                shares[run.pk] if contribution["enabled"] else 0,
                now,
                f"攻城造成真实建筑伤害 {damage}，城门剩余 {site.durability}",
            )
            action.report = report
            action.save(update_fields=["report"])
            send_report_mail_locked(run, report, now, action.result.get("merit", 0))
    site.save(update_fields=["guards", "front_line", "front_allocations", "reinforcement_at"])
    for run in active:
        if not alive_guests(run, config["commander_guest_limit"]):
            _start_return(run, now, "门客均已战败，保留实际战损返程")
    if not site.durability and capture_hp_site_locked(rally, site, active, now):
        return
    if not guard_battle:
        _resolve_locked(
            rally,
            active,
            now,
            f"本次城门攻击已结束，普通阶段最多{hitpoint_rules(rally.season)['gate_normal_rounds']}回合；建筑保留剩余血量，队伍返程",
        )
        return
    surviving = [run for run in active if run.status == "outbound"]
    if not surviving or (guard_battle and not won and not cleared):
        _resolve_locked(rally, active, now, "本波未能继续作战，建筑保留实际剩余血量，队伍返程")
        return
    _next_wave(rally, surviving, now, f"第 {wave} 场结束，建筑保留实际血量，幸存队伍准备下一场")
