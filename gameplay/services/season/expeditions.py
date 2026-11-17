"""真实门客派遣与阵营兵库占用；所有转换幂等地记录在出征行。"""

from __future__ import annotations

import copy
import logging
import math
import secrets
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from battle.services import validate_troop_capacity
from common.utils.celery import safe_apply_async
from core.exceptions import GameError
from core.utils.time_scale import scale_duration
from gameplay.models import Manor, Season, SeasonAction, SeasonExpedition, SeasonMember, SeasonRally
from gameplay.services.battle_snapshots import build_guest_battle_snapshots
from gameplay.services.missions_impl.finalization_helpers import prepare_guest_updates_for_finalize
from guests.guest_rules import compute_guest_attack_attribute_component
from guests.models import Guest, GuestStatus
from guests.services.health import recover_guest_hp
from guests.services.status import persist_guest_status_transitions

from .campaign_rules import campaign_rules, coalition_rules
from .combat import npc_opening_profile, prepare_combat, pve_lineup, simulate_combat
from .development_rules import feature_rules
from .economy import (
    grant_activity_resources_locked,
    grant_construction_amount_locked,
    grant_construction_locked,
    grant_pve_locked,
    produce_locked,
    reserve_troops_locked,
    return_troops_locked,
    technology_levels,
)
from .faction_bonus import apply_lineup_bonus, battle_hp, real_hp
from .fatigue import award_fatigue_locked, fatigue_report_effects, fatigue_snapshot
from .march import travel_time_seconds
from .merit import battle_merit_shares, grant_merit_locked, progress_merit_locked
from .operation_costs import (
    charge_attack_battle_locked,
    charge_demolition_locked,
    finish_operation_costs_locked,
    frozen_repair_cost,
    mark_outbound_arrival_locked,
    repair_cost,
    reserve_operation_costs_locked,
    run_operation_rules,
    settle_garrison_cost_locked,
    start_return_costs_locked,
)
from .quotas import daily_dispatches_used, enforce_guest_daily_limit
from .rules import season_state
from .warfare import (
    ACTIVE_STATUSES,
    campaign_open,
    capture_locked,
    check_ownership,
    city_open,
    ensure_sites_locked,
    score_sites_locked,
    validate_destination,
)

logger = logging.getLogger(__name__)


def _schedule(run: SeasonExpedition) -> None:
    run_id, due_at = run.pk, run.due_at

    def enqueue() -> None:
        from gameplay.tasks.season import advance_season_expedition_task

        safe_apply_async(
            advance_season_expedition_task,
            args=[run_id, due_at.isoformat()],
            countdown=max(0, math.ceil((due_at - timezone.now()).total_seconds())),
            defer_long_eta=True,
            logger=logger,
            log_message="season expedition dispatch failed; periodic scan will retry",
        )

    transaction.on_commit(enqueue)


def _spend(state: Any, cost: dict[str, int]) -> None:
    if any(state.resources.get(key, 0) < value for key, value in cost.items()):
        raise GameError("阵营资源不足，请先完成后勤讨伐")
    state.resources = {key: amount - cost.get(key, 0) for key, amount in state.resources.items()}
    state.save(update_fields=["resources"])


def _restore_cargo(run: SeasonExpedition, now: datetime, *, multiplier: float = 1) -> None:
    if not run.cargo:
        return
    from .recovery import settle_auto_repairs_locked

    settle_auto_repairs_locked(run.member.season, now)
    resources = {key: int(amount * multiplier) for key, amount in run.cargo.items()}
    source = run.result.get("cargo_source_site")
    site = run.member.season.sites.filter(key=source, owner=run.member.faction).first() if source else None
    if site:
        site.supplies = {
            **site.supplies,
            **{key: site.supplies.get(key, 0) + amount for key, amount in resources.items()},
        }
        site.save(update_fields=["supplies"])
    else:
        grant_activity_resources_locked(run.member, resources, now, refund=True)
    run.cargo = {}


def dispatch_locked(
    member: SeasonMember, manor: Manor, payload: dict[str, Any], now: datetime, *, rally: SeasonRally | None = None
) -> dict[str, Any]:
    season = member.season
    conf = campaign_rules(season)
    exp = conf["expeditions"]
    key, mode = payload.get("node_key", ""), payload.get("mode", "pve")
    station_seconds = exp["station_seconds"]
    if mode == "garrison":
        hours = payload.get("station_hours")
        if hours is not None:
            if (
                isinstance(hours, bool)
                or not isinstance(hours, int)
                or hours not in exp.get("station_hours_options", [1, 2, 4])
            ):
                raise GameError("请选择有效的驻军时长")
            station_seconds = hours * 3600
    activities_enabled = feature_rules(season, "activities")["enabled"]
    pending = not member.faction
    if pending and (not activities_enabled or mode not in {"pve", "explore", "npc_intercept"}):
        raise GameError("报名已记录；分组前可参与探索和讨伐，其他行动等待阵营分配")
    if pending and any(payload.get("troops", {}).values()):
        raise GameError("分组前暂不可领用公共护院，可派门客探索或讨伐")
    validate_destination(season, key, mode, now, legacy_modes=bool(rally and not rally.result.get("unified_siege")))
    coalition = coalition_rules(season)
    if coalition["enabled"]:
        if mode in {"attack", "siege"} and rally is None:
            raise GameError("攻城需要发起或加入集结，不能以个人队伍绕过集结")
        active = member.expeditions.filter(status__in=ACTIVE_STATUSES)
        occupied = (
            active.filter(kind__in=("garrison", "escort"))
            if mode in {"garrison", "escort"}
            else active.exclude(kind__in=("garrison", "escort"))
        )
        if occupied.exists():
            raise GameError(
                "驻防位已占用，请等待原队伍归队" if mode in {"garrison", "escort"} else "出征位已占用，请等待原队伍归队"
            )
    if member.expeditions.filter(status__in=ACTIVE_STATUSES).count() >= exp["max_active"]:
        raise GameError(f"最多同时派遣 {exp['max_active']} 支赛季队伍")
    today = timezone.localdate(now)
    if daily_dispatches_used(member, today) >= exp["daily_actions"]:
        raise GameError("今日出征次数已达本季配置上限")
    ids = payload.get("guest_ids", [])
    guest_limit = (
        min(manor.max_squad_size, coalition["commander_guest_limit"]) if coalition["enabled"] else manor.max_squad_size
    )
    if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids) or len(ids) > guest_limit:
        raise GameError(f"请选择 1—{guest_limit} 名不同的空闲门客")
    guests = list(
        Guest.objects.select_for_update()
        .filter(manor=manor, pk__in=ids)
        .select_related("template")
        .prefetch_related("skills")
        .order_by("pk")
    )
    if len(guests) != len(ids) or any(guest.status != GuestStatus.IDLE for guest in guests):
        raise GameError("门客已出征、重伤或不属于你的庄园")
    from guests.services.status import active_raid_guest_ids

    if active_raid_guest_ids(guest.pk for guest in guests):
        raise GameError("所选门客仍在踢馆队伍中，请等待归队")
    enforce_guest_daily_limit(season, ids, now)
    for guest in guests:
        recover_guest_hp(guest, now=now)
    loadout = {} if pending else reserve_troops_locked(member, payload.get("troops", {}), now)
    validate_troop_capacity(guests, loadout)
    if mode == "siege" and not loadout:
        raise GameError("参与破城需要携带阵营护院")
    state = produce_locked(season, now)[member.faction] if member.faction else None
    sites = ensure_sites_locked(season, now)
    if mode in {"attack", "siege", "garrison", "repair"}:
        check_ownership(sites[key], member.faction, mode, now)
    if mode == "garrison" and SeasonExpedition.objects.filter(
        member__season=season,
        node_key=key,
        status__in=["outbound", "stationed"],
        kind="garrison",
    ).count() >= exp.get("garrison_capacity", 8):
        raise GameError("据点驻防队伍已满")
    cargo = {}
    run_result: dict[str, Any] = {}
    if mode == "garrison":
        run_result["station_seconds"] = station_seconds
        run_result["garrison_merit_per_hour"] = exp.get("garrison_merit_per_hour", 0)
    if activities_enabled and mode in {"explore", "transport", "intercept", "escort", "npc_intercept"}:
        from .activities import prepare_activity_locked

        prepared = prepare_activity_locked(member, key, mode, payload, now)
        cargo, run_result = prepared["cargo"], prepared["result"]
    if mode == "transport" and not activities_enabled:
        if sites[key].owner not in {"", member.faction}:
            raise GameError("渡口被敌方控制，请先夺回通路")
        cargo = dict(exp["cargo"])
        _spend(state, cargo)
    if mode == "repair":
        from .fortifications import fortification_rules, tower_state
        from .hitpoints import hitpoint_rules, repair_ready_at
        from .operation_costs import fortification_repair_plan

        hp = hitpoint_rules(season)
        ready = repair_ready_at(sites[key])
        if hp["enabled"] and ready and now < ready:
            raise GameError(f"该建筑维修冷却中，下次可于{timezone.localtime(ready):%H:%M}派遣")
        fortifications = fortification_rules(season)
        tower = tower_state(sites[key])
        tower_missing = max(0, tower["max_hp"] - tower["hp"]) if fortifications["enabled"] else 0
        missing = max(
            0, sites[key].npc_profile.get("durability", conf["sites"][key]["durability"]) - sites[key].durability
        )
        if not missing and not tower_missing:
            raise GameError("城防完好，无需维修")
        run_result["repair_reserved_wall"] = min(exp["repair_amount"], missing)
        cargo = repair_cost(season, run_result["repair_reserved_wall"])
        if fortifications["enabled"]:
            from .resource_rules import resource_rules

            snapshot = {
                "work_amount": hp["repair_amount"] if hp["enabled"] else exp["repair_amount"],
                "hp_per_work": hp["hp_per_work"] if hp["enabled"] else 1,
                "wall_prices": copy.deepcopy(resource_rules(season)["operations"]["repair_cost_per_100_actual_wall"]),
                "tower_prices": copy.deepcopy(fortifications["tower"]["repair_cost_per_100"]),
            }
            run_result["fortification_repair"] = snapshot
            _, _, cargo = fortification_repair_plan(snapshot, missing, tower_missing)
        if activities_enabled:
            if any(sites[key].supplies.get(resource, 0) < count for resource, count in cargo.items()):
                raise GameError("据点维修物资不足，请先运输木材和铁料")
            sites[key].supplies = {
                resource: count - cargo.get(resource, 0) for resource, count in sites[key].supplies.items()
            }
            sites[key].save(update_fields=["supplies"])
            run_result["cargo_source_site"] = key
        else:
            _spend(state, cargo)
        if hp["enabled"]:
            sites[key].defenses = {
                **sites[key].defenses,
                "repair_available_at": (now + timedelta(seconds=hp["repair_interval_seconds"])).isoformat(),
            }
            sites[key].save(update_fields=["defenses"])
    from .population import npc_profile

    run_result["npc_level"] = npc_profile(season, key)["level"]
    run_result["quota_date"] = today.isoformat()
    run_result["activities_version"] = 1 if activities_enabled else 0
    snapshots = build_guest_battle_snapshots(guests)
    for guest, snapshot in zip(guests, snapshots):
        snapshot["archetype"] = guest.archetype
        snapshot["attack_attribute_component"] = compute_guest_attack_attribute_component(guest)
    apply_lineup_bonus(member, guests, snapshots)
    opening = npc_opening_profile(season, snapshots, 1, run_result["npc_level"])
    if opening:
        run_result["npc_opening"] = opening
    minimum_travel = scale_duration(exp["minimum_travel_seconds"], minimum=1)
    base_travel = exp.get("mode_travel_seconds", {}).get(mode, exp["travel_seconds"][key])
    travel = max(minimum_travel, travel_time_seconds(base_travel, guests, loadout))
    if mode == "transport":
        run_result["transport_seconds"] = scale_duration(exp["transport_seconds"], minimum=1)
    from .site_benefits import gate_march_bonus, season_travel_seconds

    travel = season_travel_seconds(season, member.faction, travel, minimum_travel)
    run_result["march_reduction_percent"] = gate_march_bonus(season, member.faction)
    due_at = now + timedelta(seconds=travel)
    if mode in {"intercept", "escort"} and run_result.get("activity", {}).get("target_run_id"):
        target = SeasonExpedition.objects.filter(pk=run_result["activity"]["target_run_id"]).first()
        if target and target.result.get("activity", {}).get("encounter_version") == 1 and due_at >= target.due_at:
            raise GameError("无法在运输窗口结束前抵达，请选择剩余时间更长的车队")
    cutoff = min(season.ends_at, season.starts_at + timedelta(days=exp["combat_days"]))
    latest_arrival = due_at
    if rally is not None:
        gathering = list(rally.expeditions.filter(status="gathering").values_list("travel_seconds", flat=True))
        target_members = rally.result.get("target_members", coalition["max_members"])
        departure = now if len(gathering) + 1 >= target_members else rally.gathers_until
        latest_arrival = departure + timedelta(seconds=max([travel, *gathering]))
    if latest_arrival >= cutoff:
        raise GameError("本次行军无法在行动期内抵达，请勿在尾声前派出新队伍")
    if mode == "pve":
        from .resource_rules import resource_rules

        resource = resource_rules(season)
        waves = resource.get("pve", {}).get("collection_waves", {})
        if resource["enabled"] and waves.get("enabled"):
            interval = scale_duration(waves["interval_seconds"], minimum=1)
            if latest_arrival + timedelta(seconds=interval * waves["max_waves"]) >= cutoff:
                raise GameError("采集队无法在赛季行动期内完成全部战斗，请提前出征")
    if key == "river:town" and not city_open(season, latest_arrival):
        hint = "请减少集结出发人数或等待下次开放" if rally is not None else "请等待下次开放"
        raise GameError(f"本次行军无法在古城开放时间内抵达，{hint}")
    from .material_rewards import reserve_material_reward_locked

    run_result.update(reserve_material_reward_locked(member, mode, now))
    if mode == "pve":
        from .resource_rules import resource_rules

        resource = resource_rules(season)
        waves = resource.get("pve", {}).get("collection_waves", {})
        if resource["enabled"] and waves.get("enabled"):
            run_result["collection_waves"] = {
                "version": 1,
                "max_waves": waves["max_waves"],
                "interval_seconds": scale_duration(waves["interval_seconds"], minimum=1),
                "level_step": waves["level_step"],
                "win_percent": waves["win_percent"],
                "wins": 0,
                "completed": 0,
            }
    if mode != "pve" or run_result.get("collection_waves"):
        from .npc_encounters import snapshot_npc_encounter

        encounter = snapshot_npc_encounter(season, key, mode)
        if encounter:
            run_result["npc_encounter"] = encounter
    run_result.update(
        reserve_operation_costs_locked(
            member,
            loadout,
            mode,
            travel,
            now,
            station_seconds=station_seconds if mode == "garrison" else None,
            reserve_convoy_battle=run_result.get("activity", {}).get("encounter_version") == 1,
        )
    )
    persist_guest_status_transitions(guests, GuestStatus.DEPLOYED, source="season_dispatch")
    run = SeasonExpedition.objects.create(
        member=member,
        rally=rally,
        node_key=key,
        kind=mode,
        guest_ids=ids,
        lineup={"guests": snapshots},
        troops=loadout,
        initial_troops=loadout,
        technology=technology_levels(state) if state else {},
        result=run_result,
        cargo=cargo,
        seed=secrets.randbelow(2**31),
        travel_seconds=travel,
        launched_at=now,
        due_at=rally.gathers_until if rally else due_at,
        status="gathering" if rally else "outbound",
    )
    run.guests.set(guests)
    _schedule(run)
    return {"message": f"队伍已出发，预计 {travel} 秒后抵达；返程前门客与赛季护院均被占用", "run_id": run.pk}


def _start_return(run: SeasonExpedition, now: datetime, message: str) -> None:
    if run.status == "returning":
        return
    if run.kind == "garrison" and run.status == "stationed" and run_operation_rules(run):
        site = run.member.season.sites.filter(key=run.node_key, owner=run.member.faction).first()
        if site:
            settle_garrison_cost_locked(run, site, now, final=True)
    if run.kind == "garrison" and run.status == "stationed":
        from .garrison_merit import settle_garrison_merit_locked

        settle_garrison_merit_locked(run, now)
    if run.kind == "transport":
        from .convoy_encounters import release_helpers_locked

        release_helpers_locked(run, now, "运输已结束或撤回，未参战队伍返程并退回额度")
    start_return_costs_locked(run, now)
    run.status = "returning"
    run.due_at = now + timedelta(seconds=run.travel_seconds)
    run.stationed_until = None
    run.error = ""
    if run.kind == "garrison" and run.result.get("garrison_service", {}).get("merit"):
        message += f"；驻守贡献 +{run.result['garrison_service']['merit']}"
    run.result = {**run.result, "message": message}
    run.save(
        update_fields=[
            "status",
            "due_at",
            "stationed_until",
            "result",
            "troops",
            "cargo",
            "lineup",
            "battles",
            "report",
            "error",
        ]
    )
    _schedule(run)


def _finish_collection_locked(
    run: SeasonExpedition, now: datetime, reason: str, *, merit_at: datetime | None = None
) -> None:
    """将整趟已胜场一次入账；首场未胜不产生物资、贡献或建设值。"""
    waves = dict(run.result["collection_waves"])
    if waves.get("finalized"):
        _start_return(run, now, reason)
        return
    wins = int(waves.get("wins", 0))
    receipt: dict[str, Any] = {"merit": 0, "construction_points": 0, "resources": {}}
    if wins:
        material = copy.deepcopy(run.result.get("material_reward") or {})
        material["multiplier"] = float(material.get("multiplier", 1)) * wins * waves["win_percent"] / 100
        receipt = grant_pve_locked(
            run.member,
            run.node_key,
            True,
            now,
            event_key=f"run:{run.pk}:pve",
            material_reward=material,
            merit_at=merit_at,
        )
    waves.update(finalized=True, reward=receipt)
    run.result = {**run.result, "collection_waves": waves}
    already_mailed = bool(run.report_id and run.member.claims.filter(key=f"battle_mail:{run.report_id}").exists())
    if run.battles:
        _record_progress_log(run, receipt, now)
    if already_mailed:
        from gameplay.services.utils.messages import create_message

        _, created = run.member.claims.get_or_create(key=f"collection_result:{run.pk}")
        if created:
            names = {"grain": "粮草", "wood": "木材", "iron": "铁料"}
            received = "、".join(f"{names.get(key, key)} {amount}" for key, amount in receipt["resources"].items())
            create_message(
                manor=run.member.manor,
                kind="system",
                title="阵营任务-资源收集结果",
                body=f"{reason}，共胜利 {wins} 场。阵营获得：{received or '无物资'}；个人贡献 +{receipt['merit']}。",
            )
    message = f"{reason}，收集战胜 {wins}/{waves['max_waves']} 场，个人贡献 +{receipt['merit']}"
    _start_return(run, now, message)


def _advance_collection_locked(run: SeasonExpedition, now: datetime) -> None:
    """只推进一个到期波次；下一波以计划时间递增，重复调用受 due_at 与行锁保护。"""
    from django.utils.dateparse import parse_datetime

    waves = dict(run.result["collection_waves"])
    planned = parse_datetime(waves.get("next_wave_at", "")) or run.due_at
    if not campaign_open(run.member.season, planned):
        last_wave = parse_datetime(waves.get("last_wave_at", ""))
        if last_wave is None and waves.get("wins"):
            last_wave = planned - timedelta(seconds=waves["interval_seconds"])
        _finish_collection_locked(run, now, "赛季行动期结束", merit_at=last_wave)
        return
    won = _fight(run, None, now)
    waves["completed"] = int(waves.get("completed", 0)) + 1
    waves["wins"] = int(waves.get("wins", 0)) + int(won)
    waves["last_wave_at"] = planned.isoformat()
    run.result = {**run.result, "collection_waves": waves}
    if (
        not won
        or waves["completed"] >= waves["max_waves"]
        or len(run.result.get("defeated_ids", [])) >= len(run.guest_ids)
    ):
        _finish_collection_locked(run, now, "收集战败" if not won else "收集结束", merit_at=planned)
        return
    from .report_mail import send_report_mail_locked

    send_report_mail_locked(run, run.report, now)
    next_at = planned + timedelta(seconds=waves["interval_seconds"])
    waves["next_wave_at"] = next_at.isoformat()
    run.result = {**run.result, "collection_waves": waves, "message": f"收集进行中，已胜 {waves['wins']} 场"}
    run.due_at = next_at
    run.save(update_fields=["result", "due_at"])
    _schedule(run)


def _advance_due_collection_locked(run: SeasonExpedition, now: datetime) -> None:
    """补齐到期波次，确保延迟扫描和召回不能跳过截止前的战斗。"""
    from django.utils.dateparse import parse_datetime

    # max_waves 配置有上限，循环只处理本队已到期的有限波次。
    while run.status == "stationed":
        waves = run.result["collection_waves"]
        planned = parse_datetime(waves.get("next_wave_at", "")) or run.due_at
        if planned > now:
            return
        try:
            with transaction.atomic():
                _advance_collection_locked(run, now)
        except GameError as exc:
            run.refresh_from_db()
            saved = run.result["collection_waves"]
            last_wave = parse_datetime(saved.get("last_wave_at", ""))
            if last_wave is None and saved.get("wins"):
                last_wave = planned - timedelta(seconds=saved["interval_seconds"])
            _finish_collection_locked(run, now, f"收集提前结束：{exc}", merit_at=last_wave or planned)
            return


def _collection_snapshot_phase(run: SeasonExpedition) -> str:
    """校验采集快照并识别阶段，不修改任务状态。"""
    from django.utils.dateparse import parse_datetime

    waves = run.result.get("collection_waves")
    if run.kind != "pve" or not isinstance(waves, dict):
        raise GameError("异常队伍不是可恢复的资源收集任务")
    if any(
        type(waves.get(key)) is not int
        for key in ("max_waves", "interval_seconds", "level_step", "win_percent", "wins", "completed")
    ):
        raise GameError("异常收集任务快照不完整，无法安全恢复")
    if (
        waves["max_waves"] <= 0
        or waves["interval_seconds"] <= 0
        or waves["level_step"] < 0
        or waves["win_percent"] <= 0
        or not 0 <= waves["wins"] <= waves["completed"] <= waves["max_waves"]
        or (not waves.get("finalized") and waves["completed"] == waves["max_waves"])
        or (not waves.get("finalized") and waves["wins"] != waves["completed"])
    ):
        raise GameError("异常收集任务波次数据无效，无法安全恢复")
    if run.battles != waves["completed"]:
        raise GameError("异常收集任务战斗记录与波次不一致，无法安全恢复")
    if waves.get("finalized"):
        if not isinstance(waves.get("reward"), dict):
            raise GameError("异常收集任务奖励快照不完整，无法安全恢复")
        return "finalized"
    next_wave = waves.get("next_wave_at")
    if next_wave:
        try:
            parsed = parse_datetime(next_wave) if isinstance(next_wave, str) else None
        except ValueError:
            parsed = None
        if parsed is None or timezone.is_naive(parsed):
            raise GameError("异常收集任务缺少有效的波次时间，无法安全恢复")
        return "stationed"
    elif run.status == "stationed" and timezone.is_aware(run.due_at):
        # 旧在途任务允许只保存 due_at，原有推进逻辑同样使用该字段兜底。
        return "stationed"
    elif not run.battles and not waves.get("completed") and not waves.get("wins") and not waves.get("finalized"):
        return "outbound"
    else:
        raise GameError("异常收集任务缺少波次时间，无法安全恢复")


def _prepare_collection_locked(run: SeasonExpedition) -> bool:
    """恢复可辨认的失败队伍，拒绝与正常任务状态矛盾的快照。"""
    phase = _collection_snapshot_phase(run)
    if run.status == "failed":
        run.status = "stationed" if phase == "finalized" else phase
        run.save(update_fields=["status"])
    elif phase != "finalized" and phase != run.status:
        raise GameError("异常收集任务状态与波次不一致，无法安全恢复")
    return phase == "finalized"


def _return_corrupt_collection_locked(run: SeasonExpedition, now: datetime, reason: str) -> None:
    """异常快照无法结算时保留原记录并立即归队，不推测未发放的收益。"""
    Manor.objects.select_for_update().get(pk=run.member.manor_id)
    run.result = {
        **run.result,
        "collection_recovery": {"reason": reason, "returned_at": now.isoformat()},
        "message": "采集记录异常，队伍已安全撤回；无法核实的收集收益未结算",
    }
    start_return_costs_locked(run, now)
    run.status = "returning"
    run.stationed_until = None
    run.error = reason
    _finish_return(run, now)
    run.save(update_fields=["stationed_until", "error"])
    logger.warning("corrupt season collection returned: run=%s reason=%s", run.pk, reason)


def recall_locked(member: SeasonMember, run_id: int, now: datetime) -> dict[str, Any]:
    run = member.expeditions.select_for_update().filter(pk=run_id).first()
    if run is None or run.status not in {"gathering", "outbound", "stationed", "transporting", "failed"}:
        raise GameError("该队伍不存在、正在返程或已经归队")
    if run.status in {"failed", "outbound", "stationed"} and run.kind == "pve" and "collection_waves" in run.result:
        try:
            finalized = _prepare_collection_locked(run)
        except GameError as exc:
            _return_corrupt_collection_locked(run, now, str(exc))
            return {"message": run.result["message"], "run_id": run.pk}
        if finalized:
            _finish_collection_locked(run, now, "已结算的异常收集任务返程")
            return {"message": run.result["message"], "run_id": run.pk}
    if run.status == "gathering" and run.rally_id:
        from .rallies import _refund_unlaunched, cancel_rally_locked

        assert run.rally is not None
        if run.rally.leader_id == member.pk:
            return cancel_rally_locked(member, run.rally_id, now)
        _refund_unlaunched(run, now, "已退出尚未出发的集结，队伍与额度均已退回")
        return {"run_id": run.pk, "message": "已退出集结，队伍已归队，额度已退回"}
    if run.status == "outbound" and run.kind == "pve" and run.result.get("collection_waves") and run.due_at <= now:
        # 如果扫描器延迟，先按既定抵达时间推进，不能通过召回跳过已经到期的战斗。
        process_expedition(run.pk, now)
        run.refresh_from_db()
        if run.status == "returning":
            return {"message": run.result["message"], "run_id": run.pk}
    if run.status == "outbound":
        elapsed = max(1, int((now - run.launched_at).total_seconds()))
        run.travel_seconds = min(elapsed, run.travel_seconds)
    if run.kind == "pve" and run.status == "stationed" and run.result.get("collection_waves"):
        _advance_due_collection_locked(run, now)
        if run.status == "returning":
            return {"message": run.result["message"], "run_id": run.pk}
        if not campaign_open(run.member.season, now):
            from django.utils.dateparse import parse_datetime

            waves = run.result["collection_waves"]
            last_wave = parse_datetime(waves.get("last_wave_at", ""))
            if last_wave is None and waves.get("wins"):
                next_wave = parse_datetime(waves.get("next_wave_at", ""))
                if next_wave is not None:
                    last_wave = next_wave - timedelta(seconds=waves["interval_seconds"])
        else:
            last_wave = None
        _finish_collection_locked(run, now, "主动撤回", merit_at=last_wave)
        return {"message": run.result["message"], "run_id": run.pk}
    if run.kind in {"intercept", "escort"} and not run.report_id:
        run.result = {**run.result, "refund_quota": True}
    _restore_cargo(run, now)
    _start_return(run, now, "已召回，保留已发生的战损，队伍正在返程")
    return {"message": run.result["message"], "run_id": run.pk}


def _guests(run: SeasonExpedition) -> list[Guest]:
    return list(
        Guest.objects.select_for_update()
        .filter(pk__in=run.guest_ids, manor=run.member.manor)
        .select_related("template")
        .order_by("id")
    )


def _finish_return(run: SeasonExpedition, now: datetime) -> None:
    guests = _guests(run)
    # 血量来自真实战报；战败者在归队时进入与个人任务相同的重伤与忠诚度流程。
    if run.result.get("refund_quota"):
        from .quotas import refund_unused_quota_locked

        refund_unused_quota_locked(run, now)
    hp = {s["guest_id"]: real_hp(s, s["current_hp"]) for s in run.lineup.get("guests", []) if s.get("guest_id")}
    defeated = set(run.result.get("defeated_ids", []))
    guests = [guest for guest in guests if guest.status == GuestStatus.DEPLOYED]
    updates, fields = prepare_guest_updates_for_finalize(
        guests,
        is_retreating=False,
        defeated_guest_ids=defeated,
        hp_updates=hp,
        now=now,
    )
    if updates:
        Guest.objects.bulk_update(updates, fields)
    finish_operation_costs_locked(run, now)
    from .material_rewards import refund_material_reward_locked

    refund_material_reward_locked(run)
    if run.member.faction:
        return_troops_locked(run.member, run.troops, now)
    _restore_cargo(run, now)
    run.status = "completed"
    run.result = {
        **run.result,
        "returned_troops": dict(run.troops),
        "message": run.result.get("message", "") + "；队伍已归队，存活护院已返还阵营兵库",
    }
    run.troops = {}
    run.save(update_fields=["status", "troops", "cargo", "result"])


def _battle_state(run: SeasonExpedition) -> dict[str, Any]:
    guests = _guests(run)
    if len(guests) != len(run.guest_ids) or any(guest.status != GuestStatus.DEPLOYED for guest in guests):
        raise GameError("出征门客归属或状态发生变化，无法继续作战")
    lineup = copy.deepcopy(run.lineup)
    previous = {guest.pk: guest for guest in guests}
    living = []
    for snapshot in lineup["guests"]:
        guest = previous[snapshot["guest_id"]]
        snapshot.setdefault("archetype", guest.archetype)
        snapshot["current_hp"] = min(snapshot["current_hp"], battle_hp(snapshot, guest.current_hp), snapshot["max_hp"])
        if snapshot["current_hp"] <= 0 or guest.pk in run.result.get("defeated_ids", []):
            if run.kind == "pve" and run.result.get("collection_waves"):
                continue
            raise GameError("队伍中有战败门客，必须先返程治疗")
        living.append(snapshot)
    if not living:
        raise GameError("队伍门客均已战败，必须返程治疗")
    lineup["guests"] = living
    troops = dict(run.troops)
    if run.kind == "pve" and run.result.get("collection_waves"):
        # 已战败门客不再提供带兵容量；超出部分作为后备保留，不参加本场战斗。
        capacity = sum(max(0, int(snapshot.get("troop_capacity", 0))) for snapshot in living)
        if sum(troops.values()) > capacity:
            from .merit import split_integer

            troops = split_integer(capacity, troops)
    lineup.update(troops=troops, technology=dict(run.technology))
    return lineup


def _apply_side(run: SeasonExpedition, report: Any, side: str, now: datetime) -> None:
    team = report.attacker_team if side == "attacker" else report.defender_team
    states = {int(row["guest_id"]): int(row["remaining_hp"]) for row in team if row.get("guest_id") is not None}
    defeated = set(run.result.get("defeated_ids", []))
    for snapshot in run.lineup["guests"]:
        gid = snapshot["guest_id"]
        remaining = states.get(gid, snapshot["current_hp"])
        if remaining <= 0:
            defeated.add(gid)
        snapshot["current_hp"] = max(1, min(snapshot["max_hp"], remaining))
        Guest.objects.filter(pk=gid, manor=run.member.manor).update(
            current_hp=real_hp(snapshot, snapshot["current_hp"]),
            last_hp_recovery_at=now,
        )
    casualties = report.losses.get(side, {}).get("casualties", [])
    losses: dict[str, int] = {}
    for row in casualties:
        if row.get("key") in run.troops:
            losses[row["key"]] = losses.get(row["key"], 0) + max(0, int(row.get("lost", 0)))
    run.troops = {key: max(0, count - losses.get(key, 0)) for key, count in run.troops.items()}
    run.result = {**run.result, "defeated_ids": sorted(defeated)}
    run.battles += 1
    run.report = report
    run.save(update_fields=["lineup", "troops", "result", "battles", "report"])


def _record_combat(
    member: SeasonMember,
    run: SeasonExpedition,
    report: Any,
    merit: int,
    now: datetime,
    label: str,
    *,
    activity_key: str | None = None,
    opponent: SeasonMember | None = None,
    metrics: dict[str, Any] | None = None,
    send_mail: bool = True,
) -> dict[str, Any]:
    from .report_mail import report_side, send_report_mail_locked

    if feature_rules(member.season, "contribution")["enabled"] and metrics is None:
        side = "attacker" if any(row.get("guest_id") in run.guest_ids for row in report.attacker_team) else "defender"
        calculated = battle_merit_shares(
            [run], report, side, report.winner == side, npc=opponent is None and side == "attacker"
        )[run.pk]
        merit, metrics = calculated["merit"], calculated["metrics"]
        if not calculated["effective"]:
            activity_key = None
    receipt = grant_merit_locked(
        member, merit, "combat", now, event_key=f"battle:{report.pk}:{member.pk}", reason=label
    )
    construction = (
        grant_construction_locked(member, activity_key, now) if activity_key and not receipt["duplicate"] else {}
    )
    SeasonAction.objects.create(
        member=member,
        token=uuid4(),
        request_hash="settled-expedition",
        opponent=opponent,
        kind="combat",
        node_key=run.node_key,
        date=timezone.localdate(now),
        week=season_state(member.season, now)["week"],
        report=report,
        result={
            **construction,
            **receipt,
            "metrics": metrics or {},
            "capture_cycle": run.result.get("capture_cycle", 0),
            "combat_side": report_side(run, report),
            "occurred_at": now.isoformat(),
            "pvp_battle": bool(
                opponent and opponent.season_id == member.season_id and opponent.faction != member.faction
            ),
            "message": label,
            "run_id": run.pk,
            "report_url": reverse("battle:report_detail", kwargs={"pk": report.pk}),
        },
    )
    if send_mail:
        send_report_mail_locked(run, report, now, receipt["merit"])
    return receipt


def _fight(
    run: SeasonExpedition,
    defender: SeasonExpedition | None,
    now: datetime,
    *,
    site: Any = None,
    include_gate: bool = False,
) -> bool:
    # 先按庄园ID顺序加锁，与任务/踢馆共用锁顺序，随后才锁真实门客。
    manor_ids = sorted({run.member.manor_id} | ({defender.member.manor_id} if defender else set()))
    list(Manor.objects.select_for_update().filter(pk__in=manor_ids).order_by("pk"))
    attacker = _battle_state(run)
    waves = run.result.get("collection_waves") if run.kind == "pve" else None
    if not waves:
        attacker = {**attacker, "guests": [fatigue_snapshot(run, row) for row in attacker["guests"]]}
    if defender:
        defending = _battle_state(defender)
        defending = {**defending, "guests": [fatigue_snapshot(defender, row) for row in defending["guests"]]}
        opponent_name = defender.member.manor.display_name
    else:
        from .warfare import campaign_nodes

        enemy = campaign_nodes(run.member.season)[run.node_key].get("enemy_label") or "据点守军"
        levels = campaign_rules(run.member.season)["expeditions"].get("enemy_levels", {})
        # 复用普通乡勇模板，NPC难度由各节点配置确定，不读取玩家当前战力自动追涨。
        fallback_key = (
            run.node_key
            if run.node_key in {"mountain:inn", "mountain:ruins", "river:docks", "forest:granary", "forest:lodge"}
            else "mountain:ruins"
        )
        opening = copy.deepcopy(run.result.get("npc_opening"))
        level = run.result.get("npc_level", levels.get(run.node_key))
        if waves:
            increase = int(waves.get("completed", 0)) * int(waves["level_step"])
            level += increase
            if opening:
                opening["level"] += increase
        defending = pve_lineup(
            fallback_key,
            enemy,
            0,
            run.seed + (run.battles if waves else 0),
            level_override=level,
            opening_profile=opening,
            encounter=run.result.get("npc_encounter") if waves else None,
            wave_index=int(waves.get("completed", 0)) if waves else 0,
        )
        opponent_name = enemy
    if defender and run_operation_rules(defender):
        if defender.status == "stationed":
            from .supply import prepare_defense_locked

            site = defender.member.season.sites.get(key=defender.node_key)
            if not prepare_defense_locked(site, [defender], now, allocations={defender.pk: defender.troops}):
                _start_return(run, now, "守军因缺粮撤回，本次交战取消，请重新确认目标")
                return False
        else:
            charge_attack_battle_locked([defender], {defender.pk: defender.troops}, now)
    from .fortifications import tower_state
    from .hitpoints import prepare_gate, settle_gate_locked
    from .tower_support import prepare_tower_support, settle_tower_support_locked

    has_tower = site is not None and (tower_state(site)["hp"] > 0 or (include_gate and site.durability > 0))
    if has_tower and site is not None and not defender and not site.guards:
        defending = {"guests": []}
    charge_attack_battle_locked([run], {run.pk: attacker["troops"]}, now)
    prepared = prepare_combat(
        run.member.manor,
        attacker,
        defending,
        opponent_name,
        run.seed + run.battles,
        preserve_state=True,
        allow_empty_defenders=has_tower,
    )
    tower = prepare_tower_support(prepared, site, site.owner) if has_tower and site is not None else {}
    gate = prepare_gate(prepared, site) if include_gate and site is not None else {}
    report = simulate_combat(run.member.manor, prepared, run.seed + run.battles)
    if gate and any(row["key"] == "wall" and row["hp"] <= 0 for row in report.defender_city_defenses):
        report.winner = "attacker"
    effects = {
        "attacker": fatigue_report_effects([run]),
        "defender": fatigue_report_effects([defender]) if defender else [],
    }
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
    _apply_side(run, report, "attacker", now)
    if not waves:
        award_fatigue_locked(
            run,
            won=report.winner == "attacker",
            survivor_ids={
                int(row["guest_id"])
                for row in report.attacker_team
                if row.get("guest_id") in run.guest_ids and row.get("remaining_hp", 0) > 0
            },
        )
    run.save(update_fields=["result"])
    if defender:
        _apply_side(defender, report, "defender", now)
        award_fatigue_locked(
            defender,
            won=report.winner == "defender",
            survivor_ids={
                int(row["guest_id"])
                for row in report.defender_team
                if row.get("guest_id") in defender.guest_ids and row.get("remaining_hp", 0) > 0
            },
        )
        defender.save(update_fields=["result"])
        exp = campaign_rules(run.member.season)["expeditions"]
        defense_won = report.winner == "defender"
        _record_combat(
            defender.member,
            defender,
            report,
            exp["defend_merit"] if defense_won else 0,
            now,
            "驻军成功击退来敌" if defense_won else "驻军交战结束，请查看战损",
            activity_key="defense_win" if defense_won else None,
            opponent=run.member,
        )
        if not defense_won or defender.battles >= exp["max_battles"] or defender.result.get("defeated_ids"):
            _start_return(defender, now, "驻防或护送交战结束，队伍返程")
    return report.winner == "attacker"


def _pick_defender(run: SeasonExpedition, now: datetime) -> SeasonExpedition | None:
    return (
        SeasonExpedition.objects.select_for_update()
        .filter(
            member__season=run.member.season,
            node_key=run.node_key,
            kind="garrison",
            status="stationed",
            due_at__gt=now,
        )
        .exclude(member__faction=run.member.faction)
        .order_by("launched_at", "id")
        .first()
    )


def _site_action(run: SeasonExpedition, now: datetime) -> None:
    season = run.member.season
    config = campaign_rules(season)
    exp = config["expeditions"]
    score_sites_locked(season, now)
    site = ensure_sites_locked(season, now)[run.node_key]
    check_ownership(site, run.member.faction, run.kind, now)
    if run.kind == "garrison":
        Manor.objects.select_for_update().get(pk=run.member.manor_id)
        _battle_state(run)
        carried = run.result.get("operation_costs", {}).get("carried_food_balance", 0)
        if carried:
            site.supplies = {**site.supplies, "grain": site.supplies.get("grain", 0) + carried}
            site.save(update_fields=["supplies"])
            run.result = {
                **run.result,
                "operation_costs": {**run.result["operation_costs"], "carried_food_balance": 0},
                "carried_food_delivered": carried,
            }
        run.status = "stationed"
        run.stationed_until = min(
            now + timedelta(seconds=run.result.get("station_seconds", exp["station_seconds"])),
            season.starts_at + timedelta(days=exp["combat_days"]),
            season.ends_at,
        )
        run.due_at = run.stationed_until
        run.result = {
            **run.result,
            "stationed_at": now.isoformat(),
            "message": (f"已从公共库携带{carried}粮入城；" if carried else "")
            + "队伍已驻防待命，离线也能迎战；未上场的队伍不会受损",
        }
        run.save(update_fields=["status", "stationed_until", "due_at", "result"])
        _schedule(run)
        return
    if run.kind == "repair":
        Manor.objects.select_for_update().get(pk=run.member.manor_id)
        _battle_state(run)
        if run.result.get("fortification_repair"):
            from .fortifications import tower_state
            from .operation_costs import _record, fortification_repair_plan

            tower = tower_state(site)
            wall_missing = max(
                0, site.npc_profile.get("durability", config["sites"][site.key]["durability"]) - site.durability
            )
            wall, restored_tower, spent = fortification_repair_plan(
                run.result["fortification_repair"], wall_missing, max(0, tower["max_hp"] - tower["hp"]), run.cargo
            )
            run.cargo = {key: count - spent.get(key, 0) for key, count in run.cargo.items()}
            _record(run, "repair", spent, actual_wall=wall, actual_tower=restored_tower)
            site.durability += wall
            site.defenses = {
                **site.defenses,
                "arrow_tower": {**site.defenses.get("arrow_tower", {}), **tower, "hp": tower["hp"] + restored_tower},
            }
            site.save(update_fields=["durability", "defenses"])
            _restore_cargo(run, now)
            _start_return(run, now, f"城防维修完成：城墙{wall}、箭塔{restored_tower}；剩余物资退回，队伍返程")
            return
        if site.durability >= site.npc_profile.get("durability", config["sites"][site.key]["durability"]):
            _restore_cargo(run, now)
            _start_return(run, now, "城防已由友军修复，维修物资退回，队伍返程")
            return
        amount = min(
            run.result.get("repair_reserved_wall", exp["repair_amount"]),
            site.npc_profile.get("durability", config["sites"][site.key]["durability"]) - site.durability,
        )
        if run_operation_rules(run):
            spent = frozen_repair_cost(run, amount)
            if any(run.cargo.get(key, 0) < count for key, count in spent.items()):
                raise GameError("本次预留维修物资不足，请重新派遣")
            run.cargo = {key: count - spent.get(key, 0) for key, count in run.cargo.items()}
            from .operation_costs import _record

            _record(run, "repair", spent, actual_wall=amount)
            _restore_cargo(run, now)
        site.durability += amount
        site.save(update_fields=["durability"])
        run.cargo = {}
        _start_return(run, now, "城防维修完成，队伍返程；维修不反复发放贡献")
        return
    from .fortifications import breach_capture, tower_state
    from .hitpoints import hitpoint_rules

    physical_hp = hitpoint_rules(season)["enabled"]
    defender = _pick_defender(run, now)
    if defender:
        won = _fight(run, defender, now, site=site)
    elif site.guards or tower_state(site)["hp"] or (physical_hp and site.durability):
        won = _fight(run, None, now, site=site, include_gate=physical_hp and not site.guards and run.kind == "siege")
        if won and site.guards:
            site.guards -= 1
    else:
        Manor.objects.select_for_update().get(pk=run.member.manor_id)
        _battle_state(run)
        won = True
        if run.kind == "attack" and site.durability:
            _start_return(run, now, "驻军已被突破，请派遣破城队削减城门耐久")
            return
    if run.status == "returning":
        return
    if (won and not defender and site.guards == 0 and not tower_state(site)["hp"]) or (
        physical_hp and not site.durability
    ):
        if site.attacker and site.attacker != run.member.faction:
            site.control = 0
        site.attacker = run.member.faction
        if site.durability and not physical_hp:
            if run.kind == "siege":
                amount = min(exp["demolition_amount"], sum(run.troops.values()))
                if not amount:
                    _start_return(run, now, "护院不足，无法继续破城")
                    return
                amount = min(site.durability, amount)
                charge_demolition_locked([run], {run.pk: run.troops}, amount, now)
                site.durability = max(0, site.durability - amount)
        elif not breach_capture(season):
            site.control += exp["control_amount"]
        if capture_locked(site, run.member.faction, now):
            # 据点易主后，旧驻防全部返程；不重置真实门客损耗。
            for old in (
                SeasonExpedition.objects.select_for_update()
                .filter(
                    member__season=season,
                    node_key=site.key,
                    kind="garrison",
                    status="stationed",
                )
                .exclude(member__faction=run.member.faction)
            ):
                _start_return(old, now, "据点易主，驻军撤回")
    site.save(
        update_fields=[
            "guards",
            "durability",
            "control",
            "attacker",
            "owner",
            "protected_until",
            "last_score_at",
            "npc_profile",
            "defenses",
            "capture_cycle",
            "supplies",
            "supply_updated_at",
        ]
    )
    merit = exp["attack_merit"] if won else exp["failure_merit"]
    label = f"{'攻坚成功' if won else '攻坚失利'}，驻军余 {site.guards} 队，城防 {site.durability}"
    (
        _record_combat(
            run.member, run, run.report, merit, now, label, activity_key="attack_win" if won else "attack_loss"
        )
        if run.report
        else _record_progress(run, merit, now, label)
    )
    _start_return(run, now, label)


def _record_progress(run: SeasonExpedition, merit: int, now: datetime, label: str) -> SeasonAction:
    member = run.member
    merit = progress_merit_locked(run, now, merit)
    event = f"site:{run.pk}:{run.result.get('progress_step', 0)}:{run.battles}"
    receipt = grant_merit_locked(member, merit, "combat", now, event_key=event, reason=label)
    construction = grant_construction_locked(member, "attack_win", now) if merit and not receipt["duplicate"] else {}
    return SeasonAction.objects.create(
        member=member,
        token=uuid4(),
        request_hash="site-progress",
        kind="combat",
        node_key=run.node_key,
        date=timezone.localdate(now),
        week=season_state(member.season, now)["week"],
        result={
            **construction,
            **receipt,
            "capture_cycle": run.result.get("capture_cycle", 0),
            "combat_side": "attacker",
            "message": label,
            "run_id": run.pk,
        },
    )


def _settle_activity_reward(
    run: SeasonExpedition, reward: dict[str, Any], now: datetime, category: str = "logistics"
) -> dict[str, Any]:
    receipt = grant_merit_locked(
        run.member, reward.get("merit", 0), category, now, event_key=f"activity:{run.pk}", reason=reward["message"]
    )
    construction = (
        grant_construction_amount_locked(run.member, reward.get("construction", 0), now)
        if not receipt["duplicate"]
        else {}
    )
    return {**reward, **receipt, **construction}


def _intercept(run: SeasonExpedition, now: datetime) -> None:
    if run.result.get("activities_version") == 1:
        from .activities import find_intercept_target_locked, transfer_intercept_locked

        target = find_intercept_target_locked(run, now)
        if target is None:
            run.result = {**run.result, "refund_quota": True}
            _start_return(run, now, "目标运输已结束或已被拦截，本次无战损返程，归队后退回额度")
            return
        won = _fight(run, target, now)
        exp = campaign_rules(run.member.season)["expeditions"]
        battle_receipt = _record_combat(
            run.member,
            run,
            run.report,
            exp["attack_merit"] if won else exp["failure_merit"],
            now,
            "运输拦截获胜" if won else "运输拦截失利",
            opponent=target.member,
            send_mail=False,
        )
        reward = transfer_intercept_locked(run, target, won, now)
        reward = _settle_activity_reward(run, reward, now, "combat")
        reward["battle_merit"] = battle_receipt["merit"]
        reward["message"] += f"；交战贡献 +{battle_receipt['merit']}，物资贡献 +{reward['merit']}"
        _record_progress_log(run, reward, now)
        # 真实玩家交战另记双方关联，用于赛季对抗有效性审计。
        run.member.actions.filter(report=run.report, kind="combat").update(opponent=target.member)
        _start_return(run, now, reward["message"])
        return
    target = (
        SeasonExpedition.objects.select_for_update()
        .filter(
            member__season=run.member.season,
            node_key=run.node_key,
            kind="transport",
            status="transporting",
            intercepted=False,
            due_at__gt=now,
        )
        .exclude(member__faction=run.member.faction)
        .order_by("due_at", "id")
        .first()
    )
    if target is None:
        _start_return(run, now, "未发现可拦截运输队，队伍无战损返程")
        return
    target.intercepted = True
    target.save(update_fields=["intercepted"])
    won = _fight(run, target, now)
    if won:
        # 只影响赛季运输物资；原货物不会同时返还给运输方与拦截方。
        state = produce_locked(run.member.season, now)[run.member.faction]
        caps = campaign_rules(run.member.season)["economy"]["resource_caps"]
        for key, amount in target.cargo.items():
            state.resources[key] = min(caps[key], state.resources.get(key, 0) + amount // 2)
        state.save(update_fields=["resources"])
        target.cargo = {}
        target.save(update_fields=["cargo"])
    merit = campaign_rules(run.member.season)["expeditions"]["attack_merit" if won else "failure_merit"]
    _record_combat(
        run.member,
        run,
        run.report,
        merit,
        now,
        "运输拦截战已结束",
        activity_key="intercept_win" if won else "intercept_loss",
    )
    _start_return(run, now, "运输拦截战结束，队伍返程")


def process_expedition(run_id: int, now: datetime | None = None) -> bool:
    now = now or timezone.now()
    hint = SeasonExpedition.objects.filter(pk=run_id).values("member__season_id").first()
    if not hint:
        return False
    with transaction.atomic():
        season = Season.objects.select_for_update().get(pk=hint["member__season_id"])
        from .recovery import settle_auto_repairs_locked

        settle_auto_repairs_locked(season, now)
        run = SeasonExpedition.objects.select_for_update().get(pk=run_id)
        if run.status == "completed" or run.due_at > now:
            return False
        if run.status in {"failed", "outbound", "stationed"} and run.kind == "pve" and "collection_waves" in run.result:
            try:
                finalized = _prepare_collection_locked(run)
            except GameError as exc:
                _return_corrupt_collection_locked(run, now, str(exc))
                return True
            if finalized:
                _finish_collection_locked(run, now, "已结算的异常收集任务返程")
                return True
        elif run.status == "failed":
            return False
        if run.rally_id and run.status in {"gathering", "outbound"}:
            from .rallies import advance_rally_locked

            assert run.rally is not None
            return advance_rally_locked(run.rally, now)
        if run.status == "returning":
            Manor.objects.select_for_update().get(pk=run.member.manor_id)
            _finish_return(run, now)
            return True
        if run.status == "stationed" and run.kind == "pve" and run.result.get("collection_waves"):
            _advance_due_collection_locked(run, now)
            return True
        if run.status == "stationed":
            _start_return(run, now, "驻防时间结束，队伍返程")
            return True
        if run.status == "transporting":
            if run.kind in {"intercept", "escort"}:
                from .convoy_encounters import target_id

                convoy_id = target_id(run)
                convoy = SeasonExpedition.objects.filter(pk=convoy_id).first() if convoy_id is not None else None
                if convoy and convoy.status == "transporting":
                    return process_expedition(convoy.pk, now)
                run.result = {**run.result, "refund_quota": not bool(run.report_id)}
                _start_return(run, now, "车队已结束，队伍返程")
                return True
            if not campaign_open(season, now):
                _restore_cargo(run, now)
                _start_return(run, now, "赛季行动期已结束，运输物资原额退回，队伍返程")
                return True
            if run.result.get("activities_version") == 1:
                from .activities import settle_transport_locked

                try:
                    with transaction.atomic():
                        from .convoy_encounters import settle_locked

                        settle_locked(run, now)
                        reward = _settle_activity_reward(run, settle_transport_locked(run, now), now)
                except GameError as exc:
                    run.refresh_from_db()
                    _restore_cargo(run, now)
                    run.result = {**run.result, "refund_quota": not bool(run.report_id)}
                    _start_return(run, now, f"运输取消：{exc}；物资已原额退回")
                    return True
            else:
                _restore_cargo(
                    run, now, multiplier=campaign_rules(season)["expeditions"].get("delivery_multiplier", 1.25)
                )
                reward = grant_pve_locked(run.member, run.node_key, True, now, activity_key="transport")
            _record_progress_log(run, reward, now)
            _start_return(run, now, "运输完成，补给与护航产出已进入阵营仓库")
            return True
        try:
            with transaction.atomic():
                # 到达时再次校验开放窗口、归属和休整状态；变化则安全返程。
                mark_outbound_arrival_locked(run, now)
                # due_at 也用于失败退避；普通行军资格仍按真实发车时固定的抵达时间判断。
                validation_time = now
                if run.kind == "pve" and run.result.get("collection_waves"):
                    validation_time = min(now, run.launched_at + timedelta(seconds=run.travel_seconds))
                validate_destination(
                    season,
                    run.node_key,
                    run.kind,
                    validation_time,
                    legacy_modes=True,
                    city_arrival_at=run.launched_at + timedelta(seconds=run.travel_seconds),
                )
                if run.kind == "explore":
                    from .activities import settle_exploration_locked

                    reward = _settle_activity_reward(run, settle_exploration_locked(run, now), now)
                    _record_progress_log(run, reward, now)
                    _start_return(run, now, reward["message"])
                elif run.kind == "npc_intercept":
                    from .activities import settle_npc_intercept_locked

                    won = _fight(run, None, now)
                    reward = _settle_activity_reward(run, settle_npc_intercept_locked(run, won, now), now)
                    _record_progress_log(run, reward, now)
                    _start_return(run, now, reward["message"])
                elif run.kind == "pve":
                    waves = run.result.get("collection_waves")
                    if waves:
                        arrival = run.launched_at + timedelta(seconds=run.travel_seconds)
                        first = arrival + timedelta(seconds=waves["interval_seconds"])
                        waves = {**waves, "next_wave_at": first.isoformat()}
                        run.status = "stationed"
                        run.due_at = first
                        run.stationed_until = arrival + timedelta(
                            seconds=waves["interval_seconds"] * waves["max_waves"]
                        )
                        run.result = {**run.result, "collection_waves": waves, "message": "已抵达，等待第一场收集战斗"}
                        run.save(update_fields=["status", "due_at", "stationed_until", "result"])
                        _schedule(run)
                        _advance_due_collection_locked(run, now)
                    else:
                        won = _fight(run, None, now)
                        reward = grant_pve_locked(
                            run.member,
                            run.node_key,
                            won,
                            now,
                            event_key=f"run:{run.pk}:pve",
                            material_reward=run.result.get("material_reward"),
                        )
                        _record_progress_log(run, reward, now)
                        _start_return(run, now, reward["message"])
                elif run.kind == "transport":
                    Manor.objects.select_for_update().get(pk=run.member.manor_id)
                    _battle_state(run)
                    route_key = "river:docks" if run.node_key == "river:warehouse" else run.node_key
                    site = ensure_sites_locked(season, now)[route_key]
                    if site.owner not in {"", run.member.faction} and not run.result.get("activity", {}).get(
                        "npc_order"
                    ):
                        raise GameError("渡口已被敌方控制，运输取消")
                    run.status = "transporting"
                    exp = campaign_rules(season)["expeditions"]
                    wait_seconds = run.result.get(
                        "transport_seconds", exp.get("legacy_transport_seconds", exp["transport_seconds"])
                    )
                    run.due_at = now + timedelta(seconds=wait_seconds)
                    run.result = {
                        **run.result,
                        "message": (
                            "已进入运输窗口，窗口结束统一多人遭遇战，余货继续交付"
                            if run.result.get("activity", {}).get("encounter_version") == 1
                            else "已进入可争夺运输，最多遭遇一次拦截"
                        ),
                    }
                    run.save(update_fields=["status", "due_at", "result"])
                    _schedule(run)
                elif run.kind in {"intercept", "escort"}:
                    from .convoy_encounters import arrive_locked

                    if not arrive_locked(run, now):
                        if run.kind == "intercept":
                            _intercept(run, now)
                        else:
                            run.result = {**run.result, "refund_quota": True}
                            _start_return(run, now, "护送目标已结束，队伍返程")
                else:
                    _site_action(run, now)
        except GameError as exc:
            # 内层事务已回滚，不提交半场战斗、重复贡献或部分扣减。
            run.refresh_from_db()
            run.member.refresh_from_db()
            _restore_cargo(run, now)
            if feature_rules(season, "activities")["enabled"] and not run.report_id:
                run.result = {**run.result, "refund_quota": True}
            _start_return(run, now, f"行动条件变化：{exc}；队伍返程")
        return True


def _record_progress_log(run: SeasonExpedition, reward: dict[str, Any], now: datetime) -> None:
    SeasonAction.objects.create(
        member=run.member,
        token=uuid4(),
        request_hash="pve-settlement",
        kind="exploration" if run.kind == "explore" else "combat" if run.kind == "intercept" else "logistics",
        node_key=run.node_key,
        date=timezone.localdate(now),
        week=season_state(run.member.season, now)["week"],
        report=run.report,
        result={
            **reward,
            "run_id": run.pk,
            "report_url": reverse("battle:report_detail", kwargs={"pk": run.report_id}) if run.report_id else "",
        },
    )
    if run.report_id:
        from .report_mail import send_report_mail_locked

        send_report_mail_locked(
            run, run.report, now, reward.get("merit", 0) + reward.get("battle_merit", 0), reward=reward
        )


def advance_due_expeditions(now: datetime | None = None, limit: int = 100) -> dict[str, int]:
    from .rallies import advance_due_rallies

    now = now or timezone.now()
    from .supply import settle_supplies_locked

    for season in Season.objects.filter(closed=False, starts_at__lte=now):
        with transaction.atomic():
            locked = Season.objects.select_for_update().get(pk=season.pk)
            settle_supplies_locked(locked, now)
            from .logistics import settle_surplus_locked

            settle_surplus_locked(locked, now)
    advance_due_rallies(now, limit)
    result = {"processed": 0, "errors": 0}
    ids = list(
        SeasonExpedition.objects.filter(status__in=ACTIVE_STATUSES, due_at__lte=now)
        .filter(~Q(status="failed") | Q(kind="pve", result__has_key="collection_waves"))
        .order_by("due_at", "pk")
        .values_list("pk", flat=True)[:limit]
    )
    for pk in ids:
        try:
            result["processed"] += int(process_expedition(pk, now))
        except Exception:
            # 事务已回滚：保留出征资源，周期扫描可重试，玩家也可召回。
            logger.exception("season expedition settlement failed: run=%s", pk)
            # 暂缓失败队伍，避免前100支异常队伍永久堵塞后续所有到期队伍。
            SeasonExpedition.objects.filter(pk=pk, status__in=ACTIVE_STATUSES, due_at__lte=now).update(
                error="结算暂未完成，后台将重试；可主动召回",
                due_at=now + timedelta(seconds=60),
            )
            result["errors"] += 1
    return result
